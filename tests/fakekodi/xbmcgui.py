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
