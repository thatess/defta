"""Arka plan servisi: çeviri işlerini sırayla yürütür ve oynatıcıya yükler (hızlı + kaliteli, en çok iki kez),
ayar açıksa video başlayınca altyazıyı kendiliğinden bulur/çevirir, dizide sıradaki bölümü önceden çevirir.
Aynı video için yeni bir seçim yapılınca süren iş iptal edilir (son seçilen kazanır)."""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

import xbmc
import xbmcgui

from . import fetch, kodi, langs, search, store
from .media import Media, from_player
from .sources.base import SourceError, Sub


PREFETCH_STEPS = (0.80, 0.85, 0.90, 0.95)   # ayar: bölümün bu kadarı izlenince (varsayılan %90; Red Light %95'te sorar)


def prefetch_at() -> float:
    return PREFETCH_STEPS[min(3, max(0, kodi.setting_int("prefetch_at", 2)))]


class Player(xbmc.Player):
    def __init__(self, service: "Service"):
        super().__init__()
        self.service = service
        self.media: Media | None = None
        self.file = ""

    def onAVStarted(self):  # noqa: N802 (Kodi API adı)
        try:
            self.file = self.getPlayingFile() if self.isPlayingVideo() else ""
        except RuntimeError:
            self.file = ""
        if not self.file:
            return
        try:
            xbmcgui.Window(10000).setProperty("defta.play_start", str(time.time()))   # eklenti betiği okur
        except Exception:  # noqa: BLE001
            pass
        try:
            self.media = from_player()
        except Exception as e:  # noqa: BLE001
            kodi.log(f"video bilgisi alınamadı: {e}", "warning")
            self.media = None
            return
        try:
            if store.want_load(self.media):
                kodi.log(f"Önceden başlamış çeviri bitince yüklenecek: {self.media.label()}")
        except Exception:  # noqa: BLE001
            pass
        uid = store.take_requested(self.media)
        if uid:
            # Sıradaki bölüm için "Çevir" denmişti: hazırsa ayara bakılmadan yüklenir
            threading.Thread(target=self.service.load_requested, args=(self.media, uid), daemon=True).start()
            return
        if any(kodi.setting_bool(k) for k in ("auto_human", "auto_ai", "auto_translate")):
            threading.Thread(target=self.service.autoload, args=(self.media, self.file), daemon=True).start()

    def onPlayBackStopped(self):  # noqa: N802
        self.media, self.file = None, ""

    def onPlayBackEnded(self):  # noqa: N802
        self.media, self.file = None, ""

    def progress(self) -> tuple[float, float]:
        """(geçen, toplam) saniye; oynatılmıyorsa (0, 0)."""
        try:
            if not self.isPlayingVideo():
                return 0.0, 0.0
            return float(self.getTime() or 0), float(self.getTotalTime() or 0)
        except RuntimeError:
            return 0.0, 0.0

    def is_playing(self, m: Media) -> bool:
        try:
            if not self.isPlayingVideo():
                return False
            playing = self.getPlayingFile()
        except RuntimeError:
            return False
        if m.path and playing == m.path:
            return True
        return self.media is not None and self.media.key() == m.key()


class Service:
    def __init__(self):
        self.monitor = xbmc.Monitor()
        self.player = Player(self)
        self.worker: threading.Thread | None = None
        self.translator = None
        self.current_job = ""
        self.need_clean = False
        self.prefetched: set = set()          # bu oturumda sıradaki bölümü için karar verilmiş videolar
        self.prefetcher: threading.Thread | None = None

    # ------------------------------------------------------------------ ana döngü
    def run(self) -> None:
        kodi.log(f"Servis başladı (Defta {kodi.version()}, Python {'.'.join(map(str, sys.version_info[:3]))})")
        try:
            n = store.scrub_secrets_once()
            if kodi.scrub_kodi_old_log():
                n += 1
                kodi.log("Güvenlik: Kodi'nin önceki oturum kaydındaki (kodi.old.log) eski anahtar izi temizlendi")
            if n:
                kodi.log(f"Güvenlik: eski dosyalarda {n} yerde anahtar izi temizlendi")
        except Exception as e:  # noqa: BLE001
            kodi.log(f"anahtar temizliği yapılamadı: {e}", "warning")
        if kodi.setting_bool("debug") and kodi.setting("debug_dir").strip():
            copied = kodi.export_kodi_logs()          # önceki oturum çöktüyse kodi.old.log sebebini gösterir
            if copied:
                kodi.log("Kodi kayıtları kayıt klasörüne kopyalandı: " + ", ".join(copied), "debug")
        self._clean("açılış")
        for j in store.list_jobs("running"):          # Kodi kapanınca yarıda kalan işler: kaldığı yerden sürer
            j["status"] = "pending"
            j["load"] = False
            store.save_job(j)
        seen = -1.0
        tick = 0
        while not self.monitor.abortRequested():
            tick += 1
            if self.worker is not None and self.worker.is_alive():
                # Aynı video için yeni seçim yapıldıysa süren iş iptal edilmiştir: çeviri durdurulur
                if self.translator is not None and self.current_job and \
                        store.job_status(self.current_job) == "cancelled":
                    kodi.log(f"İş iptal edildi (yeni seçim): {self.current_job}")
                    self.translator.stop()
            else:
                stamp = store.queue_stamp()
                if stamp != seen:                     # yalnızca yeni iş eklendiyse klasöre bakılır
                    pending = store.list_jobs("pending")
                    if pending:
                        self.worker = threading.Thread(target=self._run_job, args=(pending[0],), daemon=True)
                        self.worker.start()
                    else:
                        seen = stamp
                if (self.worker is None or not self.worker.is_alive()) and \
                        (self.need_clean or time.time() - store.last_clean() >= store.CLEAN_EVERY):
                    self._clean("çeviri bitti" if self.need_clean else "6 saatlik")
            if tick % 2 == 0:
                self._check_prefetch()
            if self.monitor.waitForAbort(1):
                break
        # Kodi kapanıyor: süren çeviri durdurulur (iş "bekliyor"a döner, açılışta kaldığı yerden sürer)
        if self.translator is not None:
            self.translator.stop()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(8)
        kodi.log("Servis durdu")
        kodi.flush_log()

    # ------------------------------------------------------------------ temizlik
    def _clean(self, why: str) -> None:
        self.need_clean = False
        try:
            n = store.auto_clean()
        except Exception as e:  # noqa: BLE001
            kodi.log(f"temizlik yapılamadı: {type(e).__name__}: {e}", "warning")
            return
        if any(n.values()):
            kodi.log("Temizlik (" + why + "): " + ", ".join(f"{k} {v}" for k, v in n.items() if v), "debug")

    # ------------------------------------------------------------------ iş
    def _save(self, job: dict) -> None:
        """İş dosyası yazılırken başka yerden verilmiş kararlar ezilmez ("iptal", "bitince yükle")."""
        disk = store.get_job(job["id"]) or {}
        if job.get("status") in ("running", "pending") and disk.get("status") == "cancelled":
            job["status"] = "cancelled"
        if disk.get("load"):
            job["load"] = True
        store.save_job(job)

    def _run_job(self, job: dict) -> None:
        from .translate import Translator
        m = Media.from_dict(job["media"])
        job["status"] = "running"
        job["started"] = time.time()
        self._save(job)
        if job["status"] == "cancelled":
            return
        self.current_job = job["id"]
        state = {"fast_file": ""}

        def cancelled() -> bool:
            return job.get("status") == "cancelled" or store.job_status(job["id"]) == "cancelled"

        def on_update(path: str, stage: str, pct: int) -> None:
            if stage == "fast":
                state["fast_file"] = path
            job["last_file"] = path
            job["progress"] = pct
            self._save(job)
            if cancelled() or not job.get("load") or not self.player.is_playing(m):
                return
            try:
                self.player.setSubtitles(path if stage == "fast" else
                                         fetch.for_kodi(path, m.label() + " Defta", job.get("target") or "tr"))
            except Exception as e:  # noqa: BLE001
                kodi.log(f"altyazı yüklenemedi: {e}", "warning")
                return
            if stage != "fast":
                store.set_loaded(m, "ai", Sub.from_dict(job["sub"]))   # listede etkin satır olsun
            kodi.log(f"Oynatıcıya yüklendi ({stage}): {os.path.basename(path)}", "debug")
            if stage == "fast":
                kodi.notify("Hızlı çeviri yüklendi")
            elif stage == "unchecked":
                kodi.notify("Çeviri yüklendi (kontrolsüz)")
            else:
                kodi.notify("Kaliteli çeviri yüklendi")

        import asyncio
        try:
            t = Translator(job, on_update, should_stop=lambda: self.monitor.abortRequested() or cancelled())
            self.translator = t
            out = t.run()
            job["status"] = "done"
            job["out_file"] = out
            job["cost"] = t.cost
            if not job.get("load") or not self.player.is_playing(m):
                kodi.notify(f"{m.short()} çevirisi hazır")
        except asyncio.CancelledError:
            if cancelled():
                job["status"] = "cancelled"           # yeni seçim yapıldı; biten parçalar önbellekte kalır
                kodi.log(f"Çeviri iptal edildi (yeni seçim): {m.label()}")
            else:
                job["status"] = "pending"             # Kodi kapanırken durduruldu: açılışta sürer
                job["load"] = False
                kodi.log(f"Çeviri durduruldu, Kodi açılınca kaldığı yerden sürecek: {m.label()}")
        except Exception as e:  # noqa: BLE001
            if cancelled():
                job["status"] = "cancelled"
            else:
                job["status"] = "failed"
                job["error"] = f"{type(e).__name__}: {e}"
                kodi.log(f"Çeviri başarısız ({m.label()}): {job['error']}\n{traceback.format_exc()}", "error")
                kodi.notify(f"Çeviri olmadı: {str(e)[:70]}", error=True)
        finally:
            self.translator = None
            self.current_job = ""
            self.need_clean = True
            job["finished"] = time.time()
            self._save(job)
            if state["fast_file"] and job.get("status") != "cancelled":
                try:
                    os.remove(state["fast_file"])         # hızlı çeviri geçiciydi; yalnızca kaliteli olan saklanır
                except OSError:
                    pass

    # ------------------------------------------------------------------ sıradaki bölüm
    def _check_prefetch(self) -> None:
        mode = kodi.setting_int("prefetch_next", 0)              # 0 kapalı, 1 sor, 2 otomatik
        m = self.player.media
        if mode not in (1, 2) or m is None or not m.is_episode:
            return
        if self.prefetcher is not None and self.prefetcher.is_alive():
            return
        key = m.key()
        if key in self.prefetched:
            return
        pos, total = self.player.progress()
        if total < 300 or pos < prefetch_at() * total:
            return
        self.prefetched.add(key)
        kodi.log(f"Sıradaki bölüm denetimi: {m.label()} %{int(100 * pos / total)} izlendi "
                 f"({'sor' if mode == 1 else 'otomatik'})")
        self.prefetcher = threading.Thread(target=self._prefetch, args=(m, mode), daemon=True)
        self.prefetcher.start()

    def _prefetch(self, m: Media, mode: int) -> None:
        try:
            self._prefetch_inner(m, mode)
        except Exception as e:  # noqa: BLE001
            kodi.log(f"sıradaki bölüm hazırlanamadı: {type(e).__name__}: {e}\n{traceback.format_exc()}", "warning")

    def _prefetch_inner(self, m: Media, mode: int) -> None:
        tgt = kodi.target_lang()
        tname = langs.tr_name(tgt)
        if not kodi.secret("openai_key"):
            kodi.notify("OpenAI anahtarı yok (Ayarlar > Genel)", error=True)
            return
        nm, res = None, None
        for next_season in (False, True):
            cand = m.next_episode(next_season=next_season)
            if store.ready_translations(cand):
                kodi.log(f"Sıradaki bölüm ({cand.label()}) için hazır çeviri var")
                kodi.notify(f"{cand.short()} çevirisi hazır")
                return
            r = search.search(cand)
            if r.human or r.foreign:
                nm, res = cand, r
                break
        if nm is None:
            kodi.log(f"Sıradaki bölüm için altyazı bulunamadı ({m.label()} sonrası)")
            kodi.notify("Sıradaki bölüme altyazı yok")
            return
        if res.human:
            kodi.log(f"Sıradaki bölümde ({nm.label()}) {tname} altyazı var, çevrilmeyecek")
            kodi.notify(f"{nm.short()}: {tname} altyazı var")
            return
        best = search.best_foreign(res)
        if best is None:
            kodi.notify("Sıradaki bölüme altyazı yok")
            return
        if any(Media.from_dict(j["media"]).key() == nm.key() for j in store.list_jobs()
               if j.get("status") in ("pending", "running")):
            kodi.notify(f"{nm.short()} zaten çevriliyor")
            return
        if mode == 1:
            from . import nextdialog
            line2 = f"{nm.title} · {nm.short()}" + (f" · {best.note}" if best.note else "")
            kodi.log(f"Sıradaki bölüm soruluyor: {nm.label()}")
            ok, why = nextdialog.ask("Sıradaki bölüm çevrilsin mi?", line2, 15,
                                     should_close=lambda: self.monitor.abortRequested()
                                     or not self.player.is_playing(m))
            kodi.log(f"Sıradaki bölüm: {why}")
            if not ok:
                return
        try:
            src, state = fetch.get_checked(best, nm)
        except SourceError as e:
            kodi.log(f"sıradaki bölüm için indirme başarısız: {e}", "warning")
            return
        store.new_job(nm, best, src, fresh=False, load=False)
        store.request_load(nm, best)            # o bölüm açılınca ayara bakılmadan yüklensin
        kodi.log(f"Sıradaki bölüm çeviriye alındı: {nm.label()} <- {best.source}:{best.release or best.sid}"
                 + (f" ({best.note})" if best.note else ""))
        kodi.notify(f"{nm.short()} çevriliyor")

    # ------------------------------------------------------------------ istenmiş sıradaki bölüm
    def load_requested(self, m: Media, uid: str) -> None:
        try:
            if self.monitor.waitForAbort(2) or not self.player.is_playing(m):
                return
            e = next((e for e in store.ready_translations(m) if e.get("uid") == uid), None)
            if e is None:
                return              # henüz bitmedi: iş "bitince yükle" olarak işaretlendi (want_load)
            tgt = kodi.target_lang()
            self.player.setSubtitles(fetch.for_kodi(e["path"], m.label() + " Defta", tgt))
            store.set_loaded(m, "ai", Sub.from_dict(e["src"]))
            kodi.log(f"İstenen sıradaki bölüm çevirisi yüklendi: {m.label()}")
            kodi.notify(f"{m.short()} çevirisi yüklendi")
        except Exception as e:  # noqa: BLE001
            kodi.log(f"istenen çeviri yüklenemedi: {type(e).__name__}: {e}", "warning")

    # ------------------------------------------------------------------ otomatik yükleme
    def autoload(self, m: Media, file: str) -> None:
        try:
            self._autoload(m, file)
        except Exception as e:  # noqa: BLE001
            kodi.log(f"otomatik yükleme hatası: {type(e).__name__}: {e}\n{traceback.format_exc()}", "error")

    def _has_target_stream(self, tgt: str) -> bool:
        try:
            streams = self.player.getAvailableSubtitleStreams() or []
        except RuntimeError:
            return False
        return any(langs.norm(s) == tgt for s in streams)

    def _autoload(self, m: Media, file: str) -> None:
        if self.monitor.waitForAbort(3):
            return
        if not self.player.is_playing(m):
            return
        tgt = kodi.target_lang()
        if self._has_target_stream(tgt):
            kodi.log(f"Otomatik: videoda zaten {langs.tr_name(tgt)} altyazı var, bir şey yapılmadı")
            return
        if kodi.setting_bool("auto_ai"):
            ready = self._good_ready(m)
            if ready and not kodi.setting_bool("auto_human"):
                self._load_ready(m, ready, tgt)
                return
        res = search.search(m)
        if not self.player.is_playing(m):
            return
        human = [s for s in res.human if s.note != "kaymış"]
        if kodi.setting_bool("auto_human") and human:
            s = human[0]
            try:
                path, state = fetch.get_checked(s, m)
                self.player.setSubtitles(fetch.for_kodi(path, m.label(), tgt))
                store.set_loaded(m, "human", s)
                store.remember_choice(m, s)
                kodi.notify(f"{langs.tr_name(tgt)} altyazı yüklendi"
                            + (" · " + fetch.SYNC_NOTE[state] if state in fetch.SYNC_NOTE else ""))
                return
            except SourceError as e:
                kodi.log(f"otomatik indirme başarısız: {e}", "warning")
        if kodi.setting_bool("auto_ai"):
            ready = self._good_ready(m)
            if ready:
                self._load_ready(m, ready, tgt)
                return
        if not kodi.setting_bool("auto_translate") or res.human:
            return
        best = search.best_foreign(res)
        # Yalnızca eşleşmeden eminken: IMDb numarası var ya da sürüm adı uyuyor
        if best is None or not (m.imdb or best.score >= 3):
            if res.foreign:
                kodi.notify(f"{langs.tr_name(tgt)} altyazı yok · listeden çevirebilirsin")
            return
        if store.running_job_for(m, best) or not kodi.secret("openai_key"):   # (sürmekte olan iş yüklenecek)
            return
        try:
            src, _ = fetch.get_checked(best, m)
        except SourceError as e:
            kodi.log(f"otomatik çeviri için indirme başarısız: {e}", "warning")
            return
        store.new_job(m, best, src, fresh=False, load=True, start_at=self.player.progress()[0])
        store.set_loaded(m, "ai", best)
        store.remember_choice(m, best)
        kodi.notify(f"{langs.tr_name(tgt)} altyazı yok · çeviri başladı")

    @staticmethod
    def _good_ready(m: Media) -> dict | None:
        bad = store.bad_uids(m)
        return next((e for e in store.ready_translations(m) if e.get("uid") not in bad), None)

    def _load_ready(self, m: Media, e: dict, tgt: str) -> None:
        from .sources.base import Sub
        self.player.setSubtitles(fetch.for_kodi(e["path"], m.label() + " Defta", tgt))
        store.set_loaded(m, "ai", Sub.from_dict(e["src"]))
        kodi.notify("Hazır çeviri yüklendi")


def main() -> None:
    try:
        Service().run()
    except Exception as e:  # noqa: BLE001
        kodi.log(f"Servis çöktü: {type(e).__name__}: {e}\n{traceback.format_exc()}", "error")
