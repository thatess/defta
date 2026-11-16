"""Sıradaki bölüm sorusu: ekranın sağ üstünde, süreli Çevir/İptal kartı (madde 30). Süre dolarsa Çevir.

Görünüm resources/skins/default/1080i/defta-siradaki.xml dosyasındadır; yazılar buradan konur. Kartta eklentinin
logosu, küçük başlık, bölüm, çevrilecek altyazının adı, iki düğme ve süreyle birlikte kısalan bir çizgi vardır.
"""
import threading

import xbmcgui

from . import kodi

DOSYA = "defta-siradaki.xml"
BASLIK, BOLUM, ALTYAZI, KALAN, BASLIK_TEMA, CEVIR, IPTAL = 1, 2, 3, 4, 5, 10, 11
CIZGILER = (20, 21)                 # süre çizgisi: sabit renkli ve temanın vurgu renginde iki katman
CIZGI_GENISLIK = 386
ADIM = 0.1                          # saniye: çizgi bu aralıkla kısalır
_GERI = (9, 10, 92, 110)            # ACTION_PARENT_DIR, PREVIOUS_MENU, NAV_BACK, BACKSPACE


class SiradakiKutu(xbmcgui.WindowXMLDialog):
    def hazirla(self, baslik, bolum, altyazi, cevir, iptal, sure_sn):
        self.baslik, self.bolum, self.altyazi = baslik, bolum, altyazi
        self.cevir, self.iptal, self.sure = cevir, iptal, max(1, int(sure_sn))
        self.cevap = None                   # True: Çevir (ya da süre doldu), False: İptal
        self.kapandi = threading.Event()

    def onInit(self):
        for kimlik, yazi in ((BASLIK, self.baslik), (BASLIK_TEMA, self.baslik), (BOLUM, self.bolum),
                             (ALTYAZI, self.altyazi), (CEVIR, self.cevir), (IPTAL, self.iptal)):
            self.getControl(kimlik).setLabel(yazi)
        self._kalan_yaz(float(self.sure))
        self.setFocusId(CEVIR)
        threading.Thread(target=self._say, daemon=True).start()

    def _kalan_yaz(self, kalan):
        """Kalan süre: sağ üstte saniye, altta kalan orana göre kısalan çizgi."""
        self.getControl(KALAN).setLabel(f"{int(kalan + 0.999)} sn")
        genislik = max(0, int(round(CIZGI_GENISLIK * kalan / self.sure)))
        for kimlik in CIZGILER:
            self.getControl(kimlik).setWidth(genislik)

    def _say(self):
        adim_sayisi = int(round(self.sure / ADIM))
        for n in range(adim_sayisi - 1, -1, -1):
            if self.kapandi.wait(ADIM):
                return
            try:
                self._kalan_yaz(n * ADIM)
            except RuntimeError:            # pencere kapanmış
                return
        self._bitir(True)

    def _bitir(self, cevap):
        if self.cevap is None:
            self.cevap = cevap
        self.kapandi.set()
        self.close()

    def onClick(self, kimlik):
        if kimlik in (CEVIR, IPTAL):
            self._bitir(kimlik == CEVIR)

    def onAction(self, eylem):
        if eylem.getId() in _GERI:
            self._bitir(False)


def sor(baslik, bolum, altyazi, cevir, iptal, sure_sn):
    """Kartı gösterir; True: çevrilsin. Kart açılamazsa hata verir (çağıran düz soru kutusuna döner)."""
    kutu = SiradakiKutu(DOSYA, kodi.eklenti_yolu(), "default", "1080i")
    kutu.hazirla(baslik, bolum, altyazi, cevir, iptal, sure_sn)
    try:
        kutu.doModal()
    finally:
        kutu.kapandi.set()
    return bool(kutu.cevap)
