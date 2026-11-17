"""Ayarların ve .env dosyasının okunması.

Her ayarın açıklaması config.toml'dadır; buradaki varsayılanlar onun aynısıdır (test_varsayilanlar_config_ile_ayni
denetler). Bölüm ve anahtar sırası da config.toml'daki gibidir.
"""
import copy
import os
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 ve öncesi
    tomllib = None

VARSAYILAN = {
    "klasorler": {"kaynak": "kaynak", "ceviri": "ceviri", "sozluk": "sozluk", "logs": "logs", "uzerine_yaz": False},
    "ceviri": {"hedef_dil": "tr", "cikti_alfabesi": "latin", "birimler": "oldugu_gibi", "bolunmus_cumle": "dagit"},
    "model": {"api_adresi": "https://api.openai.com/v1", "model": "gpt-6-luna", "anahtar_degiskeni": "OPENAI_API_KEY",
              "dusunme_seviyesi": "low", "sicaklik": -1.0, "es_zamanli_istek": 64, "zaman_asimi": 180.0, "json_modu": True,
              "fiyat_girdi": 0.10, "fiyat_onbellek": 0.01, "fiyat_cikti": 0.50, "ek": {}},
    "modlar": {"kurgu_dili": "orijinal", "kurgu_anlami": "cevir", "anime_ekleri": "orijinal", "sarki": "orijinal",
               "ses_sozcukleri": "orijinal", "ekran_yazisi": "cevir",
               "koseli_not": "sil", "parantez_not": "sil", "isim_etiketi": "sil", "reklam": "sil",
               "italik": "korunsun", "konusmaci_tiresi": "korunsun", "sarki_isareti": "korunsun", "ustte_gosterme": "sil"},
    "hitap": {"acik": True, "tablo": True, "kanit": True, "oy": 3},
    "unvan": {"acik": True, "tablo": True, "model_karari": True, "denetim": "duzelt"},
    "sozluk": {"otomatik": True, "terimler": True, "kesif": True, "anlam_notu": True, "karakterler": True,
               "sahne_ozeti": True, "kurgu_tespiti": True, "oy": 3,
               "dusunme_seviyesi": "low", "karsilik_dusunme": "medium", "hitap_dusunme": "low", "sahne_dusunme": "low",
               "aday_esigi": 2, "analiz_token": 60000, "sahne_token": 4500},
    "kontrol": {"supheli_satir_kontrolu": True, "kayma_kontrolu": True,
                "tekrar_eden_sahne": True, "deneme_sayisi": 3, "deneme_satir": 0},
    "cikti": {"satir_karakter": 42, "saniye_basina_karakter": 25, "sure_uzat_azami": 1.5},
    "parca": {"altyazi_sayisi": 0, "baglam_once": 8, "baglam_sonra": 4, "sahne_boslugu": 2.5},
    "girdi": {"tekrarlari_birlestir": True, "sub_fps": 23.976, "ass_sarki_stili": True, "ass_yazi_stili": True,
              "cift_dil": "referans"},
    # Unvan tabloları, hedef dile göre: kaynakta geçen sözcük -> çeviride yazılacak olan ("{ad}" yoksa ad arkasına gelir).
    "unvanlar": {"tr": {"Lord": "Lord", "Master": "Efendi", "Lady": "Leydi", "Sir": "Sör", "Mr": "Bay", "Mrs": "Bayan",
                        "Ms": "Bayan", "Miss": "Bayan", "King": "Kral", "Queen": "Kraliçe", "Prince": "Prens",
                        "Princess": "Prenses", "President": "Başkan", "Senator": "Senatör", "Governor": "Vali",
                        "Judge": "Yargıç", "Sheriff": "Şerif", "Doctor": "Doktor", "Dr": "Dr.", "Professor": "Profesör",
                        "Nurse": "Hemşire", "Coach": "Koç", "Farmer": "Çiftçi", "General": "General", "Colonel": "Albay",
                        "Major": "Binbaşı", "Captain": "Kaptan", "Lieutenant": "Teğmen", "Sergeant": "Çavuş",
                        "Commander": "Komutan", "Chief": "Şef", "Officer": "Memur", "Agent": "Ajan",
                        "Detective": "Dedektif", "Inspector": "Müfettiş", "隊長": "Kaptan"}},
}

# Sayı ayarlarının alt sınırı: altındaki değer çalışırken çökme ya da sessizce çevrilmemiş çıktı demekti.
EN_AZ = {("model", "es_zamanli_istek"): 1, ("model", "zaman_asimi"): 5, ("hitap", "oy"): 1, ("sozluk", "oy"): 1,
         ("sozluk", "aday_esigi"): 1, ("sozluk", "analiz_token"): 1000, ("sozluk", "sahne_token"): 500,
         ("kontrol", "deneme_sayisi"): 1, ("kontrol", "deneme_satir"): 0, ("cikti", "satir_karakter"): 10,
         ("cikti", "saniye_basina_karakter"): 5, ("cikti", "sure_uzat_azami"): 0, ("parca", "altyazi_sayisi"): 0,
         ("parca", "baglam_once"): 0, ("parca", "baglam_sonra"): 0, ("parca", "sahne_boslugu"): 0,
         ("girdi", "sub_fps"): 1}

UC_MOD = ("orijinal", "orijinal_ceviri", "ceviri")
GECERLI = {
    ("ceviri", "cikti_alfabesi"): ("latin", "serbest"),
    ("ceviri", "birimler"): ("oldugu_gibi", "metrik"),
    ("ceviri", "bolunmus_cumle"): ("dagit", "kapali"),
    ("modlar", "kurgu_dili"): UC_MOD,
    ("modlar", "kurgu_anlami"): ("cevir", "sil"),
    ("modlar", "anime_ekleri"): UC_MOD,
    ("modlar", "sarki"): UC_MOD,
    ("modlar", "ses_sozcukleri"): ("orijinal", "cevir"),
    ("modlar", "ekran_yazisi"): ("cevir", "birak", "sil"),
    ("modlar", "koseli_not"): ("cevir", "sil"),
    ("modlar", "parantez_not"): ("cevir", "sil"),
    ("modlar", "isim_etiketi"): ("kalsin", "sil"),
    ("modlar", "reklam"): ("kalsin", "sil"),
    ("modlar", "italik"): ("korunsun", "sil"),
    ("modlar", "konusmaci_tiresi"): ("korunsun", "sil"),
    ("modlar", "sarki_isareti"): ("korunsun", "sil"),
    ("modlar", "ustte_gosterme"): ("korunsun", "sil"),
    ("unvan", "denetim"): ("duzelt", "sor", "kapali"),
    ("girdi", "cift_dil"): ("referans", "asil", "ikinci", "kapali"),
}


class AyarHatasi(Exception):
    pass


def env_yukle(yol=".env"):
    """KEY=DEĞER satırlarını ortam değişkenlerine yazar. .env, Windows'taki eski değişkenin önüne geçer."""
    if not os.path.exists(yol):
        return
    with open(yol, encoding="utf-8-sig", errors="replace") as f:   # eski kodlamalı açıklama satırı açılışı düşürmesin
        for satir in f:
            satir = satir.strip()
            if not satir or satir.startswith("#") or "=" not in satir:
                continue
            ad, deger = satir.split("=", 1)
            os.environ[ad.strip()] = deger.strip().strip('"').strip("'")


def ayar_yukle(yol="config.toml"):
    if tomllib is None:
        raise AyarHatasi("Python 3.11 veya üstü gerekli (config.toml okumak için).")
    if not os.path.exists(yol):
        raise AyarHatasi(f"Ayar dosyası bulunamadı: {yol}")
    try:
        with open(yol, encoding="utf-8-sig") as f:      # Not Defteri'nin BOM'u da okunur
            metin = f.read()
    except UnicodeDecodeError:
        raise AyarHatasi(f"{os.path.basename(yol)} okunamadı: dosya UTF-8 olarak kaydedilmeli") from None
    try:
        veri = tomllib.loads(metin)
    except tomllib.TOMLDecodeError as e:
        raise AyarHatasi(f"{os.path.basename(yol)} okunamadı (yazım hatası): {e}") from None
    return ayar_birlestir(veri)


# Kaldırılan ayarlar. hizli_satiri_kisalt: okumaya süresi yetmeyen çeviri modelden kısalttırılıyordu; kısalırken
# anlam gidiyordu (nesne, özne düşüyordu). Artık yalnız ekranda kalma süresi uzatılır (sure_uzat_azami).
KALDIRILAN = {("kontrol", "hizli_satiri_kisalt")}


def ayar_birlestir(ustune=None):
    """Varsayılanların üzerine verilen sözlüğü işler ve doğrular."""
    ayar = copy.deepcopy(VARSAYILAN)
    for bolum, degerler in (ustune or {}).items():
        if bolum not in ayar or not isinstance(degerler, dict):
            raise AyarHatasi(f"Bilinmeyen ayar bölümü: [{bolum}]")
        for ad, deger in degerler.items():
            if bolum == "unvanlar":             # dil kodu -> tablo; satır eklenebilir, yeni dil eklenebilir
                if not isinstance(deger, dict):
                    raise AyarHatasi("[unvanlar] hedef dile göre yazılır: satırları [unvanlar.tr] başlığının altına taşı")
                ayar[bolum].setdefault(ad.lower(), {}).update({str(k): str(v) for k, v in deger.items()})
                continue
            if (bolum, ad) in KALDIRILAN:       # eski config.toml'da duruyor olabilir: sessizce geçilir
                continue
            if ad not in ayar[bolum]:
                raise AyarHatasi(f"Bilinmeyen ayar: [{bolum}] {ad}")
            ayar[bolum][ad] = _tur_denetle(bolum, ad, deger, ayar[bolum][ad])
    for (bolum, ad), secenekler in GECERLI.items():
        if ayar[bolum][ad] not in secenekler:
            raise AyarHatasi(
                f"[{bolum}] {ad} = {ayar[bolum][ad]!r} geçersiz. Seçenekler: {', '.join(secenekler)}")
    for (bolum, ad), en_az in EN_AZ.items():
        if ayar[bolum][ad] < en_az:
            raise AyarHatasi(f"[{bolum}] {ad} = {ayar[bolum][ad]!r}: en az {en_az} olmalı")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?", ayar["ceviri"]["hedef_dil"]):
        raise AyarHatasi(f"[ceviri] hedef_dil = {ayar['ceviri']['hedef_dil']!r}: dilin kodu yazılır (tr, en, de...)")
    return ayar


def _tur_denetle(bolum, ad, deger, varsayilan):
    """Değer varsayılanla aynı türden olmalı (sayı yerine tırnaklı yazı, true yerine 1 gibi yazım hatası çalışırken
    değil, açılışta anlaşılsın). Ondalık yerine tam sayı yazılabilir; tam sayı isteyen ayara "3.0" yazılırsa 3 sayılır,
    "2.5" ise reddedilir (deneme sayısı, satır sayısı gibi). Denetlenmiş değeri döndürür."""
    if isinstance(varsayilan, int) and not isinstance(varsayilan, bool) and isinstance(deger, float):
        if not deger.is_integer():
            raise AyarHatasi(f"[{bolum}] {ad} = {deger!r}: tam sayı olmalı")
        deger = int(deger)
    if isinstance(varsayilan, bool):
        uygun, tur = isinstance(deger, bool), "true ya da false"
    elif isinstance(varsayilan, (int, float)):                  # true / false sayı yerine geçmez
        uygun, tur = isinstance(deger, (int, float)) and not isinstance(deger, bool), "sayı"
    elif isinstance(varsayilan, str):
        uygun, tur = isinstance(deger, str), "tırnak içinde yazı"
    else:
        uygun, tur = isinstance(deger, dict), "tablo"
    if not uygun:
        raise AyarHatasi(f"[{bolum}] {ad} = {deger!r}: {tur} olmalı")
    return deger


def hedef_kodu(ayar):
    """Hedef dilin kısa kodu ("pt-BR" -> "pt"): kurallar ve unvan tablosu buna göre seçilir."""
    return ayar["ceviri"]["hedef_dil"].lower().replace("_", "-").split("-")[0]


# Dil kodu -> (İngilizce adı, kendi dilindeki adı). Önce kodun tamamı ("pt-br"), sonra kısa kodu aranır; tabloda olmayan
# kod modele olduğu gibi yazılır.
DILLER = {"tr": ("Turkish", "Türkçe"), "en": ("English", "English"), "de": ("German", "Deutsch"),
          "fr": ("French", "Français"), "es": ("Spanish", "Español"), "it": ("Italian", "Italiano"),
          "pt": ("Portuguese", "Português"), "pt-br": ("Brazilian Portuguese", "Português do Brasil"),
          "ru": ("Russian", "Русский"), "ar": ("Arabic", "العربية"),
          "ja": ("Japanese", "日本語"), "ko": ("Korean", "한국어"), "zh": ("Chinese", "中文"), "nl": ("Dutch", "Nederlands"),
          "pl": ("Polish", "Polski"), "el": ("Greek", "Ελληνικά"), "az": ("Azerbaijani", "Azərbaycanca"),
          "fa": ("Persian", "فارسی"), "uk": ("Ukrainian", "Українська"), "sv": ("Swedish", "Svenska"),
          "da": ("Danish", "Dansk"), "no": ("Norwegian", "Norsk"), "fi": ("Finnish", "Suomi"),
          "cs": ("Czech", "Čeština"), "hu": ("Hungarian", "Magyar"), "ro": ("Romanian", "Română"),
          "bg": ("Bulgarian", "Български"), "he": ("Hebrew", "עברית"), "hi": ("Hindi", "हिन्दी"),
          "id": ("Indonesian", "Bahasa Indonesia"), "vi": ("Vietnamese", "Tiếng Việt"), "th": ("Thai", "ไทย")}


def _dil_kaydi(ayar):
    """(İngilizce adı, kendi adı): önce kodun tamamı ("pt-BR" Brezilya Portekizcesi), sonra kısa kodu."""
    tam = ayar["ceviri"]["hedef_dil"].lower().replace("_", "-")
    return DILLER.get(tam) or DILLER.get(hedef_kodu(ayar)) or (None, ayar["ceviri"]["hedef_dil"])


def yerel_dil_adi(ayar):
    """Hedef dilin kendi dilindeki adı ("Türkçe"); tabloda yoksa kodu."""
    return _dil_kaydi(ayar)[1]


def dil_adi(ayar):
    """Modele yazılan hedef dil adı: İngilizce adı ve kendi adı birlikte ("Turkish (Türkçe)"). Yalnızca yerel ad bazı
    isteklerde gözden kaçıyordu."""
    ing, yerel = _dil_kaydi(ayar)
    return yerel if not ing or ing == yerel else f"{ing} ({yerel})"


def acik(ayar, bolum, parca):
    """Ana anahtarı olan bölümlerde ([hitap], [unvan]) bir parça çalışıyor mu."""
    return bool(ayar[bolum]["acik"] and ayar[bolum][parca])


def unvan_tablosu(ayar):
    """Hedef dilin unvan tablosu: kaynak sözcük -> kalıp ("Kaptan {ad}"). Tablo kapalıysa ya da o dil için
    tablo yazılmamışsa boş; o zaman kararı model verir."""
    if not acik(ayar, "unvan", "tablo"):
        return {}
    return {k: (v if "{ad}" in v else v + " {ad}") for k, v in (ayar["unvanlar"].get(hedef_kodu(ayar)) or {}).items()}


def unvan_sozcukleri(ayar):
    """Bilinen unvan sözcükleri (bütün tabloların sol tarafı). Aday bulurken adı unvandan ayırmaya yarar;
    bunlar başka yerde sıradan sözcük de olduğundan ("major problem") sözlüğe tek başına terim olarak girmez."""
    return {k for tablo in ayar["unvanlar"].values() for k in tablo}
