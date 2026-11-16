"""Kalıcı durum: küçük JSON dosyaları.

Her dosya önce geçici bir ada yazılır, sonra yerine konur: yarım yazılmış dosya kalmaz. Eklenti ile servis aynı anda
yazabildiği bilgiler (seçim, tercih, site kotası) içerik ya da site başına ayrı küçük dosyalardadır; aynı dosyaya aynı
anda iki yazım olursa sonuncusu geçerli olur (son seçilen kazanır). Kaymış işaretlerini yalnız eklenti yazar.
"""
import hashlib
import json
import os
import threading
import time

_sayac = 0
_kilit = threading.Lock()


def ozet(metin, uzunluk=16):
    return hashlib.sha1(str(metin).encode("utf-8")).hexdigest()[:uzunluk]


def kodi_dil_eki(kod):
    """Dosya adının sonuna konan dil eki: servis altyazıyı Player.setSubtitles ile yüklediğinde Kodi dili dosya adından
    okur (sondan başa, " .-" ile ayrılmış parçalarda ilk tanıdığı dil kodu). "pt-BR" ayrılacağı için Kodi'nin "pb"si."""
    return {"pt-BR": "pb"}.get(kod, str(kod or "").split("-")[0].lower())


def oku(yol, varsayilan=None):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return varsayilan


def gecici_ad(yol):
    """Aynı dosyaya aynı anda iki yazan (eklenti ve servis, iki iş parçacığı) birbirinin geçici dosyasını ezmesin."""
    global _sayac
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with _kilit:
        _sayac += 1
        return f"{yol}.{os.getpid()}.{threading.get_ident()}.{_sayac}.yeni"


def yerine_koy(gecici, yol):
    for tur in range(5):                    # Windows'ta virüs tarayıcısı bir an kilitleyebilir
        try:
            os.replace(gecici, yol)
            return yol
        except PermissionError:
            if tur == 4:
                sil(gecici)
                raise
            time.sleep(0.1)


def yaz(yol, veri):
    gecici = gecici_ad(yol)
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)
    yerine_koy(gecici, yol)


def metin_yaz(yol, metin):
    """Yazı dosyası (UTF-8, satır sonu \\n), önce geçici ada yazılıp yerine konur."""
    gecici = gecici_ad(yol)
    with open(gecici, "w", encoding="utf-8", newline="\n") as f:
        f.write(metin)
    return yerine_koy(gecici, yol)


def kopyala(kaynak, yol):
    import shutil
    gecici = gecici_ad(yol)
    shutil.copyfile(kaynak, gecici)
    return yerine_koy(gecici, yol)


def sil(yol):
    try:
        os.remove(yol)
        return True
    except OSError:
        return False


class Depo:
    """Bir klasördeki adlandırılmış JSON değerleri (site oturumu, kota, anahtar geçmişi gibi)."""

    def __init__(self, klasor):
        self.klasor = klasor

    def _yol(self, ad):
        return os.path.join(self.klasor, ad + ".json")

    def oku(self, ad, varsayilan=None):
        return oku(self._yol(ad), varsayilan)

    def yaz(self, ad, veri):
        yaz(self._yol(ad), veri)

    def sil(self, ad):
        return sil(self._yol(ad))


class Durum:
    """Eklentinin bütün kalıcı durumu tek kökten: profil klasörü."""

    def __init__(self, kok):
        self.kok = kok
        self.depo = Depo(os.path.join(kok, "durum"))

    def yol(self, *parcalar):
        return os.path.join(self.kok, *parcalar)

    # ------------------------------------------------------------------------------------ seçim ve tercih
    def secim(self, video_anahtari):
        return oku(self.yol("secim", ozet(video_anahtari) + ".json"))

    def secim_yaz(self, video_anahtari, kayit):
        kayit = dict(kayit, zaman=time.time(), video=video_anahtari)
        yaz(self.yol("secim", ozet(video_anahtari) + ".json"), kayit)

    def secili_yollar(self):
        """Bütün videolarda seçili altyazıların dosyaları: kendiliğinden temizlik bunlara dokunmaz."""
        klasor, yollar = self.yol("secim"), set()
        for ad in os.listdir(klasor) if os.path.isdir(klasor) else []:
            yol = (oku(os.path.join(klasor, ad), {}) or {}).get("yol")
            if yol:
                yollar.add(os.path.normcase(os.path.normpath(yol)))
        return yollar

    def tercih(self, yapim):
        return oku(self.yol("tercih", ozet(yapim) + ".json"), {}) or {}

    def tercih_yaz(self, yapim, grup, yukleyen, aile=None):
        """Bu yapımda son seçilen altyazı: grubu, yükleyeni ve ailesi (liste.aile_imzasi). Son seçim kazanır."""
        if grup or yukleyen or aile:
            yaz(self.yol("tercih", ozet(yapim) + ".json"), {"grup": grup or "", "yukleyen": yukleyen or "",
                                                           "aile": aile or None, "zaman": time.time()})

    # ---------------------------------------------------------------------------------------- kaymış
    def kaymislar(self, video_anahtari):
        """anahtar -> işaretlenme zamanı."""
        return (oku(self.yol("kaymis", ozet(video_anahtari) + ".json"), {}) or {}).get("isaretler", {})

    def kaymis_isaretle(self, video_anahtari, *anahtarlar):
        isaretler = self.kaymislar(video_anahtari)
        simdi = time.time()
        for n, a in enumerate(anahtarlar):
            if a:
                isaretler[a] = simdi + n * 1e-3      # aynı anda işaretlenenlerin sırası korunur
        yaz(self.yol("kaymis", ozet(video_anahtari) + ".json"), {"isaretler": isaretler, "video": video_anahtari})

    # ------------------------------------------------------------------------------------ site kotası
    def hak_doldu(self, site):
        """Sitenin günlük indirme hakkı dolduysa yenilenme zamanı (yazı), değilse None."""
        k = self.depo.oku("kota_" + site)
        if not k:
            return None
        if time.time() < k.get("bitis", 0):
            return k.get("yenilenme") or ""
        self.depo.sil("kota_" + site)
        return None

    def hak_doldu_yaz(self, site, yenilenme_metni, bitis):
        self.depo.yaz("kota_" + site, {"yenilenme": yenilenme_metni or "", "bitis": bitis})

    # ---------------------------------------------------------------------------------------- çeviriler
    def ceviri_klasoru(self, icerik):
        return self.yol("ceviriler", ozet(icerik))

    def hazir_ceviriler(self, icerik, hedef_dil):
        """Bu içeriğin bitmiş çevirileri (en yenisi başta)."""
        klasor = self.ceviri_klasoru(icerik)
        sonuc = []
        try:
            adlar = os.listdir(klasor)
        except OSError:
            return sonuc
        for ad in adlar:
            if not ad.endswith(".json"):
                continue
            bilgi = oku(os.path.join(klasor, ad))
            if not bilgi or bilgi.get("hedef_dil") != hedef_dil:
                continue
            srt_yolu = os.path.join(klasor, bilgi.get("dosya") or ad[:-5] + ".srt")
            if os.path.exists(srt_yolu):
                bilgi["yol"] = srt_yolu
                if bilgi.get("kaynak_dosya"):
                    bilgi["kaynak_yol"] = os.path.join(klasor, bilgi["kaynak_dosya"])
                sonuc.append(bilgi)
        sonuc.sort(key=lambda b: -b.get("zaman", 0))
        return sonuc

    def sozluk_yolu(self, yapim, hedef_dil):
        ad = "".join(k if k.isalnum() else "_" for k in yapim)[:80].strip("_") or "adsiz"
        ek = "" if hedef_dil.split("-")[0].lower() == "tr" else "." + hedef_dil.lower()
        return self.yol("sozlukler", ad + ek + ".json")
