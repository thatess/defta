"""İndirilen içerikten altyazı dosyası: arşivden doğru bölümün dosyası, kodlama, doğrulama, kare hızı düzeltmesi.

Dosya gerçekten altyazı mı diye denetlenir (en az 3 altyazı satırı); önce geçici bir ada yazılıp sonra yerine konur.
Sezon paketinde izlenen bölümün dosyası seçilir; animede mutlak bölüm numarası da tanınır. RAR ve 7z açılamaz.
"""
import io
import os
import re
import zipfile

from . import surum

UZANTILAR = (".srt", ".ass", ".ssa", ".vtt", ".sub")
_ZAMAN_SRT = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})([,.])(\d{3})")
_ZAMAN_ASS = re.compile(r"^((?:Dialogue|Comment):\s*[^,]*,)(\d+):(\d{2}):(\d{2})\.(\d{2}),(\d+):(\d{2}):(\d{2})\.(\d{2}),",
                        re.M)
FILM_HIZLARI = (23.976, 24.0, 25.0)
EN_BUYUK_ALTYAZI = 32 * 1024 * 1024     # arşivdeki dosyanın açılmış boyutu (bayt): ZIP bombası belleği doldurmasın


class IndirmeHatasi(Exception):
    pass


def _bayt_coz(veri):
    """Kodlamayı çekirdeğin sırasıyla çözer (UTF-16, UTF-8, Shift-JIS, Windows-1254). Diske yazılmaz: Android'de
    Kodi geçici klasör tanımlamaz."""
    from defta import srt as cekirdek_srt
    return cekirdek_srt.metin_coz(veri)


def _uzanti(ad):
    u = os.path.splitext(ad or "")[1].lower()
    return u if u in UZANTILAR else ""


def _sdh_mi(ad):
    return bool(re.search(r"(?i)(?<![a-z])(?:sdh|hi|cc|hearing)(?![a-z])", ad))


# Dosya adının sonundaki dil eki -> dil kodu ("Film.tr.srt", "Film.English.SDH.srt", "Turkish.srt")
_DOSYA_DILI = {ek: kod for kod, ekler in {
    "tr": "tr tur turkish turkce türkçe", "en": "en eng english", "ja": "ja jpn jp japanese",
    "de": "de ger deu german", "fr": "fr fre fra french", "es": "es spa spanish", "it": "it ita italian",
    "pt": "pt por portuguese pb pob br", "ru": "ru rus russian", "ar": "ar ara arabic", "ko": "ko kor korean",
    "zh": "zh chi zho chinese chs cht", "nl": "nl dut nld dutch", "pl": "pl pol polish", "el": "el gre ell greek",
    "fa": "fa per fas persian farsi", "az": "az aze azerbaijani", "uk": "uk ukr ukrainian",
    "sv": "sv swe swedish"}.items() for ek in ekler.split()}


def _dosya_dili(ad):
    """Dosya adının sonunda yazan dil (kod) ya da "". Yalnız son eke bakılır (SDH, forced gibi ekler atlanır): iki
    harfli kodlar adın içinde sıradan sözcük olabilir ("It.2017.1080p", "De.Film")."""
    govde = os.path.splitext(os.path.basename(ad))[0]
    ekler = [e for e in re.split(r"[\s._\-\[\]()]+", govde.lower()) if e]
    while ekler and ekler[-1] in ("sdh", "hi", "cc", "forced", "default", "full"):
        ekler.pop()
    return _DOSYA_DILI.get(ekler[-1], "") if ekler else ""


def _en_iyi(dosyalar, dil=""):
    """Birden çok uygun dosyadan: önce istenen dilin dosyası (adı başka dili yazan en sona), sonra srt, sonra SDH
    olmayan, sonra en büyüğü. Çok dilli arşivden (film.en.srt + film.tr.srt) başka dilin dosyası verilmesin."""
    kisa = (dil or "").split("-")[0].lower()

    def dil_sirasi(b):
        d = _dosya_dili(b.filename)
        return 0 if not kisa or d == kisa else (1 if not d else 2)
    return sorted(dosyalar, key=lambda b: (dil_sirasi(b), UZANTILAR.index(_uzanti(b.filename)), _sdh_mi(b.filename),
                                           -b.file_size))[0]


def _bolum_yaziyor(ad):
    s = surum.ayristir(os.path.basename(ad))
    return bool(s.bolumler) or s.sezon is not None


# Dosya adındaki teknik numaralar (bölüm sanılmasın): [CRC32], çözünürlük, kodek, renk derinliği, ses kanalı, sürüm eki
_TEKNIK_NUMARA = re.compile(r"(?i)\[[0-9a-f]{8}\]|\d{3,4}x\d{3,4}|(?<![0-9])(?:480|576|720|1080|2160)[pi]|[xh]\.?26[45]|"
                            r"\d{1,2}[ .-]?bit|\d\.\d(?![0-9])|(?<=\d)v\d+")


def _numaralar(ad):
    kok = _TEKNIK_NUMARA.sub(" ", os.path.splitext(os.path.basename(ad))[0])
    return [int(n) for n in re.findall(r"(?<![0-9])\d{1,4}(?![0-9])", kok)]


def _degisen_numara(dosyalar):
    """Arşivdeki adlar yalnız tek bir numarada ayrışıyorsa ("Ad_01_...", "Ad_02_...") {dosya: numara}; değilse None
    (biçim kopyaları, SDH sürümü ya da numarası okunamayan adlar)."""
    listeler = [_numaralar(b.filename) for b in dosyalar]
    if len(dosyalar) < 2 or len({len(n) for n in listeler}) != 1:
        return None
    degisen = [i for i in range(len(listeler[0])) if len({n[i] for n in listeler}) > 1]
    if len(degisen) != 1:
        return None
    numaralar = [n[degisen[0]] for n in listeler]
    if len(set(numaralar)) != len(numaralar):
        return None
    return dict(zip(dosyalar, numaralar))


def _sezon_ici_tutar(ad, video):
    s = surum.ayristir(os.path.basename(ad))
    return video.bolum in s.bolumler and s.sezon in (None, video.sezon)


def _arsivden(veri, video, bolum=None, dil=""):
    """ZIP içinden altyazı. Dizide izlenen bölümün dosyası (bolum: sitenin mutlak/AniList numarası); hiçbir dosya
    bölüm yazmıyorsa (tek bölümlük arşiv: "Turkish.srt", "Turkish.SDH.srt") en uygunu. (bayt, ad) ya da hata."""
    try:
        arsiv = zipfile.ZipFile(io.BytesIO(veri))
        adaylar = [b for b in arsiv.infolist() if not b.is_dir() and _uzanti(b.filename)
                   and not os.path.basename(b.filename).startswith(".") and "__MACOSX" not in b.filename
                   and b.file_size <= EN_BUYUK_ALTYAZI]
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, ValueError, OSError):
        raise IndirmeHatasi("bozuk arşiv") from None
    if not adaylar:
        raise IndirmeHatasi("arşivde altyazı yok")
    if video is not None and video.tur == "dizi" and video.bolum is not None:
        tutma = [(b, surum.bolum_tutar_mi(os.path.basename(b.filename), video.sezon, video.bolum, mutlak=bolum))
                 for b in adaylar]
        kesin = [b for b, t in tutma if t is True]
        numarali = [b for b in adaylar if _bolum_yaziyor(b.filename)]
        harita = None if kesin or numarali else _degisen_numara(adaylar)
        if kesin:
            secilen = _en_iyi(kesin, dil)
        elif harita:
            # Adı okunamayan bölüm paketi: adlar yalnız bölüm numarasında ayrışıyor. Rastgele bir dosya verilmez.
            tutan = [b for b, n in harita.items() if n == video.bolum or (bolum is not None and n == bolum)]
            if len(tutan) != 1:
                raise IndirmeHatasi("pakette bu bölümün dosyası yok")
            secilen = tutan[0]
        elif not numarali:
            secilen = _en_iyi(adaylar, dil)     # hiçbir dosya bölüm yazmıyor: tek bölümlük arşiv
        else:
            # Yalnız numara yazan adlar (2. sezon ve sonrası, mutlak numara bilinmiyor): sezon içi numara tek bir
            # dosyada tutuyorsa o; birden çoğunda ya da hiçbirinde tutmuyorsa yanlış bölüm verilmez.
            goreli = [b for b, t in tutma if t is not False and b in numarali and _sezon_ici_tutar(b.filename, video)]
            if len(goreli) != 1:
                raise IndirmeHatasi("pakette bu bölümün dosyası yok")
            secilen = goreli[0]
    else:
        secilen = _en_iyi(adaylar, dil)     # filmde birden çok dosya: biçim kopyaları, SDH sürümü, başka diller
    try:
        return arsiv.read(secilen), os.path.basename(secilen.filename)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError, OSError):
        raise IndirmeHatasi("bozuk arşiv") from None


def altyazi_cikar(veri, dosya_adi, video=None, bolum=None, dil=""):
    """(metin, uzantı): indirilen içerikten çözülmüş altyazı metni. dil: listede seçilen altyazının dili."""
    if not veri:
        raise IndirmeHatasi("boş dosya")
    if veri[:4] == b"PK\x03\x04":
        veri, dosya_adi = _arsivden(veri, video, bolum, dil)
    elif veri[:4] == b"Rar!" or veri[:6] == b"7z\xbc\xaf\x27\x1c":
        raise IndirmeHatasi("RAR/7z arşivi açılamıyor")
    metin = _bayt_coz(veri)
    uzanti = _uzanti(dosya_adi) or _bicim_tahmini(metin)
    if not uzanti:
        raise IndirmeHatasi("altyazı biçimi tanınmadı")
    if satir_sayisi(metin, uzanti) < 3:
        raise IndirmeHatasi("dosya altyazı değil ya da boş")
    return metin, uzanti


def _bicim_tahmini(metin):
    if "[Script Info]" in metin[:2000] or "Dialogue:" in metin:
        return ".ass"
    if metin.lstrip("﻿").startswith("WEBVTT"):
        return ".vtt"
    if "-->" in metin:
        return ".srt"
    return ""


def satir_sayisi(metin, uzanti):
    if uzanti in (".ass", ".ssa"):
        return len(re.findall(r"^Dialogue:", metin, re.M))
    if uzanti in (".srt", ".vtt"):
        # Okun iki yanında zaman olmalı: HTML yorumları da "-->" ile biter (engelleme ya da hata sayfası)
        return len(re.findall(r"\d[ \t]*-->[ \t]*\d", metin))
    return len(re.findall(r"^[{\[]\d+[}\]]", metin, re.M))


# ------------------------------------------------------------------------------------------- kare hızı
def _yakin_film_hizi(fps):
    """En yakın film hızı (23,976 / 24 / 25); hiçbirine yakın değilse None."""
    if not fps:
        return None
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return None
    en_yakin = min(FILM_HIZLARI, key=lambda f: abs(fps - f))
    return en_yakin if abs(fps - en_yakin) < 0.03 else None


def son_zaman_ms(metin, uzanti):
    if uzanti in (".ass", ".ssa"):
        sonlar = [((int(m.group(6)) * 60 + int(m.group(7))) * 60 + int(m.group(8))) * 1000 + int(m.group(9)) * 10
                  for m in _ZAMAN_ASS.finditer(metin)]
    else:
        sonlar = [((int(m.group(1)) * 60 + int(m.group(2))) * 60 + int(m.group(3))) * 1000 + int(m.group(5))
                  for m in _ZAMAN_SRT.finditer(metin)]
    return max(sonlar) if sonlar else 0


def kare_hizi_orani(altyazi_fps, video_fps, metin, uzanti, video_suresi):
    """Altyazının zamanlarının çarpılacağı oran ya da None. Yalnız 23,976 ya da 24 ile 25 arasındaki fark düzeltilir
    (fark sabit kalmaz, giderek büyür). Altyazının hızı sitenin bildirdiğinden ya da son satırın videonun bitişini
    aşmasından anlaşılır; emin olunamazsa düzeltilmez."""
    v = _yakin_film_hizi(video_fps)
    if v is None:
        return None
    a = _yakin_film_hizi(altyazi_fps)
    if a is not None and a != v and 25.0 in (a, v):
        oran = a / v
        son = son_zaman_ms(metin, uzanti) / 1000.0
        if video_suresi and video_suresi > 600 and son * oran > video_suresi + 60:
            return None                                 # düzeltilince videodan taşıyor: sitenin bilgisi yanlış
        return oran
    if v == 25.0 and video_suresi and video_suresi > 600:
        son = son_zaman_ms(metin, uzanti) / 1000.0
        if son > video_suresi + 20:                     # altyazı videodan uzun: daha yavaş bir hıza göre yapılmış
            for a in (23.976, 24.0):
                if son * (a / v) <= video_suresi + 5:
                    return a / v
    return None


def zaman_olcekle(metin, uzanti, oran):
    """Bütün zamanları oranla çarpar; biçim (srt/vtt/ass) ve içindeki her şey korunur."""
    if uzanti in (".ass", ".ssa"):
        def ass(m):
            def z(s, d, sn, cs):
                t = int(round((((int(s) * 60 + int(d)) * 60 + int(sn)) * 100 + int(cs)) * oran))
                s2, t = divmod(t, 360000)
                d2, t = divmod(t, 6000)
                sn2, cs2 = divmod(t, 100)
                return f"{s2}:{d2:02d}:{sn2:02d}.{cs2:02d}"
            return f"{m.group(1)}{z(*m.group(2, 3, 4, 5))},{z(*m.group(6, 7, 8, 9))},"
        return _ZAMAN_ASS.sub(ass, metin)

    def srt(m):
        t = int(round((((int(m.group(1)) * 60 + int(m.group(2))) * 60 + int(m.group(3))) * 1000 + int(m.group(5))) * oran))
        s, t = divmod(t, 3600000)
        d, t = divmod(t, 60000)
        sn, ms = divmod(t, 1000)
        return f"{s:02d}:{d:02d}:{sn:02d}{m.group(4)}{ms:03d}"
    return _ZAMAN_SRT.sub(srt, metin)


def yaz(yol, metin):
    """UTF-8 olarak, önce geçici ada yazıp yerine koyarak."""
    from . import kayit
    return kayit.metin_yaz(yol, metin.replace("\r\n", "\n"))
