NOTIFICATION_INFO = "info"
DLG_YESNO_YES_BTN = 11
NOTES = []
YESNO = {"answer": True, "calls": []}
BROWSE = {"path": ""}


class ListItem:
    def __init__(self, label="", label2="", path=""):
        self.label, self.label2, self.art, self.props = label, label2, {}, {}

    def setArt(self, a):
        self.art.update(a)

    def setProperty(self, k, v):
        self.props[k] = v


class Window:
    PROPS = {}

    def __init__(self, i):
        pass

    def getProperty(self, k):
        return Window.PROPS.get(k, "")

    def setProperty(self, k, v):
        Window.PROPS[k] = v

    def clearProperty(self, k):
        Window.PROPS.pop(k, None)


class Dialog:
    def notification(self, heading, msg, icon=None, time=0, sound=True):
        NOTES.append((heading, msg))

    def ok(self, *a):
        NOTES.append(a)

    def yesno(self, *a, **k):
        YESNO["calls"].append((a, k))
        return YESNO["answer"]

    def browse(self, *a, **k):
        return BROWSE["path"]

    def select(self, *a):
        return 0

    def textviewer(self, h, t):
        NOTES.append((h, t))


class DialogProgress:
    def create(self, *a):
        pass

    def update(self, *a):
        pass

    def close(self):
        pass


DIALOGS = {"script": None, "opened": []}   # script(dialog): testin kutuya ne yapacağı


class _Control:
    def __init__(self):
        self.label, self.percent = "", None

    def setLabel(self, text):
        self.label = text

    def setPercent(self, p):
        self.percent = p


class Action:
    def __init__(self, aid):
        self._id = aid

    def getId(self):
        return self._id


class WindowXMLDialog:
    def __init__(self, xml, path, skin="default", res="720p", *a, **k):
        import threading
        self.xml, self.path = xml, path
        self.controls = {}
        self._closed = threading.Event()
        self.focus = None

    def getControl(self, cid):
        return self.controls.setdefault(cid, _Control())

    def setFocusId(self, cid):
        self.focus = cid

    def doModal(self):
        DIALOGS["opened"].append(self)
        self.onInit()
        if DIALOGS["script"]:
            DIALOGS["script"](self)
        self._closed.wait(30)

    def close(self):
        self._closed.set()

    def onInit(self):
        pass
