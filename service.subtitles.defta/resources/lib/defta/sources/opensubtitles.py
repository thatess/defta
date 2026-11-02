"""OpenSubtitles.com REST API (v1).

API anahtarı zorunlu. Kullanıcı adı/şifre isteğe bağlı: girilirse giriş yapılır (indirme hakkı hesaba
yazılır), girilmezse anonim indirme kullanılır (sitede 'Allow anonymous downloads' / 'Under dev')."""
from __future__ import annotations

import json
import os
import time

from .. import kodi, langs, net
from ..media import Media
from .base import SourceError, Sub, pick_from_archive

API = "https://api.opensubtitles.com/api/v1"
NAME = "opensubtitles"


def _headers(token: str = "") -> dict:
    h = {"Api-Key": kodi.secret("os_key"), "Accept": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _token_file() -> str:
    return os.path.join(kodi.profile_dir(), "os_token.json")


def _login() -> tuple[str, str]:
    """(token, api adresi). Kullanıcı adı yoksa ("", API)."""
    user, pw = kodi.secret("os_user"), kodi.secret("os_pass")
    if not user or not pw:
        return "", API
    try:
        with open(_token_file(), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("user") == user and time.time() - d.get("t", 0) < 20 * 3600 and d.get("token"):
            return d["token"], d.get("api", API)
    except (OSError, ValueError):
        pass
    try:
        r = net.post_json(API + "/login", {"username": user, "password": pw}, _headers())
    except net.HttpError as e:
        kodi.log(f"OpenSubtitles girişi başarısız ({e.status}); anonim devam ediliyor", "warning")
        return "", API
    token = r.get("token") or ""
    base = r.get("base_url") or ""
    api = f"https://{base}/api/v1" if base and "://" not in base else (base.rstrip("/") + "/api/v1" if base else API)
    try:
        with open(_token_file(), "w", encoding="utf-8") as f:
            json.dump({"user": user, "token": token, "api": api, "t": time.time()}, f)
    except OSError:
        pass
    return token, api


def _os_langs(codes: list[str]) -> str:
    out = []
    for c in codes:
        out += {"pt": ["pt-pt", "pt-br"], "zh": ["zh-cn", "zh-tw"]}.get(c, [c])
    return ",".join(sorted(set(out)))


def search(m: Media, wanted: list[str] | None) -> list[Sub]:
    """wanted None = tüm diller."""
    base: list = [("ai_translated", "exclude"), ("machine_translated", "exclude")]
    if wanted:
        base.append(("languages", _os_langs(wanted)))
    tries = []
    imdb = m.imdb.replace("tt", "").lstrip("0") if m.imdb else ""
    if m.is_episode:
        se = [("season_number", m.season), ("episode_number", m.episode)]
        if imdb:
            tries.append(base + se + [("parent_imdb_id", imdb)])
            tries.append(base + [("imdb_id", imdb)])
        tries.append(base + se + [("query", m.title.lower())])
    else:
        if imdb:
            tries.append(base + [("imdb_id", imdb)])
        q = [("query", m.title.lower())] + ([("year", m.year)] if m.year else [])
        tries.append(base + q)
    data = []
    for params in tries:
        params = sorted(params, key=lambda kv: kv[0])   # sıralı değilse API yönlendirme yapar
        try:
            r = net.get_json(API + "/subtitles", params, _headers())
        except net.HttpError as e:
            if e.status in (401, 403):
                raise SourceError("OpenSubtitles API anahtarı kabul edilmedi") from e
            raise SourceError(f"OpenSubtitles araması başarısız ({e})") from e
        data = (r or {}).get("data") or []
        if data:
            break
    out = []
    for item in data:
        a = item.get("attributes") or {}
        files = a.get("files") or []
        if not files:
            continue
        fd = a.get("feature_details") or {}
        if m.is_episode and fd.get("episode_number") and int(fd.get("episode_number") or 0) != m.episode:
            continue
        rel = a.get("release") or files[0].get("file_name") or ""
        out.append(Sub(source=NAME, sid=str(files[0].get("file_id")), lang=langs.norm(a.get("language") or ""),
                       release=rel, releases=[rel], downloads=int(a.get("download_count") or 0),
                       rating=float(a.get("ratings") or 0), hi=bool(a.get("hearing_impaired")),
                       ai=bool(a.get("ai_translated") or a.get("machine_translated")),
                       uploader=(a.get("uploader") or {}).get("name") or "",
                       season=int(fd.get("season_number") or 0), episode=int(fd.get("episode_number") or 0),
                       sync=bool(a.get("moviehash_match"))))
    return out


def download(sub: Sub, m: Media) -> tuple[str, bytes]:
    token, api = _login()
    try:
        r = net.post_json(api + "/download", {"file_id": int(sub.sid)}, _headers(token))
    except net.HttpError as e:
        if e.status == 406 or e.status == 429:
            raise SourceError("OpenSubtitles günlük indirme hakkı doldu (ayarlardan giriş yapabilirsiniz)") from e
        if e.status == 401:
            raise SourceError("OpenSubtitles indirmeye izin vermedi: anonim indirme kapalı olabilir; "
                              "ayarlara kullanıcı adı/şifre girin") from e
        raise SourceError(f"OpenSubtitles indirme başarısız ({e})") from e
    link = (r or {}).get("link")
    if not link:
        raise SourceError("OpenSubtitles indirme bağlantısı vermedi")
    kodi.log(f"OpenSubtitles indirme: kalan hak {r.get('remaining')}", "debug")
    raw = net.get_bytes(link)
    name, data = pick_from_archive(raw, m.season, m.episode)
    return (r.get("file_name") or name), data


def check() -> str:
    try:
        net.get_json(API + "/infos/formats", None, _headers())
    except net.HttpError as e:
        return f"HATA ({e.status}): anahtar kabul edilmedi" if e.status in (401, 403) else f"HATA: {e}"
    token, _ = _login()
    if kodi.secret("os_user"):
        return "çalışıyor, giriş yapıldı" if token else "anahtar çalışıyor ama giriş başarısız (anonim)"
    return "çalışıyor (anonim indirme)"
