"""Defta'nın dosyaları (special://profile/addon_data/service.subtitles.defta/):

  translations/<video>/   hazır AI çevirileri + meta.json
  downloads/              indirilen yabancı altyazılar
  search/                 arama sonuçları (birkaç saat saklanır)
  jobs/                   çeviri işleri (yarıda kalanlar servis açılınca devam eder)
  glossaries/             seri sözlükleri
  cache.sqlite            API yanıt önbelleği, ara sonuçlar, çeviri hafızası
  temp/                   Kodi'ye verilen geçici dosyalar
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid

from . import kodi
from .media import Media
from .sources.base import Sub

SEARCH_TTL = 6 * 3600


def _read(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ arama sonuçları

def search_cache_get(m: Media, sig: str) -> list[Sub] | None:
    d = _read(os.path.join(kodi.profile_dir("search"), m.key() + ".json"))
    if not d or d.get("sig") != sig or time.time() - d.get("t", 0) > SEARCH_TTL:
        return None
    return [Sub.from_dict(x) for x in d.get("results") or []]


def search_cache_put(m: Media, sig: str, results: list[Sub]) -> None:
    try:
        _write(os.path.join(kodi.profile_dir("search"), m.key() + ".json"),
               {"t": time.time(), "sig": sig, "results": [s.to_dict() for s in results]})
    except OSError:
        pass


def should_warn(message: str, every: float = 24 * 3600) -> bool:
    path = os.path.join(kodi.profile_dir(), "warned.json")
    d = _read(path, {}) or {}
    now = time.time()
    if now - d.get(message, 0) < every:
        return False
    d[message] = now
    try:
        _write(path, d)
    except OSError:
        pass
    return True


# ------------------------------------------------------------------ indirilenler

def download_path(sub: Sub, ext: str) -> str:
    return os.path.join(kodi.profile_dir("downloads"), sub.uid() + ext)


def find_download(sub: Sub) -> str:
    d = kodi.profile_dir("downloads")
    for ext in (".srt", ".ass", ".ssa", ".vtt"):
        p = os.path.join(d, sub.uid() + ext)
        if os.path.exists(p):
            return p
    return ""


# ------------------------------------------------------------------ hazır AI çevirileri

def tr_dir(m: Media) -> str:
    return kodi.profile_dir("translations", m.key())


def tr_meta(m: Media) -> list[dict]:
    return _read(os.path.join(tr_dir(m), "meta.json"), []) or []


def tr_record(m: Media, sub: Sub, path: str, stage: str, cost: float | None = None) -> None:
    meta = [e for e in tr_meta(m) if e.get("uid") != sub.uid()]
    meta.insert(0, {"uid": sub.uid(), "file": os.path.basename(path), "src": sub.to_dict(), "stage": stage,
                    "created": time.time(), "cost": cost, "title": m.label()})
    _write(os.path.join(tr_dir(m), "meta.json"), meta)


def tr_path(m: Media, sub: Sub, target: str) -> str:
    return os.path.join(tr_dir(m), f"{sub.uid()}.{target}.srt")


def ready_translations(m: Media) -> list[dict]:
    out = []
    for e in tr_meta(m):
        p = os.path.join(tr_dir(m), e.get("file", ""))
        if e.get("file") and os.path.exists(p):
            out.append(dict(e, path=p))
    return out


# ------------------------------------------------------------------ işler

def new_job(m: Media, sub: Sub, src_file: str, fresh: bool, load: bool) -> dict:
    job = {"id": time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6], "media": m.to_dict(),
           "sub": sub.to_dict(), "src_file": src_file, "fresh": fresh, "load": load, "status": "pending",
           "created": time.time(), "target": kodi.target_lang()}
    save_job(job)
    return job


def save_job(job: dict) -> None:
    _write(os.path.join(kodi.profile_dir("jobs"), job["id"] + ".json"), job)


def list_jobs(status: str | None = None) -> list[dict]:
    d = kodi.profile_dir("jobs")
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        j = _read(os.path.join(d, name))
        if j and (status is None or j.get("status") == status):
            out.append(j)
    return out


def running_job_for(m: Media, sub: Sub) -> dict | None:
    for j in list_jobs():
        if j.get("status") in ("pending", "running") and Media.from_dict(j["media"]).key() == m.key() \
                and Sub.from_dict(j["sub"]).uid() == sub.uid():
            return j
    return None


# ------------------------------------------------------------------ temizlik

def _rm_dir(name: str) -> int:
    d = kodi.profile_dir(name)
    n = 0
    for entry in os.listdir(d):
        p = os.path.join(d, entry)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def clean(what: str, series: str = "") -> str:
    import sqlite3
    if what == "ai":
        return f"{_rm_dir('translations')} videonun AI çevirisi silindi"
    if what == "search":
        return f"{_rm_dir('search')} arama sonucu silindi"
    if what == "downloads":
        return f"{_rm_dir('downloads')} indirilen altyazı silindi"
    if what == "jobs":
        n = 0
        for j in list_jobs():
            if j.get("status") != "done":
                try:
                    os.remove(os.path.join(kodi.profile_dir("jobs"), j["id"] + ".json"))
                    n += 1
                except OSError:
                    pass
        return f"{n} yarım iş silindi"
    if what == "glossary":
        d = kodi.profile_dir("glossaries")
        n = 0
        for name in os.listdir(d):
            if series and not name.startswith(series + "."):
                continue
            os.remove(os.path.join(d, name))
            n += 1
        return f"{n} sözlük dosyası silindi"
    db = os.path.join(kodi.profile_dir(), "cache.sqlite")
    if what in ("memory", "apicache"):
        if not os.path.exists(db):
            return "silinecek bir şey yok"
        con = sqlite3.connect(db)
        try:
            tables = ["memory", "written"] if what == "memory" else ["calls", "stages"]
            n = 0
            for t in tables:
                try:
                    n += con.execute(f"DELETE FROM {t}").rowcount
                except sqlite3.Error:
                    pass
            con.commit()
            con.execute("VACUUM")
        finally:
            con.close()
        return f"{n} kayıt silindi"
    if what == "all":
        msgs = [clean(x) for x in ("ai", "search", "downloads", "jobs", "glossary")]
        for name in ("cache.sqlite", "os_token.json", "defta.log", "defta.log.1"):
            try:
                os.remove(os.path.join(kodi.profile_dir(), name))
            except OSError:
                pass
        _rm_dir("temp")
        return "Tüm Defta verileri silindi (" + "; ".join(msgs) + ")"
    return "bilinmeyen seçenek"


def glossary_names() -> list[tuple[str, str]]:
    """(dosya öneki, gösterilecek ad) listesi."""
    d = kodi.profile_dir("glossaries")
    out = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            data = _read(os.path.join(d, name), {}) or {}
            prefix = name.split(".")[0]
            out.append((prefix, f"{data.get('series') or prefix} ({len(data.get('terms') or [])} terim)"))
    return out
