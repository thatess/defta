"""Jimaku (jimaku.cc/api). Anahtar Authorization başlığında gider (Bearer'sız).

Anime kayıtları AniList kimliğine bağlıdır ve bölümleri her sezonun kendi 1. bölümünden sayılır; Japon dizi ve
filmleri TMDB kimliğiyle ("tv:1234" / "movie:1234") eşleşir. Arama, ayrıca istenmezse yalnız anime döndürür.
Emin olunamazsa sonuç gösterilmez: adla aramada ad birebir tutmalı, AniList kimliği bilinmeyen dizide yalnız
1. sezon (ya da eşleme listesinin verdiği kayıt-bölüm) kullanılır.
"""
import re
import urllib.parse

from .. import ag, gizli
from . import Aday, SiteHatasi

ADRES = "https://jimaku.cc/api/"
_UZANTILAR = (".srt", ".ass", ".ssa", ".vtt", ".zip")


def _duz(metin):
    return re.sub(r"[^\w]+", "", (metin or "").casefold())


class Jimaku:
    kod, ad = "jimaku", "Jimaku"

    def __init__(self, anahtar, depo=None, eslesme=None, **_):
        self.anahtar, self.depo = anahtar, depo
        self.eslesme = eslesme                  # (anilist_kimligi, bolum) bulan işlev; yoksa yalnız kesin durumlar
        gizli.kaydet(anahtar)

    def hazir(self):
        return bool(self.anahtar)

    def _basliklar(self):
        return {"Authorization": self.anahtar}

    def _hata(self, e):
        if isinstance(e, ag.AgHatasi):
            return SiteHatasi(self.ad, "ag", str(e))
        if e.kod == 401:
            return SiteHatasi(self.ad, "anahtar", "HTTP 401")
        if e.kod == 429:
            return SiteHatasi(self.ad, "hiz")
        return SiteHatasi(self.ad, "sunucu", f"HTTP {e.kod}")

    def _iste(self, yol, sorgu=None):
        try:
            return ag.istek("GET", ADRES + yol, self._basliklar(), sorgu=sorgu)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None

    def kayit_bul(self, video):
        """(kayıt, bölüm) ya da (None, None)."""
        dizi = video.tur == "dizi" and video.bolum is not None
        # Önce eşleme listesi (TMDB/TVDB sezon-bölüm → AniList kaydı ve bölümü): kayıt sınırını (cour) da bilir.
        # Yoksa oynatan eklentinin verdiği AniList kimliği; o zaman bölüm de AniList'in numarasıdır (eşleme listesi
        # sitelerin sezon/bölümüne çevirmiş olsa bile özgün numara anilist_bolum'da durur).
        anilist, bolum = "", None
        if self.eslesme is not None and (video.tmdb or video.tvdb):
            bulunan = self.eslesme(video)
            if bulunan:
                anilist, bolum = bulunan
        if not anilist and video.anilist:
            anilist = video.anilist
            bolum = (getattr(video, "anilist_bolum", None) or video.bolum) if dizi else None
        if not anilist:
            bolum = video.bolum if dizi else None
        if anilist:
            _, veri, _ = self._iste("entries/search", {"anilist_id": anilist})
            for k in veri or []:
                if str(k.get("anilist_id")) == str(anilist):
                    return k, bolum
            return None, None
        if video.tmdb:
            tur = "tv" if dizi else "movie"
            _, veri, _ = self._iste("entries/search", {"tmdb_id": f"{tur}:{video.tmdb}", "anime": "false"})
            for k in veri or []:
                if str(k.get("tmdb_id")) == f"{tur}:{video.tmdb}":
                    return k, bolum
        if dizi and video.sezon_no != 1:
            return None, None                   # sezonun AniList kaydı bilinmiyor: emin değiliz
        if not video.ad or video.imdb or video.tmdb or video.tvdb:
            # Kimliği olan video eşleşmediyse adıyla aranmaz: aynı adlı başka yapım (canlı çekim uyarlaması) gelir
            return None, None
        hedef = _duz(video.ad)
        _, veri, _ = self._iste("entries/search", {"query": video.ad})
        for k in veri or []:
            if hedef in {_duz(k.get("name")), _duz(k.get("english_name")), _duz(k.get("japanese_name"))}:
                return k, bolum
        return None, None

    def ara(self, video, diller):
        if "ja" not in diller:
            return []
        if not self.hazir():
            raise SiteHatasi(self.ad, "anahtar", "anahtar yok")
        kayit, bolum = self.kayit_bul(video)
        if not kayit:
            return []
        if (kayit.get("flags") or {}).get("unverified"):
            return []
        sorgu = {"episode": bolum} if bolum is not None and not (kayit.get("flags") or {}).get("movie") else None
        _, veri, _ = self._iste(f"entries/{kayit.get('id')}/files", sorgu)
        sonuc = []
        for d in veri or []:
            ad = d.get("name") or ""
            if not ad.lower().endswith(_UZANTILAR) or (d.get("size") or 1000) < 500:
                continue
            paket = ad.lower().endswith(".zip")
            # Kayıtlara tam adres yazılmaz: kimlik "kayıt/dosya adı", indirme için adresin yalnız yolu saklanır
            yol = urllib.parse.urlsplit(d.get("url") or "").path
            sonuc.append(Aday(self.kod, f"{kayit.get('id')}/{ad}", "ja", [ad], paket=paket,
                              bolum_kesin=sorgu is not None and not paket,      # Jimaku dosyayı bölümüne göre süzdü
                              indirme={"yol": yol, "ad": ad, "bolum": bolum}))
        return sonuc

    def indir(self, aday):
        yol = aday.indirme.get("yol") or ""
        # "adres": eski sürümün kaydı (tam adres); yenisi yalnız yolu saklar
        adres = aday.indirme.get("adres") or ("https://jimaku.cc" + yol if yol.startswith("/") and not yol.startswith("//") else "")
        if not adres.startswith("https://jimaku.cc/"):
            raise SiteHatasi(self.ad, "yok", "beklenmeyen adres")
        try:
            _, icerik, _ = ag.istek("GET", adres, self._basliklar(), ham=True, zaman_asimi=30, deneme=1)
        except (ag.YanitHatasi, ag.AgHatasi) as e:
            raise self._hata(e) from None
        return icerik, aday.indirme.get("ad") or "jimaku.srt"

    def test(self):
        if not self.hazir():
            return {"calisiyor": False, "neden": "anahtar yok"}
        try:
            self._iste("entries/search", {"query": "frieren"})
        except SiteHatasi as e:
            return {"calisiyor": False, "neden": e.mesaj or e.tur}
        return {"calisiyor": True, "kalan": None}
