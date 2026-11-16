"""Kaynaklarda arama, sürüm uyumu puanı, tekrarları eleme ve listenin gruplanması."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from . import kodi, net, store
from .media import Media
from .sources import ALL, LABEL
from .sources.base import SourceError, Sub

HUMAN_MAX = 10
PREF = "önceki bölümde kullandığın"

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
            kodi.log(f"{LABEL[name]} araması başarısız: {e}", "warning")      # asıl sebep (anahtar gizli)
            msg = net.explain(str(e)) or str(e)
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


_NAME_TAIL_RE = re.compile(r"(?:[ ._\-\[(]+(?:en|eng|english|tr|tur|turkish|ja|jpn|japanese|hi|sdh|cc|forced|"
                           r"srt|ass|ssa|vtt|sub|subs|zip)[\])]?)+$", re.I)


def release_key(name: str) -> str:
    """Kaynaklar arası aynı altyazıyı tanımak için sürüm adı anahtarı: dil eki, uzantı, SDH işareti,
    büyük/küçük harf ve ayraçlar yok sayılır ("show.s01e03.720p-grp.en.srt" = "Show S01E03 720p GRP")."""
    n = (name or "").strip()
    n = re.sub(r"\.(srt|ass|ssa|vtt|zip|sub)$", "", n, flags=re.I)
    prev = None
    while prev != n:
        prev, n = n, _NAME_TAIL_RE.sub("", n)
    return re.sub(r"[^a-z0-9]", "", n.lower())


def _better(a: Sub, b: Sub) -> bool:
    """a, b'den iyi mi: video karması > sürüm uyumu > OpenSubtitles (indirme sayısı güvenilir) > indirme."""
    return (a.hash_match, a.score, a.source == "opensubtitles", a.downloads, a.rating) > \
           (b.hash_match, b.score, b.source == "opensubtitles", b.downloads, b.rating)


def _merge(keep: Sub, drop: Sub) -> Sub:
    """İki satır tek satır olur: kalan, birleşenin 'senkron uyumlu' bilgisini ve indirme yedeğini taşır."""
    keep.hash_match = keep.hash_match or drop.hash_match
    keep.sync = keep.sync or drop.sync
    known = {keep.uid()} | {Sub.from_dict(a).uid() for a in keep.alts}
    for a in [drop.to_dict()] + list(drop.alts):
        if Sub.from_dict(a).uid() not in known and len(keep.alts) < 3:
            keep.alts.append(dict(a, alts=[]))
            known.add(Sub.from_dict(a).uid())
    return keep


def _dedupe(subs: list[Sub]) -> list[Sub]:
    """Aynı altyazı birden çok kaynakta (ya da aynı kaynakta iki kez) varsa tek satır kalır.
    1) aynı dil + aynı sürüm adı (eklerden arındırılmış) + aynı SDH durumu
    2) daha önce indirilmişse içerik parmak izi (metin + zamanlama) aynı olanlar
    Elenen kopya, kalanın indirme yedeği olur (kaynak indirmezse ondan indirilir)."""
    best: dict = {}
    for s in subs:
        k = (s.lang, release_key(s.release or s.sid) or s.uid(), s.hi)
        cur = best.get(k)
        best[k] = s if cur is None else _merge(s, cur) if _better(s, cur) else _merge(cur, s)
    prints = store.fingerprints()
    by_fp: dict = {}
    rest = []
    for s in best.values():
        fp = prints.get(s.uid())
        cur = by_fp.get(fp) if fp else None
        if not fp:
            rest.append(s)
        else:
            by_fp[fp] = s if cur is None else _merge(s, cur) if _better(s, cur) else _merge(cur, s)
    return rest + list(by_fp.values())


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
    pref = store.series_pref(m)
    bad = store.bad_uids(m)
    for s in subs:
        s.score = match_score(m.filename, s)
        s.sync = s.hash_match or s.sync or s.score >= 5
        s.note, s.alts = "", []
    subs = _dedupe(subs)
    blocked = store.quota_blocked()
    pref_used = False
    for s in sorted(subs, key=lambda s: (not s.hash_match, -s.score, -s.downloads)):
        if pref and not pref_used and s.source == pref.get("source") and s.lang == pref.get("lang", s.lang) and (
                (pref.get("uploader") and s.uploader == pref["uploader"]) or
                (pref.get("group") and features(s.release).get("group") == pref["group"])):
            s.note, pref_used = PREF, True       # tek satır: aynı yükleyenin her sürümü işaretlenmez
        if s.hash_match:
            s.note = "senkron uyumlu"
        if not _reachable(s, blocked):
            s.note = blocked[s.source]           # yedeği yoksa: hak dolana kadar alta iner
        if s.uid() in bad:
            s.note = "kaymış"
    demote = kodi.setting_bool("demote_sdh", True)

    def key(s: Sub):
        # Sıra: kaymış en sonda, hakkı dolan kaynak onun üstünde; video karması eşleşen en üstte;
        # sonra önceki bölümün devamı; sonra sürüm uyumu
        return (s.note == "kaymış", not _reachable(s, blocked), not s.hash_match, s.note != PREF,
                -s.score, s.hi if demote else False, -s.downloads, -s.rating)

    res.human = sorted([s for s in subs if s.lang == tgt and not s.ai], key=key)[:HUMAN_MAX]
    per = {0: 3, 1: 5, 2: 10, 3: 10 ** 6}.get(kodi.setting_int("per_source", 1), 5)
    order = {c: i for i, c in enumerate(srcs)}
    foreign = [s for s in subs if s.lang != tgt and not s.ai]
    picked: list[Sub] = []
    for name in ALL:
        mine = sorted([s for s in foreign if s.source == name], key=lambda s: (order.get(s.lang, 99),) + key(s))
        picked += mine[:per]
    res.foreign = sorted(picked, key=lambda s: key(s)[:4] + (order.get(s.lang, 99),) + key(s)[4:])
    return res


def _reachable(s: Sub, blocked: dict) -> bool:
    """İndirilebilir mi: kaynağı engelli değil ya da engelli olmayan bir kaynakta yedeği var."""
    return s.source not in blocked or any(a.get("source") not in blocked for a in s.alts)


def best_foreign(res: Results) -> Sub | None:
    """Otomatik çeviri için en güvenilir aday (sıralamanın ilki; kaymış işaretliler ve hakkı dolanlar seçilmez)."""
    blocked = store.quota_blocked()
    return next((s for s in res.foreign if s.note != "kaymış" and _reachable(s, blocked)), None)


def next_candidate(res: Results, current_uid: str, kind: str, skip: set | None = None):
    """"Kaymış" denince yüklenecek sıradaki aday: (tür, altyazı). Şu anki insan çevirisiyse önce sıradaki
    insan çevirisi; yoksa ya da şu anki bir AI çevirisiyse sıradaki çevrilecek altyazı."""
    skip = skip or set()

    def ok(s):
        return s.uid() != current_uid and s.note != "kaymış" and s.uid() not in skip
    if kind == "human":
        h = next((s for s in res.human if ok(s)), None)
        if h is not None:
            return "human", h
    f = next((s for s in res.foreign if ok(s)), None)
    if f is not None:
        return "translate", f
    h = next((s for s in res.human if ok(s)), None)
    return ("human", h) if h is not None else (None, None)
