"""Testler için sahte xbmcvfs modülü."""
import os
import shutil

kok = ""   # testler kurar: special://profile/ bunun altına düşer


def translatePath(yol):
    if yol.startswith("special://profile/"):
        return os.path.join(kok, yol[len("special://profile/"):])
    return yol


class File:
    def __init__(self, yol, kip="r"):
        self.f = open(yol, "rb")
        self.yol = yol

    def size(self):
        return os.path.getsize(self.yol)

    def readBytes(self, n=-1):
        return bytearray(self.f.read(n))

    def seek(self, n, nereden=0):
        self.f.seek(n, nereden)

    def close(self):
        self.f.close()


def copy(kaynak, hedef):
    shutil.copyfile(kaynak, hedef)
    return True


def exists(yol):
    return os.path.exists(yol)


def mkdirs(yol):
    os.makedirs(yol, exist_ok=True)
    return True


def listdir(yol):
    adlar = os.listdir(yol)
    return ([a for a in adlar if os.path.isdir(os.path.join(yol, a))],
            [a for a in adlar if not os.path.isdir(os.path.join(yol, a))])


def delete(yol):
    try:
        os.remove(yol)
        return True
    except OSError:
        return False
