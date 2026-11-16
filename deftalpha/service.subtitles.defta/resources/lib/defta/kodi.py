"""Kodi ile ilgili ortak yardımcılar: ayarlar, klasörler, kayıt, bildirim, anahtarlar.

API anahtarları hiçbir zaman kayda yazılmaz; yalnızca 'girilmiş / girilmemiş' bilgisi yazılır."""
from __future__ import annotations

import logging
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import ADDON_ID, langs

_lock = threading.Lock()


def addon():
    return xbmcaddon.Addon(ADDON_ID)


def version() -> str:
    try:
        return addon().getAddonInfo("version")
    except Exception:  # noqa: BLE001
        return "?"


def setting(key: str, default: str = "") -> str:
    try:
        v = addon().getSetting(key)
    except Exception:  # noqa: BLE001
        return default
    return default if v is None else v


def setting_bool(key: str, default: bool = False) -> bool:
    v = setting(key, "").strip().lower()
    if not v:
        return default
    return v in ("true", "1", "yes")


def setting_int(key: str, default: int = 0) -> int:
    try:
        return int(setting(key, str(default)) or default)
    except ValueError:
        return default


# ------------------------------------------------------------------ klasörler

def translate_path(p: str) -> str:
    return xbmcvfs.translatePath(p)


_DIRS: dict = {}


def profile_dir(*parts: str) -> str:
    """Defta'nın veri klasörü (yoksa oluşturulur; bir kez oluşturulan yol ezberlenir)."""
    path = _DIRS.get(parts)
    if path is None or not os.path.isdir(path):
        path = os.path.join(translate_path(f"special://profile/addon_data/{ADDON_ID}/"), *parts)
        os.makedirs(path, exist_ok=True)
        _DIRS[parts] = path
    return path


def addon_dir() -> str:
    return translate_path(f"special://home/addons/{ADDON_ID}/")


def temp_dir() -> str:
    return profile_dir("temp")


# ------------------------------------------------------------------ kayıt

_LOG_MAX = 2 * 1024 * 1024


def _append(path: str, line: str) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) > _LOG_MAX:
            old = path + ".1"
            if os.path.exists(old):
                os.remove(old)
            os.replace(path, old)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


_SECRET_KEYS = ("openai_key", "os_key", "os_pass", "subdl_key", "subsource_key", "jimaku_key")
_secret_cache = {"t": 0.0, "values": []}


def secret_values() -> list:
    """Kayda asla yazılmaması gereken değerler (anahtarlar, şifre). 60 sn önbellekli."""
    now = time.time()
    if now - _secret_cache["t"] > 60:
        vals = []
        for k in _SECRET_KEYS:
            try:
                v = secret(k)
            except Exception:  # noqa: BLE001
                v = ""
            if v and len(v) >= 6:
                vals.append(v)
        _secret_cache.update(t=now, values=sorted(set(vals), key=len, reverse=True))
    return _secret_cache["values"]


_KEY_RE = None


def scrub(text: str) -> str:
    """Anahtar değerleri ve adreslere/dosya adlarına karışmış 'api_key=...' izleri *** yapılır."""
    global _KEY_RE
    for v in secret_values():
        if v in text:
            text = text.replace(v, "***")
    if _KEY_RE is None:
        import re
        _KEY_RE = re.compile(r"(api[_-]?key[=_:]\s*)[A-Za-z0-9_-]{8,}", re.I)
    return _KEY_RE.sub(r"\1***", text)


def log(msg: str, level: str = "info") -> None:
    msg = scrub(msg)
    lvl = {"debug": xbmc.LOGDEBUG, "info": xbmc.LOGINFO, "warning": xbmc.LOGWARNING,
           "error": xbmc.LOGERROR}.get(level, xbmc.LOGINFO)
    if level == "debug" and not setting_bool("debug"):
        return
    xbmc.log(f"[Defta] {msg}", lvl)
    line = time.strftime("%Y-%m-%d %H:%M:%S") + f" {level.upper():7s} {msg}"
    with _lock:
        own = os.path.join(profile_dir(), "defta.log")
        _append(own, line)
        extra = setting("debug_dir").strip()
        if extra:
            if is_url(extra):
                _mirror(own, extra)
            else:
                try:
                    os.makedirs(extra, exist_ok=True)
                    _append(os.path.join(extra, "defta.log"), line)
                except OSError:
                    pass


# Ağ klasörü (smb://, nfs://): Shield'da kayıtlar bilgisayarın paylaşılan klasörüne yazılabilsin.
# Ağ dosyasına satır eklenemediği için kayıt dosyası en çok 5 sn'de bir oraya kopyalanır (iş parçacığı
# açılmaz: kısa ömürlü eklenti betiklerinde açık kalan iş parçacığı Kodi'yi çökertebilir).
_mirror_state = {"t": 0.0, "dirty": False}


def is_url(path: str) -> bool:
    return "://" in path and not path.startswith("special://")


def vfs_join(folder: str, name: str) -> str:
    if is_url(folder):
        return folder.rstrip("/") + "/" + name
    return os.path.join(translate_path(folder) if folder.startswith("special://") else folder, name)


def vfs_copy(src: str, dst: str) -> bool:
    try:
        return bool(xbmcvfs.copy(src, dst))
    except Exception:  # noqa: BLE001
        return False


def _mirror(own: str, folder: str, now: bool = False) -> None:
    _mirror_state["dirty"] = True
    if not now and time.time() - _mirror_state["t"] < 5:
        return
    _mirror_state["t"] = time.time()
    _mirror_state["dirty"] = False
    vfs_copy(own, vfs_join(folder, "defta.log"))


def flush_log() -> None:
    """Kayıt kopyası ağ klasöründeyse son satırları hemen gönderir (servis kapanırken)."""
    extra = setting("debug_dir").strip()
    if extra and is_url(extra) and _mirror_state["dirty"]:
        with _lock:
            _mirror(os.path.join(profile_dir(), "defta.log"), extra, now=True)


def export_kodi_logs(dest: str = "") -> list:
    """Kodi'nin kendi kayıtlarını (kodi.log, çökme öncesi kodi.old.log) kayıt klasörüne kopyalar.
    Klasör ağ klasörü (smb://...) de olabilir."""
    dest = dest or setting("debug_dir").strip()
    if not dest:
        return []
    out = []
    src_dir = translate_path("special://logpath/")
    try:
        if not is_url(dest):
            os.makedirs(translate_path(dest) if dest.startswith("special://") else dest, exist_ok=True)
        for name in os.listdir(src_dir):
            low = name.lower()
            if low in ("kodi.log", "kodi.old.log") or (low.startswith("kodi_crashlog") and low.endswith(".log")):
                # Kodi'nin kendi kaydında eski sürümlerden kalma anahtar izi olabilir: kopya temizlenerek yazılır
                if _copy_scrubbed(os.path.join(src_dir, name), vfs_join(dest, name)):
                    out.append(name)
        if vfs_copy(os.path.join(profile_dir(), "defta.log"), vfs_join(dest, "defta.log")):
            out.append("defta.log")
    except OSError as e:
        log(f"Kodi kayıtları kopyalanamadı: {e}", "warning")
    return out


def _copy_scrubbed(src: str, dst: str) -> bool:
    try:
        with open(src, encoding="utf-8", errors="replace") as f:
            text = scrub(f.read())
    except OSError:
        return False
    try:
        if is_url(dst):
            f = xbmcvfs.File(dst, "w")
            try:
                return bool(f.write(text.encode("utf-8")))
            finally:
                f.close()
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except (OSError, RuntimeError):
        return False


def scrub_kodi_old_log() -> bool:
    """Kodi'nin bir önceki oturum kaydı (kodi.old.log, artık yazılmıyor) eski Defta sürümlerinin yazdığı
    anahtar izini taşıyorsa yerinde temizlenir. Açık olan kodi.log'a dokunulmaz."""
    path = os.path.join(translate_path("special://logpath/"), "kodi.old.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    new = scrub(text)
    if new == text:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return True
    except OSError:
        return False


class KodiLogHandler(logging.Handler):
    """Çeviri motorunun (subtrans) kayıtlarını Defta kaydına yönlendirir."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        level = "debug" if record.levelno < logging.INFO else (
            "info" if record.levelno < logging.WARNING else "warning")
        for ln in msg.splitlines() or [""]:
            log("  " + ln, level)


def attach_engine_log() -> None:
    lg = logging.getLogger("subtrans")
    if not any(isinstance(h, KodiLogHandler) for h in lg.handlers):
        lg.addHandler(KodiLogHandler())
    lg.setLevel(logging.DEBUG if setting_bool("debug") else logging.INFO)
    lg.propagate = False


# ------------------------------------------------------------------ arayüz

INFO_MS, ERROR_MS = 3000, 6000


def notify(msg: str, heading: str = "Defta", ms: int | None = None, error: bool = False) -> None:
    """Kısa bildirim: bilgi 3 sn, hata 6 sn."""
    ms = ms or (ERROR_MS if error else INFO_MS)
    try:
        opened = float(xbmcgui.Window(10000).getProperty("defta.box_open") or 0)
        if opened and time.time() - opened < 60 and not error:
            return                       # sıradaki bölüm kutusu açık: bilgi bildirimi gösterilmez
    except (ValueError, RuntimeError):
        pass
    try:
        icon = os.path.join(addon_dir(), "icon.png")
        xbmcgui.Dialog().notification(heading, msg, icon if os.path.exists(icon) else xbmcgui.NOTIFICATION_INFO,
                                      ms, False)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ dil ayarları

def target_lang() -> str:
    return langs.code_at(setting_int("target_lang", 0))


def source_langs(anime: bool = False) -> list[str]:
    """Çevrilecek kaynak dilleri, öncelik sırasıyla (hedef dil hariç)."""
    tgt = target_lang()
    out = [langs.code_at(setting_int("src_lang", 1))]
    for c in langs.parse_list(setting("extra_src_langs")):
        if c not in out:
            out.append(c)
    if anime and setting_bool("anime_japanese"):
        out = ["ja"] + [c for c in out if c != "ja"]
    return [c for c in out if c != tgt] or (["en"] if tgt != "en" else ["ja"])


# ------------------------------------------------------------------ anahtarlar

ENV_NAMES = {
    "openai_key": "OPENAI_API_KEY",
    "os_key": "OPENSUBTITLES_API_KEY",
    "os_user": "OPENSUBTITLES_USERNAME",
    "os_pass": "OPENSUBTITLES_PASSWORD",
    "subdl_key": "SUBDL_API_KEY",
    "subsource_key": "SUBSOURCE_API_KEY",
    "jimaku_key": "JIMAKU_API_KEY",
}


_env_cache = {"path": None, "t": 0.0, "values": {}}


def _env_file_values() -> dict:
    """Anahtar dosyasındaki KEY=değer satırları. Ağ klasöründen (Shield) her seferinde okunmasın diye
    60 sn önbellekte tutulur."""
    path = setting("key_file").strip().strip('"')
    if not path:
        return {}
    now = time.time()
    if _env_cache["path"] == path and now - _env_cache["t"] < 60:
        return _env_cache["values"]
    try:
        text = read_text(path)            # smb://, nfs://, special:// (Shield: ağ klasörü ya da USB)
    except (OSError, RuntimeError):
        text = ""
    out = parse_env(text)
    _env_cache.update(path=path, t=now, values=out)
    return out


def parse_env(text: str) -> dict:
    """KEY=değer satırları (# yorum, export önekleri, tırnaklar yok sayılır)."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[7:].strip()
        out[k] = v.strip().strip('"').strip("'").strip()
    return out


def read_text(path: str) -> str:
    """Yerel ya da Kodi yolu (smb://, special://, USB) — metin olarak."""
    if "://" in path:
        f = xbmcvfs.File(path)
        try:
            return bytes(f.readBytes()).decode("utf-8-sig", "replace")
        finally:
            f.close()
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


KEY_SETTINGS = ("openai_key", "os_key", "os_user", "os_pass", "subdl_key", "subsource_key", "jimaku_key")
KEY_LABELS = {"openai_key": "OpenAI", "os_key": "OpenSubtitles anahtarı", "os_user": "OpenSubtitles kullanıcı adı",
              "os_pass": "OpenSubtitles şifresi", "subdl_key": "SubDL", "subsource_key": "SubSource",
              "jimaku_key": "Jimaku"}


def forget_secret_cache() -> None:
    _env_cache.update(path=None, t=0.0, values={})
    _secret_cache.update(t=0.0, values=[])


def secret(key: str) -> str:
    """Ayarlardaki değer; boşsa anahtar dosyasındaki (.env) değer. Asla kayda yazılmaz."""
    v = setting(key).strip()
    if v:
        return v
    env = ENV_NAMES.get(key)
    if not env:
        return ""
    return _env_file_values().get(env, "").strip()     # Windows ortam değişkenleri okunmaz (eski anahtar karışmasın)


def describe_secret(key: str) -> str:
    v = secret(key)
    if not v:
        return "girilmemiş"
    if setting(key).strip():
        src = "ayarlar"
    else:
        src = "anahtar dosyası"
    return f"girilmiş ({len(v)} karakter, {src})"


def source_enabled(name: str) -> bool:
    flag = {"opensubtitles": "os_enabled", "subdl": "subdl_enabled", "subsource": "subsource_enabled",
            "jimaku": "jimaku_enabled"}[name]
    key = {"opensubtitles": "os_key", "subdl": "subdl_key", "subsource": "subsource_key",
           "jimaku": "jimaku_key"}[name]
    return setting_bool(flag, name != "jimaku") and bool(secret(key))
