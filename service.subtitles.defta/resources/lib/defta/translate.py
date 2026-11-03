"""Bir altyazıyı masaüstü motoruyla (subtrans) çevirir.

'Hızlı önce, kaliteli sonra' modunda iki çeviri aynı anda başlar:
  - hızlı: yalnızca izlenen yerden itibaren birkaç dakika (ayar; dizi 2, film 5 dk), 12 satırlık küçük
    parçalar aynı anda; bölüm analizi ve anlam kontrolü yok, mevcut seri
    sözlüğüyle; bitince bir kez yüklenir (geçici dosya, kalanı orijinal dilde)
  - kaliteli: bölüm analizi + seri sözlüğü güncellemesi -> çeviri -> anlam kontrolü -> kalıcı dosya, yüklenir
Sözlük yalnızca analizden beslenir; kaydedilen tek dosya kaliteli olandır. Anlam kontrolü olmazsa
kontrolsüz tam çeviri kaydedilip yüklenir.
Hızlı çeviri atlanır: iş yüklenmeyecekse (sıradaki bölüm), mod başkaysa ya da bölüm analizi kayıtlıysa
(yeniden çeviri, yarıda kalan iş): o zaman kaliteli çeviri de hızlı çeviri kadar sürer, ikisini yüklemek gereksiz.
API yanıtları cache.sqlite'ta tutulur: yarıda kalan iş yeniden başlarsa biten parçalar tekrar ödenmez."""
from __future__ import annotations

import asyncio
import json
import sys
import os
import time
from pathlib import Path

from . import kodi, langs, net

FAST_FIRST, QUALITY_ONLY, FAST_ONLY = 0, 1, 2
FAST_WINDOWS = (0, 60, 120, 180, 300)   # ayar: Otomatik (dizi 2 dk, film 5 dk) / 1 / 2 / 3 / 5 dk
FAST_CHUNK = 12                          # hızlı çeviride parça başına satır (küçük parça = hızlı yanıt)


def fast_window(is_episode: bool) -> int:
    """Hızlı çevirinin kapsadığı süre (sn): kaliteli çeviri gelene kadar yetsin."""
    sec = FAST_WINDOWS[min(4, max(0, kodi.setting_int("fast_window", 0)))]
    return sec or (120 if is_episode else 300)


def window_chunks(job, start_ms: int, window_s: int, size: int = FAST_CHUNK) -> list:
    """İzlenen yerden itibaren window_s saniyedeki satırlar, size'lık küçük parçalara bölünür (hepsi aynı
    anda gönderilir). Bağlam için önceki 3, sonraki 2 satır da gider."""
    from subtrans.chunking import Chunk
    times = {b.pos: (b.start_ms, b.end_ms) for b in job.srt.blocks}
    lo, hi = start_ms - 5000, start_ms + window_s * 1000
    order = list(job.translate_pos)
    sel = [p for p in order if times.get(p, (0, 0))[1] >= lo and times.get(p, (0, 0))[0] <= hi]
    if not sel:
        sel = order[:size]
    index = {p: i for i, p in enumerate(order)}
    out = []
    for n, i in enumerate(range(0, len(sel), size), 1):
        part = sel[i:i + size]
        a, b = index[part[0]], index[part[-1]]
        out.append(Chunk(number=n, targets=part, before=order[max(0, a - 3):a], after=order[b + 1:b + 3]))
    return out


def _prices() -> dict:
    try:
        with open(os.path.join(kodi.addon_dir(), "resources", "lib", "prices.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def build_settings():
    from subtrans.config import DEFAULTS, Settings, _merge
    mode = kodi.setting_int("mode", FAST_FIRST)
    sdh3 = ["remove", "keep", "translate"]
    raw = _merge(DEFAULTS, {
        "language": {"target": kodi.target_lang(), "source": "auto", "established_names": "use"},
        "provider": {"type": "openai_http", "base_url": kodi.setting("api_base").strip() or "https://api.openai.com/v1",
                     "model": kodi.setting("model", "gpt-6-luna").strip() or "gpt-6-luna",
                     "api_key": kodi.secret("openai_key"), "ssl_context": net.ssl_context(),
                     "user_agent": net.user_agent(), "threads": 16, "timeout_seconds": 120,
                     "max_output_tokens": 32000},
        "run": {"cache": True, "confirm_cost": False},
        "sdh": {"sound_notes": sdh3[min(2, kodi.setting_int("sound_notes", 0))],
                "speaker_labels": sdh3[min(2, kodi.setting_int("speaker_labels", 0))],
                "speaker_dashes": ["remove", "keep"][min(1, kodi.setting_int("speaker_dashes", 0))],
                "lyrics": ["translate", "keep", "auto"][min(2, kodi.setting_int("lyrics", 0))],
                "note_only_blocks": "delete"},
        "output": {"credit": kodi.setting("credit", "Thates").strip(), "format": "srt", "position_codes": "remove",
                   "suspect_report": False, "progress": "off", "residue_filter": True,
                   "format_tags": "keep" if kodi.setting_int("format_tags", 0) == 1 else "remove"},
        "glossary": {"enabled": True, "folder": kodi.profile_dir("glossaries"), "series": "auto"},
        "quality": {"review": mode != FAST_ONLY, "episode_analysis": True},
        "prices": _prices(),
    })
    base = Path(kodi.profile_dir())
    return Settings(raw=raw, base_dir=base), mode


class Translator:
    """Tek bir işi (job) yürütür. on_update(path, stage, percent): stage "fast" (geçici) ya da "final"."""

    def __init__(self, job: dict, on_update, should_stop=lambda: False):
        self.job = job
        self.on_update = on_update
        self.should_stop = should_stop
        self.cost = None
        self.unchecked = False      # anlam kontrolü olmadı, kontrolsüz tam çeviri yüklendi
        self._loop = None
        self._task = None

    # ------------------------------------------------------------------
    def run(self) -> str:
        """Çeviriyi bu iş parçacığında yürütür. stop() başka iş parçacığından çağrılabilir (Kodi kapanırken).
        Not: asyncio.run kullanılmaz (Kodi'de sinyal kurmaya çalışıp hata verir); seçici döngü elle kurulur."""
        kodi.attach_engine_log()
        loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            self._task = loop.create_task(self._run())
            return loop.run_until_complete(self._task)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            asyncio.set_event_loop(None)
            loop.close()
            self._loop = self._task = None

    def stop(self) -> None:
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    async def _run(self) -> str:
        from subtrans import app as sapp
        from subtrans.cache import CachedProvider, Store, sha
        from subtrans.config import load_rule_pack
        from subtrans.engine import Engine, FatalError, FileJob
        from subtrans.glossary import Glossary
        from subtrans.provider import ProviderError, make_provider
        from subtrans.subformats import read_subtitle, write_atomic
        from subtrans.srt_io import verify_structure
        from subtrans.textprep import SDH_CATEGORIES, resolve_decisions

        from .media import Media
        from .sources.base import Sub
        from . import store as dstore

        job = self.job
        m = Media.from_dict(job["media"])
        sub = Sub.from_dict(job["sub"])
        settings, mode = build_settings()
        target = job.get("target") or kodi.target_lang()
        settings.raw["language"]["target"] = target
        if not kodi.secret("openai_key"):
            raise RuntimeError("OpenAI API anahtarı girilmemiş (Defta ayarları > Genel)")
        src_path = Path(job["src_file"])
        src_lang = langs.norm(sub.lang) or "en"
        rules = load_rule_pack(target)
        decisions = {k: str(settings["sdh"][k]) for k in SDH_CATEGORIES}
        decisions["established_names"] = "use"
        decisions["note_only_blocks"] = "delete"
        decisions["format_tags"] = settings["output"]["format_tags"]
        series = m.series_name()
        out_path = Path(dstore.tr_path(m, sub, target))
        gl_dir = Path(settings["glossary"]["folder"])

        def make_job(engine, rel_name: str, memory) -> FileJob:
            srt = read_subtitle(src_path)
            if not srt.blocks:
                raise RuntimeError("altyazı dosyasında satır bulunamadı")
            fj = FileJob(srt=srt, rel=Path(rel_name), out_path=out_path, src_lang=src_lang,
                         decisions=resolve_decisions(decisions, src_lang), out_fmt="srt")
            if series:
                g = Glossary.load_or_new(gl_dir, series, target)
                g.lower_races = rules.lowercase_races
                fj.series, fj.glossary = series, g
                fj.instructions = sapp.series_instructions(settings, series)
            engine.plan(fj)
            fj.memory = memory
            return fj

        store = Store(Path(kodi.profile_dir()) / "cache.sqlite")
        model = settings["provider"]["model"]
        price = settings.price(model)
        try:
            inner = make_provider(settings["provider"])
        except ProviderError as e:
            store.close()
            raise RuntimeError(str(e)) from e
        engine = Engine(settings, None, rules, decisions)
        engine.store = store
        engine.main_price = price
        engine.provider = CachedProvider(inner, store, model, fresh=bool(job.get("fresh")))
        rel = f"{m.label()} [{sub.uid()}]{src_path.suffix}"
        fj = make_job(engine, rel, None)
        fj.memory = store.memory(sapp.memory_series(fj))

        # Hızlı çeviri gerekli mi? Analiz kayıtlıysa (yarıda kalmış iş sürüyor) kaliteli çeviri zaten hızlı gelir.
        analysed = fj.glossary is not None and store.get_stage(
            "an:" + sha(fj.glossary.series, str(model), "\n".join(b.text for b in fj.srt.blocks))) is not None
        use_fast = mode == FAST_FIRST and engine.review_on and bool(job.get("load")) and not analysed
        fast_engine = fast_job = fast_task = None
        if use_fast:
            fast_settings, _ = build_settings()
            fast_settings.raw["language"]["target"] = target
            fast_settings.raw["quality"] = {"review": False, "episode_analysis": False}
            fast_engine = Engine(fast_settings, None, rules, decisions)
            fast_engine.store = store
            fast_engine.main_price = price
            fast_engine.provider = CachedProvider(inner, store, model, fresh=bool(job.get("fresh")))
            fast_job = make_job(fast_engine, "fast-" + rel, None)
            # Yalnızca izlenen yerden itibaren birkaç dakika (ayar), küçük parçalarla: kaliteli çeviri gelene kadar
            start_ms = int(float(job.get("start_at") or 0) * 1000)
            fast_job.chunks = window_chunks(fast_job, start_ms, fast_window(m.is_episode))
        kodi.log(f"Çeviri başladı: {m.label()} <- {sub.source}:{sub.release or sub.sid} ({src_lang}->{target}), "
                 f"{len(fj.srt.blocks)} blok, {len(fj.chunks)} parça, mod={mode}, hızlı={'evet' if use_fast else 'hayır'}")
        t0 = time.monotonic()
        state = {"final": False}

        async def run_fast():
            try:
                await fast_engine.translate_phase(fast_job)
                if fast_job.status != "çevrildi":
                    kodi.log("hızlı çeviri başarısız: " + "; ".join(fast_job.errors), "warning")
                    return
                for pos in fast_job.translate_pos:         # pencere dışı: orijinal metin kalır
                    fast_job.results.setdefault(pos, fast_job.prepared[pos].send_text)
                await fast_engine.finish_phase(fast_job)
                if fast_job.status != "tamam" or state["final"] or self.should_stop():
                    return
                path = os.path.join(kodi.temp_dir(), f"defta-{self.job['id']}-fast.{target}.srt")
                write_atomic(Path(path), fast_engine.final_blocks(fast_job), "srt", fast_job.srt.doc)
                kodi.log(f"Hızlı çeviri hazır: {time.monotonic() - t0:.0f} sn, {len(fast_job.chunks)} parça "
                         f"({start_ms // 60000}. dakikadan itibaren)")
                self.on_update(path, "fast", 100)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  (hızlı çeviri olmazsa kaliteli olan yine gelir)
                kodi.log(f"hızlı çeviri hatası: {type(e).__name__}: {e}", "warning")

        try:
            if use_fast:
                fast_task = asyncio.ensure_future(run_fast())
            await sapp._build_glossaries(engine, [fj])
            if self.should_stop():
                raise asyncio.CancelledError()
            await engine.translate_phase(fj)
            if fj.status != "çevrildi":
                raise RuntimeError("; ".join(fj.errors) or "çeviri başarısız")
            try:
                await engine.finish_phase(fj)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                fj.status = "başarısız"
                fj.errors.append(f"{type(e).__name__}: {e}")
            if fj.status != "tamam":
                # Çeviri tamam, yalnızca anlam kontrolü/son adım olmadı: kontrolsüz tam çeviri kullanılır
                if fj.translate_pos and all(p in fj.results for p in fj.translate_pos):
                    kodi.log("anlam kontrolü olmadı (" + "; ".join(fj.errors)[:200] + "); kontrolsüz çeviri "
                             "kullanılıyor", "warning")
                    engine._assemble(fj)
                    self.unchecked = True
                else:
                    raise RuntimeError("; ".join(fj.errors) or "anlam kontrolü başarısız")
            errs = verify_structure(fj.srt.blocks, fj.out_blocks, allow_empty=fj.dropped)
            if errs:
                raise RuntimeError("yapı kontrolü: " + "; ".join(errs[:3]))
            state["final"] = True
            blocks = engine.final_blocks(fj)
            write_atomic(out_path, blocks, "srt", fj.srt.doc)
        except FatalError as e:
            raise RuntimeError(str(e)) from e
        finally:
            state["final"] = True
            if fast_task is not None and not fast_task.done():
                fast_task.cancel()
            if fast_task is not None:
                try:
                    await fast_task
                except BaseException:  # noqa: BLE001
                    pass
            provs = [engine.provider, engine.review_provider]
            if fast_engine is not None:
                provs += [fast_engine.provider, fast_engine.review_provider]
            for prov in {id(x): x for x in provs if x is not None}.values():
                try:
                    await prov.close()
                except Exception:  # noqa: BLE001
                    pass
            store.close()
        self.cost = fj.usage.cost(price)
        if fast_job is not None and self.cost is not None:
            fc = fast_job.usage.cost(price)
            self.cost = self.cost + (fc or 0.0)
        kodi.log(f"Çeviri bitti: {m.label()} {len(fj.chunks)} parça, {fj.usage.requests} istek, "
                 f"{fj.usage.input_tokens}+{fj.usage.output_tokens} token, maliyet "
                 f"{'$%.4f' % self.cost if self.cost is not None else '?'}"
                 + (" (hızlı çeviri dahil)" if fast_job is not None else "")
                 + f", {time.monotonic() - t0:.0f} sn, anlam kontrolü {fj.review_fixes} düzeltme")
        dstore.tr_record(m, sub, str(out_path), "unchecked" if self.unchecked else "final", self.cost)
        self.on_update(str(out_path), "unchecked" if self.unchecked else "final", 100)
        return str(out_path)
