"""Testler için sahte xbmcaddon modülü."""
import os

ayarlar = {}
yol = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "service.subtitles.defta")


class Addon:
    def __init__(self, kimlik=None):
        pass

    def getSetting(self, ad):
        d = ayarlar.get(ad, "")
        if isinstance(d, bool):
            return "true" if d else "false"
        return str(d)

    def setSetting(self, ad, deger):
        ayarlar[ad] = deger

    def getLocalizedString(self, kimlik):
        from d2 import metin
        return metin._YAZI.get(kimlik, "")

    def getAddonInfo(self, ad):
        return {"version": "0.1.0", "path": yol, "id": "service.subtitles.defta",
                "profile": "special://profile/addon_data/service.subtitles.defta/"}.get(ad, "")

    def openSettings(self):
        pass
