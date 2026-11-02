"""Arka plan servisi: çeviri işlerini sırayla yürütür, çeviriyi oynatıcıya parça parça yükler,
ayar açıksa video başlayınca altyazıyı kendiliğinden bulur/çevirir."""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

import xbmc

from . import fetch, kodi, langs, search, store
from .media import Media, from_player
from .sources.base import SourceError


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
            self.media = from_player()
        except Exception as e:  # noqa: BLE001
            kodi.log(f"video bilgisi alınamadı: {e}", "warning")
            self.media = None
            return
        if any(kodi.setting_bool(k) for k in ("auto_human", "auto_ai", "auto_translate")):
            threading.Thread(target=self.service.autoload, args=(self.media, self.file), daemon=True).start()

    def onPlayBackStopped(self):  # noqa: N802
        self.media, self.file = None, ""

    def onPlayBackEnded(self):  # noqa: N802
        self.media, self.file = None, ""

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

    # ------------------------------------------------------------------ ana döngü
    def run(self) -> None:
        kodi.log(f"Servis başladı (Defta {kodi.version()}, Python {'.'.join(map(str, sys.version_info[:3]))})")
        fetch.clean_temp()
        try:
            n = store.scrub_secrets_once()
            if n:
                kodi.log(f"Güvenlik: eski dosyalarda {n} yerde anahtar izi temizlendi")
        except Exception as e:  # noqa: BLE001
            kodi.log(f"anahtar temizliği yapılamadı: {e}", "warning")
        if kodi.setting_bool("debug") and kodi.setting("debug_dir").strip():
            copied = kodi.export_kodi_logs()          # önceki oturum çöktüyse kodi.old.log sebebini gösterir
            if copied:
                kodi.log("Kodi kayıtları kayıt klasörüne kopyalandı: " + ", ".join(copied), "debug")
        store.prune_jobs()
        for j in store.list_jobs("running"):          # Kodi kapanınca yarıda kalan işler: kaldığı yerden sürer
            j["status"] = "pending"
            j["load"] = False
            store.save_job(j)
        seen = -1.0
        while not self.monitor.abortRequested():
            if self.worker is None or not self.worker.is_alive():
                stamp = store.queue_stamp()
                if stamp != seen:                     # yalnızca yeni iş eklendiyse klasöre bakılır
                    pending = store.list_jobs("pending")
                    if pending:
                        self.worker = threading.Thread(target=self._run_job, args=(pending[0],), daemon=True)
                        self.worker.start()
                    else:
                        seen = stamp
            if self.monitor.waitForAbort(1):
                break
        # Kodi kapanıyor: süren çeviri durdurulur (iş "bekliyor"a döner, açılışta kaldığı yerden sürer)
        if self.translator is not None:
            self.translator.stop()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(8)
        kodi.log("Servis durdu")

    # ------------------------------------------------------------------ iş
    def _run_job(self, job: dict) -> None:
        from .translate import Translator
        m = Media.from_dict(job["media"])
        job["status"] = "running"
        job["started"] = time.time()
        store.save_job(job)
        state = {"first": True, "fast": False}
        tname = langs.tr_name(job.get("target") or "tr")

        def on_update(path: str, stage: str, pct: int) -> None:
            job["last_file"] = path
            job["progress"] = pct
            store.save_job(job)
            if not job.get("load") or not self.player.is_playing(m):
                return
            try:
                self.player.setSubtitles(path)
            except Exception as e:  # noqa: BLE001
                kodi.log(f"altyazı yüklenemedi: {e}", "warning")
                return
            kodi.log(f"Oynatıcıya yüklendi ({stage}, %{pct}): {os.path.basename(path)}", "debug")
            if stage == "partial" and state["first"]:
                state["first"] = False
                kodi.notify(f"{tname} çeviri yükleniyor (%{pct}). Kalanı geldikçe yerine geçecek.", "Defta", 5000)
            elif stage == "fast" and not state["fast"]:
                state["fast"] = True
                kodi.notify("Çeviri tamam. Anlam kontrolü arkada sürüyor.", "Defta", 5000)
            elif stage == "final":
                kodi.notify("Kontrol edilmiş çeviri yüklendi.", "Defta", 5000)

        import asyncio
        try:
            t = Translator(job, on_update, should_stop=self.monitor.abortRequested)
            self.translator = t
            out = t.run()
            job["status"] = "done"
            job["out_file"] = out
            job["cost"] = t.cost
            if not job.get("load") or not self.player.is_playing(m):
                kodi.notify(f"{m.label()}: {tname} AI çevirisi hazır.", "Defta", 6000)
        except asyncio.CancelledError:
            job["status"] = "pending"                 # Kodi kapanırken durduruldu: açılışta sürer
            job["load"] = False
            kodi.log(f"Çeviri durduruldu, Kodi açılınca kaldığı yerden sürecek: {m.label()}")
        except Exception as e:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
            kodi.log(f"Çeviri başarısız ({m.label()}): {job['error']}\n{traceback.format_exc()}", "error")
            kodi.notify(f"Çeviri başarısız: {str(e)[:120]}", "Defta", 9000)
        finally:
            self.translator = None
            job["finished"] = time.time()
            store.save_job(job)

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
            ready = store.ready_translations(m)
            if ready and not kodi.setting_bool("auto_human"):
                self.player.setSubtitles(fetch.for_kodi(ready[0]["path"], m.label() + " Defta", tgt))
                kodi.notify("Hazır AI çevirisi yüklendi.", "Defta", 4000)
                return
        res = search.search(m)
        if not self.player.is_playing(m):
            return
        if kodi.setting_bool("auto_human") and res.human:
            s = res.human[0]
            try:
                self.player.setSubtitles(fetch.for_kodi(fetch.get(s, m), m.label(), tgt))
                kodi.notify(f"{langs.tr_name(tgt)} altyazı yüklendi ({s.source}).", "Defta", 4000)
                return
            except SourceError as e:
                kodi.log(f"otomatik indirme başarısız: {e}", "warning")
        if kodi.setting_bool("auto_ai"):
            ready = store.ready_translations(m)
            if ready:
                self.player.setSubtitles(fetch.for_kodi(ready[0]["path"], m.label() + " Defta", tgt))
                kodi.notify("Hazır AI çevirisi yüklendi.", "Defta", 4000)
                return
        if not kodi.setting_bool("auto_translate") or res.human:
            return
        best = search.best_foreign(res)
        # Yalnızca eşleşmeden eminken: IMDb numarası var ya da sürüm adı uyuyor
        if best is None or not (m.imdb or best.score >= 3):
            if res.foreign:
                kodi.notify(f"{langs.tr_name(tgt)} altyazı yok. Altyazı listesinden çevrilecek birini seçebilirsiniz.",
                            "Defta", 7000)
            return
        if store.running_job_for(m, best) or not kodi.secret("openai_key"):
            return
        try:
            src = fetch.get(best, m)
        except SourceError as e:
            kodi.log(f"otomatik çeviri için indirme başarısız: {e}", "warning")
            return
        store.new_job(m, best, src, fresh=False, load=True)
        kodi.notify(f"{langs.tr_name(tgt)} altyazı yok, AI çevirisi başladı.", "Defta", 5000)


def main() -> None:
    try:
        Service().run()
    except Exception as e:  # noqa: BLE001
        kodi.log(f"Servis çöktü: {type(e).__name__}: {e}\n{traceback.format_exc()}", "error")
