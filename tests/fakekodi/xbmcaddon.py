SETTINGS = {}


class Addon:
    def __init__(self, id=None):
        pass

    def getSetting(self, k):
        return SETTINGS.get(k, "")

    def getAddonInfo(self, k):
        return {"version": "0.1.0", "id": "service.subtitles.defta"}.get(k, "")
