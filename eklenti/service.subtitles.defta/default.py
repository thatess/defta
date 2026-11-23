"""Kodi'nin altyazı modülü girişi (arama ve indirme)."""
import os
import sys
import time

ACILIS = time.time()          # süre ölçümü: modüller yüklenmeden önce

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from d2 import giris  # noqa: E402

giris.calistir(sys.argv, ACILIS)
