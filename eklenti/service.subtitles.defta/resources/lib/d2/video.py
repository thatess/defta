"""Oynatılan videonun tanınması: kimlikler, ad, sezon/bölüm, yıl, sürüm adı, süre, kare hızı, dosya karması.

Bilgiyi önce oynatan eklenti verir (Kodi'nin video bilgisi); eksik kalan dosya adından çıkarılır. Akış adresi
(debrid jetonu taşıyabilir) yalnız bellekte tutulur: kayda, dosyaya ya da bildirime yazılmaz; ondan yalnız dosya
adı alınır.
"""
import re
import struct
import threading
from urllib.parse import unquote, urlsplit

from . import surum as surum_modulu

_VIDEO_UZANTI = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".mov", ".webm", ".mpg", ".mpeg", ".iso",
                 ".flv", ".vob")
_YEREL_SEMA = ("", "file", "smb", "nfs", "special")
KARMA_SURESI = 8.0                  # akışta karma en çok bu kadar beklenir
KARMA_EN_AZ_SURE = 600           # sn: adı bilinmeyen akışta karma yalnız bu kadar uzun videoda hesaplanır


class Video:
    def __init__(self):
        self.yol = ""               # yalnız bellekte; asla kayda yazılmaz
        self.akis = False
        self.canli = False
        self.dosya_adi = ""
        self.surum = surum_modulu.ayristir("")
        self.tur = ""               # "film", "dizi" ya da bilinmiyor ("")
        self.imdb = self.tmdb = self.tvdb = self.anilist = self.mal = ""
        self.bolum_imdb = ""                # bölümün kendi IMDb kimliği (Kodi kitaplığı; imdb dizininki olur)
        self.anilist_bolum = None           # oynatan eklentinin verdiği AniList bölüm numarası (eşleme uyguladıysa)
        self.dizi_adi = self.baslik = self.ozgun_ad = ""
        self.sezon = self.bolum = None
        self.yil = None
        self.sure = 0.0
        self.fps = None
        self.karma = None
        self.gomulu_diller = []
        self.elle_arama = ""
        self.oynayan_anahtari = ""          # elle aramada: o an oynayan videonun anahtarı (seçim ve iş ona bağlanır)
        self.ad_kaynagi = ""                # yapım adı nereden: kodi (bilgi), surum (sürüm adı), dosya, elle
        self.dizi_kimlik_kesin = False      # imdb/tmdb kesin dizinin kendisinin (Kodi kitaplığından)

    # ----------------------------------------------------------------------------------------- anahtarlar
    @property
    def ad(self):
        """Aramada kullanılan yapım adı: dizide dizi adı, filmde başlık."""
        return (self.dizi_adi if self.tur == "dizi" else "") or self.baslik or self.ozgun_ad or self.surum.baslik

    @property
    def tanindi(self):
        return bool(self.imdb or self.tmdb or self.tvdb or self.anilist or self.ad)

    @property
    def sezon_no(self):
        """Sitelere giden sezon numarası: bilinmiyorsa 1; özel bölümler (S00) 0 kalır."""
        return self.sezon if self.sezon is not None else 1

    def icerik_anahtari(self):
        """Çevirilerin bağlandığı içerik: kimlik + sezon/bölüm (sürümden bağımsız)."""
        if self.imdb:
            kok = "imdb:" + self.imdb
        elif self.tmdb:
            kok = f"tmdb:{'tv' if self.tur == 'dizi' else 'movie'}:{self.tmdb}"
        elif self.tvdb:
            kok = "tvdb:" + self.tvdb
        elif self.anilist:
            kok = "anilist:" + self.anilist
        else:
            kok = "ad:" + re.sub(r"[^\w]+", "", self.ad.casefold()) + (f":{self.yil}" if self.yil and self.tur != "dizi" else "")
        if self.tur == "dizi" and self.bolum is not None:
            kok += f":s{self.sezon or 0:02d}e{self.bolum:02d}"
        return kok

    def video_anahtari(self):
        """Seçimlerin, kaymış işaretlerinin bağlandığı video: içerik + sürüm adı (senkron sürüme bağlıdır)."""
        if self.oynayan_anahtari:           # elle arama: altyazı oynayan video için seçilir; servis o anahtarı bilir
            return self.oynayan_anahtari
        return self.icerik_anahtari() + "|" + (self.surum.ad.casefold() if self.dosya_adi else "?")

    def yapim_anahtari(self):
        """Sözlüğün bağlandığı yapım. Dizide dizinin adı: oynatan eklentinin verdiği kimlik bölümün de olabilir, dizinin
        de; ad bütün bölümlerde aynıdır. Filmde kimlik, yoksa ad + yıl."""
        if self.tur != "dizi" and (self.imdb or self.tmdb):
            return ("imdb:" + self.imdb) if self.imdb else ("tmdb:movie:" + self.tmdb)
        ad = re.sub(r"[^\w]+", "", self.ad.casefold().replace("×", "x"))
        return "ad:" + (ad or "adsiz") + (f":{self.yil}" if self.yil and self.tur != "dizi" else "")

    def ozet(self):
        """Kayda yazılabilir özet (adres yok)."""
        parcalar = [f"tür={self.tur or '?'}", f"ad={self.ad!r}"]
        if self.tur == "dizi":
            parcalar.append(f"S{self.sezon}E{self.bolum}")
        if self.yil:
            parcalar.append(f"yıl={self.yil}")
        for alan in ("imdb", "bolum_imdb", "tmdb", "tvdb", "anilist", "mal"):
            if getattr(self, alan, ""):
                parcalar.append(f"{alan}={getattr(self, alan)}")
        if getattr(self, "ad_kaynagi", ""):
            parcalar.append(f"ad_kaynağı={self.ad_kaynagi}")       # kodi (bilgi) · surum · dosya · elle
        tani = getattr(self, "surum_tanisi", "")
        parcalar.append(f"sürüm={self.dosya_adi!r}" if self.dosya_adi
                        else "sürüm=?" + (f" (oynatıcının verdiği ad: {tani})" if tani else ""))
        parcalar += [f"akış={self.akis}", f"süre={self.sure:.0f}sn", f"fps={self.fps}", f"karma={'var' if self.karma else 'yok'}"]
        if self.gomulu_diller:
            parcalar.append("gömülü=" + ",".join(self.gomulu_diller))
        return " ".join(parcalar)


# ------------------------------------------------------------------------------------------- saf yardımcılar
def sema(yol):
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]{1,9})://", yol or "")
    return m.group(1).lower() if m else ""


def jeton_gibi(ad):
    """Dosya adı görünümlü jeton mu ("9f8a7c6b5d4e3f2a1b0c9d8e7f6a5b4c.mkv"): rakam içeren, kesintisiz 20 ya da daha
    çok karakterlik parça taşıyan ad sürüm adı sayılmaz (kayda ve sitelere gitmesin). Sürüm adlarının parçaları kısadır
    ("Film.2014.1080p.BluRay.x264-GRUP", "[Grup]_Dizi_03_[FB1A088F]"); rakamsız uzun sözcük addır."""
    return any(len(parca) >= 20 and parca.isalnum() and re.search(r"\d", parca)
               for parca in re.split(r"[\s._\-\[\](){}+,~!']+", ad or ""))


def eklentinin_surum_adi(xbmc, tani=None):
    """Oynatan eklentinin altyazı eklentileri için bıraktığı sürüm adı (Red Light, Umbrella, Seren ve benzerleri ana
    pencerenin "subs.player_filename" özelliğine yazar). Debrid adresi dosya adı taşımadığında tek sürüm bilgisi budur.
    Değer adres olabilir (jeton taşıyabilir): yalnız video dosyası olan son parçası alınır; adres hiçbir yere yazılmaz.
    tani (liste) verilirse ad alınamadığında neden alınamadığı eklenir (değerin kendisi değil: jeton olabilir)."""
    def neden(yazi):
        if tani is not None:
            tani.append(yazi)
        return ""
    try:
        deger = (xbmc.getInfoLabel("Window(10000).Property(subs.player_filename)") or "").strip()
    except Exception:
        return neden("okunamadı")
    if not deger:
        return neden("boş")
    if "://" in deger:
        return adres_dosya_adi(deger) or neden("adres, video dosyası değil")
    ad = deger.split("|", 1)[0].split("?", 1)[0].replace("\\", "/").rsplit("/", 1)[-1].strip()
    if len(ad) < 5 or not re.search(r"[ ._]", ad):
        return neden(f"kısa ya da tek parça ({len(ad)} karakter)")
    if jeton_gibi(ad):
        return neden(f"jeton gibi ({len(ad)} karakter)")
    # Yalnız sürüm adına benzeyen değer: noktalı bir jeton ("eyJ...") kaydedilmesin
    s = surum_modulu.ayristir(ad)
    if ad.lower().endswith(_VIDEO_UZANTI) or surum_modulu.surum_adi_mi(s) or (s.yil and s.baslik):
        return ad
    return neden(f"sürüm adına benzemiyor ({len(ad)} karakter, {len(ad.split())} sözcük)")


def adres_dosya_adi(yol):
    """Oynatılan yoldan dosya adı: yerel dosyada dosya adı, http akışında adres yolunun son parçası (sorgu ve Kodi'nin
    "|başlık" eki atılır). Video dosyasına benzemiyorsa boş: adres kimlik ya da jeton olabilir."""
    if not yol:
        return ""
    s = sema(yol)
    if s in ("plugin", "pvr", "rtmp", "rtsp", "udp"):
        return ""
    yol = yol.split("|", 1)[0]
    if s in ("http", "https"):
        parca = unquote(urlsplit(yol).path).rsplit("/", 1)[-1]          # "%2F" de ayraçtır: jeton parçası kalmasın
    else:
        parca = re.split(r"[\\/]", yol)[-1]
    if not parca.lower().endswith(_VIDEO_UZANTI):
        return ""
    if s in ("http", "https") and jeton_gibi(parca):     # uzantılı da olsa jeton: ad sayılmaz
        return ""
    return parca


_SONDAKI_YIL = re.compile(r"\s*[(\[]\s*(?:19|20)\d\d\s*[)\]]\s*$")


def yapim_adi_ve_kaynagi(ad, dosya_adi=""):
    """(yapım adı, kaynak). Kodi kitaplıkta olmayan dosyanın başlığına dosya adını yazar
    ("Film.2014.1080p.BluRay.x264-GRUP.mkv"), oynatan eklenti de sürüm adını koyabilir: bunlardan yapım adı çıkarılır.
    Temiz bir ad ("Blade Runner 2049", "Charlotte's Web") dosya adıyla aynı olsa da bozulmaz: yalnız uzantısı ve
    sondaki "(2014)" atılır. Kaynak: "surum" (sürüm adından çıkarıldı), "dosya" (temiz dosya adı), "kodi" (bilgi)."""
    a = (ad or "").strip()
    if not a:
        return "", ""
    uzantili = a.lower().endswith(_VIDEO_UZANTI)
    kok = a.rsplit(".", 1)[0] if uzantili else a
    d = (dosya_adi or "").strip().casefold()
    dosyadan = uzantili or (bool(d) and a.casefold() in (d, d.rsplit(".", 1)[0]))
    s = surum_modulu.ayristir(kok)
    if surum_modulu.surum_adi_mi(s) or (dosyadan and " " not in kok and re.search(r"[._]", kok)):
        return (s.baslik or kok), "surum"
    return (_SONDAKI_YIL.sub("", kok) or kok), ("dosya" if dosyadan else "kodi")


def yapim_adi(ad, dosya_adi=""):
    return yapim_adi_ve_kaynagi(ad, dosya_adi)[0]


def kimlik_temizle(deger, imdb=False):
    deger = (str(deger or "")).strip()
    if imdb:
        m = re.search(r"tt\d{5,10}", deger)
        return m.group(0) if m else ""
    return deger if re.fullmatch(r"\d{1,10}", deger) else ""


def karma_hesapla(dosya_ac, boyut=None):
    """OpenSubtitles dosya karması: boyut + ilk ve son 64 KB'ın 64 bitlik toplamı. dosya_ac() -> (dosya, boyut);
    dosya.seek(n) ve dosya.read(n) yeter (xbmcvfs.File da olur)."""
    dosya, boyut = dosya_ac()
    try:
        if not boyut or boyut < 131072:
            return None
        h = boyut
        bas = dosya.read(65536)
        dosya.seek(boyut - 65536)
        son = dosya.read(65536)
        if len(bas) != 65536 or len(son) != 65536:
            return None
        for parca in (bas, son):
            for (q,) in struct.iter_unpack("<Q", parca):
                h = (h + q) & 0xFFFFFFFFFFFFFFFF
        return f"{h:016x}"
    finally:
        try:
            dosya.close()
        except Exception:
            pass


def elle_aramadan(metin):
    """Kodi'nin arama kutusuna yazılan: "Dizi Adı S01E05", "Film 2014" gibi."""
    v = Video()
    v.elle_arama = (metin or "").strip()
    v.ad_kaynagi = "elle"
    s = surum_modulu.ayristir(v.elle_arama)
    v.surum = surum_modulu.ayristir("")
    v.baslik = s.baslik or v.elle_arama
    v.yil = s.yil
    if s.sezon is not None or s.bolumler:
        v.tur, v.dizi_adi = "dizi", v.baslik
        v.sezon = s.sezon if s.sezon is not None else 1
        v.bolum = s.bolumler[0] if s.bolumler else None
    return v


def _sayi(deger):
    try:
        n = int(float(deger))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _fps(deger):
    try:
        f = float(str(deger).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if 5 < f < 300 else None


# ---------------------------------------------------------------------------------------- Kodi'den okuma
def oynatilan(xbmc, xbmcvfs, karma=True):
    """Oynatılan videonun bilgileri. xbmc, xbmcvfs modülleri dışarıdan verilir (testte sahteleri)."""
    v = Video()
    oyn = xbmc.Player()
    if not oyn.isPlayingVideo():
        return v
    try:
        v.yol = oyn.getPlayingFile() or ""
    except RuntimeError:
        v.yol = ""
    s = sema(v.yol)
    v.akis = s in ("http", "https") or s in ("plugin", "rtmp", "rtsp", "udp")
    v.canli = bool(xbmc.getCondVisibility("Pvr.IsPlayingTv") or xbmc.getCondVisibility("Pvr.IsPlayingRadio"))
    try:
        v.sure = float(oyn.getTotalTime() or 0)
    except RuntimeError:
        v.sure = 0.0
    if v.akis and v.sure <= 0:
        v.canli = True                      # süresi olmayan akış canlı yayındır
    v.fps = _fps(xbmc.getInfoLabel("Player.Process(videofps)"))
    try:
        etiket = oyn.getVideoInfoTag()
    except RuntimeError:
        etiket = None

    def oku(yontem, *args):
        try:
            return getattr(etiket, yontem)(*args) if etiket is not None else ""
        except Exception:
            return ""

    v.dizi_adi = oku("getTVShowTitle") or xbmc.getInfoLabel("VideoPlayer.TVShowTitle")
    v.baslik = oku("getTitle") or xbmc.getInfoLabel("VideoPlayer.Title")
    v.ozgun_ad = oku("getOriginalTitle") or xbmc.getInfoLabel("VideoPlayer.OriginalTitle")
    v.sezon = _sayi(oku("getSeason"))
    if v.sezon is None:
        v.sezon = _sayi(xbmc.getInfoLabel("VideoPlayer.Season"))
    v.bolum = _sayi(oku("getEpisode")) or _sayi(xbmc.getInfoLabel("VideoPlayer.Episode"))
    v.yil = _sayi(oku("getYear")) or _sayi(xbmc.getInfoLabel("VideoPlayer.Year"))
    v.imdb = kimlik_temizle(oku("getUniqueID", "imdb") or oku("getIMDBNumber") or xbmc.getInfoLabel("VideoPlayer.IMDBNumber"), imdb=True)
    v.tmdb = kimlik_temizle(oku("getUniqueID", "tmdb"))
    v.tvdb = kimlik_temizle(oku("getUniqueID", "tvdb"))
    v.anilist = kimlik_temizle(oku("getUniqueID", "anilist"))
    v.mal = kimlik_temizle(oku("getUniqueID", "mal") or oku("getUniqueID", "myanimelist"))
    medya = (oku("getMediaType") or "").lower()
    v.dosya_adi = adres_dosya_adi(v.yol)
    if not v.dosya_adi and v.akis and not v.canli:
        tani = []
        v.dosya_adi = eklentinin_surum_adi(xbmc, tani)
        v.surum_tanisi = tani[0] if tani else ""
    v.surum = surum_modulu.ayristir(v.dosya_adi)
    kaynaklar = {}
    for alan in ("dizi_adi", "baslik", "ozgun_ad"):          # "Film.2014.1080p.BluRay.mkv" -> "Film"
        ad, kaynaklar[alan] = yapim_adi_ve_kaynagi(getattr(v, alan), v.dosya_adi)
        setattr(v, alan, ad)
    dosyadan = "surum" if surum_modulu.surum_adi_mi(v.surum) else "dosya"
    if v.yil is None or v.yil < 1900:
        v.yil = v.surum.yil
    if medya == "episode" or (v.bolum and (v.dizi_adi or medya == "")):
        v.tur = "dizi"
    elif medya == "movie":
        v.tur = "film"
    # Bilgi yoksa dosya adından
    if not (v.dizi_adi or v.baslik) and v.surum.baslik:
        v.baslik, kaynaklar["baslik"] = v.surum.baslik, dosyadan
    if v.tur != "film" and v.bolum is None and v.surum.bolumler:
        v.tur = "dizi"
        if not v.dizi_adi:
            v.dizi_adi, kaynaklar["dizi_adi"] = v.surum.baslik, dosyadan
        v.sezon = v.surum.sezon if v.surum.sezon is not None else (v.sezon or 1)
        v.bolum = v.surum.bolumler[0]
    if v.tur == "dizi" and v.sezon is None:
        v.sezon = v.surum.sezon if v.surum.sezon is not None else 1
    if not v.tur:
        v.tur = "film" if (v.baslik or v.surum.baslik) else ""
    alan = next((a for a in (("dizi_adi",) if v.tur == "dizi" else ()) + ("baslik", "ozgun_ad") if getattr(v, a)), "")
    v.ad_kaynagi = kaynaklar.get(alan, "") if alan else (dosyadan if v.surum.baslik else "")
    if v.tur == "dizi":
        _kitaplik_dizi_kimlikleri(xbmc, v)
    try:
        v.gomulu_diller = [str(d).lower() for d in (oyn.getAvailableSubtitleStreams() or []) if d]
    except RuntimeError:
        v.gomulu_diller = []
    if karma:
        v.karma = _karma(xbmcvfs, v)
    return v


def _kitaplik_dizi_kimlikleri(xbmc, v):
    """Kodi kitaplığındaki bölümün kimlikleri bölümün kendisinindir; sitelere dizinin kimliği gider (yoksa başka
    dizinin aynı numaralı kaydı eşleşir, sıradaki bölümün anahtarı da tutmaz)."""
    dizi_no = _sayi(xbmc.getInfoLabel("VideoPlayer.TvShowDBID"))
    if not dizi_no:
        return
    try:
        import json
        yanit = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "VideoLibrary.GetTVShowDetails",
            "params": {"tvshowid": dizi_no, "properties": ["uniqueid", "imdbnumber"]}})))
        ayrinti = ((yanit or {}).get("result") or {}).get("tvshowdetails") or {}
    except Exception:
        return
    kimlikler = ayrinti.get("uniqueid") or {}
    imdb = kimlik_temizle(kimlikler.get("imdb") or ayrinti.get("imdbnumber") or "", imdb=True)
    if not (imdb or kimlikler.get("tmdb") or kimlikler.get("tvdb")):
        return
    if imdb and imdb != v.imdb:
        v.bolum_imdb = v.imdb
    v.imdb = imdb
    v.dizi_kimlik_kesin = True
    v.tmdb = kimlik_temizle(kimlikler.get("tmdb") or "")
    v.tvdb = kimlik_temizle(kimlikler.get("tvdb") or "")


def _karma(xbmcvfs, v):
    """Karma: yerel dosyada hep; http akışında sürüm adı biliniyorsa (adresten ya da oynatan eklentiden) ya da süresi
    belli, en az KARMA_EN_AZ_SURE uzunluğunda bir videoysa, ve adres bir oynatma listesi değilse (başı ve sonu
    okunabiliyorsa). Parçalı akışta (m3u8, plugin) olmaz. Akışta en çok KARMA_SURESI beklenir.
    Sürüm adı şartı kaldırıldı (1.0.4): oynatan eklenti ad vermediğinde (adres yalnız bir kimlik taşıyor) karma tek
    senkron bilgisidir; o da hesaplanmayınca hiçbir altyazı "senkron uyumlu" işareti alamıyordu."""
    s = sema(v.yol)
    akis_uygun = s in ("http", "https") and (v.dosya_adi or (v.sure or 0) >= KARMA_EN_AZ_SURE)
    if not v.yol or v.canli or not (s in _YEREL_SEMA or akis_uygun):
        return None
    if s in ("http", "https") and urlsplit(v.yol.split("|", 1)[0]).path.lower().endswith((".m3u8", ".m3u", ".mpd")):
        return None
    sonuc = {}

    def hesapla():
        def ac():
            f = xbmcvfs.File(v.yol)
            boyut = f.size()

            class Okuyucu:
                def read(self, n):
                    return bytes(f.readBytes(n))

                def seek(self, n):
                    f.seek(n, 0)

                def close(self):
                    f.close()
            return Okuyucu(), boyut
        try:
            sonuc["karma"] = karma_hesapla(ac)
        except Exception:
            sonuc["karma"] = None

    is_ = threading.Thread(target=hesapla, daemon=True)
    is_.start()
    is_.join(KARMA_SURESI if v.akis else 30)
    return sonuc.get("karma")


def oynatma_yeri(xbmc):
    """Şu anki oynatma yeri (sn); oynatılmıyorsa 0."""
    try:
        oyn = xbmc.Player()
        return float(oyn.getTime() or 0) if oyn.isPlayingVideo() else 0.0
    except RuntimeError:
        return 0.0


# Gömülü altyazı dilini tanımak için: ISO 639-1/639-2 kodları ve İngilizce adlar (Kodi dil kodu ya da ad verir)
_DIL_KODLARI = {"tr": ("tr", "tur", "turkish"), "en": ("en", "eng", "english"), "ja": ("ja", "jpn", "japanese"),
                "de": ("de", "ger", "deu", "german"), "fr": ("fr", "fre", "fra", "french"),
                "es": ("es", "spa", "spanish"), "it": ("it", "ita", "italian"), "pt": ("pt", "por", "portuguese"),
                "pt-BR": ("pb", "pob", "pt-br", "brazilian"), "ru": ("ru", "rus", "russian"),
                "ar": ("ar", "ara", "arabic"), "ko": ("ko", "kor", "korean"), "zh": ("zh", "chi", "zho", "chinese"),
                "nl": ("nl", "dut", "nld", "dutch"), "pl": ("pl", "pol", "polish"),
                "el": ("el", "gre", "ell", "greek"), "fa": ("fa", "per", "fas", "persian", "farsi"),
                "az": ("az", "aze", "azerbaijani"), "uk": ("uk", "ukr", "ukrainian"), "sv": ("sv", "swe", "swedish")}


def dil_eslesir(gomulu, hedef):
    g = (gomulu or "").strip().lower()
    if not g:
        return False
    ilk = g.replace("_", "-").split(" ")[0].split("(")[0]
    adlar = _DIL_KODLARI.get(hedef, (hedef.lower(),))
    return ilk in adlar or ilk.split("-")[0] in adlar and hedef != "pt-BR" and not ilk.endswith("-br")


def hedef_dilde_gomulu_var(video, hedef):
    """Videoda (gömülü ya da yanındaki dosya) hedef dilde altyazı var mı."""
    return any(dil_eslesir(d, hedef) for d in (video.gomulu_diller or ()))


def karma_anahtari(v):
    """Servisin hesapladığı karmayı eklentinin bulması için: dosya adı ve süre (eşlemeden bağımsız)."""
    from . import kayit
    return kayit.ozet(f"{v.dosya_adi}|{int(v.sure or 0)}", 16)


def ad_eksiz(ad):
    """Adın sonundaki ülke ya da yıl eki atılmış hâli ("The Office (US)" -> "The Office"); ek yoksa aynısı."""
    sade = re.sub(r"\s*[(\[]?\b(?:US|UK|AU|NZ|CA|19\d\d|20\d\d)\b[)\]]?\s*$", "", ad or "").strip()
    return sade or ad
