import os
import sys

# Python 3.8'in asyncio hızlandırıcısı (_asyncio) Kodi'nin çoklu yorumlayıcısıyla uyumsuz: ilk kullanan
# eklenti kapandıktan sonra başka biri asyncio kullanınca Kodi çöker. Saf Python sürümü kullanılır.
if "asyncio" not in sys.modules:
    sys.modules["_asyncio"] = None

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from defta.plugin import main  # noqa: E402

main()
