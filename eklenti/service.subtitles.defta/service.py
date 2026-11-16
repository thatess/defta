"""Arka plan servisi girişi."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from d2 import servis  # noqa: E402

servis.calistir()
