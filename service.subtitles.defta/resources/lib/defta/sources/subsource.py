"""SubSource API (api.subsource.net/api/v1). Anahtar X-API-Key başlığıyla gönderilir.

Akış: önce yapım aranır (IMDb ya da ad, dizide sezonla), sonra o yapımın altyazıları dil dil listelenir."""
from __future__ import annotations

import re

from .. import kodi, langs, net
from ..media import Media
from .base import SourceError, Sub, busy_error, pick_from_archive

API = "https://api.subsource.net/api/v1"
NAME = "subsource"


def _h() -> dict:
    return {"X-API-Key": kodi.secret("subsource_key"), "Accept": "application/json"}


def _get(path: str, params: dict):
    try:
        return net.get_json(API + path, params, _h())
    except net.HttpError as e:
        if e.status in (401, 403):
            raise SourceError("SubSource API anahtarı kabul edilmedi") from e
        if e.status == 404:
            return {"data": []}
        raise SourceError(f"SubSource isteği başarısız ({e})") from e


def _find_title(m: Media) -> str:
    tries = []
    if m.imdb:
        tries.append({"searchType": "imdb", "imdb": m.imdb})
    tries.append({"searchType": "text", "q": m.title.lower()})
    for p in tries:
        if m.is_episode:
            p["season"] = m.season
        data = (_get("/movies/search", p) or {}).get("data") or []
        want = m.title.lower()
        for it in data:
            names = {str(it.get("title") or "").lower(), str(it.get("alternateTitle") or "").lower()}
            year = int(it.get("releaseYear") or 0)
            if p["searchType"] == "imdb" or any(want and (want in n or n in want) for n in names if n):
                if m.year and year and abs(year - m.year) > 1 and not m.is_episode:
                    continue
                return str(it.get("movieId") or it.get("id") or "")
    return ""


_SE_RE = re.compile(r"[sS](\d{1,2})[ ._-]?[eE](\d{1,4})|(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)|[eE][pP]?(\d{1,4})(?!\d)")
# Anime adlandırması: "[Grup] Ad - 01 (720p)", "[Grup]_Ad_01_(1280x720...)", "Ad 01 [1080p]"
_ABS_RE = re.compile(r"(?:^|[ ._])(?:-[ ._])?(\d{1,3})(?:v\d)?(?=[ ._]*[\[(]|[ ._]-[ ._]|_|$)")
_RANGE_RE = re.compile(r"(?<!\d)\d{1,3}\s*[-~]\s*\d{1,3}(?!\d)")


def _ep_of(rels: list[str]) -> tuple[int, int]:
    for r in rels:
        mm = _SE_RE.search(r or "")
        if mm:
            if mm.group(1):
                return int(mm.group(1)), int(mm.group(2))
            if mm.group(3):
                return int(mm.group(3)), int(mm.group(4))
            return 0, int(mm.group(5))
    for r in rels:
        name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", r or "")       # [grup], (1280x720 ...) atılır
        name = re.sub(r"(?:19|20)\d{2}|\b\d{3,4}p\b|x26[45]|\b10bit\b|(?i:season|sezon|part|cour)\s*\d+", " ", name)
        if _RANGE_RE.search(name):
            continue                                                   # "1-6" gibi paket
        mm = _ABS_RE.search(name.strip())
        if mm:
            return 0, int(mm.group(1))
    return 0, 0


def search(m: Media, wanted: list[str] | None) -> list[Sub]:
    if not wanted:
        return []
    movie_id = _find_title(m)
    if not movie_id:
        return []
    out = []
    for code in wanted:
        name = langs.subsource_name(code)
        if not name:
            continue
        data = (_get("/subtitles", {"movieId": movie_id, "language": name, "limit": 100}) or {}).get("data") or []
        for it in data:
            rels = it.get("releaseInfo") or []
            if isinstance(rels, str):
                rels = [rels]
            season, ep = _ep_of(rels)
            if m.is_episode and ep and ep != m.episode:
                continue
            if it.get("foreignParts"):
                continue
            rating = it.get("rating")
            if isinstance(rating, dict):
                rating = rating.get("good") or 0
            out.append(Sub(source=NAME, sid=str(it.get("subtitleId") or it.get("id")), lang=code,
                           release=(rels[0] if rels else ""), releases=rels,
                           downloads=int(it.get("downloads") or 0), rating=float(rating or 0),
                           hi=bool(it.get("hearingImpaired")), uploader=str(it.get("uploaderDisplayName") or ""),
                           season=season, episode=ep, pack=m.is_episode and not ep))
    return out


def download(sub: Sub, m: Media) -> tuple[str, bytes]:
    try:
        raw = net.get_bytes(f"{API}/subtitles/{sub.sid}/download", None, _h())
    except net.HttpError as e:
        raise busy_error(e, "SubSource") or SourceError(f"SubSource indirme başarısız ({e})") from e
    return pick_from_archive(raw, m.season, m.episode)


def check() -> str:
    try:
        net.get_json(API + "/movies/search", {"searchType": "imdb", "imdb": "tt0816692"}, _h())
    except net.HttpError as e:
        return f"HATA ({e.status}): anahtar kabul edilmedi" if e.status in (401, 403) else f"HATA: {e}"
    return "çalışıyor"
