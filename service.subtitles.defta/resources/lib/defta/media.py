"""Oynatılan videonun bilgisi: ad, yıl, sezon/bölüm, IMDb, sürüm adı.

Kaynak sırası: Kodi'nin video bilgisi (Fen/Umbrella bunları doldurur) -> Fen/Umbrella'nın
'subs.player_filename' özelliği -> dosya adı çözümlemesi."""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field

VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".webm", ".mov", ".wmv", ".strm")


@dataclass
class Media:
    title: str = ""            # film adı ya da dizi adı
    year: int = 0
    season: int = 0
    episode: int = 0
    imdb: str = ""             # tt1234567 (dizide dizinin ya da bölümün)
    filename: str = ""         # sürüm adını taşıyan dosya adı
    path: str = ""             # oynatılan dosya/URL
    is_anime: bool = False
    duration: float = 0.0      # saniye (oynatıcıdan)
    hash: str = ""             # OpenSubtitles video karması (dosyanın baş/son 64 KB'ı + boyut)
    size: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def is_episode(self) -> bool:
        return self.episode > 0

    @property
    def release(self) -> str:
        return os.path.splitext(self.filename)[0] if self.filename else ""

    def key(self) -> str:
        """Bu videonun kalıcı anahtarı (hazır AI çevirisi ve arama önbelleği için).
        Dizide dizi adıyla kurulur: oynatıcı bazen dizinin, bazen bölümün IMDb numarasını verir;
        sıradaki bölümün anahtarı da önceden bilinebilsin."""
        if self.is_episode and self.title:
            return f"{slug(self.title)}-s{self.season:02d}e{self.episode:02d}"
        return self.legacy_key()

    def legacy_key(self) -> str:
        """0.2 sürümündeki anahtar (eski hazır çeviriler bulunabilsin)."""
        base = self.imdb or slug(self.title) or slug(self.release)
        k = f"{base}-{self.year}" if self.year and not self.imdb else base
        if self.is_episode:
            k += f"-s{self.season:02d}e{self.episode:02d}"
        return k

    def series_key(self) -> str:
        return slug(self.title) or slug(self.release)

    def auto_skip(self) -> str:
        """Otomatik altyazı aranmayacaksa sebebi: canlı yayın (PVR, TV kanalı) ya da tanınmayan video."""
        if self.extra.get("live"):
            return "canlı yayın"
        if not (self.imdb or self.is_episode or self.year) or not self.title:
            return "video tanınmadı"
        return ""
    def next_episode(self, next_season: bool = False) -> "Media":
        """Sıradaki bölümün bilgisi (sürüm adındaki bölüm numarası da kaydırılır, sürüm uyumu için)."""
        n = Media.from_dict(self.to_dict())
        n.season, n.episode = (self.season + 1, 1) if next_season else (self.season, self.episode + 1)
        n.imdb, n.path, n.hash, n.size, n.duration = "", "", "", 0, 0.0
        if self.filename:
            import re as _re
            n.filename = _re.sub(r"([sS])\d{1,2}([ ._-]?[eE])\d{1,4}",
                                 lambda mm: f"{mm.group(1)}{n.season:02d}{mm.group(2)}{n.episode:02d}", self.filename)
        return n

    def short(self) -> str:
        """Bildirimlerde: dizide S01E05, filmde adı."""
        if self.is_episode:
            return f"S{self.season:02d}E{self.episode:02d}"
        return self.title or self.release

    def label(self) -> str:
        t = self.title or self.release
        if self.is_episode:
            return f"{t} S{self.season:02d}E{self.episode:02d}"
        return f"{t} ({self.year})" if self.year else t

    def series_name(self) -> str:
        """Sözlük adı: dizide dizi adı, filmde film adı."""
        return self.title or self.release

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Media":
        m = Media()
        for k, v in (d or {}).items():
            if hasattr(m, k):
                setattr(m, k, v)
        return m


def slug(text: str) -> str:
    t = re.sub(r"[^\w]+", "-", (text or "").lower(), flags=re.U).strip("-")
    return t[:80]


# ------------------------------------------------------------------ dosya adı çözümleme

_SE_RES = [
    re.compile(r"[sS](\d{1,2})[ ._-]?[eE](\d{1,4})"),
    re.compile(r"\b(\d{1,2})x(\d{2,3})\b"),
    re.compile(r"[sS]eason[ ._-]?(\d{1,2}).*?[eE]pisode[ ._-]?(\d{1,4})"),
]
_ANIME_EP_RE = re.compile(r"(?:^|[ ._])-[ ._](\d{1,4})(?:v\d)?(?=[ ._\[(]|$)|[ ._][eE][pP]?(\d{1,4})(?=[ ._\[(]|$)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_GROUP_RE = re.compile(r"^\s*\[[^\]]+\]\s*")
_JUNK_RE = re.compile(r"\b(1080p|2160p|720p|480p|4k|web-?dl|webrip|web|bluray|blu-ray|bdrip|brrip|hdtv|dvdrip|"
                      r"x264|x265|h\.?264|h\.?265|hevc|aac|ddp?5\.1|atmos|10bit|remux|proper|repack|multi|dual)\b", re.I)


def clean_name(name: str) -> str:
    """URL ya da yol -> dosya adı (sorgu kısmı atılır, %xx çözülür)."""
    if not name:
        return ""
    name = name.split("|")[0]
    if "://" in name:
        name = urllib.parse.urlparse(name).path
    name = urllib.parse.unquote(name)
    return os.path.basename(name.replace("\\", "/"))


def parse_filename(name: str) -> dict:
    base = clean_name(name)
    stem = os.path.splitext(base)[0] if base.lower().endswith(VIDEO_EXT + (".srt",)) else base
    info = {"title": "", "year": 0, "season": 0, "episode": 0, "anime": False}
    s = stem
    anime_group = bool(_GROUP_RE.match(s))
    s = _GROUP_RE.sub("", s)
    cut = len(s)
    for rx in _SE_RES:
        m = rx.search(s)
        if m:
            info["season"], info["episode"] = int(m.group(1)), int(m.group(2))
            cut = min(cut, m.start())
            break
    if not info["episode"]:
        m = _ANIME_EP_RE.search(s)
        if m:
            info["season"], info["episode"] = 1, int(m.group(1) or m.group(2))
            cut = min(cut, m.start())
            info["anime"] = anime_group
    ym = _YEAR_RE.search(s[:cut] if cut < len(s) else s)
    if ym and ym.start() > 0:
        info["year"] = int(ym.group(1))
        cut = min(cut, ym.start())
    jm = _JUNK_RE.search(s)
    if jm:
        cut = min(cut, jm.start())
    title = s[:cut]
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"[\[(][^\])]*[\])]", " ", title)
    title = re.sub(r"\s+-\s*$|\s+", " ", title).strip(" -")
    info["title"] = title
    if anime_group:
        info["anime"] = True
    return info


def from_player() -> Media:
    """Şu an oynatılan videonun bilgisi (Kodi içinde çağrılır)."""
    import xbmc
    import xbmcgui

    def il(name: str) -> str:
        try:
            return xbmc.getInfoLabel(name) or ""
        except Exception:  # noqa: BLE001
            return ""

    m = Media()
    m.path = il("Player.Filenameandpath")
    prop = ""
    try:
        prop = xbmcgui.Window(10000).getProperty("subs.player_filename") or ""
    except Exception:  # noqa: BLE001
        pass
    m.filename = clean_name(prop) or clean_name(m.path)
    show = il("VideoPlayer.TVShowTitle")
    title = il("VideoPlayer.OriginalTitle") or il("VideoPlayer.Title")
    m.imdb = _imdb(il("VideoPlayer.UniqueID(imdb)") or il("VideoPlayer.IMDBNumber"))
    m.year = _int(il("VideoPlayer.Year"))
    m.season = _int(il("VideoPlayer.Season"))
    m.episode = _int(il("VideoPlayer.Episode"))
    parsed = parse_filename(m.filename)
    if show:
        m.title = show
    elif title and not title.lower().endswith(VIDEO_EXT) and title != m.filename and m.episode <= 0:
        m.title = title
    else:
        m.title = parsed["title"] or title
    if m.episode <= 0 and parsed["episode"]:
        m.season, m.episode = parsed["season"] or 1, parsed["episode"]
    if m.season < 0:
        m.season = 0
    if m.episode < 0:
        m.episode = 0
    if m.is_episode and not m.season:
        m.season = 1
    if not m.year:
        m.year = parsed["year"]
    try:
        p = xbmc.Player()
        if p.isPlayingVideo():
            m.duration = float(p.getTotalTime() or 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        live = m.path.lower().startswith("pvr://") or bool(il("VideoPlayer.ChannelName")) or any(
            xbmc.getCondVisibility(c) for c in ("PVR.IsPlayingTv", "PVR.IsPlayingRadio"))
    except Exception:  # noqa: BLE001
        live = False
    if live:
        m.extra["live"] = True
    genre = il("VideoPlayer.Genre").lower()
    m.is_anime = parsed["anime"] or "anime" in genre or "animation" in genre and _has_japanese_hint(m)
    return m


def _has_japanese_hint(m: Media) -> bool:
    return bool(re.search(r"\b(subsplease|erai|horriblesubs|judas|sakurato|ember|asw)\b", m.filename, re.I))


def _int(v: str) -> int:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _imdb(v: str) -> str:
    v = (v or "").strip()
    m = re.search(r"tt\d{5,}", v)
    return m.group(0) if m else ""
