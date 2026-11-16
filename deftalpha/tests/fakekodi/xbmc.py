"""Kodi'siz test için sahte xbmc modülü."""
import json
LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3
LOGS = []
INFO = {}
JSONRPC = {}


def log(msg, level=LOGINFO):
    LOGS.append(msg)


def getInfoLabel(name):
    return INFO.get(name, "")


def executeJSONRPC(q):
    return json.dumps({"result": {"value": JSONRPC.get(json.loads(q)["params"]["setting"], False)}})


def executebuiltin(cmd):
    pass


class Player:
    playing = ""
    loaded = []
    total = 0.0
    time = 0.0

    def __init__(self):
        pass

    def isPlayingVideo(self):
        return bool(Player.playing)

    def getPlayingFile(self):
        return Player.playing

    def setSubtitles(self, path):
        Player.loaded.append(path)

    def getAvailableSubtitleStreams(self):
        return []

    def getTotalTime(self):
        return Player.total

    def getTime(self):
        return Player.time


class Monitor:
    def abortRequested(self):
        return False

    def waitForAbort(self, t=0):
        return False
