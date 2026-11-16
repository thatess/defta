"""Ayar penceresindeki düğmelerin girişi: RunScript(.../ayar_eylemi.py,<eylem>)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from d2 import eylemler  # noqa: E402

eylemler.calistir(sys.argv)
