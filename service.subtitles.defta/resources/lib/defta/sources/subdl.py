"""SubDL API v2 (api.subdl.com/api/v2). Anahtar "Authorization: Bearer" başlığıyla gider, adrese yazılmaz.

Arama sırası: IMDb numarası -> dosya adı (sürüme göre eşleşir, bölümü dosya adından çözer) -> dizi/film adı
(ülke ekisiz adla da). İndirme /subtitles/{nId}/download (ücretsiz anahtarda günde 50)."""
from __future__ import annotations

import calendar
import re
import time

from .. import kodi, langs, net
from ..media import Media, bare_title
from .base import QuotaError, SourceError, Sub, busy_error, pick_from_archive

API = "https://api.subdl.com/api/v2"
NAME = "subdl"
_VIDEO_EXT_RE = re.compile(r"\.(mkv|mp4|avi|m4v|ts|m2ts|webm|mov|wmv)$", re.I)
_logged_shape = False


def _h() -> dict:
    return {"Authorization": "Bearer " + kodi.secret("subdl_key"), "Accept": "application/json"}


def _reset_at(e) -> float:
    """Günlük hakkın sıfırlanacağı an: X-RateLimit-Reset (unix saniye/ms ya da kalan saniye), yoksa UTC gece yarısı."""
    try:
        v = float(e.headers.get("x-ratelimit-reset") or 0)
    except (TypeError, ValueError):
        v = 0
    now = time.time()
    if v > 1e12:
        return v / 1000
    if v > 1e9:
        return v
    if v > 0:
        return now + v
    t = time.gmtime(now)
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0)) + 86400


def _error(e: net.HttpError, what: str) -> SourceError:
    p = e.payload if isinstance(e.payload, dict) else {}
    err = p.get("error") if isinstance(p.get("error"), dict) else {}
    code = str(err.get("code") or "").lower()
    if e.status in (401, 403) or "key" in code or code in ("unauthorized", "forbidden"):
        return SourceError("SubDL API anahtarı kabul edilmedi")
    if "quota" in code or "limit_exceeded" in code:
        return QuotaError("SubDL hakkı doldu", _reset_at(e))
    if e.status == 429:
        return busy_error(e, "SubDL")
    if e.status == 402:
        return SourceError("SubDL bu işlem için ücretli plan istiyor")
    return SourceError(f"SubDL {what} başarısız ({e})")


def _get(path: str, params: dict):
    try:
        return net.get_json(API + path, params, _h())
    except net.HttpError as e:
        if e.status == 404:
            return {}
        raise _error(e, "araması") from e


def search(m: Media, wanted: list[str] | None) -> list[Sub]:
    base = {"subs_per_page": 30, "type": "tv" if m.is_episode else "movie"}
    if wanted:
        base["languages"] = ",".join(langs.subdl_code(c).lower() for c in wanted)
    ep = {"season": m.season, "episode": m.episode} if m.is_episode else {}
    tries = []
    if m.imdb:
        tries.append(("/subtitles/search", {"imdb_id": m.imdb, **ep}))
    if m.filename and _VIDEO_EXT_RE.search(m.filename):
        # Dosya adından eşleştirme: "Kitchen.Nightmares.US.S01E01..." gibi adları sitenin kendisi çözer
        tries.append(("/files/search", {"filename": m.filename}))
    for title in (m.title, bare_title(m.title)):
        if title:
            p = {"film_name": title, **ep}
            if m.year and not m.is_episode:
                p["year"] = m.year
            tries.append(("/subtitles/search", p))
    for path, params in tries:
        r = _get(path, {**base, **params})
        out = _parse(r, m)
        if isinstance(r, dict):
            res = r.get("results") or []
            title = res[0].get("name") if res and isinstance(res[0], dict) else "-"
            kodi.log(f"SubDL {path} {params}: {len(r.get('subtitles') or [])} altyazı, {len(out)} uygun, "
                     f"yapım={title}, alanlar={sorted(r.keys())}", "debug")
        if out:
            return out
    return []


def _parse(r, m: Media) -> list[Sub]:
    global _logged_shape
    if not isinstance(r, dict):
        return []
    items = r.get("subtitles") or []
    if items and not _logged_shape:
        _logged_shape = True                       # yanıt biçimi değişirse kayıttan görülsün (değer yok)
        kodi.log("SubDL yanıt alanları: " + ", ".join(sorted(items[0].keys())), "debug")
    out = []
    for it in items:
        # Altyazının nId'si sayfa adresinde: "/s/info/yCdl421C4y" (indirme: /subtitles/{nId}/download)
        page = re.search(r"/s/info/([A-Za-z0-9_-]+)", str(it.get("subtitlePage") or ""))
        nid = str(it.get("n_id") or it.get("nId") or (page.group(1) if page else ""))
        if not nid:
            continue
        season, epn = int(it.get("season") or 0), int(it.get("episode") or 0)
        if m.is_episode and epn and epn != m.episode:
            continue
        rels = [x for x in (it.get("releases") or []) if isinstance(x, str)]
        rel = it.get("release_name") or (rels[0] if rels else "") or it.get("name") or ""
        out.append(Sub(source=NAME, sid=nid, lang=langs.norm(it.get("language") or it.get("lang") or ""),
                       release=rel, releases=rels or [rel],
                       hi=bool(it.get("hi") or it.get("hearing_impaired")),
                       ai=bool(it.get("ai_translated")), uploader=str(it.get("author") or ""),
                       season=season, episode=epn,
                       pack=bool(it.get("full_season")) or (m.is_episode and not epn)))
    return out


def download(sub: Sub, m: Media) -> tuple[str, bytes]:
    nid = sub.sid
    if not re.match(r"^[A-Za-z0-9_-]+$", nid):
        raise SourceError("eski SubDL kaydı: altyazıyı yeniden arayın")
    try:
        raw = net.get_bytes(f"{API}/subtitles/{nid}/download", {"format": "zip"}, _h())
    except net.HttpError as e:
        raise _error(e, "indirme") from e
    return pick_from_archive(raw, m.season, m.episode)


def check() -> str:
    try:
        r = net.get_json(API + "/me", None, _h())
    except net.HttpError as e:
        err = _error(e, "denetimi")
        return f"HATA ({e.status}): {err}" if e.status else f"HATA: {net.explain(str(e)) or e}"
    usage = (r or {}).get("usage") or {} if isinstance(r, dict) else {}
    s, d = usage.get("search") or {}, usage.get("downloads") or {}
    if not usage:
        return "HATA: beklenmeyen yanıt"
    return (f"çalışıyor · arama {s.get('remaining', '?')}/{s.get('limit', '?')}, "
            f"indirme {d.get('remaining', '?')}/{d.get('limit', '?')} kaldı")
