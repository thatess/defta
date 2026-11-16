"""Sürüm adını parçalarına ayırır: grup, kaynak, yayın hizmeti, çözünürlük, görüntü ve ses kodeki, kurgu,
sezon/bölüm, yıl ve yapım adı. Senkron puanı (puan.py) ve sıkı bölüm denetimi bunları kullanır."""
import re
from urllib.parse import unquote

_UZANTI = re.compile(r"\.(?:mkv|mp4|avi|m4v|ts|m2ts|wmv|mov|webm|flv|mpe?g|iso|srt|ass|ssa|vtt|sub|idx|smi|txt|"
                     r"zip|rar|7z)$", re.I)
# Altyazı dosya adının sonundaki dil eki: ".en", ".tr.forced", ".eng.sdh", ".pt-BR" (desteklenen her dil için)
_DIL_EKI = re.compile(r"(?:[._-](?:en|eng|english|tr|tur|turkish|ja|jpn|jp|japanese|de|ger|deu|german|fr|fre|fra|french|"
                      r"es|spa|spanish|it|ita|italian|pt-?br|pb|pob|pt|por|portuguese|ru|rus|russian|ar|ara|arabic|"
                      r"ko|kor|korean|zh-?(?:cn|tw)|zh|chi|zho|chinese|nl|dut|nld|dutch|pl|pol|polish|el|gre|ell|greek|"
                      r"fa|per|fas|persian|farsi|az|aze|azerbaijani|uk|ukr|ukrainian|sv|swe|swedish|"
                      r"sdh|hi|cc|forced|default))+$", re.I)
_SITE_ETIKETI = re.compile(r"\s*[\[{(](?:rarbg|eztv(?:\.re)?|ettv|tgx|torrentgalaxy|yts(?:\.[a-z]{2})?|yify|"
                           r"publichd|psa|vxt|rartv|eng|multi[^\])}]*)[\]})]\s*$", re.I)

_KAYNAKLAR = (
    ("bluray", r"blu-?ray|bd-?rip|br-?rip|bd-?remux|remux|uhd-?bd|bd(?:25|50|5|9)?|hd-?dvd"),
    ("web", r"web-?dl|web-?rip|webhd|web-?cap|web|amzn-?web|itunes(?:hd)?"),
    ("hdtv", r"a?hdtv(?:rip)?|pdtv|sdtv|dsr(?:ip)?|dvb(?:rip)?|tv-?rip|satrip|uhdtv"),
    ("dvd", r"dvd-?rip|dvd(?:5|9|r)?|vhs-?rip"),
    ("cam", r"hd-?cam|cam(?:-?rip)?|hd-?ts|telesync|ts|tc|telecine|dvdscr|screener|scr|r5"),
)
_HIZMETLER = {"amzn": "amzn", "amazon": "amzn", "nf": "nf", "netflix": "nf", "dsnp": "dsnp", "dsny": "dsnp",
              "hmax": "max", "max": "max", "atvp": "atvp", "hulu": "hulu", "pcok": "pcok", "pmtp": "pmtp",
              "cr": "cr", "crunchyroll": "cr", "funi": "funi", "hidi": "hidive", "hidive": "hidive", "ip": "ip",
              "stan": "stan", "crav": "crav", "it": None, "now": "now", "sho": "sho", "roku": "roku",
              "dcu": "dcu", "vrv": "vrv", "adn": "adn", "bcore": "bcore", "mubi": "mubi", "aubc": "aubc",
              "tvnz": "tvnz", "blu": None}
_COZUNURLUK = (("2160p", r"2160p|4k|uhd"), ("1080p", r"1080[pi]"), ("720p", r"720p"), ("576p", r"576p"),
               ("480p", r"480p"))
_KODEK = (("h265", r"[xh][ .]?265|hevc"), ("h264", r"[xh][ .]?264|avc"), ("av1", r"av1"), ("xvid", r"xvid"),
          ("divx", r"divx"), ("vp9", r"vp9"))
_SES = (("truehd", r"true-?hd|atmos"), ("eac3", r"e-?ac-?3|ddp(?:\d[ .]?\d)?|dd\+(?:\d[ .]?\d)?"),
        ("ac3", r"ac-?3|dd(?:\d[ .]?\d)?"), ("dts", r"dts(?:-?hd)?(?:-?ma)?(?:-?x)?"), ("aac", r"aac(?:\d[ .]?\d)?"),
        ("flac", r"flac"), ("opus", r"opus"), ("mp3", r"mp3"))
_KURGULAR = (("directors_cut", r"director'?s?[ ._-]?cut"), ("extended", r"extended(?:[ ._-](?:cut|edition))?"),
             ("theatrical", r"theatrical(?:[ ._-](?:cut|edition))?"), ("unrated", r"unrated"))

_SE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,4})(?:(?:[ ._-]?-?[ ._-]?e|-)(\d{1,4})(?![0-9]|bit))?"
                 r"(?![0-9])")
_X = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?![0-9])")
_SEZON_BOLUM_YAZI = re.compile(r"(?i)\bseason[ ._-]?(\d{1,2})[ ._-]*(?:episode|ep)[ ._-]?(\d{1,4})\b")
_SEZON = re.compile(r"(?i)(?<![a-z0-9])(?:s(\d{1,2})|season[ ._-]?(\d{1,2}))(?![0-9])(?![ ._-]?e\d)")
_YALNIZ_BOLUM = re.compile(r"(?i)(?<![a-z0-9])(?:e|ep|episode)[ ._-]?(\d{1,4})(?![0-9])")
_ANIME_BOLUM = re.compile(r"(?:^|\s)-\s+(\d{1,4})(?:v\d)?(?=$|\s|[\[(])")
# Baştaki [Grup] etiketli ya da [CRC32] eki taşıyan adlarda tiresiz bölüm numarası: "[Grup] Ad 03 (1080p)",
# "[Grup]_Ad_03_(1280x720_BD)", paketteki "Ad_03_(1280x720_Blu-ray_FLAC)_[FB1A088F]"
_ANIME_SAYI = re.compile(r"(?:^|[\s._])(\d{1,3})(?:v\d)?[\s._]*(?=[\[(]|$)")
_BASTAKI_ETIKET = re.compile(r"^\s*(?:\[[^\]]*\][\s._]*)+")
_CRC = re.compile(r"\[[0-9A-Fa-f]{8}\]")
_YIL = re.compile(r"(?<![0-9])(19[3-9]\d|20[0-4]\d)(?![0-9])")
_GRUP = re.compile(r"-\s?([A-Za-z0-9][A-Za-z0-9_&!]{1,24})$")      # noktalı olmaz: "Spider-Man.2002" grup değil
_ANIME_GRUP = re.compile(r"^\s*\[([^\]]{2,30})\]")
_BOLUM_NUMARASI = re.compile(r"(?i)(?:e|ep)?\d+(?:v\d)?")          # "Ad - 05", "Ad S01 - E05": tireden sonraki grup değil
_GRUP_ADI = re.compile(r"[A-Za-z0-9][A-Za-z0-9_&!]{1,24}")
# Grup adı olamayan teknik parçalar (tablolardakilere ek olarak)
_TEKNIK_DESEN = re.compile(r"(?i)\d+|\d+bit|\d+(?:\.\d)?ch|[0-9a-f]{8}|s\d{1,2}(?:e\d{1,4})?|e\d{1,4}|v\d|"
                           r"\d+(?:\.\d+)?[gm]b|cd\d{1,2}|\d{2,3}fps|hi10p?|3d|h?sbs|hou")
# Yalnız yeniden paylaşan sitelerin etiketi (grup değil); YTS/YIFY ise kodlayanın kendisidir
_SITE_ADLARI = {"rarbg", "rartv", "eztv", "eztvx", "ettv", "tgx", "torrentgalaxy", "publichd"}
_TEKNIK_SOZCUK = {"hindi", "french", "fre", "german", "ger", "italian", "ita", "spanish", "spa", "russian", "rus",
                  "korean", "kor", "chinese", "chi", "english", "turkish", "tur", "japanese", "esub", "esubs", "msub",
                  "msubs", "dub", "dubbed", "subbed", "remastered", "imax", "hybrid", "criterion", "uncut",
                  "uncensored", "complete", "hq", "hc", "ws", "rerip", "real", "readnfo", "sdr", "dovi", "hdr10plus",
                  "dual", "audio", "ma", "hdrip", "org", "collection", "latino", "castellano", "portuguese", "brazilian",
                  "arabic", "polish", "nordic", "vostfr", "turkce", "dublaj", "altyazi", "multisub", "tamil", "telugu",
                  "malayalam", "urdu", "persian", "farsi", "vietnamese", "thai", "indonesian", "dutch", "swedish",
                  "greek", "hebrew", "czech", "hungarian", "romanian", "ukrainian", "danish", "finnish", "norwegian"}
_GRUP_DEGIL = {"dl", "rip", "web", "hd", "sd", "x264", "x265", "h264", "h265", "hevc", "avc", "aac", "ac3", "dts",
               "1080p", "720p", "2160p", "480p", "uhd", "hdr", "hdr10", "dv", "remux", "proper", "repack", "internal",
               "limited", "extended", "unrated", "multi", "dual", "subs", "sub", "eng", "en", "tr", "jpn", "ja",
               "cut", "edition", "sdh", "forced", "atmos", "ddp", "dd", "5.1", "2.0", "7.1"}


def _ara(desen, metin):
    return re.search(r"(?i)(?<![a-z0-9])(?:" + desen + r")(?![a-z0-9])", metin)


def _ilk(tablo, metin):
    for ad, desen in tablo:
        if _ara(desen, metin):
            return ad
    return None


def surum_adi_mi(s):
    """Ad bir sürüm adı mı ("Film.2014.1080p.BluRay.x264-GRUP"), yoksa temiz bir yapım adı mı ("Charlotte's Web (2006)",
    "Blade Runner 2049"). Boşluklu adda kaynak sözcüğü ("Web") ile yıl yetmez, grup da gerekir. s: Surum."""
    if s.cozunurluk or s.kodek:
        return True
    if not s.kaynak:
        return False
    return bool(s.grup) if " " in (s.ad or "") else bool(s.grup or s.yil)


def _teknik(parca):
    """Sürüm adının teknik parçası mı (çözünürlük, kaynak, kodek, ses, kurgu, dil, sayı...): grup adı olamaz."""
    k = parca.lower()
    if not k or k in _GRUP_DEGIL or k in _TEKNIK_SOZCUK or k in _HIZMETLER or _TEKNIK_DESEN.fullmatch(k):
        return True
    return any(_ilk(tablo, parca) for tablo in (_KAYNAKLAR, _COZUNURLUK, _KODEK, _SES, _KURGULAR))


def _grup_adi_mi(parca):
    return bool(_GRUP_ADI.fullmatch(parca or "")) and not _teknik(parca)


def _grup_bul(s, ham_ad):
    """Sürüm grubu adın içindeki yerinden bulunur; grup adı listesi yoktur:
    - başta köşeli parantez (anime): "[SubsPlease] Ad - 05"
    - sonda tireyle: "...WEB-DL.x264-NTb"
    - teknik bilgiden hemen sonra gelen son sözcük, tiresiz: "...1080p.BluRay.x264.YIFY", "...HDTV.x264.KILLERS"
    - sondaki köşeli ya da normal parantez: "[YTS.MX]" -> YTS, "(1080p BluRay x265 10bit AAC 5.1 Tigole)" -> Tigole
    Son ikisi adda teknik bilgi (kaynak, çözünürlük, kodek, ses) varsa denenir: yoksa sondaki sözcük adın parçasıdır."""
    m = _ANIME_GRUP.match(s.ad)
    if m:
        return m.group(1).strip()
    m = _GRUP.search(re.sub(r"\s*[\[(][^\])]*[\])]\s*$", "", s.ad))
    if m and m.group(1).lower() not in _GRUP_DEGIL and not _ara(r"\d{3,4}p", m.group(1)) \
            and not _BOLUM_NUMARASI.fullmatch(m.group(1)):
        return m.group(1)
    if not (s.kaynak or s.cozunurluk or s.kodek or s.ses):
        return None
    parcalar = [p for p in re.split(r"[\s._]+", s.ad) if p]
    if len(parcalar) >= 3 and _grup_adi_mi(parcalar[-1]) and _teknik(parcalar[-2]):
        return parcalar[-1]
    # Sondaki etiket: site etiketleri gövdeden atıldığı için ham addan (uzantı ve dil eki atılmış) bakılır
    ham = unquote(str(ham_ad or "")).replace("\\", "/").rsplit("/", 1)[-1].strip()
    onceki = None
    while onceki != ham:
        onceki = ham
        ham = _DIL_EKI.sub("", _UZANTI.sub("", ham)).strip()
    m = re.search(r"[\[(]([^\[\]()]+)[\])]$", ham)
    if not m:
        return None
    ic = m.group(1).strip()
    alan = re.fullmatch(r"([A-Za-z0-9]+)\.[A-Za-z]{2,3}", ic)              # "YTS.MX", "EZTVx.to"
    aday = alan.group(1) if alan else ic
    if aday.lower() in _SITE_ADLARI:
        return None                         # "[rarbg]", "[eztv]": yeniden paylaşanın etiketi, grup değil
    if alan:
        return aday if _grup_adi_mi(aday) else None
    ic_parcalar = [p for p in re.split(r"[\s._]+", ic) if p]
    if ic_parcalar and _grup_adi_mi(ic_parcalar[-1]) and (len(ic_parcalar) == 1 or _teknik(ic_parcalar[-2])):
        return ic_parcalar[-1]
    return None


class Surum:
    __slots__ = ("ad", "grup", "kaynak", "hizmet", "cozunurluk", "kodek", "ses", "kurgu", "sezon", "bolumler",
                 "yil", "baslik", "paket")

    def __init__(self):
        for alan in self.__slots__:
            setattr(self, alan, None)
        self.bolumler = ()

    def __repr__(self):
        return "Surum(" + ", ".join(f"{a}={getattr(self, a)!r}" for a in self.__slots__ if getattr(self, a)) + ")"


def govde(ad):
    """Dosya adının gövdesi: yol, uzantı, dil eki ve site etiketi atılmış."""
    ad = unquote(str(ad or "")).replace("\\", "/").rsplit("/", 1)[-1].strip()
    onceki = None
    while onceki != ad:
        onceki = ad
        ad = _UZANTI.sub("", ad)
        ad = _SITE_ETIKETI.sub("", ad)
        ad = _DIL_EKI.sub("", ad)
    return ad.strip()


def ayristir(ad):
    s = Surum()
    s.ad = govde(ad)
    duz = re.sub(r"[._\s]+", " ", s.ad)            # tireler korunur: WEB-DL, grup ayracı
    s.kaynak = _ilk(_KAYNAKLAR, duz)
    s.baslik = baslik(s.ad)
    # Yayın hizmeti yapım adından sonra aranır: "Mad Max", "Now You See Me" adın parçasıdır
    teknik = duz[len(re.sub(r"[._\s]+", " ", s.baslik)):] if s.baslik and duz.lower().startswith(
        re.sub(r"[._\s]+", " ", s.baslik).lower()) else duz
    for parca in re.split(r"[\s\-\[\]()]+", teknik):
        hizmet = _HIZMETLER.get(parca.lower())
        if hizmet and parca.lower() not in ("it", "blu"):
            if s.kaynak == "web" or parca.isupper():  # "Max", "Now", "Stan" sıradan sözcük olabilir
                s.hizmet = hizmet
                break
    s.cozunurluk = _ilk(_COZUNURLUK, duz)
    s.kodek = _ilk(_KODEK, duz)
    s.ses = _ilk(_SES, duz)
    s.kurgu = _ilk(_KURGULAR, duz)
    s.grup = _grup_bul(s, ad)
    m = _SE.search(s.ad) or _SEZON_BOLUM_YAZI.search(s.ad)
    if m:
        s.sezon = int(m.group(1))
        ilk = int(m.group(2))
        son = int(m.group(3)) if len(m.groups()) > 2 and m.group(3) else ilk
        s.bolumler = tuple(range(ilk, son + 1)) if 0 <= son - ilk < 50 else (ilk,)
    else:
        m = _X.search(s.ad)
        if m and not _YIL.fullmatch(m.group(0)):
            s.sezon, s.bolumler = int(m.group(1)), (int(m.group(2)),)
        else:
            m = _SEZON.search(s.ad)
            if m:
                s.sezon, s.paket = int(m.group(1) or m.group(2)), True
            m2 = _ANIME_BOLUM.search(duz) or _YALNIZ_BOLUM.search(s.ad)
            if not m2 and (_ANIME_GRUP.match(s.ad) or _CRC.search(s.ad)):
                m2 = _ANIME_SAYI.search(_BASTAKI_ETIKET.sub("", s.ad))
            if m2:
                s.bolumler = (int(m2.group(1)),)
                s.paket = None
    yillar = _YIL.findall(duz)
    if yillar:
        s.yil = int(yillar[-1])                      # sürüm yılı addaki yıldan sonra gelir ("Blade Runner 2049 2017")
    return s


_BASLIK_SONU = re.compile(
    r"(?i)[\s._\-]*(?:\s-\s+\d{1,4}(?:v\d)?(?!\d)|\bS\d{1,2}[\s._-]?E\d{1,4}(?!\d)|\b\d{1,2}x\d{2,3}\b|\bS\d{1,2}\b|"
    r"\bseason[\s._-]?\d|\bEP?\d{1,4}\b|(?<![0-9])(?:19[3-9]\d|20[0-4]\d)(?![0-9])|\b\d{3,4}p\b|\b(?:WEB|WEB-?DL|WEBRip|"
    r"BluRay|BDRip|BRRip|HDTV|REMUX|MULTi|DVDRip|x264|x265|HEVC|PROPER|REPACK|EXTENDED|UNRATED|DIRECTORS?)\b|[\[【(（])")


def baslik(ad):
    """Yapım adı: baştaki [Grup] etiketi, bölüm/yıl/kalite bilgisi ve sonrası atılır ("Ornek.S01E05..." -> "Ornek").
    Adın parçası olan yıl atlanır: arkasından bir yıl daha geliyorsa ya da adın başındaysa ("2001 A Space Odyssey 1968")."""
    g = govde(ad)
    anime = bool(_ANIME_GRUP.match(g) or _CRC.search(g))
    g = _BASTAKI_ETIKET.sub("", g) or g
    yillar = [m.start() for m in _YIL.finditer(g)]
    kesim = None
    for m in _BASLIK_SONU.finditer(g):
        if m.start() == 0:
            continue
        if m.group(0).strip(" ._-").lower() == "web" and (_YIL.search(g, m.end()) or _SE.search(g, m.end())):
            continue                    # "Charlottes.Web.2006": arkasından yıl ya da bölüm gelen "Web" adın parçası
        yil = _YIL.search(m.group(0))
        if yil and m.group(0).strip(" ._-") == yil.group(0) and \
                any(y > m.start() + 1 and g[y:y + 4] != yil.group(0) for y in yillar):
            continue                    # arkasından başka bir yıl geliyor: bu yıl adın parçası (aynı yıl yinelenmiş olabilir)
        kesim = m.start()
        break
    if anime:                                         # "[Grup] Ad 03 (1080p)": tiresiz bölüm numarası da ad sonu
        m = _ANIME_SAYI.search(g)
        if m and m.start() > 0 and (kesim is None or m.start() < kesim):
            kesim = m.start()
    b = g[:kesim] if kesim else g
    return re.sub(r"[._]+", " ", b).strip(" -")


def bolum_tutar_mi(surum_veya_ad, sezon, bolum, mutlak=None):
    """Altyazının adı izlenen bölümü kesin yazıyor mu: sezon ve bölüm birlikte (sezon finali hatası buradan
    geliyordu). Yalnız bölüm numarası yazan (anime) adlar mutlak numarayla ya da sezon 1'de bölümle tutar.
    Dönüş: True (kesin), False (başka bölüm), None (adda bilgi yok)."""
    s = surum_veya_ad if isinstance(surum_veya_ad, Surum) else ayristir(surum_veya_ad)
    if s.sezon is not None and s.bolumler:
        return s.sezon == sezon and bolum in s.bolumler
    if s.sezon is not None and s.paket:
        return None if s.sezon == sezon else False             # sezon paketi: dosyası indirince seçilir
    if s.bolumler:                                # yalnız bölüm numarası (anime: "Ad - 15")
        if mutlak is not None and mutlak in s.bolumler:
            return True
        if sezon in (None, 0, 1):
            return bolum in s.bolumler
        if mutlak is not None and bolum not in s.bolumler:
            return False
        return None                               # 2. sezon ve sonrası: numara mutlak da olabilir, emin değiliz
    return None
