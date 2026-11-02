"""Bir altyazıyı masaüstü motoruyla (subtrans) çevirir.

Aşamalar:
  1. bölüm analizi + seri sözlüğü (isimler her bölümde aynı yazılsın)
  2. çeviri: parçalar paralel gider; her parça bitince ara dosya yazılır (on_update) -> Kodi'de yüklenir
  3. 'hızlı önce' modunda anlam kontrolünden önceki tam çeviri yüklenir
  4. anlam kontrolü + son düzeltmeler -> kalıcı dosya
API yanıtları cache.sqlite'ta tutulur: yarıda kalan iş yeniden başlarsa biten parçalar tekrar ödenmez."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from . import kodi, langs, net

FAST_FIRST, QUALITY_ONLY, FAST_ONLY = 0, 1, 2


def _prices() -> dict:
    try:
        with open(os.path.join(kodi.addon_dir(), "resources", "lib", "prices.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def build_settings(fresh: bool = False):
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
                   "suspect_report": False, "progress": "off"},
        "glossary": {"enabled": True, "folder": kodi.profile_dir("glossaries"), "series": "auto"},
        "quality": {"review": mode != FAST_ONLY, "episode_analysis": True},
        "prices": _prices(),
    })
    base = Path(kodi.profile_dir())
    return Settings(raw=raw, base_dir=base), mode


class Translator:
    """Tek bir işi (job) yürütür. on_update(path, stage, percent) Kodi'ye yükleme için çağrılır."""

    def __init__(self, job: dict, on_update, should_stop=lambda: False):
        self.job = job
        self.on_update = on_update
        self.should_stop = should_stop
        self.done_chunks = 0
        self.total_chunks = 1
        self.partial: dict = {}
        self.last_write = 0.0
        self.seq = 0
        self.cost = None

    # ------------------------------------------------------------------
    def run(self) -> str:
        kodi.attach_engine_log()
        return kodi.run_async(self._run())

    async def _run(self) -> str:
        from subtrans import app as sapp
        from subtrans.cache import CachedProvider, Store
        from subtrans.config import load_rule_pack
        from subtrans.engine import Engine, FatalError, FileJob
        from subtrans.glossary import Glossary
        from subtrans.provider import ProviderError, Usage, make_provider
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
        srt = read_subtitle(src_path)
        if not srt.blocks:
            raise RuntimeError("altyazı dosyasında satır bulunamadı")
        src_lang = langs.norm(sub.lang) or "en"
        rules = load_rule_pack(target)
        decisions = {k: str(settings["sdh"][k]) for k in SDH_CATEGORIES}
        decisions["established_names"] = "use"
        decisions["note_only_blocks"] = "delete"
        engine = Engine(settings, None, rules, decisions)
        out_path = Path(dstore.tr_path(m, sub, target))
        rel = Path(f"{m.label()} [{sub.uid()}]{src_path.suffix}")
        fj = FileJob(srt=srt, rel=rel, out_path=out_path, src_lang=src_lang,
                     decisions=resolve_decisions(decisions, src_lang), out_fmt="srt")
        series = m.series_name()
        if series:
            g = Glossary.load_or_new(Path(settings["glossary"]["folder"]), series, target)
            g.lower_races = rules.lowercase_races
            fj.series, fj.glossary = series, g
            fj.instructions = sapp.series_instructions(settings, series)
        engine.plan(fj)
        store = Store(Path(kodi.profile_dir()) / "cache.sqlite")
        model = settings["provider"]["model"]
        price = settings.price(model)
        engine.store = store
        engine.main_price = price
        try:
            inner = make_provider(settings["provider"])
        except ProviderError as e:
            raise RuntimeError(str(e)) from e
        engine.provider = CachedProvider(inner, store, model, fresh=bool(job.get("fresh")))
        fj.memory = store.memory(sapp.memory_series(fj))
        self.total_chunks = max(1, len(fj.chunks))
        kodi.log(f"Çeviri başladı: {m.label()} <- {sub.source}:{sub.release or sub.sid} ({src_lang}->{target}), "
                 f"{len(srt.blocks)} blok, {len(fj.chunks)} parça, mod={mode}")
        t0 = time.monotonic()
        try:
            await sapp._build_glossaries(engine, [fj])
            if self.should_stop():
                raise RuntimeError("durduruldu")

            def on_chunk(job_, res):
                self.done_chunks += 1
                self.partial.update(res)
                if mode == QUALITY_ONLY or self.should_stop():
                    return
                now = time.monotonic()
                if now - self.last_write >= 6 or self.done_chunks == self.total_chunks:
                    self.last_write = now
                    self._write_partial(engine, fj, write_atomic)

            engine.on_chunk = on_chunk
            await engine.translate_phase(fj)
            if fj.status != "çevrildi":
                raise RuntimeError("; ".join(fj.errors) or "çeviri başarısız")
            if mode == FAST_FIRST and engine.review_on:
                self._write_partial(engine, fj, write_atomic, stage="fast")
            await engine.finish_phase(fj)
            if fj.status != "tamam":
                raise RuntimeError("; ".join(fj.errors) or "anlam kontrolü başarısız")
            errs = verify_structure(fj.srt.blocks, fj.out_blocks, allow_empty=fj.dropped)
            if errs:
                raise RuntimeError("yapı kontrolü: " + "; ".join(errs[:3]))
            blocks = engine.final_blocks(fj)
            write_atomic(out_path, blocks, "srt", fj.srt.doc)
            sapp.record_written(store, fj, blocks, out_path.parent)
        except FatalError as e:
            raise RuntimeError(str(e)) from e
        finally:
            for prov in {id(x): x for x in (engine.provider, engine.review_provider) if x is not None}.values():
                try:
                    await prov.close()
                except Exception:  # noqa: BLE001
                    pass
            store.close()
        self.cost = fj.usage.cost(price)
        kodi.log(f"Çeviri bitti: {m.label()} {len(fj.chunks)} parça, {fj.usage.requests} istek, "
                 f"{fj.usage.input_tokens}+{fj.usage.output_tokens} token, maliyet "
                 f"{'$%.4f' % self.cost if self.cost is not None else '?'}, {time.monotonic() - t0:.0f} sn, "
                 f"anlam kontrolü {fj.review_fixes} düzeltme")
        dstore.tr_record(m, sub, str(out_path), "final", self.cost)
        self.on_update(str(out_path), "final", 100)
        return str(out_path)

    # ------------------------------------------------------------------
    def _write_partial(self, engine, fj, write_atomic, stage: str = "partial") -> None:
        """Bitmiş parçalar çevrilmiş, kalanlar orijinal hâliyle bir ara dosya yazar ve yükletir."""
        results = dict(fj.results) if stage == "fast" else dict(self.partial)
        if not results:
            return
        saved = (fj.results, fj.out_blocks, fj.suspects, fj.dropped, fj.memory_used,
                 fj.conlang_translated, fj.glossary_misses)
        try:
            fj.results = {pos: results.get(pos, fj.prepared[pos].send_text) for pos in fj.translate_pos}
            fj.suspects = {}
            check, engine._check_glossary = engine._check_glossary, (lambda j: None)  # ara dosyada sözlük denetimi yok
            try:
                engine._assemble(fj)
            finally:
                engine._check_glossary = check
            blocks = engine.final_blocks(fj)
        except Exception as e:  # noqa: BLE001
            kodi.log(f"ara dosya hazırlanamadı: {type(e).__name__}: {e}", "debug")
            return
        finally:
            (fj.results, fj.out_blocks, fj.suspects, fj.dropped, fj.memory_used,
             fj.conlang_translated, fj.glossary_misses) = saved
        self.seq += 1
        target = self.job.get("target") or "tr"
        path = os.path.join(kodi.temp_dir(), f"defta-{self.job['id']}-{self.seq}.{target}.srt")
        try:
            write_atomic(Path(path), blocks, "srt", fj.srt.doc)
        except OSError as e:
            kodi.log(f"ara dosya yazılamadı: {e}", "warning")
            return
        pct = 100 if stage == "fast" else int(100 * self.done_chunks / self.total_chunks)
        self.on_update(path, stage, pct)
