"""Sıradaki bölüm sorusu: sağ üstte küçük kutu (resources/skins/default/1080i/defta-next.xml).

Video oynamaya devam eder. İmleç "Çevir"de durur; alttaki çubuk süre boyunca boşalır, boşalınca
çevrilir. "İptal" ya da kumandadaki Geri tuşu vazgeçer. Video durursa kutu kendiliğinden kapanır.
Kutu açılamazsa (tema sorunu vb.) Kodi'nin standart evet/hayır penceresi kullanılır."""
from __future__ import annotations

import threading
import time

import xbmc
import xbmcgui

from . import kodi

BTN_YES, BTN_NO, BAR, TITLE, LINE2, ICON = 3001, 3002, 3003, 3010, 3011, 3020
OPEN_PROP = "defta.box_open"       # kutu açıkken Defta bildirim göndermez (üst üste binmesin)
ACTION_BACK = (9, 10, 92)          # üst klasör, önceki menü, geri
ACTION_STOP = 13


class NextDialog(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.title = kwargs.pop("title", "")
        self.line2 = kwargs.pop("line2", "")
        self.seconds = float(kwargs.pop("seconds", 15))
        self.should_close = kwargs.pop("should_close", lambda: False)
        super().__init__(*args, **kwargs)
        self.result = None           # True: çevir, False: iptal
        self.reason = ""
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    # Kodi çağırır ---------------------------------------------------------
    def onInit(self):  # noqa: N802
        for cid, text in ((TITLE, self.title), (LINE2, self.line2)):
            try:
                self.getControl(cid).setLabel(text)
            except Exception:  # noqa: BLE001
                pass
        try:
            import os
            icon = os.path.join(kodi.addon_dir(), "icon.png")
            if os.path.exists(icon):
                self.getControl(ICON).setImage(icon)
        except Exception:  # noqa: BLE001
            pass
        self._bar(100)
        try:
            self.setFocusId(BTN_YES)
        except Exception:  # noqa: BLE001
            pass
        if self._thread is None:
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()

    def onClick(self, control_id):  # noqa: N802
        if control_id == BTN_YES:
            self._finish(True, "Çevir seçildi")
        elif control_id == BTN_NO:
            self._finish(False, "İptal seçildi")

    def onAction(self, action):  # noqa: N802
        aid = action.getId()
        if aid in ACTION_BACK:
            self._finish(False, "Geri tuşu")
        elif aid == ACTION_STOP:
            self._finish(False, "video durduruldu")
            xbmc.executebuiltin("PlayerControl(Stop)")

    # ----------------------------------------------------------------------
    def _bar(self, pct: float) -> None:
        try:
            self.getControl(BAR).setPercent(max(0.0, min(100.0, pct)))
        except Exception:  # noqa: BLE001
            pass

    def _tick(self) -> None:
        t0 = time.monotonic()
        while not self._done.is_set():
            left = self.seconds - (time.monotonic() - t0)
            if left <= 0:
                self._finish(True, "süre doldu")
                return
            try:
                stop = self.should_close()
            except Exception:  # noqa: BLE001
                stop = False
            if stop:
                self._finish(False, "video bitti ya da durdu")
                return
            self._bar(100.0 * left / self.seconds)
            self._done.wait(0.25)

    def _finish(self, result: bool, reason: str) -> None:
        if self._done.is_set():
            return
        self.result, self.reason = result, reason
        self._done.set()
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


def ask(title: str, line2: str, seconds: float = 15, should_close=lambda: False) -> tuple[bool, str]:
    """(çevrilsin mi, sebep). Süre dolarsa True."""
    try:
        d = NextDialog("defta-next.xml", kodi.addon_dir(), "default", "1080i",
                       title=title, line2=line2, seconds=seconds, should_close=should_close)
    except Exception as e:  # noqa: BLE001
        kodi.log(f"sağ üst kutu açılamadı ({type(e).__name__}: {e}); standart pencere kullanılıyor", "warning")
        return _fallback(title, line2, seconds)
    home = xbmcgui.Window(10000)
    home.setProperty(OPEN_PROP, str(time.time()))
    try:
        d.doModal()
    finally:
        home.clearProperty(OPEN_PROP)
        d._done.set()
        if d._thread is not None:
            d._thread.join(2)
    result, reason = d.result, d.reason or "kapandı"
    del d
    return bool(result), reason


def _fallback(title: str, line2: str, seconds: float) -> tuple[bool, str]:
    kw = {"nolabel": "İptal", "yeslabel": "Çevir", "autoclose": int(seconds * 1000)}
    if hasattr(xbmcgui, "DLG_YESNO_YES_BTN"):
        kw["defaultbutton"] = xbmcgui.DLG_YESNO_YES_BTN
    t0 = time.monotonic()
    try:
        ok = xbmcgui.Dialog().yesno("Defta", f"{title}\n{line2}", **kw)
    except TypeError:
        kw.pop("defaultbutton", None)
        ok = xbmcgui.Dialog().yesno("Defta", f"{title}\n{line2}", **kw)
    if not ok and time.monotonic() - t0 >= seconds - 0.5:
        return True, "süre doldu"
    return bool(ok), "Çevir seçildi" if ok else "İptal seçildi"
