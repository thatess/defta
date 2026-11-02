# Defta

Kodi altyazı eklentisi: OpenSubtitles, SubDL, SubSource ve Jimaku'da altyazı arar; hedef dilde (varsayılan Türkçe)
altyazı yoksa yabancı bir altyazıyı OpenAI ile çevirir. Çeviri parça parça yüklenir, anlam kontrolü yapılmış hali
arkadan gelir.

- `service.subtitles.defta/` — eklenti
- `tools/build.py` — çeviri motorunu (subtrans) kopyalar, zip ve Kodi deposu üretir
- `tests/` — Kodi'siz testler (sahte Kodi modülleri, sahte OpenAI sunucusu)

API anahtarları depoda tutulmaz; Kodi'de eklenti ayarlarına girilir.
