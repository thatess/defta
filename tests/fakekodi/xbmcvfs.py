import os
MAP = {}


def translatePath(p):
    for k, v in MAP.items():
        if p.startswith(k):
            return os.path.join(v, p[len(k):])
    return p
