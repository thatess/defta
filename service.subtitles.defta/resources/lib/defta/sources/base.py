"""Kaynaklar için ortak tipler: altyazı kaydı ve arşivden doğru dosyayı seçme."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass, field

SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt")


@dataclass
class Sub:
    source: str                 # opensubtitles | subdl | subsource | jimaku
    sid: str                    # kaynağın indirme kimliği (file_id, url, subtitleId)
    lang: str                   # iki harfli dil kodu
    release: str = ""           # gösterilecek sürüm adı
    releases: list = field(default_factory=list)
    downloads: int = 0
    rating: float = 0.0
    hi: bool = False            # işitme engelli (SDH)
    ai: bool = False            # makine/AI çevirisi (insan çevirisi değil)
    uploader: str = ""
    season: int = 0
    episode: int = 0
    pack: bool = False          # sezon paketi (arşivden bölüm seçilir)
    score: float = 0.0          # sürüm uyumu puanı (sıralama)
    sync: bool = False
    hash_match: bool = False    # OpenSubtitles video karmasıyla eşleşti: senkronu kesin
    note: str = ""              # listede gösterilecek kısa not ("önceki bölümde kullandığın", "kaymış")
    alts: list = field(default_factory=list)   # aynı altyazının başka kaynaktaki kopyaları (indirme olmazsa)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["releases"] = d["releases"][:3]
        d["alts"] = [dict(a, alts=[], releases=a.get("releases", [])[:1]) for a in d["alts"][:3]]
        return d

    def __post_init__(self):
        if "?" in self.sid:                    # bağlantıdaki api_key vb. hiçbir yerde saklanmaz
            self.sid = self.sid.split("?")[0]

    @staticmethod
    def from_dict(d: dict) -> "Sub":
        s = Sub(source=d.get("source", ""), sid=str(d.get("sid", "")), lang=d.get("lang", ""))
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        s.sid = str(s.sid).split("?")[0]
        return s

    def uid(self) -> str:
        sid = self.sid.split("?")[0]
        return f"{self.source}-{re.sub(r'[^A-Za-z0-9_.-]+', '_', sid)[-80:]}"


class SourceError(Exception):
    pass


class QuotaError(SourceError):
    """Kaynak şimdilik indirmeye izin vermiyor (günlük hak doldu ya da çok istek): until = yeniden denenecek an."""

    def __init__(self, message: str, until: float, quota: bool = True):
        super().__init__(message)
        self.until = until
        self.quota = quota          # True: günlük hak doldu, False: geçici yoğunluk (429)


BUSY_WAIT = 600                     # sunucu süre söylemezse 10 dk


def busy_error(e, label: str) -> QuotaError | None:
    """HTTP 429 (çok istek): sunucunun söylediği süre ya da 10 dk sonra yeniden denenir."""
    import time
    if getattr(e, "status", 0) != 429:
        return None
    wait = e.retry_after() or BUSY_WAIT
    return QuotaError(f"{label} meşgul", time.time() + min(wait, 6 * 3600), quota=False)


_EP_PATTERNS = [
    r"[sS]0*{s}[ ._-]?[eE]0*{e}(?!\d)",
    r"(?<!\d)0*{s}x0*{e}(?!\d)",
    r"[eE][pP]?[ ._-]?0*{e}(?!\d)",
    r"(?:^|[ ._\[-])0*{e}(?:v\d)?(?=[ ._\]\[(-]|$)",
]


def pick_from_archive(raw: bytes, season: int = 0, episode: int = 0) -> tuple[str, bytes]:
    """Zip içinden altyazıyı seçer: bölüm verilmişse o bölümünkini, yoksa en büyük altyazı dosyasını.
    Arşiv değilse (düz .srt) olduğu gibi döner."""
    if not raw[:4] == b"PK\x03\x04":
        return "subtitle.srt", raw
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise SourceError(f"arşiv açılamadı: {e}") from e
    names = [n for n in zf.namelist() if n.lower().endswith(SUB_EXTS) and not n.startswith("__MACOSX")]
    if not names:
        raise SourceError("arşivde altyazı dosyası yok (rar ya da resim tabanlı altyazı olabilir)")
    chosen = None
    if episode and len(names) > 1:
        for pat in _EP_PATTERNS:
            rx = re.compile(pat.format(s=season or 1, e=episode))
            hits = [n for n in names if rx.search(n.rsplit("/", 1)[-1])]
            if hits:
                chosen = hits[0]
                break
        if chosen is None:
            raise SourceError(f"arşivde {episode}. bölümün altyazısı bulunamadı")
    if chosen is None:
        # Tercih: .srt, sonra en büyük
        chosen = max(names, key=lambda n: (n.lower().endswith(".srt"), zf.getinfo(n).file_size))
    return chosen.rsplit("/", 1)[-1], zf.read(chosen)
