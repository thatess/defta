"""Defta: Kodi altyazı eklentisi (arama + yapay zekâ çevirisi)."""
import sys

if "asyncio" not in sys.modules:          # bkz. default.py: _asyncio C modülü Kodi'yi çökertir
    sys.modules["_asyncio"] = None

ADDON_ID = "service.subtitles.defta"
