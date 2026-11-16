"""Senkron olasılığı puanı (Bazarr'ın kullandığı subliminal mantığı).

Altyazının zamanlaması belli bir video sürümüne göre yapılır. Zamanlamayı en çok etkileyen bilgi en çok puanı alır:
sürüm grubu 15, kaynak 7 (WEB-DL, BluRay, HDTV; iki WEB'in yayın hizmeti farklıysa kaynak tutmaz), ses kodeki 3,
çözünürlük 2, görüntü kodeki 2. Dosya karması tutarsa altyazı o dosyanındır: puanı her şeyin üstündedir (karması
tutanlar kendi aralarında ad puanıyla sıralanır).
Dizi, sezon ve bölüm puanlanmaz; listeye girmeden önce sıkı denetlenirler.

Kurgu (Extended, Director's Cut, Theatrical, Unrated) ayrıca karşılaştırılır: adında kurgu yazmayan sürüm sinema
kurgusu sayılır. Kurgu tutmazsa puan ne olursa olsun "Senkron uyumlu" verilmez; sıralamada en alta iner.
"""
from .surum import Surum, ayristir

AGIRLIK = (("grup", 15), ("kaynak", 7), ("ses", 3), ("cozunurluk", 2), ("kodek", 2))
TAM = sum(a for _, a in AGIRLIK)            # 29
KARMA = TAM + 1                             # dosya karması tutan altyazı
ESIK = 22                                   # "Senkron uyumlu" için grup ve kaynak birlikte tutmalı (ilk denemede ayarlanır)


def _grup(ad):
    """YIFY, YTS'nin eski adıdır: aynı kodlayıcı ailesi sayılır (kaynak ayrıca tutmalı)."""
    k = ad.lower()
    return "yts" if k == "yify" else k


def _kurgu(s):
    return s.kurgu or "theatrical"


def _alan_tutar(alan, v, a):
    if alan == "grup":
        return bool(v.grup and a.grup) and _grup(v.grup) == _grup(a.grup)
    if alan == "kaynak":
        if not (v.kaynak and a.kaynak) or v.kaynak != a.kaynak:
            return False
        return not (v.hizmet and a.hizmet and v.hizmet != a.hizmet)
    return bool(getattr(v, alan) and getattr(a, alan)) and getattr(v, alan) == getattr(a, alan)


def tek_puan(video, altyazi):
    """İki sürüm adının puanı (0..TAM)."""
    v = video if isinstance(video, Surum) else ayristir(video)
    a = altyazi if isinstance(altyazi, Surum) else ayristir(altyazi)
    puan = sum(agirlik for alan, agirlik in AGIRLIK if _alan_tutar(alan, v, a))
    # Grup, ses, çözünürlük ve kodek tutup kaynak tutmuyorsa (aynı grubun WEB-DL ve BluRay sürümleri) toplam eşiğe
    # varır ama zamanlama kaynağa bağlıdır: "Senkron uyumlu" için kaynak da tutmalı.
    return min(puan, ESIK - 1) if puan >= ESIK and not _alan_tutar("kaynak", v, a) else puan


def puanla(video_adi, altyazi_adlari, karma_tuttu=False):
    """(puan, kurgu_tutmuyor). Altyazının birden çok sürüm adı olabilir (SubDL, SubSource): kurgusu tutanların en
    iyisi alınır; hiçbirinin kurgusu tutmuyorsa kurgu tutmaz. Video adı bilinmiyorsa puan 0'dır (yalnız karma)."""
    adlar = [a for a in (altyazi_adlari or []) if a]
    v = ayristir(video_adi) if video_adi else None
    kurgu_tutmuyor = False
    if v is not None and adlar:
        surumler = [ayristir(a) for a in adlar]
        tutanlar = [s for s in surumler if _kurgu(s) == _kurgu(v)]
        kurgu_tutmuyor = not tutanlar
        aday = tutanlar or surumler
        puan = max(tek_puan(v, s) for s in aday)
    else:
        puan = 0
    if karma_tuttu:
        # Karma dosyanın kendisidir (kurgusu da aynıdır). Karması tutanlar kendi aralarında ad puanıyla sıralanır:
        # karma kaydı yanlış olabildiği için adı da tutan öne geçer.
        return KARMA + puan, False
    return puan, kurgu_tutmuyor


def uyumlu_mu(puan, kurgu_tutmuyor):
    return not kurgu_tutmuyor and puan >= ESIK
