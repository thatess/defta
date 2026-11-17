"""Altyazı penceresi (Kodi'nin altyazı modülü): arama ve indirme.

Kodi bu dosyayı her arama ve her seçimde ayrı çalıştırır. Uzun işler (çeviri) servise devredilir; hızlı çeviri
hazır olunca dosyası Kodi'ye verilir, kalanı arka planda sürer.
"""
import json
import os
import time
import traceback
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import ag, arama, ceviri_isi, eslesme, kayit, kodi, liste, metin
from . import video as video_modulu
from .siteler import Aday

SERVIS_BEKLEME = 4.0            # servisin işi aldığını bildirmesi en çok bu kadar beklenir
_DIL_AD = {"tr": metin.DIL_TR, "en": metin.DIL_EN, "ja": metin.DIL_JA, "de": metin.DIL_DE, "fr": metin.DIL_FR,
           "es": metin.DIL_ES, "it": metin.DIL_IT, "pt": metin.DIL_PT, "pt-BR": metin.DIL_PT_BR, "ru": metin.DIL_RU,
           "ar": metin.DIL_AR, "ko": metin.DIL_KO, "zh": metin.DIL_ZH, "nl": metin.DIL_NL, "pl": metin.DIL_PL,
           "el": metin.DIL_EL, "fa": metin.DIL_FA, "az": metin.DIL_AZ, "uk": metin.DIL_UK, "sv": metin.DIL_SV}


def dil_adi(kod):
    return metin.al(_DIL_AD[kod]) if kod in _DIL_AD else kod


def ingilizce_dil_adi(kod):
    if kod == "pt-BR":
        return "Portuguese (Brazil)"
    try:
        ad = xbmc.convertLanguage(kod, xbmc.ENGLISH_NAME)
    except Exception:
        ad = ""
    return ad or kod


def _bayrak(kod):
    return {"pt-BR": "pb", "zh": "zh"}.get(kod, (kod or "")[:2])


def ayarlar():
    hedef = kodi.ayar("hedef_dil") or "tr"
    kaynaklar = []
    for ad in ("kaynak_1", "kaynak_2", "kaynak_3"):
        d = kodi.ayar(ad)
        if d and d != "yok" and d != hedef and d not in kaynaklar:     # "yok": ikinci/üçüncü kaynak seçilmemiş
            kaynaklar.append(d)
    return {"hedef_dil": hedef, "kaynak_diller": kaynaklar, "jimaku_oncelik": kodi.ayar_bool("jimaku_oncelik"),
            "sonuc_hedef": kodi.ayar_int("sonuc_hedef", 3), "sonuc_cevrilecek": kodi.ayar_int("sonuc_cevrilecek", 3),
            "sdh_alta": kodi.ayar_bool("sdh_alta"), "oto_ai": kodi.ayar_int("oto_ai", 0), "dil_adi": dil_adi}


def bicim_ayarlari():
    sonuc = {"hedef_dil": kodi.ayar("hedef_dil") or "tr", "b_sen_siz": kodi.ayar_bool("b_sen_siz")}
    for ad in ("b_sarki", "b_tire", "b_italik", "b_ses_etiketi", "b_konusan", "b_ekran", "b_kurgu", "b_hitap_ekleri",
               "b_ses_sozcukleri", "b_ustte", "b_reklam"):
        sonuc[ad] = kodi.ayar(ad)
    return sonuc


def _bildir(baslik, yazi, hata=False):
    kodi.bildir(baslik, yazi, hata)


def _kayit(m):
    kodi.kayit(m)


# ------------------------------------------------------------------------------------------- video kaydı
def video_kaydi(v):
    """Son aramanın videosu (adres yazılmaz)."""
    return {a: getattr(v, a) for a in ("akis", "canli", "dosya_adi", "tur", "imdb", "bolum_imdb", "tmdb", "tvdb",
                                       "anilist", "anilist_bolum", "mal",
                                       "dizi_adi", "baslik", "ozgun_ad", "sezon", "bolum", "yil", "sure", "fps",
                                       "karma", "gomulu_diller", "elle_arama", "oynayan_anahtari")}


def kayittan_video(d):
    v = video_modulu.Video()
    for a, deger in (d or {}).items():
        setattr(v, a, deger)
    from . import surum
    v.surum = surum.ayristir(v.dosya_adi or "")
    return v


def suren_is(video_anahtari):
    try:
        isler = json.loads(kodi.ozellik("isler") or "{}")
    except ValueError:
        return None
    return isler.get(video_anahtari)


# -------------------------------------------------------------------------------------------------- arama
def ara(tutamac, sorgu):
    durum = kayit.Durum(kodi.profil())
    ay = ayarlar()
    oynayan = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False, ad_bul=video_modulu.sunucu_adi_bul(kodi.ozellik))
    paylasilan = kodi.ozellik("karma." + video_modulu.karma_anahtari(oynayan))     # servis oynatma başında hesapladı
    if paylasilan:
        oynayan.karma = None if paylasilan == "-" else paylasilan
    elif oynayan.yol:
        oynayan.karma = video_modulu._karma(xbmcvfs, oynayan)
    if sorgu.get("action") == "manualsearch" and sorgu.get("searchstring"):
        v = video_modulu.elle_aramadan(sorgu["searchstring"])
        for alan in ("sure", "fps", "dosya_adi", "surum", "karma", "akis", "canli"):
            setattr(v, alan, getattr(oynayan, alan))
        # Seçim, kaymış işareti ve çeviri işi oynayan videoya bağlanır (servisin bildiği anahtar): video kapanınca
        # çeviri durur, bitince o videoya yüklenir, yeniden açılınca kaldığı yerden sürer.
        eslesme.uygula(durum, oynayan)
        v.oynayan_anahtari = oynayan.video_anahtari()
    else:
        v = oynayan
    _kayit("arama: " + v.ozet())
    if not v.tanindi:
        _bildir(metin.al(metin.B_BULUNAMADI), "")
        return
    diller = [ay["hedef_dil"]] + ay["kaynak_diller"]
    eslesme.uygula(durum, v)
    site_listesi = arama.siteler(kodi.ayar, durum, eslesme=lambda vid: eslesme.anilist_bul(durum, vid))
    adaylar, hatalar = _onbellekle_ara(site_listesi, v, diller)
    # Anahtarı eksik siteler Kodi açık kaldıkça bir kez bildirilir (eklenti her aramada yeni yorumlayıcıda çalışır)
    bildirilen = set(filter(None, (kodi.ozellik("anahtar_bildirildi") or "").split("|")))
    arama.hatalari_isle(hatalar, durum, _bildir, _kayit, eksik=getattr(site_listesi, "eksik", ()),
                        bir_kez=bildirilen)
    kodi.ozellik("anahtar_bildirildi", "|".join(sorted(bildirilen)))
    hazirlar = durum.hazir_ceviriler(v.icerik_anahtari(), ay["hedef_dil"])
    secili = durum.secim(v.video_anahtari())
    if secili and not arama.var_mi(secili.get("yol")):
        secili = None
    kaymislar = durum.kaymislar(v.video_anahtari())
    tercih = durum.tercih(v.yapim_anahtari())
    ay["hak_dolan"] = {s.kod: durum.hak_doldu(s.kod) for s in site_listesi if durum.hak_doldu(s.kod) is not None}
    ay["kodi_otomatik"] = bool(kodi.kodi_ayari("subtitles.downloadfirst"))
    satirlar, sira, tum = liste.olustur(v, adaylar, hazirlar, secili, suren_is(v.video_anahtari()), kaymislar,
                                        tercih, ay)
    kayit.yaz(os.path.join(kodi.profil("gecici"), "son_arama.json"),
              {"video": video_kaydi(v), "adaylar": {k: a.kayit() for k, a in tum.items()}, "hazirlar": hazirlar,
               "sira": sira, "cevrilecek": [x.anahtar for x in satirlar if x.eylem == "cevir"], "zaman": time.time()})
    for s in satirlar:
        # Etiket İngilizce dil adı: Kodi indirilen dosyanın dil ekini bundan çıkarır ("Türkçe"yi tanımaz)
        li = xbmcgui.ListItem(label=ingilizce_dil_adi(s.dil), label2=s.yazi)
        li.setArt({"icon": "0", "thumb": _bayrak(s.dil)})
        li.setProperty("sync", "true" if s.uyumlu else "false")
        li.setProperty("hearing_imp", "true" if s.sdh else "false")
        adres = "plugin://{}/?{}".format(kodi.KIMLIK, urllib.parse.urlencode({"action": "download", "e": s.eylem,
                                                                              "a": s.anahtar}))
        xbmcplugin.addDirectoryItem(handle=tutamac, url=adres, listitem=li, isFolder=False)
    _kayit(f"liste: {len(satirlar)} satır ({len(adaylar)} aday, {len(hazirlar)} hazır çeviri)")


ARAMA_ONBELLEGI = 15 * 60


def _onbellekle_ara(site_listesi, v, diller):
    """Aynı videonun aynı aramasının sonucu 15 dakika saklanır: Kaymış satırından sonra pencere yeniden açılınca
    siteler bir daha aranmaz. Bir site hata verdiyse saklanmaz (sonraki açılışta yeniden denenir). Eklenti
    güncellenince eski sürümün sonucu kullanılmaz (süzme kuralları değişmiş olabilir)."""
    anahtar = kayit.ozet("|".join([v.video_anahtari(), v.elle_arama or "", ",".join(diller),
                                   ",".join(sorted(s.kod for s in site_listesi)), kodi.surum()]), 20)
    yol = os.path.join(kodi.profil("gecici"), f"arama_{anahtar}.json")
    onceki = kayit.oku(yol) or {}
    if onceki and 0 <= time.time() - onceki.get("zaman", 0) < ARAMA_ONBELLEGI:
        _kayit(f"arama önbellekten ({len(onceki.get('adaylar') or [])} aday)")
        return [Aday.kayittan(d) for d in onceki.get("adaylar") or []], []
    adaylar, hatalar = arama.ara(site_listesi, v, diller, _kayit)
    # Ek sorgusu ya da öteki sayfası alınamayan sitenin sonucu eksiktir: o da saklanmaz
    if not hatalar and not any(getattr(s, "eksik_kaldi", False) for s in site_listesi):
        kayit.yaz(yol, {"zaman": time.time(), "adaylar": [a.kayit() for a in adaylar]})
    return adaylar, hatalar


# ------------------------------------------------------------------------------------------------ indirme
def _ver(tutamac, yol):
    if yol and os.path.exists(yol):
        li = xbmcgui.ListItem(label=yol)
        xbmcplugin.addDirectoryItem(handle=tutamac, url=yol, listitem=li, isFolder=False)
        return True
    return False


def indir(tutamac, sorgu):
    durum = kayit.Durum(kodi.profil())
    son = kayit.oku(os.path.join(kodi.profil("gecici"), "son_arama.json")) or {}
    v = kayittan_video(son.get("video"))
    eylem, anahtar = sorgu.get("e", ""), sorgu.get("a", "")
    secili = durum.secim(v.video_anahtari())
    if eylem == "guvenlik":
        return
    if eylem in ("secili", "suren"):
        _ver(tutamac, (secili or {}).get("yol"))
        return
    yol = None
    if eylem == "ai":
        yol = hazir_yukle(durum, v, son, anahtar)
    elif eylem == "indir":
        yol = insan_yukle(durum, v, son, anahtar)
    elif eylem == "cevir":
        yol = cevir(durum, v, son, anahtar)
    elif eylem == "kaymis":
        yol = kaymis(durum, v, son, secili)
    elif eylem == "yeniden":
        yol = yeniden(durum, v, secili)
    _ver(tutamac, yol)


def _durdur_digerleri(v, haric=""):
    """Başka altyazı seçilince o videonun süren çevirisi durur (para boşuna gitmesin); kaldığı yer kayıtlı kalır."""
    kodi.servise_gonder("durdur", {"video": v.video_anahtari(), "haric": haric})


def _uygula(durum, v, h):
    """Hazırlanan seçimi kaydeder; o videonun öteki çevirisi durur. Dönüş: yüklenecek dosya."""
    durum.secim_yaz(v.video_anahtari(), h["secim"])
    if h.get("tercih"):
        durum.tercih_yaz(v.yapim_anahtari(), *h["tercih"])
    _durdur_digerleri(v, haric=h.get("haric", ""))
    return h["yol"]


def _hazir_hazirla(durum, v, anahtar):
    """Hazır AI çevirisi: kaydedilecek seçim (henüz yazılmaz) ya da None."""
    kimlik = anahtar[3:] if anahtar.startswith("ai:") else anahtar
    hazir = next((h for h in durum.hazir_ceviriler(v.icerik_anahtari(), kodi.ayar("hedef_dil") or "tr")
                  if h.get("kimlik") == kimlik), None)
    if not hazir:
        return None
    k = arama.hazirdan_aday(hazir)
    return {"yol": hazir["yol"], "haric": "ai:" + kimlik, "secim": arama.secim_kaydi(
        "ai", "ai:" + kimlik, (k.ad if k else ""), (k.site if k else ""), hazir.get("hedef_dil"), hazir["yol"], v,
        uyumlu=bool(k and arama.uyumlu(k, v)), kaynak=hazir.get("kaynak"))}


def _insan_hazirla(durum, v, son, anahtar):
    """İnsan altyazısı indirilir (önceden indirildiyse o kullanılır): kaydedilecek seçim ya da None."""
    d = (son.get("adaylar") or {}).get(anahtar)
    if not d:
        return None
    aday = Aday.kayittan(d)
    site_listesi = arama.siteler(kodi.ayar, durum)
    try:
        yol, indirilen, ozet = arama.insan_altyazisi(aday, v, site_listesi, durum, _bildir, _kayit)
    except Exception as e:
        _kayit(f"indirilemedi: {aday.site} · {aday.ad}: {e}")
        _bildir(metin.al(metin.B_INDIRILEMEDI), str(e), True)
        return None
    return {"yol": yol, "tercih": (arama.grup(aday), aday.yukleyen, liste.aile_imzasi(aday)), "secim": arama.secim_kaydi(
        "insan", aday.anahtar, aday.ad, indirilen.site, aday.dil, yol, v, uyumlu=arama.uyumlu(aday, v), sdh=aday.sdh,
        icerik_ozeti=ozet)}


def hazir_yukle(durum, v, son, anahtar):
    h = _hazir_hazirla(durum, v, anahtar)
    return _uygula(durum, v, h) if h else None


def insan_yukle(durum, v, son, anahtar):
    h = _insan_hazirla(durum, v, son, anahtar)
    return _uygula(durum, v, h) if h else None


# ------------------------------------------------------------------------------------------------- çeviri
def is_kimligi(v, aday_anahtari, hedef):
    return kayit.ozet(f"{v.icerik_anahtari()}|{aday_anahtari}|{hedef}", 20)


def ceviri_kimligi(aday):
    return f"{aday.site}-{kayit.ozet(aday.kimlik, 10)}"


def _bolum_etiketi(v):
    return f"S{v.sezon or 0:02d}E{v.bolum:02d}" if v.tur == "dizi" and v.bolum is not None else "film"


def _servis_aldi(is_id):
    """Servis işi aldı mı. Altyazı penceresi çeviriyi beklemez: orijinal hemen yüklenir, hızlı çeviriyi ve bitmiş
    çeviriyi servis kendisi yükler. Dönüş: hata yazısı ya da "" (iş alındı)."""
    bas = time.time()
    izleyici = xbmc.Monitor()
    while time.time() - bas < SERVIS_BEKLEME:
        deger = kodi.ozellik("sonuc." + is_id)          # iş başlayamadıysa servis nedenini hemen yazar
        if deger:
            kodi.ozellik_sil("sonuc." + is_id)
            kodi.ozellik_sil("alindi." + is_id)
            try:
                return str(json.loads(deger).get("hata") or "")
            except (ValueError, AttributeError):
                return metin.al(metin.H_SERVIS_YOK)
        if kodi.ozellik("alindi." + is_id):
            return ""
        if izleyici.waitForAbort(0.1):
            return "Kodi kapanıyor"
    return metin.al(metin.H_SERVIS_YOK)


def cevir(durum, v, son, anahtar):
    if not kodi.ayar("openai_anahtar"):
        _bildir(metin.al(metin.B_ANAHTAR_EKSIK, site="OpenAI"), "", True)
        return None
    d = (son.get("adaylar") or {}).get(anahtar)
    if not d:
        return None
    aday = Aday.kayittan(d)
    hedef = kodi.ayar("hedef_dil") or "tr"
    is_id = is_kimligi(v, aday.anahtar, hedef)
    kimlik = ceviri_kimligi(aday)
    baslangic = video_modulu.oynatma_yeri(xbmc)
    sure = kodi.ayar_int("hizli_sure", 2)
    yarim = ceviri_isi.is_bilgisi(durum, is_id)
    if yarim:
        mesaj = "devam"
    else:
        site_listesi = arama.siteler(kodi.ayar, durum)
        try:
            metin_, uzanti, indirilen = arama.indir_metin(aday, v, site_listesi, durum, _bildir, _kayit)
        except Exception as e:
            _kayit(f"kaynak indirilemedi: {aday.site} · {aday.ad}: {e}")
            _bildir(metin.al(metin.B_INDIRILEMEDI), str(e), True)
            return None
        ceviri_isi.is_kur(durum, {
            "is_id": is_id, "video_anahtari": v.video_anahtari(), "icerik": v.icerik_anahtari(),
            "yapim": v.yapim_anahtari(), "baslik": (v.dizi_adi if v.tur == "dizi" else "") or v.baslik or v.ad,
            "bolum_etiketi": _bolum_etiketi(v), "goreli": f"{_bolum_etiketi(v)}/{kimlik}",
            "kaynak": aday.kayit(), "kaynak_dil": aday.dil, "hedef_dil": hedef, "ceviri_kimligi": kimlik,
            "on_ceviri": False, "video_karma": v.karma, "durum": "bekliyor"}, metin_, uzanti)
        mesaj = "cevir"
    bilgi = ceviri_isi.is_bilgisi(durum, is_id) or {}
    kaynak_yolu = os.path.join(durum.yol("isler", is_id), bilgi.get("kaynak_dosya", ""))
    secim = arama.secim_kaydi("ai", "ai:" + kimlik, aday.ad, aday.site, hedef, kaynak_yolu, v,
                              uyumlu=arama.uyumlu(aday, v), kaynak=aday.kayit(), is_id=is_id)
    # Seçim hemen yazılır ve öteki çeviri hemen durur (yolda olan istekleri kaydedilir): ikisi aynı anda sürmez.
    _uygula(durum, v, {"yol": kaynak_yolu, "secim": secim, "tercih": (arama.grup(aday), aday.yukleyen, liste.aile_imzasi(aday)),
                       "haric": "ai:" + kimlik})
    kodi.ozellik_sil("sonuc." + is_id)
    kodi.ozellik_sil("alindi." + is_id)
    # Pencere beklemez: orijinal altyazı hemen yüklenir, izlemeye devam edilir. Hızlı çeviri hazır olunca, sonra da
    # çevirinin tamamı bitince servis kendisi yükler ("yukle").
    kodi.servise_gonder(mesaj, {"is_id": is_id, "video": v.video_anahtari(), "baslangic": baslangic, "sure": sure,
                                "hizli": kodi.hizli_acik(), "yukle": True})
    hata = _servis_aldi(is_id)
    if hata:
        _kayit(f"çeviri başlamadı: {hata}")
        _bildir(metin.al(metin.B_CEVIRI_HATASI), hata, True)
    return kaynak_yolu if arama.var_mi(kaynak_yolu) else None


# ------------------------------------------------------------------------------------------------- kaymış
def _yeni_sira(sira, kaymislar):
    isaretsiz = [x for x in sira if x[1] not in kaymislar]
    isaretli = sorted([x for x in sira if x[1] in kaymislar], key=lambda x: kaymislar[x[1]])
    return isaretsiz + isaretli


def kaymis(durum, v, son, secili):
    """Seçili altyazı kaymış diye işaretlenir ve sıradaki yüklenir.
    İnsan altyazısı seçiliyken hedef dildeki öteki insan altyazıları arasında dönülür.
    AI çevirisi seçiliyken sıra "Çevir" adaylarındadır: önce başka bir kaynağın hazır çevirisi (ücretsiz), o yoksa
    listedeki sıradaki aday çevrilir (ücretli; kullanıcı kararı, 17.11.2026). Aday kalmadıysa hazır çeviriler arasında
    başa dönülür."""
    if not secili:
        return None
    va = v.video_anahtari()
    ai = secili.get("tur") == "ai"
    isaret = [secili.get("anahtar")]
    if ai and (secili.get("kaynak") or {}).get("site"):
        k = secili["kaynak"]
        isaret.append(f"{k['site']}:{k['kimlik']}")
    durum.kaymis_isaretle(va, *isaret)
    kaymislar = durum.kaymislar(va)
    imza_yolu = durum.yol("kaymis", kayit.ozet(va) + ".imza.json")
    imzalar = (kayit.oku(imza_yolu, {}) or {})
    if secili.get("yol") and arama.var_mi(secili["yol"]):
        with open(secili["yol"], encoding="utf-8", errors="replace") as f:
            m = f.read()
        imzalar.setdefault("icerik", []).append(arama.icerik_ozeti(m))
        imzalar.setdefault("zaman", []).append(arama.zaman_imzasi(m))
    sira = _yeni_sira([tuple(x) for x in son.get("sira") or []], kaymislar)
    # Her tür kendi arasında döner: AI çevirileri ile insan altyazıları ayrı gruplardır
    adaylar = [x for x in sira if x[1] != secili.get("anahtar") and (x[0] == "ai") == ai]

    def hazirdan(liste_):
        for tur, anahtar in liste_:
            h = _hazir_hazirla(durum, v, anahtar) if tur == "ai" else _insan_hazirla(durum, v, son, anahtar)
            if not h or not arama.var_mi(h["yol"]):
                continue
            with open(h["yol"], encoding="utf-8", errors="replace") as f:
                m = f.read()
            ayni_icerik = arama.icerik_ozeti(m) in imzalar.get("icerik", [])
            ayni_zaman = bool(arama.zaman_imzasi(m)) and arama.zaman_imzasi(m) in imzalar.get("zaman", [])
            # Başa dönünce atlama yapılmaz: işaretliler sıranın sonundadır, onlara gelindiyse denenmemiş aday
            # kalmamıştır (işaretlinin kendi imzası da kayıtlıdır; atlansaydı hiçbirine dönülemezdi)
            if (ayni_icerik or ayni_zaman) and anahtar not in kaymislar:
                durum.kaymis_isaretle(va, anahtar)                   # atlanan seçim olarak yazılmaz
                imzalar.setdefault("icerik", []).append(arama.icerik_ozeti(m))
                continue
            kayit.yaz(imza_yolu, imzalar)
            return _uygula(durum, v, h)
        return None

    if ai:
        yol = hazirdan([x for x in adaylar if x[1] not in kaymislar])       # denenmemiş hazır çeviri: ücretsiz
        if yol:
            return yol
        kayit.yaz(imza_yolu, imzalar)
        for anahtar in son.get("cevrilecek") or []:                         # listedeki sırayla, denenmemiş ilk kaynak
            if anahtar in kaymislar or anahtar not in (son.get("adaylar") or {}):
                continue
            _kayit(f"kaymış: sıradaki kaynak çevriliyor ({anahtar.split(':', 1)[0]})")
            yol = cevir(durum, v, son, anahtar)
            if yol:
                return yol
    yol = hazirdan(adaylar)
    if yol:
        return yol
    kayit.yaz(imza_yolu, imzalar)
    _bildir(metin.al(metin.B_BASKA_YOK), "")
    return secili.get("yol")


# ------------------------------------------------------------------------------------------- yeniden çevir
def yeniden(durum, v, secili):
    if not secili or secili.get("tur") != "ai":
        return (secili or {}).get("yol")
    kimlik = secili["anahtar"][3:]
    hedef = kodi.ayar("hedef_dil") or "tr"
    hazir = next((h for h in durum.hazir_ceviriler(v.icerik_anahtari(), hedef) if h.get("kimlik") == kimlik), None)
    if not hazir or not arama.var_mi(hazir.get("kaynak_yol")):
        _bildir(metin.al(metin.B_CEVIRI_HATASI), "", True)
        return secili.get("yol")
    with open(hazir["kaynak_yol"], encoding="utf-8", errors="replace") as f:
        kaynak_metni = f.read()
    # İş kimliği sabit: yarıda kalan yeniden çeviri (video kapandı, hata) tekrar seçilince kaldığı yerden sürer,
    # ödenen kısım yeniden ödenmez. Yarım iş varsa soru sorulmaz.
    is_id = kayit.ozet(f"{v.icerik_anahtari()}|yeniden|{kimlik}|{hedef}", 20)
    if ceviri_isi.is_bilgisi(durum, is_id):
        kodi.servise_gonder("devam", {"is_id": is_id, "video": v.video_anahtari(), "hizli": False})
        _bildir(metin.al(metin.B_DEVAM), "")
        return secili.get("yol")
    secim = kodi.secim(metin.al(metin.P_YENIDEN_CEVIR), [metin.al(metin.P_YALNIZ_CEVIRI), metin.al(metin.P_SOZLUKLE)])
    if secim < 0:
        return secili.get("yol")
    ceviri_isi.is_kur(durum, {
        "is_id": is_id, "video_anahtari": v.video_anahtari(), "icerik": v.icerik_anahtari(),
        "yapim": hazir.get("yapim") or v.yapim_anahtari(), "baslik": v.dizi_adi or v.baslik or v.ad,
        "bolum_etiketi": _bolum_etiketi(v), "goreli": hazir.get("goreli") or f"{_bolum_etiketi(v)}/{kimlik}",
        "kaynak": hazir.get("kaynak"), "kaynak_dil": hazir.get("kaynak_dil", ""), "hedef_dil": hedef,
        "ceviri_kimligi": kimlik, "on_ceviri": False, "video_karma": hazir.get("video_karma"), "durum": "bekliyor",
        "yeniden": True,
        "sozlukle_yenile": secim == 1}, kaynak_metni, os.path.splitext(hazir["kaynak_yol"])[1] or ".srt")
    kodi.servise_gonder("cevir", {"is_id": is_id, "video": v.video_anahtari(), "hizli": False})
    return secili.get("yol")                  # yeni çeviri bitene kadar eskisi yerinde kalır


# ---------------------------------------------------------------------------------------------- giriş
def calistir(argv):
    ag.kullanici_ajani(kodi.surum())
    tutamac = int(argv[1])
    sorgu = dict(urllib.parse.parse_qsl((argv[2] if len(argv) > 2 else "").lstrip("?")))
    eylem = sorgu.get("action", "")
    try:
        if eylem in ("search", "manualsearch"):
            ara(tutamac, sorgu)
        elif eylem == "download":
            indir(tutamac, sorgu)
    except Exception as e:
        kodi.kayit("hata: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)), hata=True)
        kodi.bildir(metin.al(metin.B_HATA), str(e), hata=True)
    finally:
        xbmcplugin.endOfDirectory(tutamac)
