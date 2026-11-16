"""Sitelerde arama ve indirme: eklentinin altyazı penceresi de servisin otomatik yüklemesi de bunu kullanır.

Siteler aynı anda, her biri kendi en sağlam yoluyla aranır. Bir sitenin günlük indirme hakkı dolunca bildirim gelir
(ne zaman yenileneceğiyle); hakkı dolan siteden yalnız başka siteden indirilemeyen satırlar gizlenir.
"""
import calendar
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import indir as indirme
from . import kayit, metin, puan, surum
from .siteler import Aday, SiteHatasi, yapim
from .siteler.jimaku import Jimaku
from .siteler.opensubtitles import OpenSubtitles
from .siteler.subdl import SubDL
from .siteler.subsource import SubSource

ARAMA_SURESI = 20.0


class SiteListesi(list):
    """Aranacak siteler; açık olduğu hâlde anahtarı girilmemiş sitelerin adları .eksik'te durur."""
    eksik = ()


def siteler(ayar_oku, durum, eslesme=None):
    """Açık ve anahtarı olan siteler (anahtarsız site aranmaz, her aramada hata vermesin). ayar_oku(ad) -> yazı."""
    depo = durum.depo
    s = []
    if ayar_oku("os_kullan") == "true":
        s.append(OpenSubtitles(ayar_oku("os_anahtar"), ayar_oku("os_kullanici"), ayar_oku("os_parola"), depo))
    if ayar_oku("subdl_kullan") == "true":
        s.append(SubDL(ayar_oku("subdl_anahtar"), depo))
    if ayar_oku("subsource_kullan") == "true":
        s.append(SubSource(ayar_oku("subsource_anahtar"), depo))
    if ayar_oku("jimaku_kullan") == "true":
        s.append(Jimaku(ayar_oku("jimaku_anahtar"), depo, eslesme=eslesme))
    sonuc = SiteListesi(x for x in s if x.hazir())
    sonuc.eksik = [x.ad for x in s if not x.hazir()]
    return sonuc


def _yenilenme(hata):
    """Sitenin bildirdiği yenilenme zamanı: (yerel saat yazısı, bitiş zaman damgası). Bilinmiyorsa ya da bir günden
    uzaksa gece yarısı UTC."""
    bitis = None
    if hata.yenilenme:
        deger = str(hata.yenilenme).replace("Z", "").split(".")[0]
        for bicim in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                bitis = calendar.timegm(time.strptime(deger, bicim))
                break
            except ValueError:
                continue
    simdi = time.time()
    if bitis is None or bitis > simdi + 86400 + 3600:   # günlük hak en geç bir gün sonra yenilenir: bozuk bildirilen
        bitis = simdi - simdi % 86400 + 86400           # uzak bir tarih siteyi o güne kadar kapatmasın
    return time.strftime("%H:%M", time.localtime(bitis)), bitis


def _hata_yazisi(hata):
    return f"{hata.site}: {hata.mesaj or hata.tur}"


def hata_isle(hata, durum, bildir, kayit_yaz):
    """Tek site hatası (indirme): kota dolduysa kayda geçer ve bildirilir; anahtar hatası bildirilir."""
    hatalari_isle([hata], durum, bildir, kayit_yaz)


def hatalari_isle(hatalar, durum, bildir, kayit_yaz, eksik=(), bir_kez=None):
    """Aramanın site hataları. Kota dolunca kayda geçer ve bir kez bildirilir. Anahtarı eksik ya da geçersiz siteler
    tek bildirimde toplanır; bir_kez (küme) verilirse aynı site o oturumda bir daha bildirilmez (arka plan)."""
    gecersiz = []
    for hata in hatalar:
        kayit_yaz(_hata_yazisi(hata))
        if hata.tur == "kota":
            saat, bitis = _yenilenme(hata)
            onceki = durum.hak_doldu(_kod(hata.site))
            durum.hak_doldu_yaz(_kod(hata.site), saat, bitis)
            if onceki is None:
                bildir(metin.al(metin.B_HAK_DOLDU, site=hata.site), metin.al(metin.B_YENILENIR, saat=saat), False)
        elif hata.tur == "anahtar" and hata.site not in gecersiz:
            gecersiz.append(hata.site)
    for baslik, adlar in ((metin.B_ANAHTAR_GECERSIZ, gecersiz), (metin.B_ANAHTAR_YOK, list(eksik))):
        if bir_kez is not None:
            adlar = [a for a in adlar if a not in bir_kez]
            bir_kez.update(adlar)
        if adlar:
            bildir(metin.al(baslik), metin.al(metin.B_ANAHTAR_SITELER, siteler=", ".join(adlar)), True)


def _kod(site_adi):
    return {"OpenSubtitles": "os", "SubDL": "subdl", "SubSource": "subsource", "Jimaku": "jimaku"}.get(site_adi, site_adi)


def ara(site_listesi, video, diller, kayit_yaz):
    """Bütün sitelerde aynı anda. Dönüş: (adaylar, hatalar). En çok ARAMA_SURESI beklenir."""
    adaylar, hatalar = [], []
    kilit = threading.Lock()

    def tek(site):
        bas = time.time()
        try:
            sonuc, yanlis = yapim.baska_filmin_adlari(site.ara(video, diller), video)
            with kilit:
                adaylar.extend(sonuc)
            elenen, adlar = getattr(site, "elenen", 0), getattr(site, "elenen_adlari", [])
            ek = f" ({elenen} tanesi başka yapımın, atıldı" + (": " + ", ".join(adlar) if adlar else "") + ")" \
                if elenen else ""
            if yanlis:                              # site yapımı doğru yazmış ama altyazının adı başka filmi anlatıyor
                ek += f" ({len(yanlis)} tanesinin adı başka filmin, atıldı: " + ", ".join(list(dict.fromkeys(yanlis))[:5]) + ")"
            if getattr(site, "yapim", ""):
                ek += f" (yapım: {site.yapim})"
            kayit_yaz(f"{site.ad}: {len(sonuc)} sonuç{ek}, {time.time() - bas:.1f} sn")
        except SiteHatasi as e:
            with kilit:
                hatalar.append(e)
        except Exception as e:                                  # bir sitenin beklenmeyen hatası ötekileri durdurmasın
            kayit_yaz(f"{site.ad}: beklenmeyen hata {type(e).__name__}: {e}")
            with kilit:
                hatalar.append(SiteHatasi(site.ad, "sunucu", f"{type(e).__name__}"))
    havuz = ThreadPoolExecutor(max_workers=max(1, len(site_listesi)))
    gelecekler = [(havuz.submit(tek, s), s) for s in site_listesi]
    wait([g for g, _ in gelecekler], timeout=ARAMA_SURESI)
    havuz.shutdown(wait=False)
    with kilit:
        for g, s in gelecekler:
            if not g.done():                    # zamanında yanıt vermedi: hata sayılır (sonuç önbelleğe girmez)
                kayit_yaz(f"{s.ad}: {ARAMA_SURESI:.0f} sn içinde yanıt vermedi")
                hatalar.append(SiteHatasi(s.ad, "ag", "zaman aşımı"))
        return list(adaylar), list(hatalar)


def site_bul(site_listesi, kod):
    return next((s for s in site_listesi if s.kod == kod), None)


def indir_metin(aday, video, site_listesi, durum, bildir, kayit_yaz):
    """Adayı (olmazsa yedeklerini) indirir. Dönüş: (metin, uzantı, indirilen aday). Kare hızı farkı burada düzeltilir.
    OpenSubtitles indirme isteği kendiliğinden tekrarlanmaz; olmazsa aynı altyazı başka siteden indirilir."""
    son_hata = None
    for a in [aday] + list(aday.yedekler):
        if durum.hak_doldu(a.site) is not None:
            continue
        site = site_bul(site_listesi, a.site)
        if site is None:
            continue
        try:
            veri, ad = site.indir(a)
            metin_, uzanti = indirme.altyazi_cikar(veri, ad, video, bolum=a.indirme.get("bolum"), dil=a.dil)
        except SiteHatasi as e:
            hata_isle(e, durum, bildir, kayit_yaz)
            son_hata = e
            continue
        except Exception as e:                  # bozuk arşiv, yarım yanıt...: öteki sitedeki kopya denenir
            kayit_yaz(f"{a.site}: indirilen dosya kullanılamadı: {e}")
            son_hata = e
            continue
        # Karma (OpenSubtitles) tuttuysa altyazı tam bu dosyaya göre: kare hızı düzeltmesi yapılmaz.
        oran = None if a.karma_tuttu or aday.karma_tuttu else indirme.kare_hizi_orani(a.fps, video.fps, metin_,
                                                                                         uzanti, video.sure)
        if oran:
            kayit_yaz(f"kare hızı düzeltildi: oran {oran:.5f} (altyazı {a.fps or '?'} fps, video {video.fps} fps)")
            metin_ = indirme.zaman_olcekle(metin_, uzanti, oran)
        return metin_, uzanti, a
    raise son_hata or indirme.IndirmeHatasi("indirilemedi")


def _indirilen_kok(durum, aday, video):
    return durum.yol("indirilen", kayit.ozet(aday.anahtar + "|" + video.video_anahtari()))


def _indirilen_yolu(durum, aday, video, uzanti):
    """indirilen/<özet>.<dil>.<uzantı>: servis Player.setSubtitles ile yüklerken Kodi dili dosya adından okur."""
    return _indirilen_kok(durum, aday, video) + "." + kayit.kodi_dil_eki(aday.dil) + uzanti


def onceki_indirme(durum, aday, video):
    """Bu video için daha önce indirilmiş kopya (aday ya da yedeklerinden biri): (yol, aday) ya da (None, None).
    Kaymış döngüsü başa dönünce ve aynı altyazı yeniden seçilince indirme hakkı bir daha harcanmaz. Dil eki olmayan
    eski adlar da bulunur."""
    for a in [aday] + list(aday.yedekler):
        kok = _indirilen_kok(durum, a, video)
        for uzanti in indirme.UZANTILAR:
            for yol in (_indirilen_yolu(durum, a, video, uzanti), kok + uzanti):
                if os.path.exists(yol):
                    return yol, a
    return None, None


def insan_altyazisi(aday, video, site_listesi, durum, bildir, kayit_yaz):
    """Hedef dildeki altyazıyı indirip indirilen/ klasörüne yazar (önceden indirildiyse o kullanılır).
    Dönüş: (yol, aday, içerik özeti)."""
    yol, a = onceki_indirme(durum, aday, video)
    if yol:
        with open(yol, encoding="utf-8", errors="replace") as f:
            return yol, a, icerik_ozeti(f.read())
    metin_, uzanti, a = indir_metin(aday, video, site_listesi, durum, bildir, kayit_yaz)
    yol = _indirilen_yolu(durum, a, video, uzanti)
    indirme.yaz(yol, metin_)
    return yol, a, icerik_ozeti(metin_)


def icerik_ozeti(metin_):
    """Aynı içerikli kopyaları tanımak için: harf ve rakamlar (biçim ve zaman farkı sayılmaz)."""
    import re
    duz = re.sub(r"(?m)^\d+\s*$|\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|<[^>]+>|\{[^}]*\}",
                 "", metin_)
    return kayit.ozet(re.sub(r"[^\w]+", "", duz.casefold()), 20)


def zaman_imzasi(metin_):
    """Zamanlama imzası: ilk 30 altyazının başlangıç saniyesi (kaymış altyazıyla aynı zamanlı adayı atlamak için)."""
    import re
    zamanlar = re.findall(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->", metin_)[:30]
    if len(zamanlar) < 5:
        return ""
    return ",".join(str(int(s) * 3600 + int(d) * 60 + int(sn)) for s, d, sn, _ in zamanlar)


def secim_kaydi(tur, anahtar, ad, site, dil, yol, video, uyumlu=False, sdh=False, **ek):
    k = {"tur": tur, "anahtar": anahtar, "ad": ad, "site": site, "dil": dil, "yol": yol, "uyumlu": uyumlu, "sdh": sdh}
    k.update(ek)
    return k


def hazirdan_aday(hazir):
    """Hazır AI çevirisinin kaynağı (Aday)."""
    k = hazir.get("kaynak") or {}
    return Aday.kayittan(k) if k.get("site") else None


def uyumlu(aday, video):
    p, kt = puan.puanla(video.dosya_adi or None, aday.adlar, aday.karma_tuttu)
    return puan.uyumlu_mu(p, kt)


def grup(aday):
    return surum.ayristir(aday.ad).grup or ""


def var_mi(yol):
    return bool(yol) and os.path.exists(yol)
