"""Sıradaki bölüm (eski Defta'nın yöntemi): önce aynı sezonun sıradaki bölümünü kesin yazan altyazı aranır; yoksa
sezon bitmiş sayılır ve yeni sezonun ilk bölümüne bakılır (orada yalnız sezonu ve bölümü kesin yazanlar sayılır).
Kesin bulunamazsa ön çeviri yapılmaz. Hedef dilde altyazı ya da hazır çeviri varsa yalnız bildirim gelir; yoksa
temalı, süreli Çevir/İptal kutusu (varsayılan Çevir) ya da ayara göre doğrudan çeviri. Ön çeviri doğrudan kaliteli
yapılır; kaynak altyazı izlenen bölümün sürüm grubuna göre seçilir.
"""
import copy
import time

import xbmcgui

from . import arama, ceviri_isi, eslesme, kayit, kodi, liste, metin, surum
from . import video as video_modulu
from .giris import _bolum_etiketi, ayarlar, ceviri_kimligi, dil_adi, is_kimligi


def _sonraki(v, sezon, bolum, yeni_sezon=False):
    n = copy.copy(v)
    n.sezon, n.bolum = sezon, bolum
    n.dosya_adi, n.karma, n.yol = "", None, ""
    n.surum = surum.ayristir("")
    n.gomulu_diller = []
    # Sıradaki bölümün süresi bilinmez: izlenen bölümün süresiyle "altyazı videodan uzun, başka kare hızına göre
    # yapılmış" tahmini yapılmaz (başka kurgunun altyazısını yanlış ölçekleyip senkronu bozuyordu). Sitenin
    # bildirdiği kare hızına göre düzeltme sürer.
    n.sure = 0
    # AniList numarası: aynı kayıtta bir sonraki bölüm; yeni sezon başka AniList kaydıdır (eşleme listesinden bulunur)
    onceki_anilist_bolum = getattr(v, "anilist_bolum", None)
    n.anilist_bolum = onceki_anilist_bolum + (bolum - v.bolum) if onceki_anilist_bolum and not yeni_sezon else None
    if yeni_sezon:
        n.anilist = ""
    return n


def _kesin(aday, n, yeni_sezon):
    """Yalnız bu bölümü açıkça yazan altyazı sayılır (site sezon ve bölümü bildirdi ya da dosya adı yazıyor); paket
    ve bölümü belirsiz olanlar sayılmaz: sezon finalinde olmayan bölüm "bulunmasın"."""
    if aday.paket:
        return False
    return bool(aday.bolum_kesin) or any(surum.bolum_tutar_mi(a, n.sezon, n.bolum) is True for a in aday.adlar if a)


SORU_HATIRLAMA = 30 * 86400           # "İptal" denen bölüm bu kadar süre yeniden sorulmaz


def _soru(sure_sn, bolum="", altyazi=""):
    """Çevir/İptal; süre dolarsa Çevir. (İptal: False) Kart ekranın sağ üstünde açılır ve hangi bölümün hangi
    altyazıdan çevrileceğini yazar; açılamazsa (görünüm dosyası yüklenemedi) Kodi'nin düz soru kutusu kullanılır."""
    try:
        from . import kutu
        return kutu.sor(metin.al(metin.B_SIRADAKI), bolum or metin.al(metin.P_SIRADAKI_SORU), altyazi,
                        metin.al(metin.ON_CEVIR), metin.al(metin.P_IPTAL), sure_sn)
    except Exception as e:
        kodi.kayit(f"sıradaki bölüm kutusu açılamadı ({type(e).__name__}: {e}); düz soru kutusu kullanılıyor")
    bas = time.time()
    ek = {}
    if hasattr(xbmcgui, "DLG_YESNO_YES_BTN"):          # Kodi 20+: varsayılan düğme Çevir
        ek["defaultbutton"] = xbmcgui.DLG_YESNO_YES_BTN
    soru = "\n".join(p for p in (bolum, altyazi, metin.al(metin.P_SIRADAKI_SORU)) if p)
    cevap = xbmcgui.Dialog().yesno(metin.al(metin.B_SIRADAKI), soru,
                                   nolabel=metin.al(metin.P_IPTAL), yeslabel=metin.al(metin.ON_CEVIR),
                                   autoclose=int(sure_sn * 1000), **ek)
    return bool(cevap) or time.time() - bas >= sure_sn - 0.5


def _kart_yazilari(n, aday):
    """Kartta görünen iki satır: bölüm ("Dizi · S01E02") ve çevrilecek altyazı ("ad · site (yükleyen)")."""
    etiket = f"S{n.sezon or 0:02d}E{n.bolum:02d}" if n.bolum is not None else ""
    bolum = " · ".join(p for p in (n.ad, etiket) if p)
    site = liste.SITE_ADLARI.get(aday.site, aday.site)
    if aday.yukleyen:
        site = f"{site} ({aday.yukleyen})"
    if liste.makine_yapimi(aday):
        site += " · " + metin.al(metin.ON_MAKINE)
    return bolum, " · ".join(p for p in (aday.ad, site) if p)


def bak(servis, v, secim):
    ay = ayarlar()
    hedef = ay["hedef_dil"]
    durum = servis.durum
    if video_modulu.hedef_dilde_gomulu_var(v, hedef):
        # Aynı sürümün sıradaki bölümünde de büyük olasılıkla vardır: parayla ön çeviri yapılmaz
        kodi.kayit("sıradaki bölüm: izlenen videoda hedef dilde altyazı var, ön çeviri yapılmıyor")
        return
    tum_siteler = arama.siteler(kodi.ayar, durum, eslesme=lambda vid: eslesme.anilist_bul(durum, vid))
    sezon_ = v.sezon if v.sezon is not None else 1
    # Özel bölümlerden (S00) sonra 1. sezonun ilk bölümü gelmez: yalnız sıradaki özel bölüme bakılır
    adimlar = [(sezon_, v.bolum + 1, False)] + ([(sezon_ + 1, 1, True)] if sezon_ > 0 else [])
    for sezon, bolum, yeni_sezon in adimlar:
        n = _sonraki(v, sezon, bolum, yeni_sezon)
        # Jimaku yalnız AniList kaydı kesin bilinince (oynatan eklenti verdi ya da eşleme listesi buldu)
        site_listesi = [s for s in tum_siteler if s.kod != "jimaku" or n.anilist or eslesme.anilist_bul(durum, n)]
        hazirlar = durum.hazir_ceviriler(n.icerik_anahtari(), hedef)
        if hazirlar:           # çevirisi hazır: bölüm açılınca kendiliğinden yüklenir, bildirim gerekmez
            kodi.kayit("sıradaki bölüm: çevirisi hazır")
            return
        adaylar, hatalar = arama.ara(site_listesi, n, [hedef] + ay["kaynak_diller"], kodi.kayit)
        arama.hatalari_isle(hatalar, durum, kodi.bildir, kodi.kayit, bir_kez=servis.anahtar_bildirildi)
        adaylar = [a for a in adaylar if _kesin(a, n, yeni_sezon)]
        if not adaylar:
            continue
        kodi.kayit(f"sıradaki bölüm: S{sezon}E{bolum}, {len(adaylar)} aday")
        if any(a.dil == hedef for a in adaylar):
            kodi.bildir(metin.al(metin.B_SIRADAKI), metin.al(metin.B_SIRADAKI_HEDEF_VAR, dil=dil_adi(hedef)))
            return
        kaynaklar = liste.puanla(liste.birlestir([a for a in adaylar if a.dil in ay["kaynak_diller"]]),
                                 v.dosya_adi or None)          # izlenen bölümün sürümüne göre
        hak = {s.kod: 1 for s in site_listesi if durum.hak_doldu(s.kod) is not None}
        kaynaklar = liste.hak_dolanlari_ayikla(kaynaklar, hak)
        sira = liste.cevrilecek_sirasi(kaynaklar, {}, durum.tercih(v.yapim_anahtari()), ay["sdh_alta"],
                                       ay["kaynak_diller"])
        if not sira:
            break
        if not kodi.ayar("openai_anahtar"):     # çeviri başlayamaz: soru sorulmaz, kaynak indirilmez
            kodi.kayit("sıradaki bölüm: OpenAI anahtarı yok, ön çeviri yapılmıyor")
            return
        # Bu bölümün ön çevirisi başlamışsa (sürüyor ya da yarıda kaldı) yeniden sorulmaz: kaldığı yerden sürer
        yarim = next((b for b in ceviri_isi.yarim_isler(durum) if b.get("on_ceviri")
                      and b.get("icerik") == n.icerik_anahtari() and b.get("hedef_dil") == hedef), None)
        if yarim:
            if yarim.get("hata_sayisi", 0) < 3:
                servis.is_baslat(yarim["is_id"], hizli=False, kendiliginden=True)
            return
        if secim == 1:
            # "İptal" denen bölüm, aynı bölüm yeniden izlenince bir daha sorulmaz
            redler = {k: z for k, z in (durum.depo.oku("siradaki_red", {}) or {}).items()
                      if time.time() - z < SORU_HATIRLAMA}
            red_anahtari = kayit.ozet(n.icerik_anahtari() + "|" + hedef)
            if red_anahtari in redler:
                kodi.kayit("sıradaki bölüm: daha önce İptal denmiş, sorulmuyor")
                return
            if not _soru(kodi.ayar_int("siradaki_sure", 15), *_kart_yazilari(n, sira[0])):
                redler[red_anahtari] = time.time()
                durum.depo.yaz("siradaki_red", redler)
                return
        _baslat(servis, n, sira[0], site_listesi, kendiliginden=secim != 1)
        return
    kodi.bildir(metin.al(metin.B_SIRADAKI), metin.al(metin.B_SIRADAKI_YOK))


def _baslat(servis, n, aday, site_listesi, kendiliginden=False):
    """kendiliginden: soru sorulmadı (ayar "Otomatik"): üst üste 3 kez hata veren iş yeniden başlamaz, hata sayacı
    sıfırlanmaz (kullanıcı kutuda "Çevir" dediyse sürer)."""
    durum = servis.durum
    hedef = kodi.ayar("hedef_dil") or "tr"
    is_id = is_kimligi(n, aday.anahtar, hedef)
    onceki = ceviri_isi.is_bilgisi(durum, is_id)
    if onceki and kendiliginden and onceki.get("hata_sayisi", 0) >= 3:
        kodi.kayit(f"sıradaki bölümün çevirisi {onceki['hata_sayisi']} kez hata verdi, kendiliğinden başlamıyor: {is_id}")
        return
    if not onceki:
        try:
            metin_, uzanti, _ = arama.indir_metin(aday, n, site_listesi, durum, kodi.bildir, kodi.kayit)
        except Exception as e:
            kodi.kayit(f"sıradaki bölümün kaynağı indirilemedi: {e}")
            kodi.bildir(metin.al(metin.B_SIRADAKI), metin.al(metin.B_INDIRILEMEDI))
            return
        kimlik = ceviri_kimligi(aday)
        ceviri_isi.is_kur(durum, {
            "is_id": is_id, "video_anahtari": n.icerik_anahtari() + "|?", "icerik": n.icerik_anahtari(),
            "yapim": n.yapim_anahtari(), "baslik": (n.dizi_adi if n.tur == "dizi" else "") or n.baslik or n.ad,
            "bolum_etiketi": _bolum_etiketi(n), "goreli": f"{_bolum_etiketi(n)}/{kimlik}", "kaynak": aday.kayit(),
            "kaynak_dil": aday.dil, "hedef_dil": hedef, "ceviri_kimligi": kimlik, "on_ceviri": True,
            "durum": "bekliyor"}, metin_, uzanti)
    kodi.bildir(metin.al(metin.B_SIRADAKI), metin.al(metin.B_SIRADAKI_CEVRILIYOR))
    servis.is_baslat(is_id, hizli=False, kendiliginden=kendiliginden)
