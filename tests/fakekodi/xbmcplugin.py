ITEMS = []


def addDirectoryItem(handle, url, listitem, isFolder=False):
    ITEMS.append((url, listitem))
    return True


def endOfDirectory(handle, *a, **k):
    pass
