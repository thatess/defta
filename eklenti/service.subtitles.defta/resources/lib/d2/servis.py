"""Arka plan servisi: çeviri işleri, oynatma başında otomatik yükleme, sıradaki bölüm, kendiliğinden temizlik.

Çeviri işleri burada çalışır (altyazı penceresi kısa ömürlüdür). Video kapanınca o videonun çevirisi durur (sıradaki
bölümün ön çevirisi hariç), yeniden açılınca kaldığı yerden sürer. Kodi kapanırken işler durdurulur ve yoldaki
istekler kesilir: iş parçacığı açık kalmaz.
"""
import json
import os
import threading
import time
import traceback
import urllib.request

import xbmc
import xbmcvfs

from . import ag, arama, bakim, ceviri_isi, eslesme, gizli, kayit, kodi, liste, metin, puan
from . import video as video_modulu
from .giris import ayarlar, bicim_ayarlari, dil_adi, is_kimligi, ceviri_kimligi, _bolum_etiketi

# ------------------------------------------------------------------------- kapanışta yoldaki istekleri kesmek
def _cekirdek_acicisini_kur():
    """Çekirdeğin OpenAI istekleri de soketini kayda geçiren açıcıyla gider (yönlendirme yine izlenmez): Kodi
    kapanırken ag.kes() onları da keser."""
    from defta import model
    model._ACICI = urllib.request.build_opener(model._YonlendirmeYok, ag.KayitliHTTPSIsleyici())


def _sure_yazisi(sn):
    sn = int(round(sn))
    return f"{sn // 60} dk {sn % 60} sn" if sn >= 60 else f"{sn} sn"


def _maliyet_yazisi(usd):
    return f"{usd:.2f}".replace(".", ",")


HIZLI_YENIDEN = 4      # sn: hızlı çeviri yüklendikten sonra bir kez daha yüklenmeden önceki bekleme


class Izleyici(xbmc.Monitor):
    def __init__(self, servis):
        super().__init__()
        self.servis = servis

    def onNotification(self, gonderen, yontem, veri):
        if gonderen != kodi.KIMLIK:
            return
        try:
            icerik = json.loads(veri) if veri else {}
        except ValueError:
            icerik = {}
        if isinstance(icerik, str):                     # Kodi bazen veriyi bir kez daha JSON yazısına sarar
            try:
                icerik = json.loads(icerik)
            except ValueError:
                icerik = {}
        self.servis.mesaj(yontem.split(".", 1)[-1], icerik or {})

    def onSettingsChanged(self):
        self.servis.ayarlar_degisti()


class Oynatici(xbmc.Player):
    def __init__(self, servis):
        super().__init__()
        self.servis = servis

    def onAVStarted(self):
        self.servis.oynatma_basladi()

    def onPlayBackStopped(self):
        self.servis.oynatma_bitti()

    def onPlayBackEnded(self):
        self.servis.oynatma_bitti()

    def onPlayBackError(self):
        self.servis.oynatma_bitti()


class Servis:
    def __init__(self):
        self.durum = kayit.Durum(kodi.profil())
        self.isler = {}                     # is_id -> (CeviriIsi, iş parçacığı)
        self.kilit = threading.RLock()
        self.parcaciklar = []               # arka plan iş parçacıkları: kapanışta kısa süre beklenir
        self.anahtar_bildirildi = set()     # anahtarı eksik siteler: arka planda oturumda bir kez bildirilir
        self.video = None                   # oynatılan video (karmasız)
        self.siradaki_bakildi = set()
        self.izleyici = None
        self.son_temizlik = 0

    # ------------------------------------------------------------------------------------------- döngü
    def calistir(self):
        ag.kullanici_ajani(kodi.surum())
        _cekirdek_acicisini_kur()
        self.izleyici = Izleyici(self)
        oynatici = Oynatici(self)
        kodi.kayit(f"servis başladı ({kodi.surum()})")
        self._anahtar_gecmisi_kur()
        if xbmc.Player().isPlayingVideo():
            self.oynatma_basladi()
        while not self.izleyici.abortRequested():
            if self.izleyici.waitForAbort(1):
                break
            try:
                self._dongu()
            except Exception as e:
                kodi.kayit("servis döngüsü: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)),
                           hata=True)
        self.kapat()
        del oynatici

    def _arka(self, hedef, *args, **kwargs):
        """Arka plan iş parçacığı (kapanışta beklenir)."""
        p = threading.Thread(target=hedef, args=args, kwargs=kwargs, daemon=True)
        with self.kilit:
            self.parcaciklar = [x for x in self.parcaciklar if x.is_alive()] + [p]
        p.start()
        return p

    def kapat(self):
        with self.kilit:
            isler = list(self.isler.values())
            diger = list(self.parcaciklar)
        for is_, _ in isler:
            is_.durdur()
        ag.kes()                            # yoldaki bütün istekler (çeviri, arama, eşleme listesi) kesilir
        son = time.time() + 6
        for p in [p for _, p in isler] + diger:
            p.join(max(0.1, son - time.time()))
        acik = sum(1 for p in [p for _, p in isler] + diger if p.is_alive())
        kodi.ozellik_sil("isler")
        kodi.kayit("servis durdu" + (f" ({acik} iş parçacığı bitmedi)" if acik else ""))

    def _dongu(self):
        self._durum_yayinla()
        simdi = time.time()
        if simdi - self.son_temizlik > 6 * 3600:
            self.son_temizlik = simdi
            self._arka(self._temizlik)
        self._siradaki_denetle()

    def _esleme(self, v):
        """Eşleme listesi ilk anime açılışında indirilir, sonra ayardaki sıklıkla yenilenir."""
        anime = bool(v.anilist) or (kodi.ayar_bool("jimaku_kullan") and "ja" in ayarlar()["kaynak_diller"]
                                    and v.tur == "dizi")
        if not anime:
            return
        self._arka(eslesme.guncelle, self.durum, gun=kodi.ayar_int("esleme_siklik", 7), kayit_yaz=kodi.kayit)

    def _temizlik(self):
        try:
            with self.kilit:
                aktif = set(self.isler)
            sayi, bayt = bakim.kendiliginden(self.durum, aktif)
        except Exception as e:
            kodi.kayit(f"temizlik: {e}", hata=True)
            return
        if sayi:
            mb = f"{bayt / 1e6:.1f}".replace(".", ",")
            kodi.kayit(f"kendiliğinden temizlik: {sayi} dosya, {mb} MB")     # bildirim yok: kullanıcının işi değil

    def _durum_yayinla(self):
        with self.kilit:
            ozet = {}
            for is_, parcacik in self.isler.values():
                if not parcacik.is_alive() or is_.iptal.is_set():     # durdurulan iş yoldakileri bitiriyor
                    continue
                b = is_.bilgi
                ozet[b["video_anahtari"]] = {"is_id": b["is_id"], "yuzde": round(is_.yuzde, 1),
                                             "ad": (b.get("kaynak") or {}).get("adlar", [""])[0],
                                             "ceviri_anahtari": "ai:" + b["ceviri_kimligi"]}
        kodi.ozellik("isler", json.dumps(ozet, ensure_ascii=False))

    # ---------------------------------------------------------------------------------------- mesajlar
    def mesaj(self, ad, veri):
        kodi.kayit(f"mesaj: {ad} {json.dumps({k: v for k, v in veri.items() if k != 'video'}, ensure_ascii=False)}",
                   ayrinti=True)
        if ad in ("cevir", "devam"):
            self.is_baslat(veri.get("is_id", ""), veri.get("baslangic", 0.0), veri.get("sure", 2),
                           hizli=veri.get("hizli", True), devam=(ad == "devam"), video_anahtari=veri.get("video", ""),
                           yukle=bool(veri.get("yukle")))
        elif ad == "durdur":
            self.durdur_video(veri.get("video", ""), haric=veri.get("haric", ""))
        elif ad == "hepsini_durdur":                    # bakım: silmeden önce bütün işler durur
            self._arka(self.hepsini_durdur, veri.get("cevap", ""))

    def hepsini_durdur(self, cevap=""):
        """Bakım silmeden önce: işler sert durdurulur (yoldakiler beklenmez), iş parçacıkları bitince yanıt verilir."""
        with self.kilit:
            isler = list(self.isler.values())
        for is_, _ in isler:
            is_.durdur(sert=True)
        son = time.time() + 20
        for _, p in isler:
            p.join(max(0.1, son - time.time()))
        for is_, _ in isler:                            # hızlı çeviriler de bitsin: silinen klasöre yazmasınlar
            is_.hizli_bitsin(max(0.1, son - time.time()))
        if cevap:
            kodi.ozellik(cevap, "1")

    def durdur_video(self, video_anahtari, haric="", on_ceviri_de=False):
        with self.kilit:
            hedefler = [is_ for is_, p in self.isler.values() if p.is_alive()
                        and is_.bilgi.get("video_anahtari") == video_anahtari
                        and "ai:" + is_.bilgi.get("ceviri_kimligi", "") != haric
                        and (on_ceviri_de or not is_.bilgi.get("on_ceviri"))]
        for is_ in hedefler:
            kodi.kayit(f"çeviri durduruluyor: {is_.bilgi['is_id']}")
            is_.durdur()

    # ------------------------------------------------------------------------------------------- işler
    def is_baslat(self, is_id, baslangic=0.0, sure=2, hizli=True, devam=False, otomatik=False, kendiliginden=False,
                  video_anahtari="", yukle=False):
        """video_anahtari: işi başlatan video. Yarım iş aynı bölümün başka sürümünde (başka akış) sürdürülünce iş o
        videonun olur: biten çeviri ona yüklenir, o video kapanınca ya da orada başka altyazı seçilince durur.
        yukle: altyazı penceresi sonucu beklemiyor (orijinali yükledi); hızlı çeviriyi hazır olunca servis yükler."""
        bilgi = ceviri_isi.is_bilgisi(self.durum, is_id)
        anahtar = kodi.ayar("openai_anahtar")
        if not bilgi or not anahtar:        # neden, "alındı" bilgisinden önce yazılır: pencere ilk bakışta görür
            kodi.ozellik("sonuc." + is_id, json.dumps({"hata": metin.al(metin.B_ANAHTAR_EKSIK, site="OpenAI") if bilgi
                                                       else metin.al(metin.H_IS_YOK)}))
            kodi.ozellik("alindi." + is_id, "1")
            return
        kodi.ozellik("alindi." + is_id, "1")
        with self.kilit:                    # denetim ve kayıt tek adımda: aynı iş iki kez başlamaz
            onceki = self.isler.get(is_id)
            if onceki and onceki[1].is_alive() and not onceki[0].iptal.is_set():
                if video_anahtari:
                    onceki[0].bilgi["video_anahtari"] = video_anahtari
                if hizli:                   # süren işi tekrar seçince: biten kısım + bulunduğun yerin hızlı çevirisi
                    self._hizli_baslat(onceki[0], baslangic, sure, otomatik, yukle=yukle)
                return
            eski = onceki if onceki and onceki[1].is_alive() else None    # durduruldu, yoldakileri bekliyor
            # İş kendi hedef diliyle sürer: ayar sonradan değiştiyse yarım iş başka dile baştan çevrilmez
            is_ = ceviri_isi.CeviriIsi(self.durum, bilgi, anahtar,
                                       dict(bicim_ayarlari(), **({"hedef_dil": bilgi["hedef_dil"]}
                                                                 if bilgi.get("hedef_dil") else {})))
            parcacik = threading.Thread(target=self._calistir, args=(is_, baslangic, sure, hizli, devam, otomatik, eski,
                                                                    otomatik or kendiliginden, video_anahtari, yukle),
                                        daemon=True)
            self.isler[is_id] = (is_, parcacik)
            parcacik.start()                # kilit içinde: kayıttaki iş hep çalışır görünür (ikinci istek, durdurma)

    def _hizli_baslat(self, is_, baslangic, sure, otomatik, bildir=False, yukle=False):
        """Hızlı çeviri ayrı iş parçacığında, kaliteli çeviriyle aynı anda. Kaliteli çeviri bitince iş klasörünü
        silmeden önce bunun bitmesini bekler (hizli_bitti): hızlı çeviri hep önce yüklenir."""
        is_.hizli_basladi()
        try:
            return self._arka(self._hizli_akis, is_, baslangic, sure, otomatik, bildir, yukle)
        except BaseException:
            is_.hizli_bitti()
            raise

    def _hizli_akis(self, is_, baslangic, sure, otomatik, bildir, yukle=False):
        """Hızlı çeviri hazır olunca, seçili altyazı hâlâ buysa servis yükler (altyazı penceresi beklemez: orijinali
        yükleyip kapanmıştır)."""
        b = is_.bilgi
        try:
            yol = is_.hizli(baslangic, sure)
            self._bitince_yukle(b, yol)
            if bildir:
                kodi.bildir(metin.al(metin.B_HIZLI_HAZIR),
                            metin.al(metin.B_HIZLI_HAZIR_YAZI, dakika=sure, dil=dil_adi(b.get("hedef_dil", "tr"))))
            if self.izleyici is not None:
                # Altyazı penceresi orijinali Kodi'ye verir; Kodi onu bizim yüklediğimiz hızlı çeviriden SONRA
                # yüklerse (büyük dosya, yavaş cihaz) hızlı çeviri görünmeden kaybolur. Birkaç saniye sonra seçili
                # altyazı hâlâ bu hızlı çeviriyse bir kez daha yüklenir. Bekleme ayrıca Kodi dosyayı açana kadar
                # biten çevirinin iş klasörünü silmesini önler.
                if not self.izleyici.waitForAbort(HIZLI_YENIDEN) and (self.durum.secim(b["video_anahtari"]) or {}).get("yol") == yol:
                    self._yukle(b["video_anahtari"], yol)
                    self.izleyici.waitForAbort(2)
        except ceviri_isi.Iptal:
            pass
        except Exception as e:              # orijinal yüklü, çevirinin tamamı sürüyor: bitince o yüklenir.
            kodi.kayit(f"hızlı çeviri olmadı: {gizli.gizle(str(e))}", hata=True)     # kota bittiyse onu o bildirir
        finally:
            is_.hizli_bitti()

    def _calistir(self, is_, baslangic, sure, hizli, devam, otomatik, eski=None, kendiliginden=False,
                  video_anahtari="", yukle=False):
        b = is_.bilgi
        is_id = b["is_id"]
        hedef = b.get("hedef_dil", "tr")
        if eski is not None:                # aynı iş az önce durduruldu: yoldaki istekleri kaydedilsin, sonra sürer
            eski_is, eski = eski
            eski.join(200)                  # çekirdeğin istek zaman aşımı 180 sn
            eski_is.hizli_bitsin(30)        # eskisinin hızlı çevirisi de bitsin: ikisi aynı dosyalara yazmasın
            if eski.is_alive():
                kodi.bildir(metin.al(metin.B_CEVIRI_HATASI), metin.al(metin.H_ONCEKI_DURMADI), hata=True)
                with self.kilit:
                    if self.isler.get(is_id, (None,))[0] is is_:
                        del self.isler[is_id]
                return
            guncel = ceviri_isi.is_bilgisi(self.durum, is_id)
            if guncel:                      # eski işin yazdığı maliyet ve süre kaybolmasın
                is_.bilgi.clear()
                is_.bilgi.update(guncel)
            if not guncel:                                              # o arada bitirmiş: çeviri hazır
                hazir = next((h for h in self.durum.hazir_ceviriler(b["icerik"], hedef)
                              if h.get("kimlik") == b["ceviri_kimligi"]), None)
                if hazir:
                    self._bitince_yukle(b, hazir["yol"])
                else:
                    kodi.bildir(metin.al(metin.B_CEVIRI_HATASI), metin.al(metin.H_IS_YOK), hata=True)
                with self.kilit:
                    if self.isler.get(is_id, (None,))[0] is is_:
                        del self.isler[is_id]
                return
        if video_anahtari:
            b["video_anahtari"] = video_anahtari
        is_.ayar_degistiyse_sifirla()      # çeviriyi etkileyen ayar değiştiyse iş baştan (madde 26)
        # Hata sayacı yalnız kullanıcı kendisi başlatınca sıfırlanır (kendiliğinden sürenlerde birikir)
        is_.kaydet(durum="suruyor", hata_sayisi=b.get("hata_sayisi", 0) if kendiliginden else 0)
        try:
            if hizli:
                if devam and is_.onbellek():
                    kodi.bildir(metin.al(metin.B_DEVAM), "")
                # Kaliteli çeviri hızlıyı beklemeden başlar (hızlının süresi kadar erken biter)
                self._hizli_baslat(is_, baslangic, sure, otomatik, bildir=not devam, yukle=yukle)
            yol, bilgi = is_.kaliteli()
            kodi.kayit(f"çeviri bitti: {is_id} ({bilgi['sure']:.0f} sn, {bilgi['maliyet']:.4f} USD)")
            kodi.bildir(metin.al(metin.B_CEVIRI_TAMAM), metin.al(metin.B_SURE_MALIYET, sure=_sure_yazisi(bilgi["sure"]),
                                                                  maliyet=_maliyet_yazisi(bilgi["maliyet"])))
            self._bitince_yukle(b, yol)
            self._kodi_kopyasini_sil(b, bilgi)
        except ceviri_isi.Iptal:
            is_.oturumu_kaydet()
            is_.kaydet(durum="durdu")
            kodi.kayit(f"çeviri durdu: {is_id}")
            if not b.get("on_ceviri"):
                kodi.bildir(metin.al(metin.B_CEVIRI_DURDU), metin.al(metin.B_KALDIGI_YER))
        except ceviri_isi.BakiyeBitti as e:
            is_.oturumu_kaydet()
            is_.kaydet(durum="durdu")
            kodi.kayit(f"OpenAI bakiyesi bitti: {e}", hata=True)
            kodi.bildir(metin.al(metin.B_BAKIYE_BITTI), metin.al(metin.B_DURDU_KAYITLI), hata=True)
        except Exception as e:
            is_.oturumu_kaydet()
            is_.kaydet(durum="hata", hata=str(e), hata_sayisi=is_.bilgi.get("hata_sayisi", 0) + 1)
            kodi.kayit("çeviri hatası: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)), hata=True)
            kodi.bildir(metin.al(metin.B_CEVIRI_HATASI), str(e), hata=True)
        finally:
            with self.kilit:
                if self.isler.get(is_id, (None,))[0] is is_:
                    del self.isler[is_id]

    def _bitince_yukle(self, b, yol):
        """Bitmiş çeviri, hâlâ o video oynuyor ve seçili altyazı buysa yerine konur."""
        va = b["video_anahtari"]
        secili = self.durum.secim(va) or {}
        if secili.get("anahtar") == "ai:" + b["ceviri_kimligi"]:
            secili["yol"] = yol
            self.durum.secim_yaz(va, secili)
            self._yukle(va, yol)

    def _kodi_kopyasini_sil(self, b, bilgi):
        """"Çevir"e basınca pencere orijinal altyazıyı verir; Kodi onu videonun yanına (ya da kendi altyazı klasörüne)
        <video>.<dil>.<uzantı> adıyla kaydeder ve sonraki açılışta kendiliğinden seçebilir. Çeviri bitince o kopyaya
        gerek kalmaz: silinir. Yalnız içeriği kaynağımızla aynı olan dosya silinir (kullanıcının kendi altyazısına
        dokunulmaz); satır sonu farkı sayılmaz (yeniden çeviride kaynak yeniden yazılır). Akışta Kodi geçici klasöre
        yazar: bakılmaz."""
        try:
            v = self.video
            if v is None or v.akis or not v.yol or v.video_anahtari() != b["video_anahtari"]:
                return
            kaynak_yolu = os.path.join(self.durum.ceviri_klasoru(b["icerik"]), bilgi.get("kaynak_dosya") or "")
            if not os.path.isfile(kaynak_yolu):
                return
            with open(kaynak_yolu, "rb") as f:
                kaynak = f.read().replace(b"\r\n", b"\n")
            ayrac = "/" if "://" in v.yol else os.sep
            klasor, _, video_adi = v.yol.rpartition(ayrac)
            govde, uzanti = os.path.splitext(video_adi)[0], os.path.splitext(kaynak_yolu)[1].lower()
            klasorler = [klasor]
            if kodi.kodi_ayari("subtitles.storagemode") == 1 and kodi.kodi_ayari("subtitles.custompath"):
                klasorler.append(str(kodi.kodi_ayari("subtitles.custompath")).rstrip("/\\"))
            for k in klasorler:
                a = "/" if "://" in k else os.sep
                for ad in xbmcvfs.listdir(k)[1]:
                    if not (ad.startswith(govde + ".") and ad.lower().endswith(uzanti)) or ad == video_adi:
                        continue
                    dosya = xbmcvfs.File(k + a + ad)
                    try:                    # altyazı küçüktür; büyük dosya (yanlış eşleşen ad) okunmaz
                        ayni = (dosya.size() <= 2 * len(kaynak) + 1024
                                and bytes(dosya.readBytes()).replace(b"\r\n", b"\n") == kaynak)
                    finally:
                        dosya.close()
                    if ayni and xbmcvfs.delete(k + a + ad):
                        kodi.kayit(f"Kodi'nin kaydettiği orijinal altyazı silindi: {ad}")
        except Exception as e:                  # temizlik çeviriyi etkilemesin
            kodi.kayit(f"orijinal altyazının kopyası silinemedi: {type(e).__name__}")

    def _yukle(self, video_anahtari, yol):
        if self.video is not None and self.video.video_anahtari() == video_anahtari and xbmc.Player().isPlayingVideo():
            xbmc.Player().setSubtitles(yol)

    # ------------------------------------------------------------------------------------------ oynatma
    def oynatma_basladi(self):
        self._arka(self._oynatma_basladi)

    def _oynatma_basladi(self):
        if self.izleyici is not None and self.izleyici.waitForAbort(1.5):    # oynatan eklentinin bilgisi dolsun
            return
        try:
            v = video_modulu.oynatilan(xbmc, xbmcvfs, karma=False)
            if not xbmc.Player().isPlayingVideo():
                return
            eslesme.uygula(self.durum, v)               # eklentiyle aynı anahtarlar (yalnız AniList veren eklentiler)
            onceki, self.video = self.video, v
            if onceki is not None and onceki.video_anahtari() != v.video_anahtari():
                self.durdur_video(onceki.video_anahtari())    # bir video ötekinin yerine geçti (durma olayı gelmeyebilir)
            karma_p = self._arka(self._karma_yayinla, v)
            self.siradaki_bakildi.discard(v.video_anahtari())
            kodi.kayit("oynatma: " + v.ozet())
            self._esleme(v)
            self._kodi_otomatik_sor()
            if self.video is not v or not xbmc.Player().isPlayingVideo():
                return                      # soru kutusu açıkken video kapandı ya da değişti: iş başlatılmaz
            if self._yarim_isi_surdur(v):
                return
            self._otomatik(v, karma_p)
        except Exception as e:
            kodi.kayit("oynatma başında: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)), hata=True)

    def _karma_yayinla(self, v):
        """Video karması oynatma başında bir kez hesaplanır, altyazı penceresi bunu kullanır (akışta 8 sn sürebilir)."""
        try:
            karma = video_modulu._karma(xbmcvfs, v) if (v.akis or v.yol) else None
        except Exception:
            karma = None
        kodi.ozellik("karma." + video_modulu.karma_anahtari(v), karma or "-")
        if self.video is v and not v.karma:
            v.karma = karma

    def oynatma_bitti(self):
        v, self.video = self.video, None
        if v is not None:
            self.durdur_video(v.video_anahtari())

    def _yarim_isi_surdur(self, v):
        """Video yeniden açılınca yarım kalan çevirisi kaldığı yerden sürer; biten kısım hemen yüklenir. Sıradaki
        bölümün ön çevirisi bitmeden o bölüm açıldıysa iş bu videonun olur ve aynı yoldan sürer."""
        if not kodi.ayar("openai_anahtar"):         # iş süremez: seçime dokunulmaz, otomatik yükleme atlanmaz
            return False
        secili = self.durum.secim(v.video_anahtari()) or {}
        for bilgi in ceviri_isi.yarim_isler(self.durum):
            kimlik = bilgi.get("ceviri_kimligi", "")
            if bilgi.get("on_ceviri"):
                if bilgi.get("icerik") != v.icerik_anahtari():
                    continue
                guncel = {"video_anahtari": v.video_anahtari(), "on_ceviri": False}
                bilgi.update(guncel)
                kayit.yaz(self.durum.yol("isler", bilgi["is_id"], "is.json"), bilgi)
                with self.kilit:
                    calisan = self.isler.get(bilgi["is_id"])
                    if calisan:
                        calisan[0].bilgi.update(guncel)
                k = bilgi.get("kaynak") or {}
                self.durum.secim_yaz(v.video_anahtari(), arama.secim_kaydi(
                    "ai", "ai:" + kimlik, (k.get("adlar") or [""])[0], k.get("site", ""), bilgi.get("hedef_dil"),
                    self.durum.yol("isler", bilgi["is_id"], bilgi.get("kaynak_dosya", "")), v, kaynak=k,
                    is_id=bilgi["is_id"]))
            elif bilgi.get("video_anahtari") != v.video_anahtari() or secili.get("anahtar") != "ai:" + kimlik:
                continue
            if bilgi.get("hata_sayisi", 0) >= 3:            # üst üste hata: kendiliğinden sürmez, seçilince sürer
                kodi.kayit(f"yarım çeviri {bilgi['hata_sayisi']} kez hata verdi, kendiliğinden sürmüyor: {bilgi['is_id']}")
                continue
            # Yeniden çeviri yarıda kaldıysa eskisi yerinde kalır (madde 27): yalnız arkada sürer, bitince yüklenir.
            yeniden = bool(bilgi.get("yeniden"))
            kodi.kayit(f"yarım çeviri sürüyor: {bilgi['is_id']}" + (" (yeniden çeviri)" if yeniden else ""))
            self.is_baslat(bilgi["is_id"], video_modulu.oynatma_yeri(xbmc), kodi.ayar_int("hizli_sure", 2),
                           hizli=not yeniden and kodi.hizli_acik(), devam=True, otomatik=not yeniden,
                           kendiliginden=True)
            return not yeniden
        return False

    def _kodi_otomatik_sor(self):
        """Kodi'nin "İlk altyazıyı otomatik indir" ayarı açıksa bir kez sorulur; habersiz Kodi ayarı değişmez."""
        if self.durum.depo.oku("kodi_otomatik_soruldu"):
            return
        if not kodi.kodi_ayari("subtitles.downloadfirst"):
            return
        self.durum.depo.yaz("kodi_otomatik_soruldu", {"zaman": time.time()})
        if kodi.onay(metin.al(metin.P_KODI_OTOMATIK)):
            kodi.ayar_yaz("oto_hedef", True)
            kodi.kodi_ayari_yaz("subtitles.downloadfirst", False)

    # --------------------------------------------------------------------------------- otomatik yükleme
    def _hala_bu_video(self, v, ilk_secim):
        """Arama sürerken başka video açılmadı ve kullanıcı kendisi altyazı seçmedi mi (seçim baştakiyle aynı)."""
        return (self.video is v and self.durum.secim(v.video_anahtari()) == ilk_secim
                and xbmc.Player().isPlayingVideo())

    def _otomatik(self, v, karma_p=None):
        oto_hedef, oto_ai = kodi.ayar_bool("oto_hedef"), kodi.ayar_int("oto_ai", 0)
        ay = ayarlar()
        hedef = ay["hedef_dil"]
        if v.canli or not v.tanindi:
            return
        if video_modulu.hedef_dilde_gomulu_var(v, hedef):
            if oto_hedef or oto_ai:
                kodi.kayit("otomatik: videoda hedef dilde altyazı var")
            return
        secili = ilk_secim = self.durum.secim(v.video_anahtari())
        if secili and arama.var_mi(secili.get("yol")):
            if oto_ai if secili.get("tur") == "ai" else oto_hedef:
                xbmc.Player().setSubtitles(secili["yol"])
            return
        if (oto_hedef or oto_ai) and karma_p is not None:
            karma_p.join(10)                # oynatma başında hesaplanan karma (bir daha okunmaz)
            if not self._hala_bu_video(v, ilk_secim):
                return                      # beklerken video değişti ya da kullanıcı kendisi altyazı seçti
        # Bu videoda kaymış diye işaretlenen altyazı kendiliğinden yüklenmez, paralı çeviriye kaynak da olmaz
        kaymislar = self.durum.kaymislar(v.video_anahtari())
        hazirlar = [h for h in liste.hazir_puanla(self.durum.hazir_ceviriler(v.icerik_anahtari(), hedef), v)
                    if "ai:" + h.get("kimlik", "") not in kaymislar]
        # Kullanıcının onayladığı sıradaki bölüm çevirisi (ön çeviri) AI ayarı kapalı olsa da yüklenir
        on_ceviriler = [h for h in hazirlar if h.get("on_ceviri")]
        secilecek = (hazirlar if oto_ai else on_ceviriler)
        if secilecek:
            h = liste.hedef_sirasi(secilecek, [], {}, {}, ay["sdh_alta"])[0][2]
            k = arama.hazirdan_aday(h)
            self.durum.secim_yaz(v.video_anahtari(), arama.secim_kaydi(
                "ai", "ai:" + h["kimlik"], k.ad if k else "", k.site if k else "", hedef, h["yol"], v,
                uyumlu=bool(k and puan.uyumlu_mu(h.get("puan", 0), h.get("kurgu"))), kaynak=h.get("kaynak")))
            xbmc.Player().setSubtitles(h["yol"])
            kodi.bildir(metin.al(metin.B_ALTYAZI_YUKLENDI), metin.al(metin.B_OTOMATIK, site=metin.al(metin.ON_AI)))
            return
        if not (oto_hedef or oto_ai == 2):
            return
        durum = self.durum
        site_listesi = arama.siteler(kodi.ayar, durum, eslesme=lambda vid: eslesme.anilist_bul(durum, vid))
        diller = [hedef] + (ay["kaynak_diller"] if oto_ai == 2 else [])
        adaylar, hatalar = arama.ara(site_listesi, v, diller, kodi.kayit)
        arama.hatalari_isle(hatalar, durum, kodi.bildir, kodi.kayit, bir_kez=self.anahtar_bildirildi)
        adaylar = liste.puanla(liste.birlestir(adaylar), v.dosya_adi or None)
        hak = {s.kod: 1 for s in site_listesi if durum.hak_doldu(s.kod) is not None}
        adaylar = liste.hak_dolanlari_ayikla(adaylar, hak)
        tercih = durum.tercih(v.yapim_anahtari())
        if oto_hedef:
            insanlar = [a for a in adaylar if a.dil == hedef and a.anahtar not in kaymislar]
            sira = liste.hedef_sirasi([], insanlar, {}, tercih, ay["sdh_alta"])
            for _, _, a in sira:
                if not self._hala_bu_video(v, ilk_secim):
                    return
                try:
                    yol, indirilen, ozet = arama.insan_altyazisi(a, v, site_listesi, durum, kodi.bildir, kodi.kayit)
                except Exception as e:
                    kodi.kayit(f"otomatik indirme olmadı: {a.site} · {a.ad}: {e}")
                    continue
                if not self._hala_bu_video(v, ilk_secim):
                    return
                durum.secim_yaz(v.video_anahtari(), arama.secim_kaydi(
                    "insan", a.anahtar, a.ad, indirilen.site, a.dil, yol, v, uyumlu=arama.uyumlu(a, v), sdh=a.sdh,
                    icerik_ozeti=ozet))
                durum.tercih_yaz(v.yapim_anahtari(), arama.grup(a), a.yukleyen, liste.aile_imzasi(a))
                xbmc.Player().setSubtitles(yol)
                kodi.bildir(metin.al(metin.B_ALTYAZI_YUKLENDI),
                            metin.al(metin.B_OTOMATIK, site=liste.SITE_ADLARI.get(indirilen.site, indirilen.site)))
                return
        if oto_ai == 2 and self._hala_bu_video(v, ilk_secim):
            kaynaklar = [a for a in adaylar if a.dil in ay["kaynak_diller"] and a.anahtar not in kaymislar]
            sira = liste.cevrilecek_sirasi(kaynaklar, {}, tercih, ay["sdh_alta"], ay["kaynak_diller"])
            if sira:
                self._otomatik_cevir(v, sira[0], site_listesi, ilk_secim)

    def _otomatik_cevir(self, v, aday, site_listesi, ilk_secim=None):
        """Otomatik çeviri: hızlı çeviri videonun başından, kalanı arka planda (aynı akış)."""
        if not kodi.ayar("openai_anahtar"):         # çeviri başlayamaz: kaynak indirilmez, seçim değişmez
            kodi.kayit("otomatik çeviri: OpenAI anahtarı yok")
            return
        hedef = kodi.ayar("hedef_dil") or "tr"
        is_id = is_kimligi(v, aday.anahtar, hedef)
        onceki = ceviri_isi.is_bilgisi(self.durum, is_id)
        if onceki and onceki.get("hata_sayisi", 0) >= 3:
            kodi.kayit(f"otomatik çeviri {onceki['hata_sayisi']} kez hata verdi, kendiliğinden başlamıyor: {is_id}")
            return
        if not onceki:
            try:
                metin_, uzanti, _ = arama.indir_metin(aday, v, site_listesi, self.durum, kodi.bildir, kodi.kayit)
            except Exception as e:
                kodi.kayit(f"otomatik çeviri kaynağı indirilemedi: {e}")
                return
            kimlik = ceviri_kimligi(aday)
            ceviri_isi.is_kur(self.durum, {
                "is_id": is_id, "video_anahtari": v.video_anahtari(), "icerik": v.icerik_anahtari(),
                "yapim": v.yapim_anahtari(), "baslik": (v.dizi_adi if v.tur == "dizi" else "") or v.baslik or v.ad,
                "bolum_etiketi": _bolum_etiketi(v), "goreli": f"{_bolum_etiketi(v)}/{kimlik}", "kaynak": aday.kayit(),
                "kaynak_dil": aday.dil, "hedef_dil": hedef, "ceviri_kimligi": kimlik, "on_ceviri": False,
                "video_karma": v.karma, "durum": "bekliyor"}, metin_, uzanti)
            if not self._hala_bu_video(v, ilk_secim):
                return                      # kaynak inerken video kapandı ya da altyazı seçildi: iş hazır bekler
        bilgi = ceviri_isi.is_bilgisi(self.durum, is_id)
        self.durum.secim_yaz(v.video_anahtari(), arama.secim_kaydi(
            "ai", "ai:" + bilgi["ceviri_kimligi"], aday.ad, aday.site, hedef,
            os.path.join(self.durum.yol("isler", is_id), bilgi["kaynak_dosya"]), v, kaynak=aday.kayit(), is_id=is_id))
        self.is_baslat(is_id, video_modulu.oynatma_yeri(xbmc), kodi.ayar_int("hizli_sure", 2), hizli=kodi.hizli_acik(),
                       otomatik=True, video_anahtari=v.video_anahtari())

    # ---------------------------------------------------------------------------------- sıradaki bölüm
    def _siradaki_denetle(self):
        v = self.video
        secim = kodi.ayar_int("siradaki", 1)
        if v is None or secim == 0 or v.tur != "dizi" or v.bolum is None or v.canli:
            return
        anahtar = v.video_anahtari()
        if anahtar in self.siradaki_bakildi:
            return
        oyn = xbmc.Player()
        if not oyn.isPlayingVideo():        # durduktan sonra sorulmaz: Kodi her soruyu hata diye kaydediyor
            return
        try:
            toplam, simdi = oyn.getTotalTime(), oyn.getTime()
        except RuntimeError:
            return
        if not toplam or simdi / toplam * 100 < kodi.ayar_int("siradaki_yer", 90):
            return
        self.siradaki_bakildi.add(anahtar)
        self._arka(self._siradaki, v, secim)

    def _siradaki(self, v, secim):
        try:
            from . import siradaki
            siradaki.bak(self, v, secim)
        except Exception as e:
            kodi.kayit("sıradaki bölüm: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)), hata=True)

    # -------------------------------------------------------------------------------------------- ayarlar
    # Yalnız API anahtarları: parolanın son karakterleri gösterilmez, dosyaya da yazılmaz
    _ANAHTAR_AYARLARI = ("openai_anahtar", "os_anahtar", "subdl_anahtar", "subsource_anahtar", "jimaku_anahtar")

    def _anahtar_gecmisi_kur(self):
        son = self.durum.depo.oku("anahtar_son4", None)
        if son is None:
            self.durum.depo.yaz("anahtar_son4", {a: (kodi.ayar(a) or "")[-4:] for a in self._ANAHTAR_AYARLARI})

    def ayarlar_degisti(self):
        """Elle girilen anahtar öncekinin yerine geçer; bağlantı testi son değişikliği (tarih, eski son 4) gösterir."""
        if kodi.ozellik("sifirlaniyor"):            # fabrika ayarlarına dönülüyor: geçmiş yazılmaz
            return
        son = self.durum.depo.oku("anahtar_son4", {}) or {}
        gecmis = self.durum.depo.oku("anahtar_gecmisi", {}) or {}
        degisti = False
        for a in self._ANAHTAR_AYARLARI:
            yeni = (kodi.ayar(a) or "")[-4:]
            if son.get(a, "") != yeni:
                gecmis[a] = {"tarih": time.strftime("%d.%m.%Y %H:%M"), "eski": son.get(a, "")}
                son[a] = yeni
                degisti = True
        if degisti:
            self.durum.depo.yaz("anahtar_son4", son)
            self.durum.depo.yaz("anahtar_gecmisi", gecmis)


def calistir():
    Servis().calistir()
