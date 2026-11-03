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
import re
import shutil
import time
import uuid

from . import kodi
from .media import Media
from .sources.base import Sub

SEARCH_TTL = 6 * 3600
TEMP_AGE = 6 * 3600            # geçici dosyalar, hızlı çeviriler, biten/iptal işlerin kaydı
KEEP_AGE = 30 * 24 * 3600      # indirilen yabancı altyazılar, yarım çevirilerin parçaları (API önbelleği)
CLEAN_EVERY = 6 * 3600


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


def content_fingerprint(path: str) -> str:
    """Altyazının metni + zamanlaması (boşluk, satır sonu, BOM, biçim etiketleri yok sayılır).
    Aynı metin ama farklı zamanlama = farklı altyazı (senkronu farklıdır)."""
    import hashlib
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    text = raw.decode("utf-8-sig", "ignore").replace("\r", "")
    text = re.sub(r"<[^>]+>|\{[^}]*\}", "", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip() and not ln.strip().isdigit()]
    if len(lines) < 5:
        return ""
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def remember_fingerprint(sub: Sub, path: str) -> None:
    fp = content_fingerprint(path)
    if not fp:
        return
    d = _read(os.path.join(kodi.profile_dir(), "fingerprints.json"), {}) or {}
    if d.get(sub.uid()) == fp:
        return
    d[sub.uid()] = fp
    if len(d) > 3000:
        for k in list(d)[:1000]:
            d.pop(k, None)
    try:
        _write(os.path.join(kodi.profile_dir(), "fingerprints.json"), d)
    except OSError:
        pass


def fingerprints() -> dict:
    return _read(os.path.join(kodi.profile_dir(), "fingerprints.json"), {}) or {}


def find_download(sub: Sub) -> str:
    d = kodi.profile_dir("downloads")
    for ext in (".srt", ".ass", ".ssa", ".vtt"):
        p = os.path.join(d, sub.uid() + ext)
        if os.path.exists(p):
            try:
                os.utime(p, None)          # kullanıldı: 30 günlük süre baştan başlar
            except OSError:
                pass
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
    out, seen = [], set()
    dirs = [tr_dir(m)]
    legacy = m.legacy_key()
    if legacy != m.key() and os.path.isdir(os.path.join(kodi.profile_dir("translations"), legacy)):
        dirs.append(kodi.profile_dir("translations", legacy))     # 0.2 sürümünün kaydettikleri
    for d in dirs:
        for e in _read(os.path.join(d, "meta.json"), []) or []:
            p = os.path.join(d, e.get("file", ""))
            if e.get("file") and os.path.exists(p) and e.get("uid") not in seen:
                seen.add(e.get("uid"))
                out.append(dict(e, path=p))
    bad = bad_uids(m)
    out.sort(key=lambda e: e.get("uid") in bad)                   # kaymış işaretlenenler alta
    return out


# ------------------------------------------------------------------ senkron geri bildirimi ve tercihler

def _state_path(name: str) -> str:
    return os.path.join(kodi.profile_dir(), name)


def set_loaded(m: Media, kind: str, sub: Sub) -> None:
    """Bu videoya en son Defta'nın yüklediği altyazı (kind: human | ai | source)."""
    d = _read(_state_path("loaded.json"), {}) or {}
    d[m.key()] = {"kind": kind, "sub": sub.to_dict(), "t": time.time()}
    if len(d) > 300:                                              # dosya büyümesin
        for k in sorted(d, key=lambda k: d[k].get("t", 0))[:100]:
            d.pop(k, None)
    try:
        _write(_state_path("loaded.json"), d)
    except OSError:
        pass


def get_loaded(m: Media) -> dict | None:
    return (_read(_state_path("loaded.json"), {}) or {}).get(m.key())


def mark_bad(m: Media, sub: Sub) -> None:
    d = _read(_state_path("badsync.json"), {}) or {}
    lst = d.setdefault(m.key(), [])
    if sub.uid() not in lst:
        lst.append(sub.uid())
    try:
        _write(_state_path("badsync.json"), d)
    except OSError:
        pass
    pref = series_pref(m)
    if pref and ((sub.uploader and pref.get("uploader") == sub.uploader) or
                 (pref.get("group") and pref.get("group") == _group(sub))):
        prefs = _read(_state_path("series_prefs.json"), {}) or {}
        prefs.pop(m.series_key(), None)                           # bu yükleyenin ayrıcalığı bu dizide kalkar
        try:
            _write(_state_path("series_prefs.json"), prefs)
        except OSError:
            pass


def bad_uids(m: Media) -> set:
    return set((_read(_state_path("badsync.json"), {}) or {}).get(m.key(), []))


def _group(sub: Sub) -> str:
    from .search import features
    return features(sub.release).get("group", "")


def remember_choice(m: Media, sub: Sub) -> None:
    """Dizide seçilen altyazının yükleyeni/grubu: sıradaki bölümde onun devamı öne alınır."""
    if not m.is_episode or sub.source in ("local", ""):
        return
    from .search import features
    f = features(sub.release)
    prefs = _read(_state_path("series_prefs.json"), {}) or {}
    prefs[m.series_key()] = {"source": sub.source, "uploader": sub.uploader, "group": f.get("group", ""),
                             "kind": f.get("source", ""), "hi": sub.hi, "lang": sub.lang, "t": time.time()}
    try:
        _write(_state_path("series_prefs.json"), prefs)
    except OSError:
        pass


def series_pref(m: Media) -> dict | None:
    if not m.is_episode:
        return None
    return (_read(_state_path("series_prefs.json"), {}) or {}).get(m.series_key())


# ------------------------------------------------------------------ işler

def new_job(m: Media, sub: Sub, src_file: str, fresh: bool, load: bool, start_at: float = 0.0) -> dict:
    """start_at: seçildiği an videonun neresindeydi (sn); hızlı çeviri oradan başlar."""
    job = {"id": time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6], "media": m.to_dict(),
           "sub": sub.to_dict(), "src_file": src_file, "fresh": fresh, "load": load, "status": "pending",
           "created": time.time(), "target": kodi.target_lang(), "start_at": start_at}
    save_job(job)
    return job


def save_job(job: dict) -> None:
    _write(os.path.join(kodi.profile_dir("jobs"), job["id"] + ".json"), job)
    if job.get("status") == "pending":
        touch_queue()


def _queue_marker() -> str:
    return os.path.join(kodi.profile_dir("jobs"), ".queue")


def touch_queue() -> None:
    """Yeni iş var: servis klasörü her saniye taramak yerine bu dosyanın zamanına bakar."""
    try:
        with open(_queue_marker(), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def queue_stamp() -> float:
    try:
        return os.path.getmtime(_queue_marker())
    except OSError:
        return 0.0


def prune_jobs(max_age: float = TEMP_AGE) -> int:
    """Biten/başarısız eski işlerin dosyalarını siler (hazır çeviriler silinmez)."""
    n = 0
    now = time.time()
    for j in list_jobs():
        if j.get("status") in ("done", "failed", "cancelled") and \
                now - float(j.get("finished") or j.get("created") or now) > max_age:
            try:
                os.remove(os.path.join(kodi.profile_dir("jobs"), j["id"] + ".json"))
                n += 1
            except OSError:
                pass
    return n


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


def cancel_jobs_for(m: Media, except_uid: str = "") -> int:
    """Aynı video için bekleyen/süren diğer işleri iptal eder (son seçilen kazanır).
    Biten parçalar önbellekte kaldığı için aynı altyazı yeniden seçilirse kaldığı yerden sürer."""
    n = 0
    for j in list_jobs():
        if j.get("status") in ("pending", "running") and Media.from_dict(j["media"]).key() == m.key() \
                and Sub.from_dict(j["sub"]).uid() != except_uid:
            j["status"] = "cancelled"
            save_job(j)
            n += 1
    return n


def get_job(job_id: str) -> dict | None:
    return _read(os.path.join(kodi.profile_dir("jobs"), job_id + ".json"))


def job_status(job_id: str) -> str:
    return (get_job(job_id) or {}).get("status", "")


def want_load(m: Media) -> int:
    """Video oynatılmaya başladı: önceden başlamış (sıradaki bölüm) işleri bitince yüklenecek yapar."""
    n = 0
    for j in list_jobs():
        if j.get("status") in ("pending", "running") and not j.get("load") \
                and Media.from_dict(j["media"]).key() == m.key():
            j["load"] = True
            save_job(j)
            n += 1
    return n


def running_job_for(m: Media, sub: Sub) -> dict | None:
    for j in list_jobs():
        if j.get("status") in ("pending", "running") and Media.from_dict(j["media"]).key() == m.key() \
                and Sub.from_dict(j["sub"]).uid() == sub.uid():
            return j
    return None


# ------------------------------------------------------------------ temizlik

def _remove_older(folder: str, max_age: float) -> int:
    n = 0
    now = time.time()
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > max_age:
                os.remove(path)
                n += 1
        except OSError:
            pass
    return n


def auto_clean() -> dict:
    """Kendiliğinden temizlik (Kodi açılınca, her çeviri bitince ve 6 saatte bir):
    6 saat: geçici dosyalar (hızlı çeviriler, Kodi'ye verilen kopyalar, iptal edilenlerin artıkları),
            biten/başarısız/iptal işlerin kayıtları, eski arama sonuçları
    30 gün: indirilen yabancı altyazılar (kullanıldıkça süre yenilenir), yarım çevirilerin parçaları
    Kalıcı: hazır AI çevirileri, seri sözlükleri, çeviri hafızası."""
    import sqlite3
    out = {"temp": _remove_older(kodi.temp_dir(), TEMP_AGE),
           "search": _remove_older(kodi.profile_dir("search"), SEARCH_TTL),
           "downloads": _remove_older(kodi.profile_dir("downloads"), KEEP_AGE),
           "jobs": prune_jobs(TEMP_AGE), "cache": 0, "fingerprints": _backfill_fingerprints()}
    db = os.path.join(kodi.profile_dir(), "cache.sqlite")
    if os.path.exists(db):
        try:
            con = sqlite3.connect(db, timeout=10)
            try:
                cut = time.time() - KEEP_AGE
                for table in ("calls", "stages"):
                    try:
                        out["cache"] += con.execute(f"DELETE FROM {table} WHERE created < ?", (cut,)).rowcount
                    except sqlite3.Error:
                        pass
                con.commit()
            finally:
                con.close()
        except sqlite3.Error:
            pass
    try:
        with open(os.path.join(kodi.profile_dir(), "cleaned"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass
    return out


def _backfill_fingerprints() -> int:
    """Önceki sürümlerde indirilmiş dosyaların içerik parmak izi (aynı altyazıyı tek göstermek için)."""
    d = fingerprints()
    folder = kodi.profile_dir("downloads")
    added = 0
    for name in os.listdir(folder):
        uid, ext = os.path.splitext(name)
        if ext.lower() not in (".srt", ".ass", ".ssa", ".vtt") or uid.endswith(".fps") or uid in d:
            continue
        fp = content_fingerprint(os.path.join(folder, name))
        if fp:
            d[uid] = fp
            added += 1
    if added:
        try:
            _write(os.path.join(kodi.profile_dir(), "fingerprints.json"), d)
        except OSError:
            pass
    return added


def last_clean() -> float:
    try:
        return os.path.getmtime(os.path.join(kodi.profile_dir(), "cleaned"))
    except OSError:
        return 0.0


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
        try:
            con = sqlite3.connect(db, timeout=15)       # çeviri sürüyorsa bitmesini bekler
        except sqlite3.Error as e:
            return f"önbellek açılamadı: {e}"
        try:
            tables = ["memory", "written"] if what == "memory" else ["calls", "stages"]
            n = 0
            for t in tables:
                try:
                    n += con.execute(f"DELETE FROM {t}").rowcount
                except sqlite3.OperationalError as e:
                    if "no such table" not in str(e):
                        return f"önbellek şu an kullanımda, biraz sonra yeniden deneyin ({e})"
            con.commit()
            try:
                con.execute("VACUUM")                     # dosya gerçekten küçülsün
            except sqlite3.Error:
                pass
        finally:
            con.close()
        return f"{n} kayıt silindi"
    if what == "syncmarks":
        n = 0
        for name in ("badsync.json", "series_prefs.json", "loaded.json"):
            try:
                os.remove(_state_path(name))
                n += 1
            except OSError:
                pass
        return "Senkron işaretleri ve dizi tercihleri silindi"
    if what == "all":
        msgs = [clean(x) for x in ("ai", "search", "downloads", "jobs", "glossary", "syncmarks")]
        for name in ("cache.sqlite", "os_token.json", "defta.log", "defta.log.1"):
            try:
                os.remove(os.path.join(kodi.profile_dir(), name))
            except OSError:
                pass
        _rm_dir("temp")
        return "Tüm Defta verileri silindi (" + "; ".join(msgs) + ")"
    return "bilinmeyen seçenek"


def scrub_secrets_once() -> int:
    """Her sürümde bir kez çalışır (açılışı yavaşlatmasın)."""
    marker = os.path.join(kodi.profile_dir(), "scrubbed.txt")
    try:
        with open(marker, encoding="utf-8") as f:
            if f.read().strip() == kodi.version():
                return 0
    except OSError:
        pass
    n = scrub_secrets()
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(kodi.version())
    except OSError:
        pass
    return n


def scrub_secrets() -> int:
    """Eski sürümün dosya adına ya da içeriğine yazdığı anahtarları temizler (SubDL bağlantısındaki api_key).
    Adında 'api_key' geçen dosyalar silinir; kayıt ve json dosyalarındaki anahtarlar *** yapılır."""
    secrets = kodi.secret_values()
    roots = [kodi.profile_dir()]
    extra = kodi.setting("debug_dir").strip()
    if extra and os.path.isdir(extra):
        roots.append(extra)
    fixed = 0
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                if "api_key" in name.lower():
                    try:
                        os.remove(path)
                        fixed += 1
                    except OSError:
                        pass
                    continue
                if not name.endswith((".json", ".log", ".1", ".txt")) or name == "scrubbed.txt":
                    continue
                try:
                    if os.path.getsize(path) > 8 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
                new = text
                for v in secrets:
                    new = new.replace(v, "***")
                new = re.sub(r"api_key[=_][A-Za-z0-9_-]{8,}", "api_key=***", new)
                if new != text:
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(new)
                        fixed += 1
                    except OSError:
                        pass
    return fixed


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
