"""Kodi deposunu günceller. depo/<kimlik>/ altında her eklentinin en yeni paketi ve KALICI'daki paketler kalır
(öbür eskiler silinir); hepsinin addon.xml'inden depo/addons.xml ve addons.xml.md5 üretilir. Kodi en yeni sürümü kurar,
"Sürümler" penceresi listedeki eskileri de sunar. Depo eklentisinin zip dosyası köke de konur ve index.html yazılır:
Kodi'de kaynak olarak eklenen https://thatess.github.io/defta/ adresinde (GitHub Pages) "Zip dosyasından yükle" bu
dosyayı görür. Yeni sürüm yayımlamak: paketi depo/<kimlik>/ klasörüne koy, bu betiği çalıştır, gönder."""
import hashlib
import os
import re
import shutil
import sys
import zipfile

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPO = os.path.join(KOK, "depo")
KALICI = {"service.subtitles.defta-0.4.35.zip"}    # Deftalpha: ilk Defta, hatıra olarak hep durur


def surum_anahtari(ad):
    return [int(p) for p in re.findall(r"\d+", ad.rsplit("-", 1)[-1])]


def en_yeni(klasor, kimlik):
    paketler = [a for a in os.listdir(klasor) if a.startswith(kimlik + "-") and a.endswith(".zip")]
    return max(paketler, key=surum_anahtari) if paketler else None


def depo_eklentisini_paketle():
    """araclar/repository.defta klasöründeki depo eklentisinin o sürümü için zip yoksa üretir."""
    kaynak = os.path.join(KOK, "araclar", "repository.defta")
    with open(os.path.join(kaynak, "addon.xml"), encoding="utf-8") as f:
        surum = re.search(r'<addon[^>]*\bversion="([^"]+)"', f.read()).group(1)
    hedef = os.path.join(DEPO, "repository.defta", f"repository.defta-{surum}.zip")
    if not os.path.exists(hedef):
        with zipfile.ZipFile(hedef, "w", zipfile.ZIP_DEFLATED) as z:
            for ad in sorted(os.listdir(kaynak)):
                z.write(os.path.join(kaynak, ad), "repository.defta/" + ad)


def main():
    depo_eklentisini_paketle()
    parcalar = []
    for kimlik in sorted(os.listdir(DEPO)):
        klasor = os.path.join(DEPO, kimlik)
        yeni = en_yeni(klasor, kimlik) if os.path.isdir(klasor) else None
        if not yeni:
            continue
        for eski in os.listdir(klasor):
            if eski.endswith(".zip") and eski != yeni and eski not in KALICI:
                os.remove(os.path.join(klasor, eski))
        paketler = sorted((a for a in os.listdir(klasor) if a.endswith(".zip")), key=surum_anahtari)
        for paket in paketler:
            with zipfile.ZipFile(os.path.join(klasor, paket)) as z:
                xml = z.read(kimlik + "/addon.xml").decode("utf-8")
                if paket == yeni and kimlik + "/icon.png" in z.namelist():
                    with open(os.path.join(klasor, "icon.png"), "wb") as f:
                        f.write(z.read(kimlik + "/icon.png"))
            parcalar.append(re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml).strip())
            print(paket)
    metin = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + "\n".join(parcalar) + "\n</addons>\n"
    with open(os.path.join(DEPO, "addons.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(metin)
    with open(os.path.join(DEPO, "addons.xml.md5"), "w", encoding="ascii", newline="\n") as f:
        f.write(hashlib.md5(metin.encode("utf-8")).hexdigest())
    depo_zip = en_yeni(os.path.join(DEPO, "repository.defta"), "repository.defta")
    for eski in os.listdir(KOK):
        if eski.startswith("repository.defta-") and eski.endswith(".zip"):
            os.remove(os.path.join(KOK, eski))
    shutil.copyfile(os.path.join(DEPO, "repository.defta", depo_zip), os.path.join(KOK, depo_zip))
    with open(os.path.join(KOK, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write('<!DOCTYPE html>\n<html lang="tr"><head><meta charset="utf-8"><title>Defta</title></head><body>\n'
                '<h1>Defta</h1>\n<p>Kodi: Zip dosyasından yükle</p>\n'
                f'<a href="{depo_zip}">{depo_zip}</a>\n</body></html>\n')
    return 0


if __name__ == "__main__":
    sys.exit(main())
