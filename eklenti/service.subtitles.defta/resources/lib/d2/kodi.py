"""Kodi ile konuşan tek yer: ayarlar, yazılar, kayıt, bildirim, yollar, pencere özellikleri, servis mesajları.

Eklentinin arama/indirme bölümü ile arka plan servisi ayrı Python yorumlayıcılarında çalışır; ikisi de bu modülü
kullanır. Kayda giden her yazı gizli.gizle'den geçer.
"""
import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import gizli, metin

KIMLIK = "service.subtitles.defta"
AD = "Defta"  # ekranda görünen ad
_ANA_PENCERE = 10000

def _eklenti():
    # Her seferinde yenisi: servis uzun yaşar, ayar penceresinde yapılan değişiklik eski nesneye yansımaz.
    return xbmcaddon.Addon(KIMLIK)


metin.kur(lambda kimlik: _eklenti().getLocalizedString(kimlik))


# ----------------------------------------------------------------------------------------------------- ayarlar
_GIZLI_AYARLAR = ("openai_anahtar", "os_anahtar", "os_parola", "subdl_anahtar", "subsource_anahtar", "jimaku_anahtar")


def ayar(ad):
    deger = _eklenti().getSetting(ad) or ""
    if deger and ad in _GIZLI_AYARLAR:
        gizli.kaydet(deger)                 # her okunan anahtar kayıtta ve hata yazısında gizlenir
    return deger


def ayar_bool(ad):
    return ayar(ad).strip().lower() == "true"


def ayar_int(ad, varsayilan=0):
    try:
        return int(float(ayar(ad)))
    except ValueError:
        return varsayilan


def ayar_yaz(ad, deger):
    if isinstance(deger, bool):
        deger = "true" if deger else "false"
    _eklenti().setSetting(ad, str(deger))


SEKME_TABANLARI = (-200, -100)


def ayarlari_ac(sekme=None):
    """Ayar penceresini açar. sekme (0'dan başlayan sıra) verilirse o sekme seçili gelir: düğme pencereyi kapatıp
    yeniden açtığında kullanıcı en başa dönmez."""
    if sekme is None:
        _eklenti().openSettings()
        return
    xbmc.executebuiltin(f"Addon.OpenSettings({KIMLIK})")
    for _ in range(20):                                   # pencere açılana kadar (en çok 2 sn)
        if xbmc.getCondVisibility("Window.IsVisible(addonsettings)"):
            break
        xbmc.sleep(100)
    xbmc.sleep(300)
    # Sekme düğmelerinin kimliği Kodi sürümüne göre -200'den (20 ve sonrası) ya da -100'den (19) başlar; ötekinde
    # o kimlikte denetim olmadığı için komut bir şey yapmaz.
    for taban in SEKME_TABANLARI:
        xbmc.executebuiltin(f"SetFocus({taban + int(sekme)})")


class Calisiyor:
    """Ayar düğmesi çalışırken görünen işaret: basıldığı an bildirim ve ekranın üstünde ilerleme şeridi çıkar, iş
    bitince şerit kapanır. Uzun süren düğmede (liste indirme, bağlantı testi) "basıldı mı?" sorusu kalmaz."""

    def __init__(self, yazi):
        self.yazi, self.serit = yazi, None

    def __enter__(self):
        bildir(self.yazi, metin.al(metin.B_CALISIYOR))
        try:
            self.serit = xbmcgui.DialogProgressBG()
            self.serit.create(AD, f"{self.yazi} · {metin.al(metin.B_CALISIYOR)}")
        except Exception:
            self.serit = None
        return self

    def __exit__(self, *_):
        if self.serit is not None:
            try:
                self.serit.close()
            except Exception:
                pass
        return False


def surum():
    return _eklenti().getAddonInfo("version")


def eklenti_yolu():
    return xbmcvfs.translatePath(_eklenti().getAddonInfo("path"))


def simge():
    return os.path.join(eklenti_yolu(), "icon.png")


# ------------------------------------------------------------------------------------------------------- yollar
def profil(*klasorler):
    """Eklentinin veri klasörü (addon_data) ya da onun alt klasörü; yoksa oluşturulur."""
    yol = os.path.join(xbmcvfs.translatePath(f"special://profile/addon_data/{KIMLIK}/"), *klasorler)
    os.makedirs(yol, exist_ok=True)
    return yol


# -------------------------------------------------------------------------------------------------------- kayıt
def kayit(mesaj, hata=False, ayrinti=False):
    seviye = xbmc.LOGERROR if hata else (xbmc.LOGDEBUG if ayrinti else xbmc.LOGINFO)
    xbmc.log("[defta] " + gizli.gizle(mesaj), seviye)


# --------------------------------------------------------------------------------------------- ekran ve yazılar
BILDIRIM_SURESI, HATA_SURESI = 4000, 8000


def bildir(baslik, yazi="", hata=False):
    """Kısa bildirim: bilgi 4 sn (iki satırı okumaya yeter), hata 8 sn (seyrek gelir, okunup anlaşılmalı)."""
    xbmcgui.Dialog().notification(gizli.gizle(baslik), gizli.gizle(yazi), simge(),
                                  HATA_SURESI if hata else BILDIRIM_SURESI, False)


def onay(soru, evet=None, hayir=None):
    return bool(xbmcgui.Dialog().yesno(AD, soru, nolabel=hayir or metin.al(metin.P_HAYIR),
                                       yeslabel=evet or metin.al(metin.P_EVET)))


def secim(baslik, secenekler):
    return xbmcgui.Dialog().select(baslik, secenekler)


def goster(baslik, yazi):
    xbmcgui.Dialog().textviewer(baslik, gizli.gizle(yazi))


# Dosya ve klasör seçicinin kökü. Kodi "files" kaynağında ağ konumlarını (Windows ağı / SMB, NFS) göstermiyor; tanımadığı
# (boş) kaynak adında yerel sürücüleri ve ağ konumlarını birlikte gösteriyor: her şey tek yerden seçilir.
SECICI_KOKU = ""


def dosya_sec(baslik, maske=""):
    return xbmcgui.Dialog().browse(1, baslik, SECICI_KOKU, maske) or ""


def yazi_sor(baslik, varsayilan="", gizli_yazi=False):
    """Ekran klavyesiyle yazı ister; vazgeçilirse None (boş yazı geçerli bir cevaptır)."""
    klavye = xbmc.Keyboard(varsayilan, baslik, gizli_yazi)
    klavye.doModal()
    return klavye.getText() if klavye.isConfirmed() else None


def klasor_sec(baslik, yazilabilir=False):
    """Klasör seçtirir (yazilabilir: yalnız yazılabilen klasörler). Vazgeçilirse boş döner."""
    return xbmcgui.Dialog().browse(3 if yazilabilir else 0, baslik, SECICI_KOKU) or ""


# ---------------------------------------------------------------------------- pencere özellikleri ve mesajlar
def ozellik(ad, deger=None):
    """Ana pencerenin özelliği: eklenti ile servis arasında küçük durum bilgileri (ikisi de okuyup yazabilir)."""
    pencere = xbmcgui.Window(_ANA_PENCERE)
    if deger is None:
        return pencere.getProperty("defta." + ad)
    pencere.setProperty("defta." + ad, deger)
    return deger


def ozellik_sil(ad):
    xbmcgui.Window(_ANA_PENCERE).clearProperty("defta." + ad)


def servise_gonder(ad, veri=None):
    """Servise mesaj: JSON-RPC NotifyAll (veri JSON olarak gider; virgül ve tırnak sorun olmaz)."""
    xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "JSONRPC.NotifyAll",
                                    "params": {"sender": KIMLIK, "message": ad, "data": veri or {}}}))


def jsonrpc(yontem, **parametreler):
    yanit = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": yontem,
                                                       "params": parametreler})))
    return yanit.get("result")


def kodi_ayari(ad):
    sonuc = jsonrpc("Settings.GetSettingValue", setting=ad) or {}
    return sonuc.get("value")


def kodi_ayari_yaz(ad, deger):
    return jsonrpc("Settings.SetSettingValue", setting=ad, value=deger)
