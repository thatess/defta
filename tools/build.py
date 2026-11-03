"""Defta paketleyici: masaüstü motorunu (subtrans) eklentiye kopyalar, kural paketlerini JSON'a çevirir,
Kodi zip'lerini ve depo (repository) dosyalarını üretir.

  python tools/build.py            -> dist/service.subtitles.defta-<sürüm>.zip
  python tools/build.py --repo     -> ayrıca docs/ altında Kodi deposu (GitHub Pages)
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(os.environ.get("SUBTRANS_DIR", ROOT.parent / "altyazi_cevirici"))
ADDON = ROOT / "service.subtitles.defta"
REPO_ADDON = ROOT / "repository.defta"
DIST = ROOT / "dist"
DOCS = ROOT / "docs"


def version(addon_dir: Path) -> str:
    return re.search(r'<addon[^>]+version="([^"]+)"', (addon_dir / "addon.xml").read_text(encoding="utf-8")).group(1)


def sync_engine() -> None:
    lib = ADDON / "resources" / "lib"
    dst = lib / "subtrans"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ENGINE / "subtrans", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    rules = lib / "rules"
    if rules.exists():
        shutil.rmtree(rules)
    rules.mkdir()
    for f in (ENGINE / "rules").glob("*.toml"):
        with open(f, "rb") as fh:
            (rules / (f.stem + ".json")).write_text(json.dumps(tomllib.load(fh), ensure_ascii=False, indent=1),
                                                    encoding="utf-8")
    with open(ENGINE / "config.toml", "rb") as fh:
        prices = tomllib.load(fh).get("prices", {})
    (lib / "prices.json").write_text(json.dumps(prices, indent=1), encoding="utf-8")


def make_zip(addon_dir: Path, out_dir: Path) -> Path:
    ver = version(addon_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{addon_dir.name}-{ver}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(addon_dir.rglob("*")):
            if p.is_dir() or "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            z.write(p, f"{addon_dir.name}/{p.relative_to(addon_dir).as_posix()}")
    return out


def build_repo() -> None:
    """docs/: addons.xml, addons.xml.md5, <id>/<id>-<sürüm>.zip (+ kök dizinde repository zip)."""
    if DOCS.exists():
        shutil.rmtree(DOCS)          # yalnızca güncel sürümler yayımlanır
    DOCS.mkdir()
    xmls = []
    # Eski sürümler (releases/): Kodi'de eklenti sayfası > Sürümler'den geri dönülebilsin.
    # 0.1.x eklenmez (Kodi'yi çökertiyordu, SubDL anahtarını kayda yazıyordu).
    cur = version(ADDON)
    for old in sorted((ROOT / "releases").glob(f"{ADDON.name}-*.zip")):
        v = old.stem.rsplit("-", 1)[1]
        if v == cur:
            continue
        (DOCS / ADDON.name).mkdir(parents=True, exist_ok=True)
        shutil.copy(old, DOCS / ADDON.name / old.name)
        with zipfile.ZipFile(old) as zf:
            xml = zf.read(f"{ADDON.name}/addon.xml").decode("utf-8")
        xmls.append(re.sub(r"<\?xml[^>]*\?>\s*", "", xml).strip())
        print("   eski sürüm:", old.name)
    for addon_dir in (ADDON, REPO_ADDON):
        z = make_zip(addon_dir, DOCS / addon_dir.name)
        if addon_dir == ADDON:
            (ROOT / "releases").mkdir(exist_ok=True)
            shutil.copy(z, ROOT / "releases" / z.name)        # bir sonraki sürümde "eski sürüm" olarak kalır
        for asset in ("icon.png", "fanart.jpg"):
            if (addon_dir / asset).exists():
                shutil.copy(addon_dir / asset, DOCS / addon_dir.name / asset)
        xml = (addon_dir / "addon.xml").read_text(encoding="utf-8")
        xmls.append(re.sub(r"<\?xml[^>]*\?>\s*", "", xml).strip())
        print("  ", z.relative_to(ROOT))
    body = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + "\n".join(xmls) + "\n</addons>\n"
    (DOCS / "addons.xml").write_text(body, encoding="utf-8")
    (DOCS / "addons.xml.md5").write_text(hashlib.md5(body.encode("utf-8")).hexdigest(), encoding="utf-8")
    shutil.copy(DOCS / REPO_ADDON.name / f"{REPO_ADDON.name}-{version(REPO_ADDON)}.zip",
                DOCS / f"{REPO_ADDON.name}.zip")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    links = "".join(f'<a href="{p.name}">{p.name}</a><br>\n' for p in sorted(DOCS.iterdir())
                    if p.name not in ("index.html", ".nojekyll"))
    (DOCS / "index.html").write_text(f"<!doctype html><meta charset='utf-8'><title>Defta</title>\n{links}",
                                     encoding="utf-8")


if __name__ == "__main__":
    sync_engine()
    z = make_zip(ADDON, DIST)
    print("eklenti:", z.relative_to(ROOT))
    if "--repo" in sys.argv:
        build_repo()
