"""SubSource API v1 (api.subsource.net/api/v1). Anahtar X-API-Key başlığında gider.

Önce yapım bulunur: IMDb kimliğiyle, yoksa adla (ad ve yıl sıkı tutmalı); dizide sezonu ayrı bir kayıttır. Sonra her
dil için o kaydın altyazıları alınır. Sürüm adı listesinden sezon ve bölüm okunur; bölümü yazmayan altyazı sezon
paketidir, dosyası indirince seçilir. Birden çok dosyalı altyazının ("files") sürüm adı çoğu kez yalnız ilk dosyayı
yazar ("..._01_..." adlı 25 bölümlük Türkçe paket): adı başka bölümü yazsa da paket sayılır. Video karmasıyla arama
yoktur.

SubSource bölüme göre süzmüyor (seasonNumber ve episodeNumber yok sayılıyor; kayıt zaten sezonun kaydı): dizide bütün
sezon gelir, sayfa başına en çok 100. İzlenen bölümün altyazısı sonraki sayfalarda kalmasın diye dizide her dilin öteki
sayfaları da alınır; filmde yalnız hedef dilinkiler. Arama süresi dolmak üzereyse durulur.
"""
import re
import time

from .. import ag, gizli, surum
from ..video import ad_eksiz
from . import DIL_ADLARI, EK_ARAMA_SINIRI, Aday, SiteHatasi, dil_kodu, tam_altyazi_degil

ADRES = "https://api.subsource.net/api/v1/"
SAYFA = 100                             # sayfa başına en çok altyazı
SAYFA_SINIRI = 5                        # dil başına en çok sayfa


def _ss_dilleri(kod):
    """SubSource'un dil adları. Çincede "chinese" yok: basitleştirilmiş (GB) ve geleneksel (Big5) ayrı adlarla
    (canlı: Interstellar'da "chinese" 0, "chinese_bg_code" 2, "big_5_code" 1)."""
    return {"zh": ["chinese_bg_code", "big_5_code"]}.get(kod) or [DIL_ADLARI.get(kod, kod)]


def _duz(metin):
    return re.sub(r"[^\w]+", "", (metin or "").casefold())


class SubSource:
    kod, ad = "subsource", "SubSource"

    def __init__(self, anahtar, depo=None, **_):
        self.anahtar, self.depo = anahtar, depo
        gizli.kaydet(anahtar)

    def hazir(self):
        return bool(self.anahtar)

    def _basliklar(self):
        return {"X-API-Key": self.anahtar}

    def _hata(self, e):
        if isinstance(e, ag.AgHatasi):
            return SiteHatasi(self.ad, "ag", str(e))
        if e.kod in (401, 403):
            return SiteHatasi(self.ad, "anahtar", f"HTTP {e.kod}")
        if e.kod == 429:
            return SiteHatasi(self.ad, "hiz")
        return SiteHatasi(self.ad, "sunucu", f"HTTP {e.kod}")

    def _iste(self, yol, sorgu=None, ham=False):
        try:
            # İndirme (ham) kendiliğinden yeniden denenmez: yanıtı gelmeyen ilk istek de haktan düşmüş olabilir
            return ag.istek("GET", ADRES + yol, self._basliklar(), sorgu=sorgu, ham=ham, zaman_asimi=30 if ham else 15,
                            deneme=1 if ham else 2)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None

    def _yapim(self, video):
        """SubSource'un yapım kimliği; bulunan kimlik bir hafta saklanır (her aramada 1-2 istek az)."""
        dizi = video.tur == "dizi" and video.bolum is not None
        # Kimliksiz filmde yıl da anahtarın parçası: aynı adlı başka yılın filmi (yeniden çekim) bu kaydı almasın
        ek = video.sezon_no if dizi else ("" if video.imdb else (video.yil or ""))
        anahtar = f"{video.imdb or (video.ad or '').lower()}:{ek}"
        saklanan = ((self.depo.oku("ss_yapimlar", {}) if self.depo else {}) or {}).get(anahtar) or {}
        if saklanan.get("id") and time.time() - saklanan.get("zaman", 0) < 7 * 86400:
            return saklanan["id"]
        kimlik = self._yapim_bul(video, dizi)
        if kimlik and self.depo:
            hepsi = self.depo.oku("ss_yapimlar", {}) or {}
            hepsi = {k: v for k, v in hepsi.items() if time.time() - (v or {}).get("zaman", 0) < 7 * 86400}
            hepsi[anahtar] = {"id": kimlik, "zaman": time.time()}
            self.depo.yaz("ss_yapimlar", hepsi)
        return kimlik

    def _yapim_bul(self, video, dizi):
        sorgular = []                       # (sorgu, adla aramada aranan ad)
        if video.imdb:
            sorgular.append(({"searchType": "imdb", "imdb": video.imdb}, ""))
        if video.ad:
            sorgular.append(({"searchType": "text", "q": video.ad.lower()}, video.ad))
            eksiz = ad_eksiz(video.ad)      # "The Office (US)" bulunamazsa "The Office" (madde 9)
            if eksiz != video.ad:
                sorgular.append(({"searchType": "text", "q": eksiz.lower()}, eksiz))
        for sorgu, aranan in sorgular:
            if dizi:
                sorgu["season"] = video.sezon_no
            _, veri, _ = self._iste("movies/search", sorgu)
            kayitlar = (veri or {}).get("data") or []
            if sorgu["searchType"] == "imdb":
                for k in kayitlar:
                    if not dizi or _sayi(k.get("season")) in (None, video.sezon_no):
                        return k.get("movieId")
                continue
            hedef = _duz(aranan)
            for k in kayitlar:
                adlar = {_duz(k.get("title")), _duz(k.get("alternateTitle"))}
                if hedef not in adlar:
                    continue
                if not dizi and video.yil and _sayi(k.get("releaseYear")) not in (None, video.yil):
                    continue
                if dizi and _sayi(k.get("season")) not in (None, video.sezon_no):
                    continue
                return k.get("movieId")
        return None

    def ara(self, video, diller):
        if not self.hazir():
            raise SiteHatasi(self.ad, "anahtar", "anahtar yok")
        kimlik = self._yapim(video)
        if not kimlik:
            return []
        dizi = video.tur == "dizi" and video.bolum is not None
        sonuc, bas = [], time.time()
        for sira, dil in enumerate(diller):
            ogeler = []
            for ad in _ss_dilleri(dil):
                sorgu = {"movieId": kimlik, "language": ad, "limit": SAYFA}
                if dizi:
                    sorgu.update({"seasonNumber": video.sezon_no, "episodeNumber": video.bolum})
                ogeler += self._dil_altyazilari(sorgu, dizi or sira == 0, bas)
            for oge in ogeler:
                if oge.get("foreignParts"):
                    continue
                adlar = oge.get("releaseInfo") or []
                if isinstance(adlar, str):
                    adlar = [adlar]
                if tam_altyazi_degil(adlar, video):
                    continue
                paket = False
                if dizi:
                    tutanlar = [surum.bolum_tutar_mi(a, video.sezon, video.bolum) for a in adlar]
                    if True not in tutanlar:
                        if False in tutanlar and None not in tutanlar and _dosya_sayisi(oge) <= 1:
                            continue                                # tek dosya ve adı başka bölümü yazıyor
                        paket = True
                    adlar = [a for a, t in zip(adlar, tutanlar) if t is not False] or adlar
                dil_k = dil_kodu(oge.get("language")) or dil
                sonuc.append(Aday(self.kod, oge.get("subtitleId"), dil_k, adlar,
                                  sdh=bool(oge.get("hearingImpaired")), paket=paket,
                                  sezon=video.sezon if dizi else None, bolumler=(video.bolum,) if dizi and not paket else (),
                                  bolum_kesin=dizi and not paket,
                                  yukleyen=_yukleyen(oge), indirme={"kimlik": oge.get("subtitleId")}))
        return sonuc

    def _dil_altyazilari(self, sorgu, butun_sayfalar, bas):
        """Bir dilin altyazıları: ilk sayfa, gerekirse öteki sayfalar da (en çok SAYFA_SINIRI). Sonraki bir sayfa
        alınamazsa eldekiler döner."""
        _, veri, _ = self._iste("subtitles", sorgu)
        ogeler, gorulen = [], set()

        def ekle(v):
            for oge in _veri_listesi(v):
                if oge.get("subtitleId") not in gorulen:
                    gorulen.add(oge.get("subtitleId"))
                    ogeler.append(oge)
        ekle(veri)
        sayfalama = veri.get("pagination") if isinstance(veri, dict) else None
        sayfalar = _sayi(sayfalama.get("pages")) if isinstance(sayfalama, dict) else None
        if butun_sayfalar and sayfalar:
            for sayfa in range(2, min(sayfalar, SAYFA_SINIRI) + 1):
                if time.time() - bas > EK_ARAMA_SINIRI:
                    break
                try:
                    _, v, _ = self._iste("subtitles", dict(sorgu, page=sayfa))
                except SiteHatasi:
                    self.eksik_kaldi = True         # öteki sayfalar alınamadı: arama önbelleğe girmez
                    break
                ekle(v)
        return ogeler

    def indir(self, aday):
        _, icerik, _ = self._iste(f"subtitles/{aday.indirme['kimlik']}/download", ham=True)
        return icerik, f"subsource_{aday.indirme['kimlik']}.zip"

    def test(self):
        if not self.hazir():
            return {"calisiyor": False, "neden": "anahtar yok"}
        try:
            self._iste("movies/search", {"searchType": "text", "q": "test"})
        except SiteHatasi as e:
            return {"calisiyor": False, "neden": e.mesaj or e.tur}
        # SubSource günlük indirme hakkı bildirmez; "x-ratelimit-remaining" hız sınırıdır, hak diye gösterilmez
        return {"calisiyor": True, "kalan": None}


def _sayi(deger):
    if isinstance(deger, bool):
        return None
    try:
        return int(deger)
    except (TypeError, ValueError, OverflowError):
        return None


def _veri_listesi(veri):
    """Yanıttaki altyazılar (sözlük öğeleri); biçim bozuksa boş."""
    d = veri.get("data") if isinstance(veri, dict) else None
    return [o for o in d if isinstance(o, dict)] if isinstance(d, list) else []


def _dosya_sayisi(oge):
    """Altyazı arşivindeki dosya sayısı ("files": sayı ya da ad listesi); bilinmiyorsa 0."""
    dosyalar = oge.get("files")
    return len(dosyalar) if isinstance(dosyalar, list) else (_sayi(dosyalar) or 0)


def _yukleyen(oge):
    for k in oge.get("contributors") if isinstance(oge.get("contributors"), list) else []:
        if isinstance(k, dict) and k.get("id") == oge.get("uploaderId"):
            return k.get("displayname") or ""
    return ""
