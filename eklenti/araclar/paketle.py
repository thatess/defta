"""Paketleme: settings.xml ve strings.po üretilir, çekirdek (defta) kopyalanır, Kodi'nin kurabileceği zip yazılır.
Kullanım: python araclar/paketle.py [çekirdek klasörü]   (varsayılan: ../defta)"""
import os
import re
import shutil
import subprocess
import sys
import zipfile

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EKLENTI = os.path.join(KOK, "service.subtitles.defta")


def cekirdek_klasoru(yol):
    """Kopyalanacak çekirdek paketi. Deponun kökü verildiyse içindeki defta paketi alınır: kök kopyalanırsa
    kullanıcının anahtar dosyası (.env), .git ve loglar da zip'e girerdi."""
    alt = os.path.join(yol, "defta")
    if os.path.isfile(os.path.join(alt, "__init__.py")):
        return alt
    if not os.path.isfile(os.path.join(yol, "__init__.py")):
        raise SystemExit(f"Çekirdek paketi (defta) bulunamadı: {yol}")
    return yol


def pakete_girer(ad):
    """Önbellek, derlenmiş dosya ve noktayla başlayan dosya ya da klasör (.env, .git) pakete girmez."""
    return ad != "__pycache__" and not ad.endswith(".pyc") and not ad.startswith(".")


def zip_yaz(zip_yolu, eklenti=EKLENTI):
    with zipfile.ZipFile(zip_yolu, "w", zipfile.ZIP_DEFLATED) as z:
        for kok, klasorler, adlar in os.walk(eklenti):
            klasorler[:] = [k for k in klasorler if pakete_girer(k)]
            for ad in sorted(adlar):
                if not pakete_girer(ad):
                    continue
                tam = os.path.join(kok, ad)
                z.write(tam, os.path.join("service.subtitles.defta", os.path.relpath(tam, eklenti)).replace("\\", "/"))
    return zip_yolu


def main():
    cekirdek = cekirdek_klasoru(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(KOK), "cekirdek"))
    subprocess.check_call([sys.executable, os.path.join(KOK, "araclar", "ayarlar_uret.py")])
    sys.path.insert(0, os.path.join(EKLENTI, "resources", "lib"))
    from d2 import metin
    po = os.path.join(EKLENTI, "resources", "language", "resource.language.en_gb", "strings.po")
    os.makedirs(os.path.dirname(po), exist_ok=True)
    with open(po, "w", encoding="utf-8", newline="\n") as f:
        f.write(metin.po_metni())
    hedef = os.path.join(EKLENTI, "resources", "lib", "defta")
    shutil.rmtree(hedef, ignore_errors=True)
    shutil.copytree(cekirdek, hedef, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".*"))
    with open(os.path.join(EKLENTI, "addon.xml"), encoding="utf-8") as f:
        surum = re.search(r'<addon[^>]*version="([^"]+)"', f.read()).group(1)
    os.makedirs(os.path.join(KOK, "dist"), exist_ok=True)
    zip_yolu = os.path.join(KOK, "dist", f"service.subtitles.defta-{surum}.zip")
    print(zip_yaz(zip_yolu))


if __name__ == "__main__":
    main()
