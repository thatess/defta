"""Kaynaklarda arama, sürüm uyumu puanı, tekrarları eleme ve listenin gruplanması."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from . import kodi, store
from .media import Media
from .sources import ALL, LABEL
from .sources.base import SourceError, Sub

HUMAN_MAX = 10

_SOURCES = {"bluray": "bluray", "blu-ray": "bluray", "bdrip": "bluray", "brrip": "bluray", "bdremux": "bluray",
            "remux": "bluray", "web-dl": "webdl", "webdl": "webdl", "web": "webdl", "webrip": "webrip",
            "hdtv": "hdtv", "dvdrip": "dvd", "dvd": "dvd", "hdrip": "hdrip", "hdcam": "cam", "cam": "cam"}
_SERVICES = {"amzn", "nf", "dsnp", "hmax", "max", "atvp", "hulu", "pcok", "pmtp", "cr", "crunchyroll", "itunes",
             "stan", "blutv", "exxen", "gain", "tod"}
_RES = {"2160p", "1080p", "720p", "480p", "4k", "uhd"}
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?")


def features(name: str) -> dict:
    n = (name or "").lower().replace("web-dl", "webdl").replace("blu-ray", "bluray")
    toks = _TOKEN_RE.findall(n.replace(".", " ").replace("_", " "))
    flat = set(t for tok in toks for t in tok.split("-")) | set(toks)
    src = next((_SOURCES[t] for t in flat if t in _SOURCES), "")
    group = ""
    m = re.search(r"-([A-Za-z0-9]{2,15})(?:\[[^\]]*\])?(?:\.[a-z0-9]{2,4})?$", (name or "").strip())
    if m:
        group = m.group(1).lower()
    else:
        m = re.match(r"^\s*\[([^\]]+)\]", name or "")
        group = m.group(1).lower() if m else ""
    return {"source": src, "group": group, "service": next((t for t in flat if t in _SERVICES), ""),
            "res": next((t for t in flat if t in _RES), ""), "tokens": flat}


def match_score(video_name: str, sub: Sub) -> float:
    """Sürüm adı uyumu (0 = bilgi yok). Grup adı en güçlü işaret, sonra kaynak türü ve servis."""
    if not video_name:
        return 0.0
    v = features(video_name)
    best = 0.0
    for rel in (sub.releases or [sub.release])[:6]:
        s = features(rel)
        sc = 0.0
        if v["group"] and v["group"] == s["group"]:
            sc += 5
        if v["source"] and v["source"] == s["source"]:
            sc += 3
        elif v["source"] and s["source"] and {v["source"], s["source"]} <= {"webdl", "webrip"}:
            sc += 1.5
        if v["service"] and v["service"] == s["service"]:
            sc += 2
        if v["res"] and v["res"] == s["res"]:
            sc += 0.5
        best = max(best, sc)
    return best


@dataclass
class Results:
    human: list = field(default_factory=list)      # hedef dilde insan çevirileri
    foreign: list = field(default_factory=list)    # çevrilecek altyazılar
    errors: list = field(default_factory=list)


def _run_sources(m: Media, wanted: list[str] | None, names: list[str], errors: list,
                 before_os=None) -> list[Sub]:
    out: list[Sub] = []
    lock = threading.Lock()

    def one(name: str):
        mod = ALL[name]
        if name == "opensubtitles" and before_os is not None:
            before_os()                     # video karması hazır olsun (diğer kaynaklar beklemez)
        try:
            res = mod.search(m, wanted)
        except SourceError as e:
            msg = str(e)
            if "bağlantı kurulamadı" in msg:
                msg = "siteye ulaşılamıyor (internet sağlayıcısı engelliyor olabilir)"
            with lock:
                errors.append(f"{LABEL[name]}: {msg}")
            return
        except Exception as e:  # noqa: BLE001
            kodi.log(f"{LABEL[name]} araması beklenmedik hata: {type(e).__name__}: {e}", "error")
            with lock:
                errors.append(f"{LABEL[name]}: beklenmedik hata")
            return
        kodi.log(f"{LABEL[name]}: {len(res)} sonuç", "debug")
        with lock:
            out.extend(res)

    threads = [threading.Thread(target=one, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()          # Kodi, açık iş parçacığı kalan betiği kapatırken çöker: hepsi beklenir
    return out


def _dedupe(subs: list[Sub]) -> list[Sub]:
    best: dict = {}
    for s in subs:
        k = (s.lang, re.sub(r"[^a-z0-9]", "", (s.release or s.sid).lower()), s.hi)
        cur = best.get(k)
        if cur is None or (s.downloads, s.source == "opensubtitles") > (cur.downloads, cur.source == "opensubtitles"):
            best[k] = s
    return list(best.values())


def search(m: Media, use_cache: bool = True) -> Results:
    from . import sync
    tgt = kodi.target_lang()
    srcs = kodi.source_langs(m.is_anime)
    names = [n for n in ALL if kodi.source_enabled(n)]
    res = Results()
    if not names:
        res.errors.append("Hiçbir kaynak açık değil ya da API anahtarı girilmemiş (Defta ayarları > Kaynaklar)")
        return res
    sig = "|".join([kodi.version(), tgt] + srcs + names)     # yeni sürümde eski sonuçlar kullanılmaz
    subs = store.search_cache_get(m, sig) if use_cache else None
    if subs is None:
        hash_job = None
        if "opensubtitles" in names and m.path and not m.hash:
            def compute():
                m.hash, m.size = sync.moviehash(m.path)
                if m.hash:
                    kodi.log(f"Video karması: {m.hash}", "debug")
            hash_job = threading.Thread(target=compute)
            hash_job.start()
        wanted = [tgt] + srcs
        subs = _run_sources(m, wanted, names, res.errors, before_os=hash_job.join if hash_job else None)
        if hash_job is not None:
            hash_job.join()
        if not any(s.lang in srcs for s in subs) and "opensubtitles" in names:
            # İstenen kaynak dillerinde yok: başka dillerden (filmin kendi dili vb.) bakılır
            subs += [s for s in _run_sources(m, None, ["opensubtitles"], res.errors) if s.lang != tgt]
        if subs or not res.errors:
            store.search_cache_put(m, sig, subs)
    if not m.is_anime and any(s.source == "jimaku" for s in subs):
        m.is_anime = True                       # Jimaku'da kaydı var: anime (Japonca ayarı sırayı belirler)
        srcs = kodi.source_langs(True)
    video = m.filename
    pref = store.series_pref(m)
    bad = store.bad_uids(m)
    for s in subs:
        s.score = match_score(video, s)
        s.sync = s.hash_match or s.sync or s.score >= 5
        s.note = ""
        if pref and s.source == pref.get("source") and s.lang == pref.get("lang", s.lang) and (
                (pref.get("uploader") and s.uploader == pref["uploader"]) or
                (pref.get("group") and features(s.release).get("group") == pref["group"])):
            s.note = "önceki bölümde kullandığın"
        if s.hash_match:
            s.note = "senkron uyumlu"
        if s.uid() in bad:
            s.note = "kaymış"
    demote = kodi.setting_bool("demote_sdh", True)

    def key(s: Sub):
        # Sıra: kaymış en sonda; video karması eşleşen en üstte; sonra önceki bölümün devamı; sonra sürüm uyumu
        return (s.note == "kaymış", not s.hash_match, s.note != "önceki bölümde kullandığın", -s.score,
                s.hi if demote else False, -s.downloads, -s.rating)

    subs = _dedupe(subs)
    res.human = sorted([s for s in subs if s.lang == tgt and not s.ai], key=key)[:HUMAN_MAX]
    per = {0: 3, 1: 5, 2: 10, 3: 10 ** 6}.get(kodi.setting_int("per_source", 1), 5)
    order = {c: i for i, c in enumerate(srcs)}
    foreign = [s for s in subs if s.lang != tgt and not s.ai]
    picked: list[Sub] = []
    for name in ALL:
        mine = sorted([s for s in foreign if s.source == name], key=lambda s: (order.get(s.lang, 99),) + key(s))
        picked += mine[:per]
    res.foreign = sorted(picked, key=lambda s: key(s)[:3] + (order.get(s.lang, 99),) + key(s)[3:])
    return res


def best_foreign(res: Results) -> Sub | None:
    """Otomatik çeviri için en güvenilir aday (sıralamanın ilki; kaymış işaretliler hiç seçilmez)."""
    return next((s for s in res.foreign if s.note != "kaymış"), None)


def next_candidate(res: Results, current_uid: str, kind: str):
    """"Kaymış" denince yüklenecek sıradaki aday: (tür, altyazı). Şu anki insan çevirisiyse önce sıradaki
    insan çevirisi; yoksa ya da şu anki bir AI çevirisiyse sıradaki çevrilecek altyazı."""
    def ok(s):
        return s.uid() != current_uid and s.note != "kaymış"
    if kind == "human":
        h = next((s for s in res.human if ok(s)), None)
        if h is not None:
            return "human", h
    f = next((s for s in res.foreign if ok(s)), None)
    if f is not None:
        return "translate", f
    h = next((s for s in res.human if ok(s)), None)
    return ("human", h) if h is not None else (None, None)
