"""Testler için sahte xbmc modülü."""
import time

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3
kayitlar = []
infolabel = {}
kosullar = {}
jsonrpc_istekleri = []
jsonrpc_yanit = {"result": {"value": False}}
oynatici = {"oynuyor": False, "yol": "", "sure": 0.0, "yer": 0.0, "etiket": {}, "altyazi_akislari": [], "yuklenen": []}


def log(mesaj, seviye=LOGDEBUG):
    kayitlar.append((seviye, mesaj))


def getInfoLabel(ad):
    return infolabel.get(ad, "")


def getCondVisibility(ad):
    return kosullar.get(ad, False)


def executeJSONRPC(istek):
    import json
    jsonrpc_istekleri.append(json.loads(istek))
    return json.dumps(jsonrpc_yanit)


komutlar = []


def executebuiltin(komut, bekle=False):
    komutlar.append(komut)


ISO_639_1, ISO_639_2, ENGLISH_NAME = 0, 1, 2
_INGILIZCE = {"tr": "Turkish", "en": "English", "ja": "Japanese", "de": "German", "fr": "French", "es": "Spanish"}


def convertLanguage(dil, bicim):
    return _INGILIZCE.get(dil, "") if bicim == ENGLISH_NAME else dil


class Monitor:
    def waitForAbort(self, sure=0):
        time.sleep(min(sure or 0, 0.01))
        return False

    def abortRequested(self):
        return False


class InfoTagVideo:
    def __init__(self, d):
        self.d = d

    def __getattr__(self, ad):
        if ad.startswith("get"):
            def al(*args):
                if ad == "getUniqueID":
                    return self.d.get("uniqueid", {}).get(args[0], "")
                return self.d.get(ad[3:].lower(), "")
            return al
        raise AttributeError(ad)


class Player:
    def isPlayingVideo(self):
        return oynatici["oynuyor"]

    def getPlayingFile(self):
        return oynatici["yol"]

    def getTotalTime(self):
        return oynatici["sure"]

    def getTime(self):
        return oynatici["yer"]

    def getVideoInfoTag(self):
        return InfoTagVideo(oynatici["etiket"])

    def getAvailableSubtitleStreams(self):
        return oynatici["altyazi_akislari"]

    def setSubtitles(self, yol):
        oynatici["yuklenen"].append(yol)


def sleep(ms):
    pass



klavye_cevaplari = []      # sırayla verilecek cevaplar; None = vazgeçildi
klavye_sorulari = []


class Keyboard:
    def __init__(self, varsayilan="", baslik="", gizli=False):
        klavye_sorulari.append((baslik, varsayilan, gizli))
        self.cevap = klavye_cevaplari.pop(0) if klavye_cevaplari else None

    def doModal(self):
        pass

    def isConfirmed(self):
        return self.cevap is not None

    def getText(self):
        return self.cevap or ""
