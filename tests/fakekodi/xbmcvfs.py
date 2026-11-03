import os
MAP = {}


def translatePath(p):
    for k, v in MAP.items():
        if p.startswith(k):
            return os.path.join(v, p[len(k):])
    return p


COPIES = []


def copy(src, dst):
    import shutil
    COPIES.append((src, dst))
    if "://" in dst:
        return True                      # ağ klasörü taklidi: kopyalandı say
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copyfile(src, dst)
    return True


class File:
    def __init__(self, path, mode="r"):
        self._f = open(translatePath(path), "rb")

    def size(self):
        cur = self._f.tell()
        self._f.seek(0, 2)
        n = self._f.tell()
        self._f.seek(cur)
        return n

    def readBytes(self, n=-1):
        return bytearray(self._f.read(n))

    def seek(self, pos, whence=0):
        return self._f.seek(pos, whence)

    def close(self):
        self._f.close()
