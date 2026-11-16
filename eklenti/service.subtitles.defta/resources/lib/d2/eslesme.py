"""Anime eşleme listesi (AniBridge, MIT lisanslı, her gün güncellenen açık veri).

TMDB/TVDB sezon-bölüm ile AniList kayıt-bölüm arasında çeviri yapar: Jimaku'nun AniList kaydını bulmak ve AniList'e
göre numaralanmış bir bölümü (Otaku) OpenSubtitles ve SubDL'in sezon/bölümüne çevirmek için. İlk anime açılışında
indirilir; ayardaki sıklıkla (varsayılan haftalık) ya da "Şimdi güncelle" ile yenilenir. Liste (yaklaşık 9 MB)
indirilince yalnız gereken eşlemelerden küçük bir dizin çıkarılır; aramalar o dizini okur.
"""
import json
import os
import re
import time

from . import ag, kayit, metin

ADRES = "https://github.com/anibridge/anibridge-mappings/releases/latest/download/mappings.min.json"
_dizin_onbellek = {}


DIZIN_SURUMU = 2


def _aralik(metin_):
    """Bölüm aralığı "x", "x-y" ya da açık uçlu "x-": (baş, son); son None = açık uçlu."""
    m = re.fullmatch(r"\s*(\d+)\s*(-\s*(\d*))?\s*", str(metin_))
    if not m:
        return None
    bas = int(m.group(1))
    if m.group(2) is None:
        return bas, bas
    return bas, (int(m.group(3)) if m.group(3) else None)


def _hedef(metin_):
    """Hedef aralığı "x[-y][,x2[-y2]...][|oran]": ([(baş, son), ...], oran). Oran n > 0: her kaynak bölüm n hedef
    bölüme yayılır; -n: n kaynak bölüm bir hedef bölümdür."""
    yazi, oran = str(metin_), 1.0
    if "|" in yazi:
        yazi, o = yazi.rsplit("|", 1)
        try:
            oran = float(o)
        except ValueError:
            return None
        if not oran:
            return None
    parcalar = [_aralik(p) for p in yazi.split(",")]
    if not parcalar or None in parcalar:
        return None
    return [list(p) for p in parcalar], oran


def cevir(bolum, bas, son, parcalar, oran):
    """Kaynak bölümünün hedefteki numarası (aralıkta değilse None)."""
    if bolum < bas or (son is not None and bolum > son):
        return None
    k = bolum - bas
    k = int(k * oran) if oran > 0 else int(k // -oran)
    for p_bas, p_son in parcalar:
        uzunluk = None if p_son is None else p_son - p_bas + 1
        if uzunluk is None or k < uzunluk:
            return p_bas + k
        k -= uzunluk
    return None


def dizin_cikar(veri):
    """Ham listeden dizin: "tmdb:<kimlik>:<sezon>" / "tvdb:..." -> [[baş, son, anilist, parçalar, oran]];
    "anilist:<kimlik>" -> [[baş, son, "tmdb"|"tvdb", dizi kimliği, sezon, parçalar, oran]] (son None: açık uçlu)."""
    dizin = {}
    for kaynak, hedefler in veri.items():
        if not isinstance(hedefler, dict):
            continue
        m = re.fullmatch(r"(tmdb|tvdb)_show:(\d+):s(\d+)", kaynak)
        if m:
            for hedef, araliklar in hedefler.items():
                h = re.fullmatch(r"anilist:(\d+)", hedef)
                if not h or not isinstance(araliklar, dict):
                    continue
                for k_ar, h_ar in araliklar.items():
                    ka, ha = _aralik(k_ar), _hedef(h_ar)
                    if ka and ha:
                        dizin.setdefault(f"{m.group(1)}:{m.group(2)}:{int(m.group(3))}", []).append(
                            [ka[0], ka[1], int(h.group(1)), ha[0], ha[1]])
            continue
        m = re.fullmatch(r"anilist:(\d+)", kaynak)
        if m:
            for hedef, araliklar in hedefler.items():
                h = re.fullmatch(r"(tmdb|tvdb)_show:(\d+):s(\d+)", hedef)
                if not h or not isinstance(araliklar, dict):
                    continue
                for k_ar, h_ar in araliklar.items():
                    ka, ha = _aralik(k_ar), _hedef(h_ar)
                    if ka and ha:
                        dizin.setdefault("anilist:" + m.group(1), []).append(
                            [ka[0], ka[1], h.group(1), int(h.group(2)), int(h.group(3)), ha[0], ha[1]])
    return dizin


def _anilist_sayisi(dizin):
    sayi = {k for k in dizin if k.startswith("anilist:")}
    for k, girdiler in dizin.items():
        if not k.startswith("anilist:") and k != "$surum":
            sayi.update(f"anilist:{g[2]}" for g in girdiler)
    return len(sayi)


def _yol(durum, ad):
    return durum.yol("esleme", ad)


def guncelle(durum, bildir=False, zorla=False, gun=7, bildirici=None, kayit_yaz=None):
    """Liste eskiyse (ya da zorla) indirip dizini yazar. Dönüş: True (güncellendi)."""
    bilgi = kayit.oku(_yol(durum, "bilgi.json"), {}) or {}
    guncel = bilgi.get("surum") == DIZIN_SURUMU and os.path.exists(_yol(durum, "dizin.json"))
    if not zorla and guncel and time.time() - bilgi.get("zaman", 0) < gun * 86400:
        return False
    def olmadi(neden):
        if kayit_yaz:
            kayit_yaz(f"eşleme listesi indirilemedi: {neden}")
        if bildir and bildirici:
            bildirici(metin.al(metin.B_HATA), str(neden), True)
        return False
    try:
        _, veri, _ = ag.istek("GET", ADRES, ham=True, zaman_asimi=120)
    except (ag.AgHatasi, ag.YanitHatasi) as e:
        return olmadi(e)
    # Bozuk (yarım, HTML hata sayfası) ya da boş liste çalışan dizinin yerine geçmez; sonraki açılışta yeniden denenir
    try:
        ham = json.loads(veri.decode("utf-8"))
        del veri
        tarih = str((ham.get("$meta") or {}).get("generated_on") or "")[:10]
        dizin = dizin_cikar(ham)
        del ham
    except (ValueError, AttributeError, TypeError):
        return olmadi("liste okunamadı")
    sayi = _anilist_sayisi(dizin)
    if not sayi:
        return olmadi("listede eşleme yok")
    kayit.yaz(_yol(durum, "dizin.json"), dizin)
    kayit.yaz(_yol(durum, "bilgi.json"), {"zaman": time.time(), "tarih": tarih, "sayi": sayi, "surum": DIZIN_SURUMU})
    _dizin_onbellek.clear()
    if kayit_yaz:
        kayit_yaz(f"eşleme listesi güncellendi: {tarih}, {sayi} AniList kaydı")
    if bildir and bildirici:
        bildirici(metin.al(metin.B_ESLEME_GUNCEL), metin.al(metin.B_ESLEME_YAZI, tarih=tarih, sayi=sayi), False)
    return True


def _dizin(durum):
    yol = _yol(durum, "dizin.json")
    try:
        zaman = os.path.getmtime(yol)
    except OSError:
        return {}
    if (kayit.oku(_yol(durum, "bilgi.json"), {}) or {}).get("surum") != DIZIN_SURUMU:
        return {}                               # eski biçim: yeniden indirilene kadar kullanılmaz
    if _dizin_onbellek.get("zaman") != zaman:
        _dizin_onbellek.update({"zaman": zaman, "veri": kayit.oku(yol, {}) or {}})
    return _dizin_onbellek["veri"]


def anilist_bul(durum, video):
    """İzlenen bölümün (TMDB/TVDB sezon-bölüm) AniList kaydı ve o kayıttaki bölümü; bilinmiyorsa None."""
    if video.tur != "dizi" or video.bolum is None:
        return None
    dizin = _dizin(durum)
    for tur, kimlik in (("tmdb", video.tmdb), ("tvdb", video.tvdb)):
        if not kimlik:
            continue
        sezon = video.sezon if video.sezon is not None else 1
        for bas, son, anilist, parcalar, oran in dizin.get(f"{tur}:{kimlik}:{sezon}", []):
            hedef = cevir(video.bolum, bas, son, parcalar, oran)
            if hedef is not None:
                return str(anilist), hedef
    return None


def sezon_bolum_bul(durum, anilist, bolum):
    """AniList kayıt-bölümünden TMDB/TVDB dizi kimliği, sezon ve bölüm: (tür, kimlik, sezon, bölüm) ya da None."""
    for bas, son, tur, kimlik, sezon, parcalar, oran in _dizin(durum).get(f"anilist:{anilist}", []):
        hedef = cevir(bolum, bas, son, parcalar, oran)
        if hedef is not None:
            return tur, str(kimlik), sezon, hedef
    return None


def uygula(durum, video):
    """Oynatan eklenti yalnız AniList kimliği verdiyse (Otaku) sitelerin sezon/bölümü eşleme listesinden bulunur."""
    if not video.anilist or video.tur != "dizi" or video.bolum is None or (video.tmdb or video.tvdb):
        return
    bulunan = sezon_bolum_bul(durum, video.anilist, video.bolum)
    if bulunan:
        tur, kimlik, sezon, bolum = bulunan
        setattr(video, tur, kimlik)
        video.anilist_bolum = video.bolum
        video.sezon, video.bolum = sezon, bolum
