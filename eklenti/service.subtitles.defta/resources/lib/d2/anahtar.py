"""Anahtarların .env ya da .txt dosyasından okunması ve ayarlardakilerle karşılaştırılması.

Anahtar adları büyük/küçük harf ve küçük yazım farklarına dayanıklı tanınır (OPENAI_API_KEY, OpenSubtitle_Key,
SUB_DL_KEY...). Dosya her açılışta okunmaz: bir kez okunur, ayarlara kaydedilir. Anahtarlar tek yerde, eklentinin
ayarlarında durur; ekranda yalnız son 4 karakteri görünür.
"""
import os
import re
from urllib.parse import quote, unquote

# ayar adı -> (site adı, tanıma kuralı: normalleştirilmiş adda geçmesi gerekenler, tür)
ALANLAR = (
    ("openai_anahtar", "OpenAI", ("openai", "openaikey", "chatgpt"), "anahtar"),
    ("os_anahtar", "OpenSubtitles", ("opensub", "opensubtitle", "osapi", "ostoken"), "anahtar"),
    ("os_kullanici", "OpenSubtitles", ("opensub", "opensubtitle"), "kullanici"),
    ("os_parola", "OpenSubtitles", ("opensub", "opensubtitle"), "parola"),
    ("subdl_anahtar", "SubDL", ("subdl",), "anahtar"),
    ("subsource_anahtar", "SubSource", ("subsource", "subsrc"), "anahtar"),
    ("jimaku_anahtar", "Jimaku", ("jimaku",), "anahtar"),
)
_KULLANICI = ("user", "username", "kullanici", "login", "email")
_PAROLA = ("pass", "password", "parola", "sifre", "pwd")
# Anahtar olmayan ayarlar (OPENSUBTITLES_USER_AGENT, OPENSUBTITLES_APP_NAME, OPENAI_ORG_ID, OPENAI_BASE_URL...)
_DISLA = ("agent", "appname", "app", "url", "host", "endpoint", "base", "model", "org", "project", "version",
          "lang", "timeout", "proxy", "limit")
_ANAHTAR_IZI = ("key", "token", "secret", "anahtar", "api")


def _duz(ad):
    return re.sub(r"[^a-z0-9]", "", ad.lower().replace("ı", "i").replace("ş", "s").replace("ğ", "g"))


def tur_bul(ad):
    """Satırdaki anahtar adının hangi ayara gittiği (ayar adı) ya da None."""
    d = _duz(ad)
    if not d or any(x in d for x in _DISLA):
        return None
    if any(k in d for k in _PAROLA):
        tur = "parola"
    elif any(k in d for k in _KULLANICI):
        tur = "kullanici"
    else:
        tur = "anahtar"
    anahtar_izi = any(k in d for k in _ANAHTAR_IZI)
    for ayar_adi, _, tanimlar, alan_turu in ALANLAR:
        # "os..." kısaltmaları yalnız adın başında sayılır: "PHOTOS_API_KEY" OpenSubtitles anahtarı değildir
        if alan_turu == tur and any(d.startswith(t) if t.startswith("os") else t in d for t in tanimlar):
            if tur == "anahtar" and not (anahtar_izi or d in tanimlar):
                continue                        # yalnız "SUBDL=..." gibi ad ya da içinde key/token geçen ad
            return ayar_adi
    if tur == "anahtar" and anahtar_izi and ("subtitle" in d and "open" in d):
        return "os_anahtar"
    return None


def dosya_bul(yol):
    """Seçilen yol klasörse içindeki .env ya da .txt dosyası (önce .env)."""
    if not os.path.isdir(yol):
        return yol
    adlar = sorted(os.listdir(yol))
    for ad in adlar:
        if ad.lower() == ".env" or ad.lower().endswith(".env"):
            return os.path.join(yol, ad)
    for ad in adlar:
        if ad.lower().endswith(".txt"):
            return os.path.join(yol, ad)
    return ""


def oku(yol):
    """Dosyadaki tanınan anahtarlar: ayar adı -> değer."""
    with open(yol, encoding="utf-8-sig", errors="replace") as f:
        return oku_metin(f.read())


def oku_metin(metin):
    """Satırlar AD=DEĞER ya da AD: DEĞER; # ile başlayan satır ve değerden sonraki "# ..." yorumu atlanır.
    Dönüş: ayar adı -> değer."""
    sonuc = {}
    for satir in metin.lstrip("\ufeff").splitlines():
        satir = satir.strip()
        if not satir or satir.startswith("#"):
            continue
        satir = re.sub(r"^\s*export\s+", "", satir)
        m = re.match(r"^([A-Za-z0-9_.\- ]{2,60}?)\s*[=:]\s*(.+)$", satir)
        if not m:
            continue
        ad, deger = m.group(1), m.group(2).strip()
        tirnakli = re.match(r"""^(["'])(.*?)\1\s*(?:#.*)?$""", deger)
        if tirnakli:
            deger = tirnakli.group(2).strip()
        else:                               # tırnaksız değerde boşluktan sonraki "# ..." satır sonu yorumudur
            deger = re.sub(r"\s+#.*$", "", deger).strip('"').strip("'").strip()
        if not deger:
            continue
        ayar_adi = tur_bul(ad)
        if ayar_adi and ayar_adi not in sonuc:
            sonuc[ayar_adi] = deger
    return sonuc


def karsilastir(dosyadakiler, ayardakiler):
    """Her alan için (ayar adı, site, durum, eski son4, yeni son4). durum: ayni / degisti / eklendi / dosyada_yok / yok."""
    sonuc = []
    for ayar_adi, site, _, tur in ALANLAR:
        yeni, eski = dosyadakiler.get(ayar_adi, ""), ayardakiler.get(ayar_adi, "")
        goster = (lambda d: d[-4:]) if tur != "kullanici" else (lambda d: d)
        if yeni and eski:
            durum = "ayni" if yeni == eski else "degisti"
        elif yeni:
            durum = "eklendi"
        elif eski:
            durum = "dosyada_yok"
        else:
            durum = "yok"
        sonuc.append((ayar_adi, site + ("" if tur == "anahtar" else f" ({'kullanıcı adı' if tur == 'kullanici' else 'parola'})"),
                      durum, goster(eski) if eski else "", goster(yeni) if yeni else ""))
    return sonuc


def ag_adresi(yazi):
    """Elle yazılan ağ adresini ayırır: (kimliksiz adres, kullanıcı adı, parola); adres değilse None.
    "\\\\sunucu\\paylaşım\\anahtar.txt", "//sunucu/paylaşım" ve "sunucu/paylaşım" → "smb://sunucu/paylaşım…". Adresin
    içine yazılmış "kullanıcı:parola@" ayrılır: parolalı adres hiçbir yere kaydedilmez."""
    s = (yazi or "").strip().replace("\\", "/")
    if "://" not in s:
        s = "smb://" + s.lstrip("/")
    sema, kalan = s.split("://", 1)
    kullanici = parola = ""
    sunucu, egik, yol = kalan.partition("/")
    if "@" in sunucu:
        kimlik, sunucu = sunucu.rsplit("@", 1)
        kullanici, _, parola = kimlik.partition(":")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", sema) or not sunucu.strip():
        return None
    return sema.lower() + "://" + sunucu.strip() + egik + yol, unquote(kullanici), unquote(parola)


def ag_adresi_kimlikli(adres, kullanici, parola=""):
    """Kullanıcı adı ve parolayı adrese ekler (Kodi böyle bekler). Dönen değer kayda ya da ekrana yazılmaz."""
    if not kullanici:
        return adres
    sema, kalan = adres.split("://", 1)
    return sema + "://" + quote(kullanici, safe="") + (":" + quote(parola, safe="") if parola else "") + "@" + kalan
