"""Altyazı biçimleri: srt, ass/ssa, vtt ve sub içeriğini Satir listesine çevirir. Çıktı her zaman srt'dir.

Okuyucular yalnızca biçimi çözer: zaman, metin ve biçimin kendisinin söylediği bilgiler (italik, üstte gösterme,
ekran yazısı, şarkı). Metnin temizliği (temizlik.kaynak_hazirla) ve çeviri her biçim için aynıdır; bu yüzden
okuyucular srt'de alışılmış yazımı üretir: italik <i>...</i>, üstte gösterme {\\an8}, şarkı ♪ ... ♪, konuşmacı "AD: ".
"""
import re

from . import temizlik
from .srt import Satir, _ms, _numara, srt_ayristir

UZANTILAR = (".srt", ".ass", ".ssa", ".vtt", ".sub")      # sıra önceliktir: aynı adın iki biçimi varsa ilki çevrilir


class BicimHatasi(Exception):
    """Dosya okunabilir bir metin altyazısı değil (görüntü tabanlı .sub gibi)."""


def ayristir(icerik, uzanti, ayar):
    """İçeriği Satir listesine çevirir. Uzantı yanıltabilir (adı .srt olan vtt): önce içeriğin kendisine bakılır."""
    if icerik.lstrip("﻿ \t\r\n").startswith("WEBVTT"):
        tur = "vtt"
    elif re.search(r"(?im)^\[(script info|events|v4\+? styles)\]", icerik[:4000]):
        tur = "ass"
    else:
        tur = uzanti.lower().lstrip(".")
    if tur == "vtt":
        return _vtt(icerik)
    if tur in ("ass", "ssa"):
        return _ass(icerik, ayar["girdi"], ayar["modlar"]["sarki"])
    if tur == "sub":
        return _sub(icerik, ayar["girdi"]["sub_fps"])
    return srt_ayristir(icerik)


def _numarala(satirlar):
    for n, s in enumerate(satirlar, 1):
        s.no = n
    return satirlar


# ---- ASS / SSA ----------------------------------------------------------------------------------------------
# Sayı alanları sınırlıdır: binlerce haneli "saat" int() sınırını aşıp okuyucuyu çökertmesin, satır tanınmasın.
ASS_ZAMAN = re.compile(r"(?<!\d)(\d{1,4}):(\d{1,2}):(\d{1,2})[.:,](\d{1,3})")
ASS_BLOK = re.compile(r"\{([^{}]*)\}")
OLAY_ALANLARI = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
# Stil adından şarkı: "OP", "ED2", "Song-Romaji", "Karaoke", "Insert Lyrics" ("Default", "Top", "Red" değil).
# Stil adından ekran yazısı: "sign_93_Harbour", "Signs", "Ep_Title", "Show_Title", "Typeset", "TS".
YAZI_STILI = re.compile(r"(?i)(?<![a-z])(signs?|titles?|typeset|ts|captions?)(?![a-z])")
SARKI_STILI = re.compile(r"(?i)(?<![a-z])(op|ed|opening|ending|songs?|lyrics?|karaoke|kara|romaji|kanji|insert)(?![a-z])")


def _ass_zaman(metin):
    m = ASS_ZAMAN.search(metin)
    return _ms(m.group(1), m.group(2), m.group(3), m.group(4)[:3]) if m else None


def _ass_metin(metin, stil_italik):
    """(metin, üstte mi, ekran yazısı mı, karaoke mi). Kodlar {..} içindedir: italik <i> etiketine çevrilir,
    konum kodu (\\pos, \\move) satırı ekran yazısı yapar, çizim kipindeki (\\p1) vektör komutları atılır."""
    cikti, durum = [], {"italik": False, "ust": False, "ekran": False, "kara": False, "cizim": False,
                        "konum": None, "kucuk": False}

    def italik(yeni):
        if yeni != durum["italik"]:
            cikti.append("<i>" if yeni else "</i>")
            durum["italik"] = yeni

    italik(stil_italik)
    son = 0
    for m in ASS_BLOK.finditer(metin):
        if not durum["cizim"]:
            cikti.append(metin[son:m.start()])
        son = m.end()
        for kod in re.findall(r"\\([^\\]+)", m.group(1)):
            kod = kod.strip()
            if kod in ("i1", "i0"):
                italik(kod == "i1")
            elif kod.startswith("r") and "(" not in kod:        # \r, \rStil: stilin kendi biçimine dönüş
                italik(stil_italik)
            elif re.fullmatch(r"an[789]|a[567]", kod):          # \an: tuş takımı düzeni; \a: eski düzen (5-7 üst)
                durum["ust"] = True
            elif re.match(r"(pos|move)\s*\(", kod):
                durum["ekran"] = True
                sayilar = re.findall(r"-?\d+(?:\.\d+)?", kod)
                if len(sayilar) >= 2 and durum["konum"] is None:
                    durum["konum"] = (float(sayilar[1]), float(sayilar[0]))      # (y, x): satır sırası için
            elif m.start() == 0 and re.fullmatch(r"fscy\s*\d+(?:\.\d+)?", kod) and float(kod[4:].strip()) <= 60:
                durum["kucuk"] = True                                        # yarım boy yazı: okunuş (furigana)
            elif re.fullmatch(r"p\d+", kod):
                durum["cizim"] = kod != "p0"
            elif re.match(r"[kK][fo]?\d", kod):
                durum["kara"] = True
    if not durum["cizim"]:
        cikti.append(metin[son:])
    italik(False)
    yazi = "".join(cikti).replace("\\N", "\n").replace("\\n", " ").replace("\\h", " ")
    yazi = re.sub(r"<i>(\s*)</i>", r"\1", yazi)
    yazi = "\n".join(x.strip() for x in yazi.split("\n") if x.strip())
    _ass_metin.son = (durum["konum"], durum["kucuk"])                 # TV altyazısı düzeni için (_tv_duzeni)
    return yazi, durum["ust"], durum["ekran"], durum["kara"]


_KANA_YALNIZ = re.compile(r"[ぁ-ゖァ-ヺー・…\s]+")


def _tv_duzeni(kayitlar):
    """Japon TV yayınından dönüştürülmüş ASS (ARIB): her satır ayrı konumlu bir olaydır, okunuşlar (furigana) yarım
    boy ayrı olaylardır. Konum burada ekran yazısı demek değildir. Okunuşlar atılır, aynı anda duran satırlar
    yukarıdan aşağı tek altyazıda birleşir; yoksa "さんた", "はたち" gibi okunuşlar ayrı replik diye çevriliyor,
    sözlük adımı bütün satırları ekran yazısı sayıp boş kalıyordu (Dandadan, Vinland Saga, gerçek test K)."""
    konusma = [k for k in kayitlar if not k["sarki"]]
    konumlu = [k for k in konusma if k.get("konum") is not None]
    japonca = sum(1 for k in konusma if re.search(r"[ぁ-ゖァ-ヺ一-龥]", k["metin"]))
    if len(konusma) < 20 or len(konumlu) < 0.8 * len(konusma) or japonca < 0.5 * len(konusma):
        return kayitlar
    kalan = [k for k in kayitlar if not (k.get("kucuk") and _KANA_YALNIZ.fullmatch(temizlik.ETIKET.sub("", k["metin"])))]
    gruplar, sonuc = {}, []
    for k in kalan:
        if k["sarki"] or k.get("yazi_stili"):
            sonuc.append(k)
            continue
        anahtar = (k["bas"], k["bit"])
        if anahtar not in gruplar:
            gruplar[anahtar] = dict(k, ekran=False, parcalar=[])
            sonuc.append(gruplar[anahtar])
        gruplar[anahtar]["parcalar"].append(k)
    for k in sonuc:
        if "parcalar" in k:
            sirali = sorted(k.pop("parcalar"), key=lambda x: x.get("konum") or (0, 0))
            k["metin"] = "\n".join(x["metin"] for x in sirali)
            k["ust"] = k["stil_ust"] = False
    return sonuc


def _ass(icerik, girdi, sarki_modu):
    stiller, stil_alanlari, olay_alanlari, bolum = {}, None, None, ""
    kayitlar = []
    for ham in icerik.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = ham.strip()
        if s.startswith("["):
            bolum = s.lower()
            continue
        tur, _, govde = s.partition(":")
        tur = tur.strip().lower()
        if "styles" in bolum:
            if tur == "format":
                stil_alanlari = [a.strip().lower() for a in govde.split(",")]
            elif tur == "style" and stil_alanlari:
                d = dict(zip(stil_alanlari, (x.strip() for x in govde.split(","))))
                hiza = d.get("alignment", "")
                hiza = int(hiza) if hiza.isascii() and hiza.isdigit() and len(hiza) <= 2 else 2
                # [V4+ Styles] hizayı tuş takımı düzeniyle yazar (7-9 üst), eski [V4 Styles] 5-7 ile.
                stiller[d.get("name", "").lstrip("*")] = (d.get("italic", "0") not in ("0", ""),
                                                          hiza in ((7, 8, 9) if "+" in bolum else (5, 6, 7)))
        elif bolum == "[events]":
            if tur == "format":
                olay_alanlari = [a.strip().lower() for a in govde.split(",")]
            elif tur in ("dialogue", "comment"):
                alanlar = olay_alanlari or OLAY_ALANLARI
                degerler = govde.split(",", len(alanlar) - 1)        # metin son alandır, içinde virgül olabilir
                if len(degerler) < len(alanlar):
                    continue
                d = dict(zip(alanlar, degerler))
                efekt = d.get("effect", "").strip().lower()
                # Karaoke şablonu: asıl dize "Comment" + "karaoke" olarak durur, hece hece üretilen satırlar "fx"tir.
                if (tur == "comment" and efekt != "karaoke") or (tur == "dialogue" and efekt == "fx"):
                    continue
                bas, bit = _ass_zaman(d.get("start", "")), _ass_zaman(d.get("end", ""))
                stil = d.get("style", "").strip().lstrip("*")
                stil_italik, stil_ust = stiller.get(stil, (False, False))
                metin, ust, ekran, kara = _ass_metin(d.get("text", ""), stil_italik)
                konum, kucuk = _ass_metin.son
                if bas is None or bit is None or bit <= bas or not temizlik.ETIKET.sub("", metin).strip():
                    continue
                sarki = bool(girdi["ass_sarki_stili"] and (tur == "comment" or kara or SARKI_STILI.search(stil)))
                yazi_stili = bool(girdi["ass_yazi_stili"] and YAZI_STILI.search(stil))
                ekran = bool(ekran or yazi_stili) and not sarki
                kayitlar.append({"bas": bas, "bit": bit, "metin": metin, "ust": ust and not ekran,
                                 "stil_ust": stil_ust and not ekran, "ekran": ekran, "sarki": sarki, "stil": stil, "ref": "",
                                 "konum": konum, "kucuk": kucuk, "yazi_stili": yazi_stili})
    kayitlar.sort(key=lambda k: k["bas"])                            # sıralama kararlıdır: eşit zamanda dosya sırası
    kayitlar = _tv_duzeni(kayitlar)
    kayitlar = _cift_dil(_sarkiyi_teklestir(kayitlar, sarki_modu), girdi["cift_dil"])
    satirlar, son = [], {}                                           # son: metin -> o metinle yazılmış son satır
    for k in kayitlar:
        metin = k["metin"]
        if k["sarki"] and "♪" not in metin and "♫" not in metin:
            metin = f"♪ {metin} ♪"
        onceki = son.get(metin)
        if onceki is not None and k["bas"] <= onceki.bit + 100:      # aynı yazı üst üste katmanlarda ya da art arda
            onceki.bit = max(onceki.bit, k["bit"])
            continue
        son[metin] = Satir(0, k["bas"], k["bit"], ("{\\an8}" if k["ust"] or k["stil_ust"] else "") + metin,
                           ekran=k["ekran"], ref=k["ref"])
        satirlar.append(son[metin])
    return _numarala(satirlar)


def _sarkiyi_teklestir(kayitlar, sarki_modu):
    """Aynı anda birkaç stilde yazılmış şarkı dizesinden (romaji + kanji + çeviri) yalnızca biri kalır.
    Şarkı özgün kalacaksa Latin harfli okunuş (romaji) yeğlenir, çevrilecekse çevirisi; kanji en sona kalır."""
    def puan(k):
        if temizlik.latin_disi(temizlik.ETIKET.sub("", k["metin"])):
            return 2
        romaji = "rom" in k["stil"].lower()
        return 0 if romaji == (sarki_modu != "ceviri") else 1

    secilen, sarkilar = [], []
    for k in kayitlar:
        if not k["sarki"]:
            secilen.append(k)
            continue
        es = next((x for x in reversed(sarkilar) if _ortusme(x, k) >= 0.5 * min(x["bit"] - x["bas"], k["bit"] - k["bas"])), None)
        if es is None:
            sarkilar.append(k)
            secilen.append(k)
        elif puan(k) < puan(es):
            es.update(k)
    return secilen


def _ortusme(a, b):
    return min(a["bit"], b["bit"]) - max(a["bas"], b["bas"])


def _cift_dil(kayitlar, mod):
    """Çift dilli dosya: aynı replikler iki dilde, iki ayrı satır olarak yazılmış (Japonca + İngilizce gibi).
    İkisi de çevrilirse her replik ekrana iki kez gelir. "Asıl" parça Latin dışı yazıyla yazılandır (özgün dil),
    "ikinci" Latin harfli olan (hazır çevirisi). mod: "referans" asıl çevrilir, ikincinin aynı andaki satırı modele
    yalnızca başvuru olarak gider; "asil" / "ikinci" yalnızca o parça kalır; "kapali" dokunulmaz.
    Ekran yazıları, şarkılar ve karşılığı olmayan satırlar (yalnızca bir dilde yazılmış replik) yerinde kalır."""
    if mod == "kapali":
        return kayitlar
    konusma = [k for k in kayitlar if not k["ekran"] and not k["sarki"]]
    asil = [k for k in konusma if temizlik.latin_disi(temizlik.ETIKET.sub("", k["metin"]))]
    ikinci = [k for k in konusma if not temizlik.latin_disi(temizlik.ETIKET.sub("", k["metin"]))]
    if min(len(asil), len(ikinci)) < max(10, 0.2 * len(konusma)):
        return kayitlar
    esler, n = {}, 0                    # asıl satırın sırası -> aynı anda ekranda duran ikinci dil satırları
    for i, a in enumerate(asil):
        while n < len(ikinci) and ikinci[n]["bit"] <= a["bas"] - 15000:      # ikisi de zamana göre sıralı
            n += 1
        for b in ikinci[n:]:
            if b["bas"] >= a["bit"]:
                break
            if _ortusme(a, b) >= 0.4 * min(a["bit"] - a["bas"], b["bit"] - b["bas"]):
                esler.setdefault(i, []).append(b)
    esli_ikinci = {id(b) for liste in esler.values() for b in liste}
    if len(esli_ikinci) < 0.6 * len(ikinci):
        return kayitlar                 # iki dil aynı replikleri taşımıyor: çift dilli değil, karışık dilli dosya
    atilan = set()
    if mod == "ikinci":
        atilan = {id(asil[i]) for i in esler}
    else:
        atilan = esli_ikinci
        for i, a in enumerate(asil):
            a["stil_ust"] = False       # asıl parça yalnızca ötekiyle üst üste binmesin diye üste konmuştur
            if mod == "referans" and i in esler:
                a["ref"] = " / ".join(" ".join(temizlik.ETIKET.sub("", b["metin"]).split()) for b in esler[i])
    return [k for k in kayitlar if id(k) not in atilan]


# ---- WebVTT ------------------------------------------------------------------------------------------------
VTT_ZAMAN = re.compile(r"(?<![\d:])(?:(\d{1,4}):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*"
                       r"(?:(\d{1,4}):)?(\d{1,2}):(\d{2})[.,](\d{3})(.*)")
VTT_SES = re.compile(r"<v(?:\.[\w.-]+)*\s+([^<>]+)>")
VTT_AN = re.compile(r"<\d{1,2}:\d{2}(?::\d{2})?\.\d{3}>")       # karaoke zaman etiketi


def _vtt_etiket(m):
    """<v Ad> konuşmacı etiketi olur: Latin harfliyse "AD: ", değilse Japonca altyazılardaki gibi （ad）."""
    ad = " ".join(m.group(1).split())
    return f"（{ad}）" if temizlik.latin_disi(ad) else ad.upper() + ": "


def _vtt(icerik):
    satirlar = []
    for blok in re.split(r"\n[ \t]*\n", icerik.replace("\r\n", "\n").replace("\r", "\n")):
        sira = blok.strip("\n").split("\n")
        if re.match(r"\s*(NOTE|STYLE|REGION)\b", sira[0]):
            continue
        # Her zaman satırı yeni bir altyazıdır: araya boş satır konmamış dosyada sonraki altyazı (zaman satırıyla
        # birlikte) öncekinin metnine karışmasın. Sıradaki numara (srt'den çevrilmiş dosya) kimliktir, metin değil.
        zamanlar = [n for n, x in enumerate(sira) if "-->" in x and VTT_ZAMAN.search(x)]
        for k, son in zip(zamanlar, zamanlar[1:] + [len(sira)]):
            govde, no = sira[k + 1:son], _numara(sira[k - 1]) if k else None
            if son < len(sira) and govde and no is not None and _numara(govde[-1]) == no + 1:
                govde.pop()
            g = VTT_ZAMAN.search(sira[k]).groups()
            bas, bit = _ms(g[0] or 0, g[1], g[2], g[3]), _ms(g[4] or 0, g[5], g[6], g[7])
            # "line" ayarı: yüzde olarak ekranın üst yarısı ya da üstten sayılan ilk satırlar üstte gösterme demektir.
            hiza = re.search(r"\bline:(-?\d+(?:\.\d+)?)(%?)", g[8])
            ust = bool(hiza and (float(hiza.group(1)) < 50 if hiza.group(2) else 0 <= float(hiza.group(1)) <= 6))
            metin = VTT_AN.sub("", VTT_SES.sub(_vtt_etiket, "\n".join(x for x in govde if x.strip())))
            if metin.strip() and bit > bas:
                satirlar.append(Satir(len(satirlar) + 1, bas, bit, ("{\\an8}" if ust else "") + metin))
    return satirlar


# ---- .sub: MicroDVD, MPL2, SubViewer -------------------------------------------------------------------------
MICRODVD = re.compile(r"^\{(\d{1,9})\}\{(\d{0,9})\}(.*)$")
MPL2 = re.compile(r"^\[(\d{1,9})\]\[(\d{0,9})\](.*)$")
SUBVIEWER = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3}),(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*$")


def _sub(icerik, fps):
    if "\x00" in icerik[:4000]:
        raise BicimHatasi("görüntü tabanlı altyazı (VobSub): metin içermez, çevrilemez")
    sira = [x.strip() for x in icerik.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    dolu = [x for x in sira if x]
    if any(SUBVIEWER.match(x) for x in dolu[:40]):
        return _subviewer(sira)
    for desen, birim in ((MICRODVD, None), (MPL2, 100)):          # MPL2 zamanı saniyenin onda biriyle yazar
        if any(desen.match(x) for x in dolu[:5]):
            return _kare_satirlari([desen.match(x) for x in dolu], birim, fps)
    raise BicimHatasi("tanınmayan .sub biçimi (MicroDVD, MPL2 ya da SubViewer değil)")


def _kare_satirlari(eslesmeler, birim, fps):
    """MicroDVD ({kare}{kare}metin) ve MPL2 ([an][an]metin). birim: bir sayının milisaniyesi; None ise kare (fps)."""
    kayitlar = [(int(m.group(1)), int(m.group(2)) if m.group(2) else None, m.group(3)) for m in eslesmeler if m]
    # İlk satır kare hızını bildiriyor: {1}{1}23.976. Ekranda kalan sayı ({0}{50}23) repliktir; "00" hız değildir.
    if (birim is None and kayitlar and kayitlar[0][0] <= 1 and (kayitlar[0][1] or 0) <= 1
            and re.fullmatch(r"\d{2}(?:[.,]\d+)?", kayitlar[0][2].strip())):
        bildirilen = float(kayitlar.pop(0)[2].strip().replace(",", "."))
        fps = bildirilen if bildirilen >= 1 else fps
    ms = (lambda n: round(n * 1000 / fps)) if birim is None else (lambda n: n * birim)
    satirlar = []
    for n, (bas, bit, metin) in enumerate(kayitlar):
        bas_ms = ms(bas)
        if bit is None or bit <= bas:        # bitişi yazılmamış: sonraki altyazıya kadar, en çok 3 saniye
            bit_ms = min(ms(kayitlar[n + 1][0]) if n + 1 < len(kayitlar) else bas_ms + 3000, bas_ms + 3000)
        else:
            bit_ms = ms(bit)
        tumu_italik = bool(re.match(r"\s*\{Y:[^{}]*i", metin))
        siralar = []
        for sira in metin.split("|"):
            italik = tumu_italik or bool(re.match(r"\s*(?:\{y:[^{}]*i[^{}]*\}|/)", sira))
            sira = re.sub(r"\{[^{}]*\}", "", sira).strip().lstrip("/").strip()
            if sira:
                siralar.append(f"<i>{sira}</i>" if italik else sira)
        if siralar and bit_ms > bas_ms:
            satirlar.append(Satir(len(satirlar) + 1, bas_ms, bit_ms, "\n".join(siralar)))
    return satirlar


def _subviewer(sira):
    """SubViewer: "00:04:35.03,00:04:38.82" satırını metin izler; metindeki [br] satır sonudur."""
    satirlar, i = [], 0
    while i < len(sira):
        m = SUBVIEWER.match(sira[i])
        i += 1
        if not m:
            continue
        govde = []
        while i < len(sira) and sira[i] and not SUBVIEWER.match(sira[i]):
            govde.append(sira[i])
            i += 1
        g = m.groups()
        metin = re.sub(r"\s*\[br\]\s*", "\n", "\n".join(govde), flags=re.I)
        if metin.strip():
            satirlar.append(Satir(len(satirlar) + 1, _ms(*g[:4]), _ms(*g[4:]), metin))
    return satirlar
