"""Testler için sahte xbmcplugin modülü."""
ogeler = []
bitti = []


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
    ogeler.append((url, listitem))
    return True


def endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=True):
    bitti.append(handle)
