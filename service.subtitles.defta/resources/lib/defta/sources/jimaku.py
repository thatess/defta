"""Jimaku (jimaku.cc) — anime için Japonca altyazı. Anahtar Authorization başlığıyla gönderilir."""
from __future__ import annotations

from .. import kodi, net
from ..media import Media
from .base import SUB_EXTS, SourceError, Sub, pick_from_archive

API = "https://jimaku.cc/api"
NAME = "jimaku"


def _h() -> dict:
    return {"Authorization": kodi.secret("jimaku_key"), "Accept": "application/json"}


def search(m: Media, wanted: list[str] | None) -> list[Sub]:
    if wanted is not None and "ja" not in wanted:
        return []
    q = m.title + (f" {m.season}" if m.is_episode and m.season > 1 else "")
    try:
        entries = net.get_json(API + "/entries/search", {"query": q}, _h()) or []
        if not entries and m.is_episode and m.season > 1:
            entries = net.get_json(API + "/entries/search", {"query": m.title}, _h()) or []
    except net.HttpError as e:
        if e.status in (401, 403):
            raise SourceError("Jimaku API anahtarı kabul edilmedi") from e
        raise SourceError(f"Jimaku araması başarısız ({e})") from e
    if not entries:
        return []
    entry = entries[0]
    params = {"episode": m.episode} if m.is_episode else {}
    try:
        files = net.get_json(f"{API}/entries/{entry.get('id')}/files", params, _h()) or []
    except net.HttpError as e:
        raise SourceError(f"Jimaku dosya listesi alınamadı ({e})") from e
    out = []
    for f in files:
        name = f.get("name") or ""
        if not name.lower().endswith(SUB_EXTS + (".zip",)):
            continue
        out.append(Sub(source=NAME, sid=f.get("url") or "", lang="ja", release=name, releases=[name],
                       season=m.season, episode=m.episode, pack=name.lower().endswith(".zip")))
    return out


def download(sub: Sub, m: Media) -> tuple[str, bytes]:
    if not sub.sid.startswith("https://jimaku.cc/"):
        raise SourceError("Jimaku indirme adresi beklenmedik")
    raw = net.get_bytes(sub.sid, None, _h())
    name, data = pick_from_archive(raw, m.season, m.episode)
    return (sub.release if not sub.pack else name), data


def check() -> str:
    try:
        net.get_json(API + "/entries/search", {"query": "frieren"}, _h())
    except net.HttpError as e:
        return f"HATA ({e.status}): anahtar kabul edilmedi" if e.status in (401, 403) else f"HATA: {e}"
    return "çalışıyor"
