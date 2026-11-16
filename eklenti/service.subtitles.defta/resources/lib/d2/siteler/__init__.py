"""Altyazı siteleri: ortak aday yapısı, hatalar ve dil kodları."""
import re
import unicodedata
from urllib.parse import unquote

from .. import surum

AZ_SONUC = 5            # dolu sonuç sayfasında hedef dilden bundan az varsa hedef dil ayrıca aranır
EK_ARAMA_SINIRI = 8.0   # site aramasının başından bu kadar saniye geçtiyse ek arama yapılmaz (20 sn sınırı aşılmasın)
EK_ZAMAN_ASIMI = 6      # ek aramanın zaman aşımı (sn), tek deneme

# Eklentinin dil kodu -> İngilizce adı (sitelerin dil adlarını tanımak için)
DIL_ADLARI = {"tr": "turkish", "en": "english", "ja": "japanese", "de": "german", "fr": "french", "es": "spanish",
              "it": "italian", "pt": "portuguese", "pt-BR": "brazilian_portuguese", "ru": "russian", "ar": "arabic",
              "ko": "korean", "zh": "chinese", "nl": "dutch", "pl": "polish", "el": "greek", "fa": "farsi_persian",
              "az": "azerbaijani", "uk": "ukrainian", "sv": "swedish"}
_ADDAN_KODA = {v: k for k, v in DIL_ADLARI.items()}
_ADDAN_KODA.update({"persian": "fa", "farsi": "fa", "brazillian portuguese": "pt-BR", "portuguese (brazil)": "pt-BR",
                    "chinese_bg_code": "zh", "big 5 code": "zh", "chinese bg code": "zh"})


def dil_kodu(deger):
    """Sitenin verdiği dili ("EN", "english", "pt-br", "Brazilian_portuguese") eklentinin koduna çevirir."""
    d = str(deger or "").strip()
    if not d:
        return ""
    k = d.lower().replace("-", "_")
    if k in ("pt_br", "br_pt", "pob", "pb"):
        return "pt-BR"
    if k in _ADDAN_KODA:
        return _ADDAN_KODA[k]
    k2 = k.replace("_", " ")
    if k2 in _ADDAN_KODA:
        return _ADDAN_KODA[k2]
    if re.fullmatch(r"[a-z]{2}", k):
        return k
    m = re.fullmatch(r"([a-z]{2})_[a-z]{2,4}", k)          # pt-PT, zh-CN, zh-TW, ZH_BG, en-US
    if m:
        return m.group(1)
    return ""


def sade_ad(ad):
    """Karşılaştırma için ad: küçük harf, aksansız, tek boşlukla ayrılmış sözcükler ("Amélie" -> "amelie",
    "Law & Order: SVU" -> "law and order svu", "YILDIZLARARASI" ve "Yıldızlararası" -> "yildizlararasi")."""
    ad = str(ad or "").casefold().replace("ı", "i").replace("&", " and ")
    ad = "".join(c for c in unicodedata.normalize("NFKD", ad) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", ad))


_YABANCI_KONUSMA = r"(?<!non[ ._-])forced|foreign[ ._-]?parts?(?:[ ._-]?only)?"          # "Non-Forced" tam altyazıdır
_EKSIK_ALTYAZI = re.compile(r"(?i)(?<![a-z0-9])(" + _YABANCI_KONUSMA + r"|trailer|teaser|bonus|extras?|featurettes?|"
                            r"making[ ._-]?of|behind[ ._-]?the[ ._-]?scenes)(?![a-z0-9])")
_EKSIK_DIZI = re.compile(r"(?i)(?<![a-z0-9])(" + _YABANCI_KONUSMA + r")(?![a-z0-9])")


def tam_altyazi_degil(adlar, video=""):
    """Sürüm adı yalnız yabancı dildeki konuşmaların altyazısını ("FORCED"), fragmanınkini ("Teaser", "Trailer") ya da
    ek diskin, kamera arkasının altyazısını ("Bonus", "Extras", "Featurette", "Making of") anlatıyorsa filmin tam
    altyazısı değildir: listede gösterilmez, çeviriye kaynak olmaz. Videonun kendi adında bu sözcük geçiyorsa
    ("Trailer Park Boys") bakılmaz. Dizide yalnız FORCED'a bakılır: bölüm adı "Teaser" ya da "Bonus" olabilir.
    video: Video nesnesi (ya da yalnız adı)."""
    if isinstance(video, str):
        ad, desen = video, _EKSIK_ALTYAZI
    else:
        ad, desen = video_adi(video), (_EKSIK_DIZI if getattr(video, "tur", "") == "dizi" else _EKSIK_ALTYAZI)
    if desen.search(ad or ""):
        return False
    return any(desen.search(str(a or "")) for a in adlar)


_SDH = re.compile(r"(?i)(?<![a-z0-9])sdh(?![a-z0-9])")
_SDH_EKI = re.compile(r"(?i)(?<![a-z0-9])(?:hi|cc)(?![a-z0-9])")


def adda_sdh(adlar):
    """Sürüm ya da dosya adı işitme engelli (SDH) altyazıyı mı anlatıyor; site işaretlemese de: "SDH" her yerde, "HI"
    ve "CC" yalnız sondaki eklerde (".en.hi.srt"), çünkü adın içinde yapım adının parçası olabilir ("Hi Bye, Mama")."""
    for ad in adlar:
        ad = str(ad or "")
        if _SDH.search(ad):
            return True
        son = unquote(ad).replace("\\", "/").rsplit("/", 1)[-1].strip()
        govde = surum.govde(son)
        if govde and son.startswith(govde) and _SDH_EKI.search(son[len(govde):]):
            return True
    return False


def video_adi(video):
    """tam_altyazi_degil için videonun adı ve dosya adı."""
    return f"{getattr(video, 'ad', '') or ''} {getattr(video, 'dosya_adi', '') or ''}"


def hedef_dil_eksik(dolu, sayfadaki_diller, diller):
    """Dolu sonuç sayfasında hedef dilden (diller[0]) AZ_SONUC'tan az altyazı varsa hedef dil (ayrıca aranacak),
    yoksa None. sayfadaki_diller: sayfadaki her altyazının sitenin yazdığı dili (süzmeden önce)."""
    if not dolu or not diller or len(set(diller)) < 2:
        return None
    hedef = diller[0]
    return hedef if sum(1 for d in sayfadaki_diller if dil_kodu(d) == hedef) < AZ_SONUC else None


class SiteHatasi(Exception):
    """tur: anahtar (yanlış ya da yok), kota (günlük indirme hakkı doldu), hiz (hız sınırı), ag (bağlantı),
    sunucu (sitenin hatası), yok (indirilecek dosya bulunamadı)."""

    def __init__(self, site, tur, mesaj="", yenilenme=None):
        super().__init__(f"{site}: {tur}" + (f" ({mesaj})" if mesaj else ""))
        self.site, self.tur, self.mesaj, self.yenilenme = site, tur, mesaj, yenilenme


class Aday:
    """Bir sitedeki bir altyazı. indirme: indirmek için gereken sitenin kendi bilgisi (JSON'a yazılabilir; anahtar
    ya da imzalı adres içermez)."""
    __slots__ = ("site", "kimlik", "dil", "adlar", "sdh", "karma_tuttu", "fps", "sezon", "bolumler", "paket",
                 "bolum_kesin", "indirme", "yukleyen", "indirme_sayisi", "puan", "kurgu_tutmuyor", "yedekler")

    def __init__(self, site, kimlik, dil, adlar, **alanlar):
        self.site, self.kimlik, self.dil = site, str(kimlik), dil
        self.adlar = [str(a) for a in (adlar or []) if a] or [""]
        self.sdh = bool(alanlar.get("sdh", False)) or adda_sdh(self.adlar)
        self.karma_tuttu = alanlar.get("karma_tuttu", False)
        self.fps = alanlar.get("fps")
        self.sezon = alanlar.get("sezon")
        self.bolumler = tuple(alanlar.get("bolumler") or ())
        self.paket = alanlar.get("paket", False)
        self.bolum_kesin = alanlar.get("bolum_kesin", False)   # bölümü site ya da dosya adı açıkça yazıyor
        self.indirme = alanlar.get("indirme") or {}
        self.yukleyen = alanlar.get("yukleyen", "")
        self.indirme_sayisi = alanlar.get("indirme_sayisi", 0)
        self.puan = 0
        self.kurgu_tutmuyor = False
        self.yedekler = []                  # aynı altyazının başka sitelerdeki kopyaları

    @property
    def anahtar(self):
        return f"{self.site}:{self.kimlik}"

    @property
    def ad(self):
        return self.adlar[0]

    def kayit(self):
        d = {a: getattr(self, a) for a in self.__slots__ if a != "yedekler"}
        d["bolumler"] = list(self.bolumler)
        d["yedekler"] = [y.kayit() for y in self.yedekler]
        d["anahtar"] = self.anahtar             # liste, çevrilmiş kaynağı bununla tanır (özellik olduğu için ayrıca)
        return d

    @classmethod
    def kayittan(cls, d):
        a = cls(d["site"], d["kimlik"], d["dil"], d.get("adlar"))
        for alan in cls.__slots__:
            if alan in d and alan not in ("site", "kimlik", "dil", "adlar", "yedekler"):
                setattr(a, alan, tuple(d[alan]) if alan == "bolumler" else d[alan])
        a.yedekler = [cls.kayittan(y) for y in d.get("yedekler") or []]
        return a
