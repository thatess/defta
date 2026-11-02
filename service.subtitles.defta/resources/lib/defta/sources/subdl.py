"""SubDL API (api.subdl.com/api/v1/subtitles). İndirme dl.subdl.com üzerinden zip olarak gelir."""
from __future__ import annotations

import re

from .. import kodi, langs, net
from ..media import Media
from .base import SourceError, Sub, pick_from_archive

API = "https://api.subdl.com/api/v1/subtitles"
DL = "https://dl.subdl.com"
NAME = "subdl"


def search(m: Media, wanted: list[str] | None) -> list[Sub]:
    out = _search(m, wanted, use_imdb=bool(m.imdb))
    if not out and m.imdb and m.title:
        # Dizide oynatıcı bölümün IMDb numarasını verebilir; SubDL dizinin numarasını bekler: adla tekrar
        out = _search(m, wanted, use_imdb=False)
    return out


def _search(m: Media, wanted: list[str] | None, use_imdb: bool) -> list[Sub]:
    params = {"api_key": kodi.secret("subdl_key"), "subs_per_page": 30, "releases": 1, "comment": 1}
    if wanted:
        params["languages"] = ",".join(langs.subdl_code(c) for c in wanted)
    if use_imdb:
        params["imdb_id"] = m.imdb
    else:
        params["film_name"] = m.title
        if m.year and not m.is_episode:
            params["year"] = m.year
    if m.is_episode:
        params.update(type="tv", season_number=m.season, episode_number=m.episode)
    else:
        params["type"] = "movie"
    try:
        r = net.get_json(API, params)
    except net.HttpError as e:
        if e.status in (401, 403):
            raise SourceError("SubDL API anahtarı kabul edilmedi") from e
        raise SourceError(f"SubDL araması başarısız ({e})") from e
    if not isinstance(r, dict) or not r.get("status"):
        err = str((r or {}).get("error") or "") if isinstance(r, dict) else ""
        if "api" in err.lower() and "key" in err.lower():
            raise SourceError("SubDL API anahtarı kabul edilmedi")
        return []
    out = []
    for it in r.get("subtitles") or []:
        url = (it.get("url") or "").split("?")[0]      # SubDL adrese api_key ekleyebiliyor: asla saklanmaz
        if not url:
            continue
        season, ep = int(it.get("season") or 0), int(it.get("episode") or 0)
        if m.is_episode and ep and ep != m.episode:
            continue
        rels = [x for x in (it.get("releases") or []) if isinstance(x, str)]
        rel = it.get("release_name") or (rels[0] if rels else "") or it.get("name") or ""
        out.append(Sub(source=NAME, sid=url, lang=langs.norm(it.get("language") or it.get("lang") or ""),
                       release=rel, releases=rels or [rel], hi=bool(it.get("hi")),
                       ai=bool(it.get("ai_translated")), uploader=it.get("author") or "",
                       season=season, episode=ep, pack=bool(it.get("full_season")) or (m.is_episode and not ep)))
    return out


def download(sub: Sub, m: Media) -> tuple[str, bytes]:
    path = sub.sid.split("?")[0]
    url = path if path.startswith("http") else DL + "/" + path.lstrip("/")
    if not re.match(r"^https://dl\.subdl\.com/", url):
        raise SourceError("SubDL indirme adresi beklenmedik")
    try:
        raw = net.get_bytes(url)
    except net.HttpError as e:
        if e.status not in (401, 403):
            raise SourceError(f"SubDL indirme başarısız ({e})") from e
        try:
            raw = net.get_bytes(url, {"api_key": kodi.secret("subdl_key")})
        except net.HttpError as e2:
            raise SourceError(f"SubDL indirme başarısız ({e2})") from e2
    return pick_from_archive(raw, m.season, m.episode)


def check() -> str:
    try:
        r = net.get_json(API, {"api_key": kodi.secret("subdl_key"), "imdb_id": "tt0816692", "type": "movie",
                               "languages": "EN", "subs_per_page": 1})
    except net.HttpError as e:
        return f"HATA ({e.status}): anahtar kabul edilmedi" if e.status in (401, 403) else f"HATA: {e}"
    if isinstance(r, dict) and r.get("status"):
        return "çalışıyor"
    return "HATA: " + str((r or {}).get("error") if isinstance(r, dict) else r)[:120]
