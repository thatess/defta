"""Yapım denetimi: sitenin bildirdiği yapım (film ya da dizi) oynatılan video mu.

OpenSubtitles bilgiyi altyazı başına verir (feature_details), SubDL yanıt başına (results). Karşılaştırma iki
aşamalıdır: önce kimlikler (IMDb, TMDB), kimlik yoksa ad, film yılı ve film/dizi türü. Ad yalnız güvenilirse karar
verdirir: anlamsız bir dosya adından ("video_0001.mp4") çıkarılan ad hiçbir yapımı doğrulamaz.
"""
import math
import re

from .. import surum
from . import sade_ad

# Tek başına yapım adı sayılmayan sözcükler: "Movie.1080p.mkv" adı "Scary Movie"yi doğrulamaz
GENEL_ADLAR = {"movie", "movies", "film", "filmler", "video", "videos", "episode", "bolum", "dizi", "series", "show",
               "season", "part", "sample", "trailer", "new", "untitled", "home", "test", "the", "and"}


def kimlik_no(deger):
    """IMDb/TMDB kimliğinin yalnız rakamı, baştaki sıfırlar atılmış ("tt0816692", 816692, "816692.0" -> "816692")."""
    if deger is None or deger == "" or isinstance(deger, bool):
        return ""
    if isinstance(deger, float):
        if not math.isfinite(deger):
            return ""
        deger = int(deger)
    d = str(deger).strip().lower()
    d = d[2:] if d.startswith("tt") else d
    m = re.fullmatch(r"(\d+)(?:\.0+)?", d)
    return m.group(1).lstrip("0") if m else ""


def elle(video):
    return bool(getattr(video, "elle_arama", ""))


def anlamli(ad):
    """Adda en az bir gerçek sözcük var mı (3+ harf, genel sözcük değil): "abc123", "Movie", "2012" değil."""
    return any(len(s) >= 3 and s.isalpha() and s not in GENEL_ADLAR for s in sade_ad(ad).split())


def ad_guvenilir(video):
    """Videonun adına göre karar verilebilir mi. Elle yazılan ve Kodi'nin ya da oynatan eklentinin verdiği ad güvenilir.
    Dosyadan çıkarılan ad yalnız anlamlıysa ve düzenli adlandırmadan geliyorsa güvenilir: sürüm adı
    ("Film.2014.1080p.BluRay.x264-GRUP") ya da yılı ya da bölümü yazan temiz ad ("Film (2014).mkv",
    "Dizi - S01E01 - Bölüm.mkv"). "video_0001.mp4", "Movie.1080p.mkv", "abc123.mkv" güvenilmez."""
    if elle(video):
        return True
    kaynak = getattr(video, "ad_kaynagi", "")
    if kaynak == "kodi":
        return bool(sade_ad(video.ad))
    if not anlamli(video.ad):
        return False
    if kaynak == "surum":
        return True
    s = video.surum
    duzenli = bool(re.search(r"[(\[]\s*(?:19|20)\d\d\s*[)\]]", video.dosya_adi or "")) or \
        (s.sezon is not None and bool(s.bolumler))
    if kaynak == "dosya":
        return duzenli
    # Kaynağı bilinmiyor (Video nesnesi elle kurulmuş): ad dosya adından geliyorsa sürüm adı ya da düzenli ad olmalı
    if sade_ad(s.baslik) == sade_ad(video.ad):
        return surum.surum_adi_mi(s) or duzenli
    return True


def video_adlari(video):
    """Videonun yapım adları. Dizide dizinin adı (bölüm başlığı değil), ülke ya da yıl eki atılmış hâliyle; filmde
    başlık ve özgün ad (sondaki yıl adın parçası olabilir: "Blade Runner 2049"). Elle aramada yalnız yazılan ad
    (oynatılan dosyanın adı başka yapımın olabilir)."""
    from ..video import ad_eksiz
    if video.tur == "dizi":
        adlar = [video.dizi_adi] + ([] if elle(video) else [video.surum.baslik])
        adlar += [ad_eksiz(a) for a in adlar if a]
    else:
        adlar = [video.baslik] + ([] if elle(video) else [video.ozgun_ad, video.surum.baslik])
    return {s for s in (sade_ad(a) for a in adlar if a) if s}


def ad_tutar(video_adlari_, site_adlari, kapsama=True):
    """Ad birebir tutuyor ya da (kapsama: filmde) videonun adı sitedeki adın içinde bütün sözcükleriyle geçiyor
    ("Dune" -> "Dune Part Two"). Tersi sayılmaz: "Law and Order SVU" videosu "Law and Order" dizisi değildir. Tek ve
    genel bir sözcük ("movie") başka adın içinde aranmaz. Dizide yan dizi ("Dexter: New Blood") tutmasın diye kapsama
    kapatılır."""
    for v in video_adlari_:
        for o in site_adlari:
            if v.replace(" ", "") == o.replace(" ", ""):
                return True
            uygun = len(v.split()) >= 2 or (len(v) >= 5 and v not in GENEL_ADLAR)
            if kapsama and uygun and f" {v} " in f" {o} ":
                return True
    return False


def yan_dizi(video_adlari_, site_adlari):
    """Dizide adlardan biri ötekinin başı, sonunda fazladan sözcüklerle: "Law and Order" / "Law and Order SVU", "Dexter" /
    "Dexter New Blood", "House" / "House of the Dragon" ayrı dizilerdir. Fazla sözcükler baştaysa ("Daredevil" /
    "Marvels Daredevil", "Andor" / "Star Wars Andor") aynı dizi olabilir: sayılmaz."""
    for v in video_adlari_:
        for o in site_adlari:
            a, b = v.split(), o.split()
            kisa, uzun = (a, b) if len(a) < len(b) else (b, a)
            if kisa and len(kisa) < len(uzun) and uzun[:len(kisa)] == kisa:
                return True
    return False


def video_yili(video):
    """Karşılaştırmada kullanılacak film yılı. Yıl yalnız temiz bir dosya adından geliyorsa ("Blade Runner 2049.mkv")
    ya da adın kendisiyse ("2012.1080p.BluRay") kullanılmaz."""
    if elle(video):
        return video.yil
    s = video.surum
    yil = video.yil or (s.yil if surum.surum_adi_mi(s) else None)
    if not yil or (yil == s.yil and not surum.surum_adi_mi(s)) or sade_ad(video.ad) == str(yil):
        return None
    return yil


def kimlik_durumu(video, imdb="", ust_imdb="", tmdb=""):
    """Sitenin bildirdiği kimlikler videonunkilerle karşılaştırılır. Filmde imdb ve tmdb filmin; dizide ust_imdb ve
    tmdb dizinin, imdb bölümün kendisinin. TMDB yalnız tür aynıysa verilmeli (film ve dizi numaraları ayrı sayılır).
    -> "kimlik" (biri tutuyor), "farkli" (karşılaştırılabilenlerin hiçbiri tutmuyor) ya da None.
    Dizide oynatan eklentinin verdiği kimlik bölümün de olabilir: dizinin kimliği tutmuyor diye karar ancak videonun
    kimliğinin dizinin kendisi olduğu kesinse (Kodi kitaplığı) verilir; bölümün kimliğine ancak o biliniyorsa bakılır."""
    v_imdb, v_bolum = kimlik_no(video.imdb), kimlik_no(getattr(video, "bolum_imdb", ""))
    imdb, ust_imdb, tmdb = kimlik_no(imdb), kimlik_no(ust_imdb), kimlik_no(tmdb)
    v_tmdb = kimlik_no(video.tmdb)
    hepsi = {v_imdb, v_bolum} - {""}
    if (imdb and imdb in hepsi) or (ust_imdb and ust_imdb in hepsi) or (tmdb and tmdb == v_tmdb):
        return "kimlik"
    if video.tur == "dizi":
        kesin = bool(getattr(video, "dizi_kimlik_kesin", False)) or bool(v_bolum)
        tutmayan = (bool(ust_imdb and v_imdb) and kesin) or bool(imdb and v_bolum) or (bool(tmdb and v_tmdb) and kesin)
    else:
        tutmayan = bool(imdb and v_imdb) or bool(tmdb and v_tmdb)
    return "farkli" if tutmayan else None


def ad_durumu(video, site_adlari, site_yili=None, tur_tutmuyor=False, yil=None):
    """Kimlik karar veremediyse ad: "ad" (tutuyor) · "ad_farkli" (ad, film yılı ya da film/dizi türü tutmuyor; yerel ya
    da romaji ad da olabilir) · None (karşılaştıracak ad yok). yil: video_yili(video)."""
    if tur_tutmuyor:
        return "ad_farkli"
    dizi = video.tur == "dizi"
    v_adlar = video_adlari(video)
    s_adlar = {s for s in (sade_ad(a) for a in site_adlari if a) if s}
    if not (v_adlar and s_adlar):
        return None
    if not ad_tutar(v_adlar, s_adlar, kapsama=not dizi):
        return "ad_farkli"
    if yil_farkli(video, yil, site_yili, s_adlar):
        return "ad_farkli"                              # aynı adlı başka film (yeniden çekim)
    return "ad"


def yil_farkli(video, yil, site_yili, site_adlari):
    """Film yılı sitedekinden 1'den çok farklı mı. Yıl sitedeki adın içinde geçiyorsa adın parçasıdır ("Blade Runner
    2049" dosyası 2049 yılı diye okunur): o zaman fark sayılmaz."""
    if video.tur == "dizi" or not yil or not site_yili:
        return False
    try:
        fark = abs(int(site_yili) - int(yil)) > 1
    except (TypeError, ValueError, OverflowError):
        return False
    return fark and str(yil) not in {w for a in site_adlari for w in a.split()}


def baska_filmin_adlari(adaylar, video):
    """Sitenin yapım bilgisi doğru olsa da yükleyen altyazıyı yanlış yapıma koymuş olabilir: sürüm adı başka bir filmi
    ya da bir dizinin bölümünü anlatıyorsa aday atılır ("2307 Winter's Dream 2016 720p" Interstellar (2014) altyazısı
    diye; SubDL'de The Simpsons 12. sezonun Türkçeleri Attack on Titan diye yüklenmiş). Kanıt: hiçbir adı videonun adını
    tutmuyor ve biri anlamlı bir ad ile 1'den çok farklı bir yıl ya da dizi bölümü (S12E03) taşıyor. Yerel adla yüklenen
    altyazı ("Yıldızlararası 2160p BluRay") yıl taşımadığı ya da yılı tuttuğu için kalır. Yalnız filmde ve videonun adı
    güvenilirse; yıl farkına yalnız videonun yılı biliniyorsa bakılır. -> (kalan adaylar, atılanların adları)."""
    if video.tur != "film" or not ad_guvenilir(video):
        return adaylar, []
    yil, v_adlar = video_yili(video), video_adlari(video)
    if not v_adlar:
        return adaylar, []
    kalan, atilan = [], []
    for a in adaylar:
        b = _baska_film(v_adlar, a.adlar, yil)
        if b:
            atilan.append(b)
        else:
            kalan.append(a)
    return kalan, atilan


def _baska_film(v_adlar, adlar, yil):
    kanit = ""
    for ad in adlar:
        s = surum.ayristir(ad)
        b = sade_ad(s.baslik)
        if not b:
            continue
        if ad_tutar(v_adlar, {b}) or ad_tutar({b}, v_adlar):
            return ""
        try:
            fark = bool(s.yil and yil) and abs(int(s.yil) - int(yil)) > 1
        except (TypeError, ValueError, OverflowError):
            fark = False
        if not kanit and fark and anlamli(s.baslik):
            kanit = f"{s.baslik} ({s.yil})"
        elif not kanit and s.sezon and s.bolumler and anlamli(s.baslik):          # dizi bölümü (S00 özel bölüm sayılmaz)
            kanit = f"{s.baslik} S{s.sezon:02d}E{s.bolumler[0]:02d}"
    return kanit
