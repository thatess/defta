"""Altyazı indirme (önbellekli) ve Kodi'ye verilecek geçici kopya."""
from __future__ import annotations

import os
import re
import shutil
import time

from . import kodi, store
from .media import Media
from .sources import ALL, LABEL
from .sources.base import QuotaError, SourceError, Sub

SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt")


FROM = {"opensubtitles": "OpenSubtitles'tan", "subdl": "SubDL'den", "subsource": "SubSource'tan",
        "jimaku": "Jimaku'dan"}


def _download(sub: Sub, m: Media) -> tuple[str, bytes]:
    mod = ALL.get(sub.source)
    if mod is None:
        raise SourceError("bilinmeyen kaynak")
    t0 = time.time()
    try:
        out = mod.download(sub, m)
    except QuotaError as e:
        store.set_quota(sub.source, e.until, e.quota)
        raise
    store.clear_quota(sub.source, before=t0)
    return out


def get(sub: Sub, m: Media) -> str:
    """Altyazıyı indirir (daha önce indirildiyse diskten) ve dosya yolunu döner. Kaynak indirmezse
    (hak doldu, site meşgul, dosya bozuk) aynı altyazının başka kaynaktaki kopyası sessizce denenir.
    Hata mesajları bildirimde gösterilecek kadar kısadır; ayrıntı kayıtta."""
    have = store.find_download(sub)
    if have:
        return have
    first = None
    for i, cand in enumerate([sub] + [Sub.from_dict(a) for a in sub.alts]):
        if i and cand.source in store.quota_blocked():
            continue
        try:
            name, data = _download(cand, m)
        except SourceError as e:
            kodi.log(f"İndirilemedi: {LABEL.get(cand.source, cand.source)} {cand.release or cand.sid}: {e}", "warning")
            first = first or e
            continue
        if i:
            kodi.notify(FROM.get(cand.source, cand.source) + " indirildi")
        ext = os.path.splitext(name or "")[1].lower()
        path = store.download_path(sub, ext if ext in SUB_EXTS else ".srt")
        with open(path, "wb") as f:
            f.write(data)
        store.remember_fingerprint(sub, path)       # içeriği başka kaynaktakiyle aynıysa listede tek görünsün
        kodi.log(f"İndirildi: {LABEL.get(cand.source, cand.source)} {cand.release or cand.sid} -> "
                 f"{os.path.basename(path)}")
        return path
    if isinstance(first, QuotaError):
        raise SourceError(first.args[0].split(" (")[0] + " · " + store.quota_note(first.until, first.quota)
                          .split(" · ")[1]) from first
    raise SourceError("İndirilemedi · başka seç") from first


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


SYNC_NOTE = {"fps": "Kare hızı düzeltildi",
             "long": "Altyazı uzun, kayabilir",
             "short": "Altyazı kısa, eksik olabilir"}


def for_kodi(path: str, label: str, lang: str) -> str:
    """Kodi'ye verilecek kopya. Dosya adındaki dil kodu Kodi'nin altyazı dilini göstermesini sağlar."""
    ext = os.path.splitext(path)[1].lower() or ".srt"
    safe = re.sub(r"[^\w .()-]+", "", label, flags=re.U).strip()[:60] or "Defta"
    dst = os.path.join(kodi.temp_dir(), f"{safe}.{int(time.time() * 1000) % 10 ** 8}.{lang}{ext}")
    shutil.copyfile(path, dst)
    return dst


def playback_started() -> float:
    """Şu anki videonun başladığı an. Servis video başlayınca ana pencereye yazar; yoksa oynatıcının
    geçen süresinden tahmin edilir."""
    try:
        import xbmcgui
        v = float(xbmcgui.Window(10000).getProperty("defta.play_start") or 0)
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    try:
        import xbmc
        p = xbmc.Player()
        if p.isPlayingVideo():
            return time.time() - float(p.getTime() or 0) - 30
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _looks_target(path: str, name: str, tgt: str) -> bool:
    """Zaten hedef dilde olan dosya (ör. Defta'nın ya da başka eklentinin yüklediği Türkçe altyazı)."""
    from . import langs
    parts = name.lower().rsplit(".", 2)
    if len(parts) == 3 and langs.norm(parts[1]) == tgt:
        return True
    if tgt == "tr":
        try:
            with open(path, "rb") as f:
                head = f.read(20000).decode("utf-8", "ignore")
        except OSError:
            return False
        letters = sum(head.count(c) for c in "ğşıİĞŞ")
        return letters >= 15
    return False


def sidecar_subtitle(video: str, tgt: str = "") -> str:
    """Videonun yanındaki, aynı adla başlayan altyazı dosyası (Kodi bunu kendiliğinden yükler); hedef
    dildekiler sayılmaz. Yalnızca yerel dosyalarda."""
    if not video or "://" in video or not os.path.isfile(video):
        return ""
    folder, stem = os.path.split(os.path.splitext(video)[0])
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for name in names:
        p = os.path.join(folder, name)
        if name.startswith(stem) and name[len(stem):len(stem) + 1] == "." and name.lower().endswith(SUB_EXTS) \
                and not (tgt and _looks_target(p, name, tgt)):       # "Ep1" için "Ep10.en.srt" değil
            return p
    return ""


def loaded_subtitle(since: float, tgt: str = "", video: str = "") -> str:
    """Başka bir eklentiyle (a4k vb.) bu video başladıktan sonra yüklenmiş en yeni altyazı dosyası.
    Kodi ve eklentiler indirdikleri altyazıyı geçici klasörlere koyar; oralara bakılır. Video başlamadan
    önceki dosyalar (ör. önceki bölümün altyazısı) ve zaten hedef dilde olanlar sayılmaz.
    Bulunamazsa videonun yanındaki altyazı dosyası."""
    if since <= 0:
        return sidecar_subtitle(video, tgt)
    # Yalnızca altyazının konabileceği yerler: Kodi'nin geçici klasörü ve adında "subtitle" geçen ya da video
    # eklentilerinin (Fen vb.) veri klasörleri. Bütün eklentileri derinlemesine taramak Shield'da listeyi yavaşlatır.
    data = kodi.translate_path("special://profile/addon_data/")
    roots = [(kodi.translate_path("special://temp/"), 3)]
    try:
        roots += [(os.path.join(data, n), 3 if "subtitle" in n.lower() else 2) for n in sorted(os.listdir(data))
                  if n != kodi.ADDON_ID and ("subtitle" in n.lower() or n.startswith("plugin.video."))]
    except OSError:
        pass
    own = None                          # Defta'nın kendi verdiği dosyalar: yalnızca aday çıkınca hesaplanır
    best, best_t = "", 0.0
    for root, depth in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, files in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= depth:
                dirnames[:] = []
            for name in files:
                if not name.lower().endswith(SUB_EXTS):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    t = os.path.getmtime(p)
                except OSError:
                    continue
                if t >= since and t > best_t and not (tgt and _looks_target(p, name, tgt)):
                    if own is None:
                        own = _own_given(since)
                    if _digest(p) not in own:
                        best, best_t = p, t
    return best or sidecar_subtitle(video, tgt)


def _digest(path: str) -> str:
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return ""


def _own_given(since: float) -> set:
    """Defta'nın bu video için Kodi'ye verdiği dosyalar: Kodi bunların kopyasını geçici klasörüne koyabilir,
    "başka eklentinin altyazısı" sanılmasın."""
    d = kodi.temp_dir()
    out = set()
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if os.path.getmtime(p) >= since - 5:
                out.add(_digest(p))
        except OSError:
            pass
    return out
