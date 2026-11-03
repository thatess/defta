"""Jimaku (jimaku.cc) — anime için Japonca altyazı. Anahtar Authorization başlığıyla gönderilir."""
from __future__ import annotations

import re

from .. import kodi, net
from ..media import Media
from .base import SUB_EXTS, SourceError, Sub, pick_from_archive

API = "https://jimaku.cc/api"
NAME = "jimaku"


def _h() -> dict:
    return {"Authorization": kodi.secret("jimaku_key"), "Accept": "application/json"}


_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_SEASON_RES = (
    re.compile(r"\b(?:season|s)\s*(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.I),
    re.compile(r"\b(II|III|IV|V|VI)\b"),
)


def _norm(text: str) -> str:
    t = re.sub(r"[^\w]+", " ", (text or "").lower(), flags=re.U)
    return re.sub(r"\s+", " ", t).strip()


def _season_of(name: str) -> tuple[int, str]:
    """("Mob Psycho 100 II") -> (2, "mob psycho 100"). Sezon belirtisi yoksa 0 (belirtilmemiş)."""
    for rx in _SEASON_RES:
        mm = rx.search(name or "")
        if mm:
            v = mm.group(1).lower()
            n = _ROMAN.get(v) or int(v)
            return n, _norm(name[:mm.start()] + " " + name[mm.end():])
    return 0, _norm(name)


def _title_score(title: str, names: list[str]) -> float:
    t = _norm(title)
    if not t:
        return 0.0
    best = 0.0
    tw = set(t.split())
    for n in names:
        if not n:
            continue
        _, base = _season_of(n)
        if not base:
            continue
        if base == t:
            return 100.0
        bw = set(base.split())
        best = max(best, 100.0 * len(tw & bw) / max(len(tw | bw), 1))   # sözcük örtüşmesi (Jaccard)
    return best


def pick_entry(m: Media, entries: list) -> dict | None:
    """Başlık ve sezonu en iyi uyan kayıt. Jimaku'da yalnızca anime var; zayıf eşleşme (anime olmayan bir
    filmin adına benzeyen bir anime) kabul edilmez."""
    best, best_sc = None, 0.0
    for e in entries or []:
        names = [e.get("english_name") or "", e.get("name") or "", e.get("japanese_name") or ""]
        sc = _title_score(m.title, names)
        if sc < 75:
            continue
        flags = e.get("flags") or {}
        if m.is_episode:
            seasons = {_season_of(n)[0] for n in names[:2] if n} - {0}
            if m.season in seasons or (not seasons and m.season <= 1):
                sc += 30                 # sezon tutuyor
            elif not seasons:
                sc -= 25                 # kayıtta sezon yok: tek kayıtta süren uzun diziler için yedek
            else:
                sc -= 60                 # başka sezonun kaydı
            if flags.get("movie"):
                sc -= 50
        elif flags.get("movie"):
            sc += 20
        elif sc < 100:
            continue                     # film: anime filmi olarak işaretli değilse yalnızca birebir ad
        if sc > best_sc:
            best, best_sc = e, sc
    return best if best_sc >= 75 else None


def search(m: Media, wanted: list[str] | None) -> list[Sub]:
    """Video anime olarak tanınmasa da sorulur: Jimaku'da yalnızca anime olduğu için güçlü bir başlık
    eşleşmesi zaten "bu bir anime" demektir."""
    if not m.title:
        return []
    try:
        entries = net.get_json(API + "/entries/search", {"query": m.title}, _h()) or []
        if m.is_episode and m.season > 1:
            more = net.get_json(API + "/entries/search", {"query": f"{m.title} {m.season}"}, _h()) or []
            seen = {e.get("id") for e in entries}
            entries += [e for e in more if e.get("id") not in seen]
    except net.HttpError as e:
        if e.status in (401, 403):
            raise SourceError("Jimaku anahtarı kabul edilmedi") from e
        raise SourceError(f"Jimaku araması olmadı ({e})") from e
    entry = pick_entry(m, entries)
    if entry is None:
        kodi.log(f"Jimaku: '{m.title}'" + (f" {m.season}. sezon" if m.is_episode else "")
                 + f" için kayıt bulunamadı ({len(entries)} aday)", "debug")
        return []
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
    kodi.log(f"Jimaku: '{m.title}' -> '{entry.get('english_name') or entry.get('name')}' "
             f"(#{entry.get('id')}), {len(out)} dosya", "debug")
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
