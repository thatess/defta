"""Altyazı indirme (önbellekli) ve Kodi'ye verilecek geçici kopya."""
from __future__ import annotations

import os
import re
import shutil
import time

from . import kodi, store
from .media import Media
from .sources import ALL, LABEL
from .sources.base import SourceError, Sub

SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt")


def get(sub: Sub, m: Media) -> str:
    """Altyazıyı indirir (daha önce indirildiyse diskten) ve dosya yolunu döner."""
    have = store.find_download(sub)
    if have:
        return have
    mod = ALL.get(sub.source)
    if mod is None:
        raise SourceError("bilinmeyen kaynak")
    name, data = mod.download(sub, m)
    ext = os.path.splitext(name or "")[1].lower()
    if ext not in SUB_EXTS:
        ext = ".srt"
    path = store.download_path(sub, ext)
    with open(path, "wb") as f:
        f.write(data)
    kodi.log(f"İndirildi: {LABEL.get(sub.source, sub.source)} {sub.release or sub.sid} -> {os.path.basename(path)}")
    return path


def get_checked(sub: Sub, m: Media) -> tuple[str, str]:
    """İndirir ve videonun süresiyle karşılaştırır: (yol, durum).
    durum: ok | fps (kare hızı farkı düzeltildi, yol düzeltilmiş kopya) | long (videodan uzun: büyük
    ihtimalle başka sürüm) | short | unknown. Düzeltilmiş kopya ayrı dosyadır, indirilen bozulmaz."""
    from . import sync
    path = get(sub, m)
    if not m.duration:
        return path, "unknown"
    state, factor = sync.check(sync.subtitle_end(path), m.duration)
    if state == "fps":
        stem, ext = os.path.splitext(path)
        fixed = stem + ".fps" + ext
        if os.path.exists(fixed) or sync.fix_fps(path, factor, fixed):
            kodi.log(f"Kare hızı farkı düzeltildi (x{factor:.4f}): {os.path.basename(path)}")
            return fixed, "fps"
        state = "long"
    if state in ("long", "short"):
        kodi.log(f"Süre uyuşmuyor ({state}): {sub.release or sub.sid}, video {m.duration:.0f} sn", "warning")
    return path, state


SYNC_NOTE = {"fps": "Kare hızı farkı vardı, zamanlama düzeltildi.",
             "long": "Bu altyazı videodan uzun: büyük ihtimalle başka sürüm, kayabilir.",
             "short": "Bu altyazı videonun yarısından kısa: eksik ya da başka sürüm olabilir."}


def for_kodi(path: str, label: str, lang: str) -> str:
    """Kodi'ye verilecek kopya. Dosya adındaki dil kodu Kodi'nin altyazı dilini göstermesini sağlar."""
    ext = os.path.splitext(path)[1].lower() or ".srt"
    safe = re.sub(r"[^\w .()-]+", "", label, flags=re.U).strip()[:60] or "Defta"
    dst = os.path.join(kodi.temp_dir(), f"{safe}.{int(time.time() * 1000) % 10 ** 8}.{lang}{ext}")
    shutil.copyfile(path, dst)
    return dst


def clean_temp(max_age: float = 24 * 3600) -> None:
    d = kodi.temp_dir()
    now = time.time()
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if now - os.path.getmtime(p) > max_age:
                os.remove(p)
        except OSError:
            pass


def newest_loaded_subtitle(max_age: float = 12 * 3600) -> str:
    """Başka bir eklentiyle (a4k vb.) seçilip yüklenmiş en yeni altyazı dosyası.
    Kodi ve eklentiler indirdikleri altyazıyı geçici klasörlere koyar; oralara bakılır."""
    roots = [kodi.translate_path("special://temp/"),
             kodi.translate_path("special://profile/addon_data/")]
    mine = os.path.normcase(kodi.profile_dir())
    best, best_t = "", 0.0
    now = time.time()
    for root in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(root):
            if os.path.normcase(dirpath).startswith(mine) or dirpath.count(os.sep) - base_depth >= 4:
                dirnames[:] = []
                continue
            for name in files:
                if not name.lower().endswith(SUB_EXTS):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    t = os.path.getmtime(p)
                except OSError:
                    continue
                if now - t < max_age and t > best_t:
                    best, best_t = p, t
    return best
