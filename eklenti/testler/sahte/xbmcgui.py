"""Testler için sahte xbmcgui modülü."""
bildirimler = []
cevaplar = {"yesno": True, "select": 0, "browse": ""}
pencere = {}
goruntulenen = []
gozatilan = []


class ListItem:
    def __init__(self, label="", label2="", path=""):
        self.label, self.label2, self.art, self.ozellikler = label, label2, {}, {}

    def setArt(self, d):
        self.art.update(d)

    def setProperty(self, ad, deger):
        self.ozellikler[ad] = deger

    def getProperty(self, ad):
        return self.ozellikler.get(ad, "")


seritler = []          # (olay, başlık, yazı): DialogProgressBG çağrıları


class DialogProgressBG:
    def create(self, baslik, yazi=""):
        seritler.append(("ac", baslik, yazi))

    def update(self, yuzde=0, baslik="", yazi=""):
        seritler.append(("guncelle", baslik, yazi))

    def close(self):
        seritler.append(("kapat", "", ""))


class Dialog:
    def notification(self, baslik, yazi, simge="", sure=3000, ses=True):
        bildirimler.append((baslik, yazi))

    def yesno(self, baslik, yazi, nolabel="", yeslabel="", autoclose=0, defaultbutton=0):
        return cevaplar["yesno"]

    def select(self, baslik, secenekler, *a, **k):
        return cevaplar["select"]

    def browse(self, tur, baslik, kaynak, maske="", *a, **k):
        gozatilan.append((tur, kaynak, maske))
        return cevaplar["browse"]

    def textviewer(self, baslik, yazi, *a, **k):
        goruntulenen.append((baslik, yazi))


class Window:
    def __init__(self, kimlik):
        pass

    def getProperty(self, ad):
        return pencere.get(ad, "")

    def setProperty(self, ad, deger):
        pencere[ad] = deger

    def clearProperty(self, ad):
        pencere.pop(ad, None)


class WindowXMLDialog:
    """Sahte kutu: doModal, onInit'i çağırır ve "tiklanacak" düğmeye basar (None: kimse basmaz, süre dolar)."""
    tiklanacak = None

    def __init__(self, *a, **k):
        self.denetimler, self.kapali = {}, False

    def getControl(self, kimlik):
        return self.denetimler.setdefault(kimlik, _Denetim())

    def setFocusId(self, kimlik):
        self.odak = kimlik

    def doModal(self):
        self.onInit()
        if WindowXMLDialog.tiklanacak is not None:
            self.onClick(WindowXMLDialog.tiklanacak)
        else:
            self.kapandi.wait(5)

    def close(self):
        self.kapali = True


class _Denetim:
    genislik = None

    def setWidth(self, genislik):
        self.genislik = genislik

    def setLabel(self, yazi):
        self.yazi = yazi
