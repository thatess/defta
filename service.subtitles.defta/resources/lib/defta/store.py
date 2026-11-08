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
import threading
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
    # Geçici dosya adı süreç + iş parçacığına özgü: eklenti ve servis aynı dosyaya aynı anda yazabilir
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ arama sonuçları

def _rm(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _touch(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


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
    d = _state("warned.json")
    now = time.time()
    if now - d.get(message, 0) < every:
        return False
    d[message] = now
    _put_state("warned.json", d)
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
    d = _state("fingerprints.json")
    if d.get(sub.uid()) == fp:
        return
    d[sub.uid()] = fp
    if len(d) > 3000:
        for k in list(d)[:1000]:
            d.pop(k, None)
    _put_state("fingerprints.json", d)


def fingerprints() -> dict:
    return _state("fingerprints.json")


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


def _state(name: str) -> dict:
    return _read(_state_path(name), {}) or {}


def _put_state(name: str, d: dict) -> None:
    try:
        _write(_state_path(name), d)
    except OSError:
        pass


def set_loaded(m: Media, kind: str, sub: Sub) -> None:
    """Bu videoya en son Defta'nın yüklediği altyazı (kind: human | ai | source)."""
    d = _state("loaded.json")
    d[m.key()] = {"kind": kind, "sub": sub.to_dict(), "t": time.time()}
    if len(d) > 300:                                              # dosya büyümesin
        for k in sorted(d, key=lambda k: d[k].get("t", 0))[:100]:
            d.pop(k, None)
    _put_state("loaded.json", d)


def get_loaded(m: Media) -> dict | None:
    return _state("loaded.json").get(m.key())


def mark_bad(m: Media, sub: Sub) -> None:
    d = _state("badsync.json")
    lst = d.setdefault(m.key(), [])
    if sub.uid() not in lst:
        lst.append(sub.uid())
    _put_state("badsync.json", d)
    pref = _state("series_prefs.json").get(m.series_key())     # seçildiği bölüm dahil (series_pref orada boş döner)
    if pref and ((sub.uploader and pref.get("uploader") == sub.uploader) or
                 (pref.get("group") and pref.get("group") == _group(sub))):
        prefs = _state("series_prefs.json")
        prefs.pop(m.series_key(), None)                           # bu yükleyenin ayrıcalığı bu dizide kalkar
        _put_state("series_prefs.json", prefs)


def bad_uids(m: Media) -> set:
    return set(_state("badsync.json").get(m.key(), []))


def _group(sub: Sub) -> str:
    from .search import features
    return features(sub.release).get("group", "")


def remember_choice(m: Media, sub: Sub) -> None:
    """Dizide seçilen altyazının yükleyeni/grubu: sıradaki bölümde onun devamı öne alınır."""
    if not m.is_episode or sub.source in ("local", ""):
        return
    from .search import features
    f = features(sub.release)
    prefs = _state("series_prefs.json")
    prefs[m.series_key()] = {"source": sub.source, "uploader": sub.uploader, "group": f.get("group", ""),
                             "kind": f.get("source", ""), "hi": sub.hi, "lang": sub.lang, "t": time.time(),
                             "ep": m.key()}
    _put_state("series_prefs.json", prefs)


def request_load(m: Media, sub: Sub) -> None:
    """Sıradaki bölüm için çeviri istendi: o bölüm açıldığında (ayar ne olursa olsun) yüklenecek."""
    d = _state("requested.json")
    d[m.key()] = {"uid": sub.uid(), "t": time.time()}
    now = time.time()
    d = {k: v for k, v in d.items() if now - v.get("t", 0) < KEEP_AGE}
    _put_state("requested.json", d)


def take_requested(m: Media) -> str:
    """Bu bölüm için istenmiş çevirinin kimliği (bir kez döner, sonra silinir)."""
    d = _state("requested.json")
    e = d.pop(m.key(), None)
    if e is None:
        return ""
    _put_state("requested.json", d)
    return e.get("uid", "") if time.time() - e.get("t", 0) < KEEP_AGE else ""


def series_pref(m: Media) -> dict | None:
    """Dizide önceki bölümde seçilen yükleyen/grup. Seçildiği bölümün kendisinde uygulanmaz (aynı bölümde
    çeviri değiştirince listede 'önceki bölümde kullandığın' satırları çoğalmasın)."""
    if not m.is_episode:
        return None
    p = _state("series_prefs.json").get(m.series_key())
    return None if not p or p.get("ep") == m.key() else p


# ------------------------------------------------------------------ kaynak indirme hakkı

def set_quota(source: str, until: float, quota: bool) -> None:
    """Kaynak 'until' anına kadar indirmeye izin vermiyor: listede alta iner, indirmede başka kaynak denenir."""
    d = _state("quota.json")
    d[source] = {"until": until, "quota": quota, "t": time.time()}
    _put_state("quota.json", d)


def clear_quota(source: str, before: float = 0.0) -> None:
    """Engel kalkar. before: yalnızca bu andan önce konmuşsa (indirme sırasında 'son hakkın buydu' diye
    yeni konan işaret silinmesin)."""
    d = _state("quota.json")
    e = d.get(source)
    if e is not None and not (before and float(e.get("t") or 0) >= before):
        d.pop(source)
        _put_state("quota.json", d)


def quota_blocked() -> dict:
    """Şu an engelli kaynaklar: {kaynak: kısa not}. Süresi geçenler kendiliğinden kalkar."""
    now = time.time()
    out = {}
    for src, e in _state("quota.json").items():
        until = float(e.get("until") or 0)
        if until > now:
            out[src] = quota_note(until, e.get("quota", True))
    return out


def quota_note(until: float, quota: bool) -> str:
    """'hak doldu · 03:00' (günlük hak, sıfırlanma saati) ya da 'bekleniyor · 10 dk' (geçici yoğunluk)."""
    if quota:
        return "hak doldu · " + time.strftime("%H:%M", time.localtime(until))
    return f"bekleniyor · {max(1, int((until - time.time() + 59) // 60))} dk"


# ------------------------------------------------------------------ OpenAI kredisi

def set_credit_out(out: bool) -> None:
    """Kredi bitti: otomatik çeviriler (video açılınca, sıradaki bölüm) kendiliğinden başlamaz.
    Elle çeviri seçilince kalkar (kredi yüklendiyse çeviri yürür, yüklenmediyse hemen yine durur)."""
    d = _state("state.json")
    if out:
        d["credit_out"] = time.time()
    elif not d.pop("credit_out", None):
        return
    _put_state("state.json", d)


def credit_out() -> bool:
    return bool(_state("state.json").get("credit_out"))


# ------------------------------------------------------------------ işler

def new_job(m: Media, sub: Sub, src_file: str, fresh: bool, load: bool, start_at: float = 0.0,
            auto: bool = False) -> dict:
    """start_at: seçildiği an videonun neresindeydi (sn); hızlı çeviri oradan başlar.
    auto: kullanıcı seçmedi (video açılınca ya da sıradaki bölüm için kendiliğinden)."""
    job = {"id": time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6], "media": m.to_dict(),
           "sub": sub.to_dict(), "src_file": src_file, "fresh": fresh, "load": load, "status": "pending",
           "created": time.time(), "target": kodi.target_lang(), "start_at": start_at, "auto": auto}
    save_job(job)
    return job


def _job_path(job_id: str) -> str:
    return os.path.join(kodi.profile_dir("jobs"), job_id + ".json")


def save_job(job: dict) -> None:
    _write(_job_path(job["id"]), job)
    if job.get("status") == "pending":
        touch_queue()


def _queue_marker() -> str:
    return os.path.join(kodi.profile_dir("jobs"), ".queue")


def touch_queue() -> None:
    """Yeni iş var: servis klasörü her saniye taramak yerine bu dosyanın zamanına bakar."""
    _touch(_queue_marker(), str(time.time()))


def queue_stamp() -> float:
    return _mtime(_queue_marker())


def prune_jobs(max_age: float = TEMP_AGE) -> int:
    """Biten/başarısız eski işlerin dosyalarını siler (hazır çeviriler silinmez)."""
    now = time.time()
    return sum(_rm(_job_path(j["id"])) for j in list_jobs() if j.get("status") in ("done", "failed", "cancelled")
               and now - float(j.get("finished") or j.get("created") or now) > max_age)


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


def active_jobs(m: Media) -> list[dict]:
    """Bu video için bekleyen ya da süren işler."""
    return [j for j in list_jobs() if j.get("status") in ("pending", "running")
            and Media.from_dict(j["media"]).key() == m.key()]


def cancel_jobs_for(m: Media, except_uid: str = "") -> int:
    """Aynı video için bekleyen/süren diğer işleri iptal eder (son seçilen kazanır).
    Biten parçalar önbellekte kaldığı için aynı altyazı yeniden seçilirse kaldığı yerden sürer."""
    jobs = [j for j in active_jobs(m) if Sub.from_dict(j["sub"]).uid() != except_uid]
    for j in jobs:
        j["status"] = "cancelled"
        save_job(j)
    return len(jobs)


def get_job(job_id: str) -> dict | None:
    return _read(_job_path(job_id))


def job_status(job_id: str) -> str:
    return (get_job(job_id) or {}).get("status", "")


def want_load(m: Media) -> int:
    """Video oynatılmaya başladı: önceden başlamış (sıradaki bölüm) işleri bitince yüklenecek yapar."""
    jobs = [j for j in active_jobs(m) if not j.get("load")]
    for j in jobs:
        j["load"] = True
        save_job(j)
    return len(jobs)


def running_job_for(m: Media, sub: Sub) -> dict | None:
    return next((j for j in active_jobs(m) if Sub.from_dict(j["sub"]).uid() == sub.uid()), None)


# ------------------------------------------------------------------ temizlik

def _remove_older(folder: str, max_age: float) -> int:
    now = time.time()
    paths = (os.path.join(folder, name) for name in os.listdir(folder))
    return sum(_rm(p) for p in paths if os.path.isfile(p) and now - _mtime(p) > max_age)


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
    _touch(os.path.join(kodi.profile_dir(), "cleaned"), str(time.time()))
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
        _put_state("fingerprints.json", d)
    return added


def last_clean() -> float:
    return _mtime(os.path.join(kodi.profile_dir(), "cleaned"))


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
        n = sum(_rm(_job_path(j["id"])) for j in list_jobs() if j.get("status") != "done")
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
        for name in ("badsync.json", "series_prefs.json", "loaded.json", "requested.json"):
            _rm(_state_path(name))
        return "Senkron işaretleri ve dizi tercihleri silindi"
    if what == "all":
        msgs = [clean(x) for x in ("ai", "search", "downloads", "jobs", "glossary", "syncmarks")]
        for name in ("cache.sqlite", "os_token.json", "state.json", "quota.json", "defta.log", "defta.log.1"):
            _rm(_state_path(name))
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
    _touch(marker, kodi.version())
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
                    fixed += _rm(path)
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
