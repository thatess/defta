"""Ayar penceresindeki düğmeler: anahtarları dosyadan yükle, bağlantı testi, bakım ve dışa aktarma."""
import os
import time
import traceback

import xbmcvfs

from . import ag, anahtar, arama, bakim, gizli, kayit, kodi, metin

_SITE_AYARI = {"OpenSubtitles": "os_anahtar", "SubDL": "subdl_anahtar", "SubSource": "subsource_anahtar",
               "Jimaku": "jimaku_anahtar"}


def _durum():
    return kayit.Durum(kodi.profil())


def _ayardakiler():
    return {a: kodi.ayar(a) for a, _, _, _ in anahtar.ALANLAR}


def _gecmis_yaz(durum, degisenler, ayardakiler):
    gecmis = durum.depo.oku("anahtar_gecmisi", {}) or {}
    son = durum.depo.oku("anahtar_son4", {}) or {}
    for a, yeni in degisenler.items():
        if not a.endswith("_anahtar"):          # kullanıcı adı ve parola: son karakterleri yazılmaz
            continue
        gecmis[a] = {"tarih": time.strftime("%d.%m.%Y %H:%M"), "eski": (ayardakiler.get(a) or "")[-4:]}
        son[a] = (yeni or "")[-4:]
    durum.depo.yaz("anahtar_gecmisi", gecmis)
    durum.depo.yaz("anahtar_son4", son)


def anahtar_yukle():
    durum = _durum()
    secim = kodi.secim(metin.al(metin.P_ANAHTAR_KAYNAGI), [metin.al(metin.P_DOSYA_SEC), metin.al(metin.P_KLASOR_SEC),
                                                         metin.al(metin.P_ADRES_YAZ)])
    if secim < 0:
        return
    if secim == 2:
        yol = _ag_adresi_sor(durum)
    else:
        yol = kodi.dosya_sec(metin.al(metin.AYAR_ANAHTAR_YUKLE), ".env|.txt") if secim == 0 else \
            kodi.klasor_sec(metin.al(metin.AYAR_ANAHTAR_YUKLE))
    if not yol:
        return
    metin_ = _anahtar_dosyasi_oku(xbmcvfs.translatePath(yol))
    if metin_ is None:
        kodi.bildir(metin.al(metin.B_ANAHTARLAR), metin.al(metin.P_ADRES_ACILAMADI if secim == 2 else metin.P_DOSYA_YOK),
                    hata=True)
        return
    dosyadakiler = anahtar.oku_metin(metin_)
    if not dosyadakiler:
        kodi.bildir(metin.al(metin.B_ANAHTARLAR), metin.al(metin.P_ANAHTAR_BULUNAMADI), hata=True)
        return
    ayardakiler = _ayardakiler()
    satirlar, degisenler, degisti_var = [], {}, False
    for ayar_adi, site, durum_, eski, yeni in anahtar.karsilastir(dosyadakiler, ayardakiler):
        gizli = ayar_adi.endswith("parola")
        if durum_ == "ayni":
            satirlar.append(metin.al(metin.P_ANAHTAR_AYNI, site=site, son4="" if gizli else eski))
        elif durum_ == "degisti":
            satirlar.append(metin.al(metin.P_ANAHTAR_DEGISTI, site=site, eski="" if gizli else eski, yeni="" if gizli else yeni))
            degisenler[ayar_adi] = dosyadakiler[ayar_adi]
            degisti_var = True
        elif durum_ == "eklendi":
            satirlar.append(metin.al(metin.P_ANAHTAR_EKLENDI, site=site, son4="" if gizli else yeni))
            degisenler[ayar_adi] = dosyadakiler[ayar_adi]
        elif durum_ == "dosyada_yok":
            satirlar.append(metin.al(metin.P_ANAHTAR_DOSYADA_YOK, site=site))
        else:
            satirlar.append(metin.al(metin.P_ANAHTAR_HIC_YOK, site=site))
    bulunan = len(dosyadakiler)
    eksik = sum(1 for _, _, d, _, _ in anahtar.karsilastir(dosyadakiler, ayardakiler) if d in ("yok", "dosyada_yok"))
    ozet = metin.al(metin.B_ANAHTAR_SONUC, bulunan=bulunan, eksik=eksik)
    dosya_bolumu = metin.al(metin.P_DOSYADAKILER) + ":\n" + "\n".join(satirlar)
    if not degisenler:
        kodi.goster(metin.al(metin.B_ANAHTARLAR), ozet + "\n\n" + dosya_bolumu)
        return
    # Yalnız yeni anahtar eklendiyse sorulmaz; var olan bir anahtar değişecekse önce sorulur (madde 5). Soru kısa
    # tutulur (yalnız değişenler): uzun yazı Kodi'nin soru kutusunda sayfalara bölünüp gözden kaçıyor.
    if degisti_var:
        degisecekler = [st for st in satirlar if "→" in st]
        if not kodi.onay(metin.al(metin.P_DEGISECEKLER) + ":\n" + "\n".join(degisecekler),
                         evet=metin.al(metin.P_UYGULA), hayir=metin.al(metin.P_VAZGEC)):
            return
    _gecmis_yaz(durum, degisenler, ayardakiler)
    for a, deger in degisenler.items():
        kodi.ayar_yaz(a, deger)
    with kodi.Calisiyor(metin.al(metin.AYAR_BAGLANTI_TESTI)):
        test_sonucu = baglanti_testi(goster=False, yalniz={_ayar_sitesi(a) for a in degisenler})
    # Kısa tutulur (tek sayfa): her site için test satırı anahtarın son 4 karakterini ve çalışıp çalışmadığını söyler;
    # dosya dökümü ayrıca yazılınca sayfa taşıyor, alt yarısı görülmüyordu.
    kodi.goster(metin.al(metin.B_ANAHTARLAR), metin.al(metin.P_KAYDEDILDI) + " " + ozet + "\n\n" + test_sonucu)


def _ag_adresi_sor(durum):
    """Elle yazılan ağ adresi (ör. smb://192.168.1.1/paylasim ya da içindeki dosya). Listede açılmayan sunucu (modem,
    NAS) için: kullanıcı adı ve parola ayrı sorulur ve adrese eklenir. Parolalı adres hiçbir yere yazılmaz; yalnız
    adresin kendisi bir dahaki sefere hazır gelsin diye saklanır."""
    son = (durum.depo.oku("anahtar_adresi", {}) or {}).get("adres") or "smb://"
    yazilan = kodi.yazi_sor(metin.al(metin.P_AG_ADRESI), son)
    if not yazilan or yazilan.strip() in ("", "smb://"):
        return ""
    ayrik = anahtar.ag_adresi(yazilan)
    if not ayrik:
        kodi.bildir(metin.al(metin.B_ANAHTARLAR), metin.al(metin.P_ADRES_ACILAMADI), hata=True)
        return ""
    adres, kullanici, parola = ayrik
    durum.depo.yaz("anahtar_adresi", {"adres": adres})
    if not kullanici:
        kullanici = kodi.yazi_sor(metin.al(metin.P_AG_KULLANICI))
        if kullanici is None:
            return ""
        kullanici = kullanici.strip()
        if kullanici:
            parola = kodi.yazi_sor(metin.al(metin.P_AG_PAROLA), gizli_yazi=True)
            if parola is None:
                return ""
    gizli.kaydet(parola)
    return anahtar.ag_adresi_kimlikli(adres, kullanici, parola)


def _ag_yolu_mu(yol):
    return "://" in yol and not yol.lower().startswith("file://")


def _anahtar_dosyasi_oku(yol):
    """Seçilen dosya ya da klasördeki .env/.txt dosyasının yazısı (ağ klasörü de olur: smb://, nfs://); yoksa None."""
    if not _ag_yolu_mu(yol):
        dosya = anahtar.dosya_bul(yol)
        if not dosya or not os.path.exists(dosya):
            return None
        with open(dosya, "rb") as f:
            return f.read().decode("utf-8-sig", errors="replace")
    dosya = yol
    if yol.endswith("/") or xbmcvfs.exists(yol.rstrip("/") + "/"):
        klasor = yol.rstrip("/") + "/"
        try:
            _, dosyalar = xbmcvfs.listdir(klasor)
        except Exception:
            return None
        dosyalar = sorted(dosyalar)
        secilen = next((a for a in dosyalar if a.lower().endswith(".env")), None) or \
            next((a for a in dosyalar if a.lower().endswith(".txt")), None)
        if not secilen:
            return None
        dosya = klasor + secilen
    f = xbmcvfs.File(dosya)
    try:
        veri = f.readBytes()
    finally:
        f.close()
    return bytes(veri).decode("utf-8-sig", errors="replace") if veri else None


_SITE_ADRESI = {"OpenAI": "api.openai.com", "OpenSubtitles": "api.opensubtitles.com", "SubDL": "api.subdl.com",
                "SubSource": "api.subsource.net", "Jimaku": "jimaku.cc"}


def _yonlendirici():
    import xbmc
    adres = xbmc.getInfoLabel("Network.GatewayAddress") or ""
    return adres if adres.count(".") == 3 else ""


def _servisi_durdur():
    """Silmeden önce servis bütün çevirileri durdurur (yoldaki istekleri kaydedip); en çok 25 sn beklenir."""
    cevap = "durdu.%d" % int(time.time() * 1000)
    kodi.ozellik_sil(cevap)
    kodi.servise_gonder("hepsini_durdur", {"cevap": cevap})
    import xbmc
    izleyici = xbmc.Monitor()
    bas = time.time()
    while time.time() - bas < 25 and not kodi.ozellik(cevap):
        if izleyici.waitForAbort(0.25):
            break
    kodi.ozellik_sil(cevap)


def _ayar_sitesi(ayar_adi):
    for site, a in _SITE_AYARI.items():
        if ayar_adi.startswith(a.split("_")[0]):
            return site
    return "OpenAI" if ayar_adi.startswith("openai") else ""


def _openai_test(anahtar_):
    if not anahtar_:
        return {"calisiyor": False, "neden": metin.al(metin.P_TEST_ANAHTAR_YOK)}
    try:
        ag.istek("GET", "https://api.openai.com/v1/models", {"Authorization": "Bearer " + anahtar_}, deneme=1)
        return {"calisiyor": True, "kalan": None}
    except ag.YanitHatasi as e:
        return {"calisiyor": False, "neden": f"HTTP {e.kod}"}
    except ag.AgHatasi as e:
        return {"calisiyor": False, "neden": str(e)}


def baglanti_testi(goster=True, yalniz=None):
    ag.kullanici_ajani(kodi.surum())
    durum = _durum()
    gecmis = durum.depo.oku("anahtar_gecmisi", {}) or {}
    satirlar = []
    testler = [("OpenAI", "openai_anahtar", lambda: _openai_test(kodi.ayar("openai_anahtar")))]
    siteler_ = arama.siteler(lambda a: "true" if a.endswith("_kullan") else kodi.ayar(a), durum)
    testi = {site.ad: site.test for site in siteler_}
    for ad in _SITE_AYARI:                      # anahtarı olmayan site de "anahtar yok" diye listelenir
        testler.append((ad, _SITE_AYARI[ad], testi.get(ad)))
    for ad, ayar_adi, test in testler:
        if yalniz is not None and ad not in yalniz:
            continue
        deger = kodi.ayar(ayar_adi) if ayar_adi else ""
        parcalar = [ad + ":"]
        if not deger:
            parcalar.append(metin.al(metin.P_TEST_ANAHTAR_YOK))
        else:
            parcalar.append("…" + deger[-4:])
            try:
                sonuc = test()
            except Exception as e:
                sonuc = {"calisiyor": False, "neden": type(e).__name__}
            parcalar.append(metin.al(metin.P_TEST_CALISIYOR) if sonuc.get("calisiyor") else
                            metin.al(metin.P_TEST_CALISMIYOR, neden=sonuc.get("neden", "")))
            if not sonuc.get("calisiyor") and "çözülemedi" in str(sonuc.get("neden", "")):
                tani = ag.dns_tanisi(_SITE_ADRESI.get(ad, ""), _yonlendirici())
                kodi.kayit(f"{ad} DNS tanısı: {tani}")
                parcalar.append(tani)
            if sonuc.get("kalan") is not None:
                parcalar.append(metin.al(metin.P_TEST_KALAN, kalan=sonuc["kalan"]))
        g = gecmis.get(ayar_adi)
        if g and g.get("eski"):
            parcalar.append(metin.al(metin.P_TEST_DEGISIKLIK, tarih=g.get("tarih", ""), son4=g["eski"]))
        elif g:
            parcalar.append(metin.al(metin.P_TEST_EKLENDI, tarih=g.get("tarih", "")))
        satirlar.append(" · ".join([parcalar[0] + " " + parcalar[1]] + parcalar[2:]))
    yazi = "\n".join(satirlar)
    if goster:
        kodi.goster(metin.al(metin.P_TEST_BASLIK), yazi)
    return yazi


def testi_goster():
    """Bağlantı testi düğmesi: siteler sırayla yoklanırken (birkaç saniye) çalışıyor işareti görünür."""
    with kodi.Calisiyor(metin.al(metin.AYAR_BAGLANTI_TESTI)):
        yazi = baglanti_testi(goster=False)
    kodi.goster(metin.al(metin.P_TEST_BASLIK), yazi)


_KALEM_ADI = {"ceviriler": metin.N_CEVIRILER, "sozlukler": metin.N_SOZLUKLER, "yarim": metin.N_YARIM,
              "indirilen": metin.N_INDIRILEN, "kayitlar": metin.N_KAYITLAR, "gecici": metin.N_GECICI,
              "isaretler": metin.N_ISARETLER, "esleme": metin.N_ESLEME}


def sil(kalem):
    if not kodi.onay(metin.al(metin.P_SIL_ONAY, ne=metin.al(_KALEM_ADI[kalem]))):
        return
    with kodi.Calisiyor(metin.al(_KALEM_ADI[kalem])):
        if kalem in ("ceviriler", "sozlukler", "yarim"):
            _servisi_durdur()
        sonuc = bakim.temizle(_durum(), [kalem])
    sayi, bayt = sonuc[kalem]
    kodi.bildir(metin.al(_KALEM_ADI[kalem]) + " · " + metin.al(metin.B_SILINDI),
                metin.al(metin.N_ADET_MB, sayi=sayi, mb=f"{bayt / 1e6:.1f}".replace(".", ",")))


def tumunu_temizle():
    if not kodi.onay(metin.al(metin.P_TUMUNU_ONAY)):
        return
    with kodi.Calisiyor(metin.al(metin.AYAR_TUMUNU_TEMIZLE)):
        _servisi_durdur()
        sonuc = bakim.temizle(_durum(), list(bakim.KALEMLER))
    toplam = sum(b for _, b in sonuc.values())
    kodi.bildir(metin.al(metin.B_ONBELLEK_TEMIZ), metin.al(metin.N_OZET, cevir=sonuc["ceviriler"][0],
                                                            sozluk=sonuc["sozlukler"][0],
                                                            mb=f"{toplam / 1e6:.1f}".replace(".", ",")))


def fabrika():
    if not kodi.onay(metin.al(metin.P_FABRIKA_ONAY)):
        return
    with kodi.Calisiyor(metin.al(metin.AYAR_FABRIKA)):
        _servisi_durdur()
        durum = _durum()
        kodi.ozellik("sifirlaniyor", "1")           # servis bu arada anahtar geçmişi yazmasın
        try:
            varsayilanlar = bakim.ayar_varsayilanlari(os.path.join(kodi.eklenti_yolu(), "resources", "settings.xml"))
            anahtarlar = [a for a, _, _, _ in anahtar.ALANLAR]
            for ad in anahtarlar + [a for a in varsayilanlar if a not in anahtarlar]:      # önce anahtarlar
                kodi.ayar_yaz(ad, varsayilanlar.get(ad, ""))
            time.sleep(1)                           # ayar değişikliği bildirimleri servise ulaşsın
            bakim.fabrika(durum)
        finally:
            kodi.ozellik_sil("sifirlaniyor")
    kodi.bildir(metin.al(metin.AYAR_FABRIKA), metin.al(metin.B_SILINDI))


def _dosya_islevleri():
    def kopyala(kaynak, hedef):
        return bool(xbmcvfs.copy(kaynak, hedef))

    def klasor_kur(yol):
        if not xbmcvfs.exists(yol):
            xbmcvfs.mkdirs(yol)
    return kopyala, klasor_kur


def _disa_klasoru(durum):
    """Dışa aktarılacak klasör düğmeye basınca sorulur (ayrı bir ayar değil: ayar penceresi açıkken seçilen klasör
    kaydedilmeden düğmeye basılınca eski klasör kullanılıyordu). Son kullanılan klasör ilk seçenek olarak sunulur."""
    son = (durum.depo.oku("disa_klasor", {}) or {}).get("yol") or ""
    if son:
        secim = kodi.secim(metin.al(metin.P_DISA_KLASOR_SEC), [metin.al(metin.P_DISA_SON_KLASOR, klasor=son),
                                                             metin.al(metin.P_DISA_BASKA_KLASOR)])
        if secim < 0:
            return ""
        if secim == 0:
            return son
    klasor = kodi.klasor_sec(metin.al(metin.P_DISA_KLASOR_SEC), yazilabilir=True)
    if klasor:
        durum.depo.yaz("disa_klasor", {"yol": klasor})
    return klasor


def _kodi_kayitlari():
    """Kodi'nin şimdiki ve önceki oturum kaydı (çökmeden sonra önceki oturum kodi.old.log'dadır), süzülmüş."""
    sonuc = {}
    for ad in ("kodi.log", "kodi.old.log"):
        try:
            dosya = xbmcvfs.File(xbmcvfs.translatePath("special://logpath/" + ad))
            try:
                ham = bytes(dosya.readBytes())
            finally:
                dosya.close()
        except Exception:
            continue
        if ham:
            sonuc[ad.replace(".log", "") + "-defta.txt"] = bakim.kodi_kaydini_suz(ham.decode("utf-8", "replace"),
                                                                               gizli.gizle)
    return sonuc


def disa(tur):
    durum = _durum()
    klasor = _disa_klasoru(durum)
    if not klasor:
        return
    kopyala, klasor_kur = _dosya_islevleri()
    with kodi.Calisiyor(metin.al(metin.AYAR_DISA_CEVIRI if tur == "ceviri" else metin.AYAR_DISA_HATA)):
        if tur == "ceviri":
            sayi = bakim.disa_ceviriler(durum, klasor, kopyala, klasor_kur)
        else:
            sayi = bakim.disa_hata(durum, klasor, kopyala, klasor_kur, _kodi_kayitlari())
    kodi.bildir(metin.al(metin.B_DISA_AKTARILDI), metin.al(metin.B_DISA_YAZI, sayi=sayi))


def esleme_simdi():
    from . import eslesme
    with kodi.Calisiyor(metin.al(metin.GRUP_ESLEME)):
        eslesme.guncelle(_durum(), bildir=True, zorla=True, bildirici=kodi.bildir, kayit_yaz=kodi.kayit)


EYLEMLER = {
    "anahtar_yukle": anahtar_yukle, "baglanti_testi": lambda: testi_goster(), "tumunu_temizle": tumunu_temizle,
    "fabrika": fabrika, "disa_ceviri": lambda: disa("ceviri"), "disa_hata": lambda: disa("hata"),
    "esleme_simdi": esleme_simdi,
}
for _kalem, _ad in (("ceviriler", "sil_ceviri"), ("sozlukler", "sil_sozluk"), ("yarim", "sil_yarim"),
                    ("indirilen", "sil_indirilen"), ("kayitlar", "sil_kayit"), ("gecici", "sil_gecici"),
                    ("isaretler", "sil_isaret"), ("esleme", "sil_esleme")):
    EYLEMLER[_ad] = (lambda k: (lambda: sil(k)))(_kalem)


# Ayar yazdığı için pencereyi kapatan düğme -> yeniden açılınca seçili gelecek sekmenin sırası (ayarlar_uret.SEKMELER)
YENIDEN_ACILAN = {"anahtar_yukle": 1}
AYAR_YAZANLAR = ("anahtar_yukle", "fabrika")     # ayar yazan düğmeler: bilgi sayfası eski değerleri tutmasın


def calistir(argv):
    eylem = argv[1] if len(argv) > 1 else ""
    islev = EYLEMLER.get(eylem)
    if islev is None:
        return
    try:
        islev()
    except Exception as e:
        kodi.kayit("ayar eylemi: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)), hata=True)
        kodi.bildir(metin.al(metin.B_HATA), str(e), hata=True)
    finally:
        # Yalnız ayar yazan düğme pencereyi kapatır (settings.xml: <close>); iş bitince aynı sekmede yeniden açılır.
        # Öteki düğmelerde pencere hiç kapanmaz: yeniden açmak kullanıcıyı ilk sekmeye atıyordu.
        if eylem in AYAR_YAZANLAR:
            kodi.bilgi_penceresini_kapat()
        if eylem in YENIDEN_ACILAN:
            kodi.ayarlari_ac(YENIDEN_ACILAN[eylem])
