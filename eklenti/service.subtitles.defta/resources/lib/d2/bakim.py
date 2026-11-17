"""Bakım: önbellek temizliği, ayrı silme, fabrika ayarları, kendiliğinden temizlik, dışa aktarma.

Kalıcı olanlar (kendiliğinden silinmez): çeviriler, bütün sözlükler, seçim ve kaymış işaretleri.
Geçici dosyalar 6 saat, kalıntılar (indirilen altyazılar, kayıtlar) 30 gün sonra silinir; yarım çeviri 30 gün durur.
Fabrika ayarları kullanıcının .env/.txt dosyasına ve dışa aktarılan dosyalara dokunmaz.
"""
import os
import re
import shutil
import time

from . import kayit

KALEMLER = {                    # kalem -> profil içindeki klasörler
    "ceviriler": ("ceviriler",),
    "sozlukler": ("sozlukler",),
    "yarim": ("isler",),
    "indirilen": ("indirilen",),
    "kayitlar": ("kayitlar",),
    "gecici": ("gecici",),
    "isaretler": ("secim", "kaymis", "tercih"),
    "esleme": ("esleme",),
}
GECICI_OMRU = 6 * 3600
KALINTI_OMRU = 30 * 86400


def _boyut(yol):
    toplam, sayi = 0, 0
    for kok, _, adlar in os.walk(yol):
        for ad in adlar:
            try:
                toplam += os.path.getsize(os.path.join(kok, ad))
                sayi += 1
            except OSError:
                pass
    return toplam, sayi


def _sil(yol):
    if os.path.isdir(yol):
        shutil.rmtree(yol, ignore_errors=True)
    elif os.path.exists(yol):
        kayit.sil(yol)


def temizle(durum, kalemler):
    """Seçilen kalemleri siler. Dönüş: {kalem: (dosya sayısı, bayt)} ve çeviri/sözlük sayıları."""
    sonuc = {}
    for kalem in kalemler:
        bayt = sayi = 0
        for klasor in KALEMLER[kalem]:
            yol = durum.yol(klasor)
            b, n = _boyut(yol)
            if kalem == "ceviriler":
                n = sum(len([a for a in os.listdir(os.path.join(yol, k)) if a.endswith(".json")])
                        for k in (os.listdir(yol) if os.path.isdir(yol) else []) if os.path.isdir(os.path.join(yol, k)))
            elif kalem == "sozlukler":
                n = len([a for a in os.listdir(yol) if a.endswith(".json")]) if os.path.isdir(yol) else 0
            bayt, sayi = bayt + b, sayi + n
            _sil(yol)
        if kalem == "sozlukler":
            _yarim_islerin_sozlugunu_sil(durum)
        sonuc[kalem] = (sayi, bayt)
    return sonuc


def _yarim_islerin_sozlugunu_sil(durum):
    """Sözlük elle silinince yarım çeviriler baştan başlar (madde 26): başladıkları sözlüğün kopyası ve yarım
    kayıtları silinir."""
    isler = durum.yol("isler")
    for ad in os.listdir(isler) if os.path.isdir(isler) else []:
        kayit.sil(os.path.join(isler, ad, "sozluk_anlik.json"))
        for alt in ("onbellek", "hizli_kayit"):
            _sil(os.path.join(isler, ad, alt))


def fabrika(durum):
    """Profil klasöründe ayar dosyası dışındaki her şeyi siler (ayarları varsayılana döndürmek çağıranın işi)."""
    for ad in os.listdir(durum.kok) if os.path.isdir(durum.kok) else []:
        if ad == "settings.xml":
            continue
        _sil(os.path.join(durum.kok, ad))


def ayar_varsayilanlari(ayar_dosyasi):
    """resources/settings.xml'deki varsayılanlar: ad -> değer (yazı)."""
    with open(ayar_dosyasi, encoding="utf-8") as f:
        metin = f.read()
    sonuc = {}
    for m in re.finditer(r'<setting id="([^"]+)" type="([^"]+)"[^>]*>(.*?)</setting>', metin, re.S):
        ad, tur, govde = m.groups()
        if tur == "action":
            continue
        d = re.search(r"<default>(.*?)</default>", govde, re.S)
        sonuc[ad] = d.group(1) if d else ""
    return sonuc


def kendiliginden(durum, aktif_isler=()):
    """Eskiyenleri siler. Dönüş: (geçici dışındaki silinen dosya sayısı, bayt): bildirimde gösterilir."""
    simdi = time.time()
    sayac = {"sayi": 0, "bayt": 0}

    def eski(yol, omur):
        try:
            return simdi - os.path.getmtime(yol) > omur
        except OSError:
            return False

    def say_sil(yol):
        b, n = _boyut(yol) if os.path.isdir(yol) else (_dosya_boyutu(yol), 1)
        _sil(yol)
        sayac["sayi"] += n
        sayac["bayt"] += b
    for kok, _, adlar in os.walk(durum.yol("gecici")):
        for ad in adlar:
            if eski(os.path.join(kok, ad), GECICI_OMRU):
                kayit.sil(os.path.join(kok, ad))
    secili = durum.secili_yollar()         # seçili altyazı kalıcıdır: eskise de silinmez (yeniden indirmek hak harcar)
    for klasor in ("indirilen",):
        for kok, _, adlar in os.walk(durum.yol(klasor)):
            for ad in adlar:
                yol = os.path.join(kok, ad)
                if eski(yol, KALINTI_OMRU) and os.path.normcase(os.path.normpath(yol)) not in secili:
                    say_sil(yol)
    kayitlar = durum.yol("kayitlar")
    for ad in os.listdir(kayitlar) if os.path.isdir(kayitlar) else []:
        if eski(os.path.join(kayitlar, ad), KALINTI_OMRU):
            say_sil(os.path.join(kayitlar, ad))
    isler = durum.yol("isler")
    for ad in os.listdir(isler) if os.path.isdir(isler) else []:
        # Kaydı (is.json) olmayan klasör, bakım silerken geç biten işin yeniden yarattığı kalıntıdır: kendi zamanına bakılır
        kayit_yolu = os.path.join(isler, ad, "is.json")
        if ad not in aktif_isler and eski(kayit_yolu if os.path.exists(kayit_yolu) else os.path.join(isler, ad),
                                          KALINTI_OMRU):
            say_sil(os.path.join(isler, ad))
    # Eski çeviri sürümleri (yeniden çevrilince Kodi'de açık kalıp silinemeyenler)
    ceviriler = durum.yol("ceviriler")
    for klasor in os.listdir(ceviriler) if os.path.isdir(ceviriler) else []:
        tam = os.path.join(ceviriler, klasor)
        kullanilan = set()
        for ad in os.listdir(tam):
            if ad.endswith(".json"):
                b = kayit.oku(os.path.join(tam, ad), {}) or {}
                kullanilan.update(x for x in (b.get("dosya"), b.get("kaynak_dosya")) if x)
                kullanilan.add(ad)
        for ad in os.listdir(tam):
            if ad not in kullanilan and eski(os.path.join(tam, ad), 86400):
                say_sil(os.path.join(tam, ad))
    return sayac["sayi"], sayac["bayt"]


def _dosya_boyutu(yol):
    try:
        return os.path.getsize(yol)
    except OSError:
        return 0


def disa_ceviriler(durum, hedef_klasor, kopyala, klasor_kur):
    """Çevirileri dizi ve sezon klasörleriyle hedefe kopyalar. kopyala(kaynak, hedef), klasor_kur(yol): Kodi'nin
    dosya işlevleri (SMB adresi de olur). Dönüş: kopyalanan sayısı."""
    sayi, kullanilan = 0, set()
    kok = durum.yol("ceviriler")
    for klasor in os.listdir(kok) if os.path.isdir(kok) else []:
        tam = os.path.join(kok, klasor)
        for ad in os.listdir(tam):
            if not ad.endswith(".json"):
                continue
            b = kayit.oku(os.path.join(tam, ad), {}) or {}
            if not b.get("dosya") or not os.path.exists(os.path.join(tam, b["dosya"])):
                continue
            baslik = _guvenli(b.get("baslik") or "Defta")
            etiket = b.get("bolum_etiketi") or ""
            m = re.match(r"S(\d+)E(\d+)", etiket)
            parcalar = [baslik] + ([f"Sezon {int(m.group(1))}"] if m else [])
            ad_kok = (_guvenli(((b.get("kaynak") or {}).get("adlar") or [""])[0])
                      or _guvenli(f"{baslik} {etiket if etiket != 'film' else ''}"))
            dosya_adi = f"{ad_kok}.{b.get('hedef_dil', 'tr')}.srt"
            yol = hedef_klasor.rstrip("/\\")
            for p in parcalar:
                yol = yol + "/" + p
                klasor_kur(yol + "/")
            if (yol + "/" + dosya_adi).lower() in kullanilan:      # aynı bölümün başka kaynaktan çevirisi
                dosya_adi = f"{ad_kok}.{_guvenli(b.get('kimlik', ''))[-6:]}.{b.get('hedef_dil', 'tr')}.srt"
            kullanilan.add((yol + "/" + dosya_adi).lower())
            if kopyala(os.path.join(tam, b["dosya"]), yol + "/" + dosya_adi):
                sayi += 1
    return sayi


def disa_hata(durum, hedef_klasor, kopyala, klasor_kur, kodi_kayitlari=None):
    """Hata ayıklama: kayıtlar ve sözlükler (anahtar içermezler) tarihli bir klasöre. kodi_kayitlari: dosya adı ->
    Kodi kaydının süzülmüş metni (kodi_kaydini_suz)."""
    hedef = hedef_klasor.rstrip("/\\") + "/defta-hata-" + time.strftime("%Y%m%d-%H%M%S")
    sayi = 0
    for klasor in ("kayitlar", "sozlukler"):
        kok = durum.yol(klasor)
        for k, _, adlar in os.walk(kok):
            goreli = os.path.relpath(k, durum.kok).replace("\\", "/")
            klasor_kur(hedef + "/" + goreli + "/")
            for ad in adlar:
                if kopyala(os.path.join(k, ad), hedef + "/" + goreli + "/" + ad):
                    sayi += 1
    # Son arama listesi (video, adaylar, puanlar; adres yok) ve Kodi kaydının süzülmüş hâli: çökme, senkron işareti
    # ve hız sorularına bunlar olmadan bakılamıyor.
    son_arama = durum.yol("gecici", "son_arama.json")
    if os.path.isfile(son_arama) and kopyala(son_arama, hedef + "/son_arama.json"):
        sayi += 1
    for ad, metin_ in (kodi_kayitlari or {}).items():
        gecici = durum.yol("gecici", ad)
        kayit.metin_yaz(gecici, metin_)
        if kopyala(gecici, hedef + "/" + ad):
            sayi += 1
        kayit.sil(gecici)
    return sayi


_ADRES = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,9}://\S+")
_KAYIT_SATIRI = re.compile(r"\[defta\]| error <|EXCEPTION|Traceback|^\s+File \"|Error:|Exception:|CAddonMgr.*defta|"
                           r"CPythonInvoker|Starting Kodi|Platform:|Running on|application stopped|exiting")
_TARIHLI = re.compile(r"\d{4}-\d\d-\d\d ")
KAYIT_SATIR_SINIRI = 4000


def kodi_kaydini_suz(metin_, gizle=None):
    """Kodi kaydından yalnız eklentinin satırları, hatalar ve açılış bilgisi; adresler (akış adresi jeton taşır) ve
    anahtarlar silinmiş olarak. Çok uzunsa son KAYIT_SATIR_SINIRI satır."""
    secilen = []
    hata_ardi = 0
    for satir in metin_.splitlines():
        if _KAYIT_SATIRI.search(satir):
            if "EXCEPTION" in satir or "Traceback" in satir or " error <" in satir:
                hata_ardi = 12
            else:                           # dökümün "File ..." satırı: ardındaki kod satırı da alınır
                hata_ardi = max(hata_ardi - 1, 1 if 'File "' in satir else 0)
        elif hata_ardi > 0 and not _TARIHLI.match(satir):
            hata_ardi -= 1                  # hata dökümünün devam satırları
        else:
            hata_ardi = 0
            continue
        satir = _ADRES.sub("[adres]", satir)
        secilen.append(gizle(satir) if gizle else satir)
    return "\n".join(secilen[-KAYIT_SATIR_SINIRI:]) + "\n"


def _guvenli(ad):
    return re.sub(r'[\\/:*?"<>|]+', " ", ad or "").strip()[:120]
