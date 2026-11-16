"""Gizli bilgilerin kayda, ekrana ve dosya adına sızmaması.

Anahtarlar, parolalar ve oturum jetonları kayıtta yalnız son 4 karakteriyle görünür; anahtarın yarım kalmış uzun
bir parçası hiç görünmez. Akış adresleri (debrid jetonu
taşıyabilir) hiçbir yere yazılmaz. Hiçbir adresin yolu ya da sorgusu kayda geçmez (indirme yolu jeton, sorgu anahtar
taşıyabilir): kayıtta yalnız sunucu adı kalır.
"""
import re
import threading

_kilit = threading.Lock()
_degerler = set()

_ADRES = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,9})://([^\s/'\"<>|?#]+)([^\s'\"<>|]*)(\|[^\s'\"<>]*)?")
_SORGU_ANAHTARI = re.compile(r"(?i)\b((?:api_key|apikey|api-key|x-api-key|token|access_token|key|password|"
                             r"parola|authorization)\s*[=:]\s*)(?:bearer\s+)?[^&\s'\",;]+")
# Kayıtlı bir anahtarın ya da jetonun bütünü yazıda geçmese de uzun bir parçası gizlenir (ucu kesilmiş adres, yarım
# kopya). Kısa değerlerde (parola) parça aranmaz: sıradan sözcükleri bozmasın.
_PARCA = 8
_PARCALI_DEGER = 16


def kaydet(*degerler):
    """Kayıtta gizlenecek değerleri ekler (anahtar, parola, jeton). Kısa değerler eklenmez: sıradan sözcükleri
    bozmasın."""
    with _kilit:
        for d in degerler:
            if d and isinstance(d, str) and len(d.strip()) >= 6:
                _degerler.add(d.strip())


def _adres(m):
    sema, sunucu, yol = m.group(1).lower(), m.group(2), m.group(3) or ""
    sunucu_adi = sunucu.rsplit("@", 1)[-1]                    # kullanıcı:parola@ kısmı atılır
    return f"{sema}://{sunucu_adi}/…" if yol or m.group(4) else f"{sema}://{sunucu_adi}"


def _parcalari_gizle(s, d):
    i = 0
    while i <= len(d) - _PARCA:
        yer = s.find(d[i:i + _PARCA])
        if yer < 0:
            i += 1
            continue
        n = _PARCA
        while i + n < len(d) and yer + n < len(s) and s[yer + n] == d[i + n]:
            n += 1
        s = s[:yer] + "…" + s[yer + n:]          # aynı parça başka yerde de geçebilir: i ilerlemeden yeniden bakılır
    return s


def gizle(metin):
    s = str(metin)
    with _kilit:
        degerler = sorted(_degerler, key=len, reverse=True)
    for d in degerler:
        if d in s:
            s = s.replace(d, "…" + d[-4:])
    for d in degerler:
        if len(d) >= _PARCALI_DEGER:
            s = _parcalari_gizle(s, d)
    s = _SORGU_ANAHTARI.sub(lambda m: m.group(1) + "…", s)
    return _ADRES.sub(_adres, s)
