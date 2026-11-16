"""Senkron: video karması (OpenSubtitles hash), süre kontrolü ve kare hızı düzeltmesi.

Karma: dosya boyutu + ilk ve son 64 KB'ın 8 baytlık sözcüklerinin toplamı (OpenSubtitles algoritması).
Eşleşen altyazı bu dosya için yapılmıştır, senkronu kesindir. Yayınlarda (Fen/Umbrella) Kodi dosyanın
başını ve sonunu aralık isteğiyle okuyabiliyorsa çalışır; okuyamazsa sessizce atlanır."""
from __future__ import annotations

import struct
import threading

from . import kodi

CHUNK = 65536
FPS_PAIRS = ((25.0, 23.976), (25.0, 24.0), (24.0, 23.976), (30.0, 25.0), (29.97, 25.0))


def _read_head_tail(path: str):
    import xbmcvfs
    f = xbmcvfs.File(path)
    try:
        size = int(f.size() or 0)
        if size < CHUNK * 2:
            return None
        head = f.readBytes(CHUNK)
        f.seek(size - CHUNK, 0)
        tail = f.readBytes(CHUNK)
    finally:
        f.close()
    if len(head) < CHUNK or len(tail) < CHUNK:
        return None
    return size, bytes(head), bytes(tail)


def compute_hash(size: int, head: bytes, tail: bytes) -> str:
    h = size
    for block in (head, tail):
        for (q,) in struct.iter_unpack("<Q", block[:CHUNK]):
            h = (h + q) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def moviehash(path: str, timeout: float = 8.0) -> tuple[str, int]:
    """("", 0) okunamazsa. Uzun sürerse (yavaş yayın) beklemeden vazgeçilir."""
    if not path or path.startswith(("plugin://", "pvr://", "upnp://")):
        return "", 0
    box: dict = {}

    def run():
        try:
            box["r"] = _read_head_tail(path)
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    r = box.get("r")
    if not r:
        if "e" in box:
            kodi.log(f"Video karması hesaplanamadı: {type(box['e']).__name__}", "debug")
        elif t.is_alive():
            kodi.log("Video karması zaman aşımı (yayın yavaş), atlandı", "debug")
        return "", 0
    size, head, tail = r
    return compute_hash(size, head, tail), size


# ------------------------------------------------------------------ süre kontrolü

def subtitle_end(path: str) -> float:
    """Altyazının son satırının bitiş zamanı (saniye). Okunamazsa 0."""
    try:
        from pathlib import Path
        from subtrans.subformats import read_subtitle
        blocks = read_subtitle(Path(path)).blocks
    except Exception:  # noqa: BLE001
        return 0.0
    return max((b.end_ms for b in blocks), default=0) / 1000.0


def check(sub_end: float, duration: float) -> tuple[str, float]:
    """("ok", 1) | ("fps", çarpan) | ("long", oran) | ("short", oran) | ("unknown", 0).
    Altyazı videodan uzunsa kesin uyumsuzdur; oran tam bir kare hızı oranındaysa düzeltilebilir.
    Kısa olması normaldir (jenerik), yalnızca çok kısaysa (yarısından az) uyarılır."""
    if sub_end <= 0 or duration <= 60:
        return "unknown", 0.0
    ratio = sub_end / duration
    if ratio <= 1.01:
        return ("short", ratio) if ratio < 0.5 else ("ok", 1.0)
    for a, b in FPS_PAIRS:
        for f in (a / b, b / a):
            if f > 1 and abs(ratio - f) <= 0.006:
                return "fps", 1.0 / f
    return "long", ratio


def fix_fps(path: str, factor: float, out_path: str) -> bool:
    """Bütün zaman kodlarını çarpanla ölçekler ve yeni dosyaya yazar."""
    try:
        from pathlib import Path
        from subtrans.srt_io import Block
        from subtrans.subformats import read_subtitle, write_atomic
        srt = read_subtitle(Path(path))
        blocks = [Block(pos=b.pos, index=b.index, start_ms=int(round(b.start_ms * factor)),
                        end_ms=int(round(b.end_ms * factor)), text=b.text, tail=b.tail) for b in srt.blocks]
        fmt = srt.doc.fmt if srt.doc is not None else "srt"
        write_atomic(Path(out_path), blocks, fmt, srt.doc)
        return True
    except Exception as e:  # noqa: BLE001
        kodi.log(f"kare hızı düzeltmesi yapılamadı: {e}", "warning")
        return False
