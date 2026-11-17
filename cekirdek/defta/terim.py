"""Terim sözlüğünün kurulması (2. sürüm).

Üç iş birbirinden ayrıdır:
  1. ADAYI SCRIPT BULUR  (adaylari_bul): aynı dosya her zaman aynı aday listesini verir.
  2. TÜRÜNÜ MODEL SÖYLER (siniflandir): kapalı liste; her adayın ne olduğu, Latin yazımı, yerleşik karşılığı.
     Scriptin göremediği adlar (kanji ile yazılmış kod adı, küçük harfli uydurma sözcük) için dosyayı okuyan
     ayrı ve dar bir istek aynı anda gider (kesfet).
  3. KARŞILIĞI SCRIPT BELİRLER (hedef_belirle): kullanıcının kuralları tür başına burada uygulanır; model bir
     kuralı çiğneyemez (kod adı çevrilmez, uydurma ad çevrilmez, sıradan sözcük sözlüğe girmez...).

Sonuç Sozluk.girdi_ekle ile saklanır: tür, karşılık, yazım biçimleri, geçiş sayısı ve örnek satırla birlikte.
"""
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import temizlik
from .ayar import VARSAYILAN, acik, dil_adi, hedef_kodu, unvan_sozcukleri, unvan_tablosu
from .istem import dile_gore
from .kaynak import EK as EK_LATIN, okunus_yazimi, ses_latin, ses_satiri
from .model import Ret, json_coz
from .sozluk import UNVAN as HITAP_EKI, _anahtar, _iskelet_yakinligi, ekli_ad_mi, tek_sozcuk_ad

MAKRONSUZ = str.maketrans("āēīōūĀĒĪŌŪ", "aeiouAEIOU")
SAPKASIZ = str.maketrans("âîû", "aiu")
TURLER = ("name", "descriptive", "codename", "epithet", "title", "people", "term", "fictional", "common")
PARTI = 40                      # bir sınıflandırma isteğindeki aday sayısı (istekler aynı anda gider)

# ---- 1. aday bulma (script) -------------------------------------------------------------------------------
BUYUK = "[A-ZÀ-ÖØ-Þ" + "".join(ch for ch in map(chr, range(0x100, 0x180)) if ch.isupper()) + r"][\w'’\-]*"
AD_DIZISI = re.compile(rf"{BUYUK}(?:\s+(?:of|the|de|la|del|von|van|al)\s+{BUYUK}|\s+{BUYUK})*")
# Cümle ortasında büyük harfle yazılsa da ad olmayan sözcükler
ZAMIRLER = {"I", "I'm", "I'll", "I've", "I'd", "OK", "Okay", "God"}
SIRADAN_BUYUK = {"mister", "madam", "ma'am", "father", "mother", "daddy", "mommy", "uncle", "auntie", "grandpa",
                 "grandma", "brother", "sister", "monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday", "christmas", "english", "french", "german", "spanish", "italian",
                 "american", "british", "internet", "heaven", "jesus", "christ"}
KATAKANA = r"[ァ-ヺー]"
# Yalnız uzatma çizgisinden oluşan dizi (えーー, うわーーー) sözcük değildir: en az bir katakana harfi aranır.
KATAKANA_AD = re.compile(rf"(?=ー*[ァ-ヺ]){KATAKANA}{{2,}}(?:・{KATAKANA}{{2,}})*")
OKUNUSLU = re.compile(rf"([一-龥々]{{1,6}})[(（]((?:{KATAKANA}|・){{2,}}|[ぁ-ん]{{2,}})[)）]")     # 東国(オスタニア), 黄昏(たそがれ)
AYRACLI = re.compile(rf"(?:{KATAKANA}|[一-龥々]){{0,10}}〈[^〈〉]{{1,16}}〉")                         # プロジェクト〈アップル〉
OKUNUS_KATAKANA = re.compile(rf"[(]{KATAKANA}+[)]$")
JP_ETIKET = re.compile(r"^[ \t]*（([^（）]{1,12})）", re.M)                                          # （ロイド）
JP_ROL = re.compile(r"(?<![一-龥々])[一-龥]{1,4}(?:屋|長|官|相|卿|王|姫|司令|大臣|総統|博士|教授|刑事|警部)")
JP_EKLI_KANJI = re.compile(r"([一-龥々]{1,4})(?:さん|様|先生|先輩|くん|ちゃん)")
JP_EKLI_HIRAGANA = re.compile(r"(?<![ぁ-ん])([ぁ-ん]{2,5})(?:さん|くん|ちゃん)")                    # しんちゃん
# Sıradan sözcük + hitap eki (犬さん, ワンちゃん, お客さん): sık geçiyorsa bir karakterin ya da hayvanın sevimli /
# kibar adı olabilir; model öyle derse tek bir karşılık alır ki her satırda başka çevrilmesin.
JP_EKLI_SOZ = re.compile(r"((?:(?<![一-龥々ぁ-ん])[おご])?(?<![一-龥々])[一-龥々]{1,4}|(?<![ァ-ヺー])[ァ-ヺー]{2,8})"
                         r"(さ[ーあ〜～]*ん|ちゃ[ーあ〜～]*ん|く[ーう〜～]*ん|さま|様)")
EKLI_SOZ_IPUCU = ('an ordinary word with a Japanese honorific attached. If the honorific makes it cute or turns it into '
                  'a way of calling or naming someone or something (クマさん, ネコちゃん, 魚屋さん), it is an "epithet". '
                  'If the honorific adds nothing to a plain everyday word (お客さん "customer", 皆さん "everyone"), '
                  'it is "common"')
# Karşılık isteğinde ekli sözcüğün tonu: Türkçesi bu istekten gelir.
EKLI_KARSILIK_IPUCU = ('an ordinary word with a Japanese honorific attached, used to call or name someone or something: '
                       'the one natural way a native speaker of TARGET language would say it every time, WITHOUT any '
                       'honorific, in the same tone: a cute or affectionate one is the pet or diminutive form a child '
                       'would use (クマさん -> the TARGET language word for "little bear", never the plain word for '
                       '"bear"), a polite one for a trade or role (魚屋さん) is the plain polite word')


def _sinir(parca):
    """Sözcük sınırıyla arama deseni (Latin harfli adaylar için)."""
    return r"(?<![\w'’-])" + re.escape(parca) + r"(?![\w-])"


def _latin_adaylar(satir_metinleri, esik, bilinen_unvanlar):
    """Büyük harfle yazılan, tekrar eden sözcük ve sözcük dizileri.
    (aday -> sayı, çoğul -> tekil, unvan -> sayı, (unvan, ad) çiftleri, bir adın önünde duran sözcük -> sayı)
    döndürür. Sonuncusu, bilinen listede olmayan unvanı ("Warden Hale") yakalamak içindir."""
    orta, unvanlar, ciftler, onler = set(), Counter(), Counter(), Counter()   # ciftler: ("Lord", "Elrond") -> sayı
    for satir in satir_metinleri:
        for m in AD_DIZISI.finditer(satir):
            parca, once = m.group(0), satir[:m.start()].rstrip()
            basta = not once or once[-1] in ".!?…:\"“”'‘(—-"
            if basta and parca.split()[0] not in bilinen_unvanlar:   # cümle başı: ilk sözcüğün adlığı belirsiz, atılır
                parca = parca.split(None, 1)[1] if " " in parca else ""
                if parca and not re.match(BUYUK, parca):
                    parca = ""
            sozcukler = parca.split()
            while sozcukler and (sozcukler[0] in ZAMIRLER or not sozcukler[0][:1].isupper()):
                sozcukler.pop(0)                                                # "I'm the Doctor" -> Doctor
            while sozcukler and sozcukler[-1] in ZAMIRLER:
                sozcukler.pop()                                                 # "Tell Mr. Pell I am busy" -> Pell
            while len(sozcukler) > 1 and sozcukler[0] in bilinen_unvanlar:    # "Captain Venn" -> Captain + Venn
                unvan = sozcukler.pop(0)
                unvanlar[unvan] += 1
                if not sozcukler[0][:1].isupper():                              # "Lord of Mordor": çift değil
                    while sozcukler and not sozcukler[0][:1].isupper():
                        sozcukler.pop(0)
                    break
                ciftler[(unvan, re.sub(r"['’]s?$", "", " ".join(sozcukler)))] += 1
            if len(sozcukler) > 1 and sozcukler[1][:1].isupper() and sozcukler[0].isalpha():
                onler[sozcukler[0]] += 1
            parca = re.sub(r"['’]s?$", "", " ".join(sozcukler))
            if parca in bilinen_unvanlar:        # tek başına büyük harfle: ad gibi kullanılıyor olabilir ("the Doctor")
                unvanlar[parca] += 1
            elif parca and parca not in ZAMIRLER and not HITAP_EKI.match(parca):
                orta.add(parca)
    unvanli = {ad for _, ad in ciftler}                    # bir unvandan sonra geçen adlar
    orta = sorted(orta)       # küme sırası çalıştırmadan çalıştırmaya değişir; çoğul birleştirme sıraya bağlıdır
    tum = "\n".join(satir_metinleri)
    sayim = Counter()
    for parca in orta:
        # Küçük harfli hali de geçiyorsa sıradan sözcüktür (If, Look, Long).
        if " " not in parca and re.search(_sinir(parca.lower()), tum):
            continue
        n = len(re.findall(_sinir(parca), tum))
        # Sözcük dizisi ("Operation Lantern") ve unvanla anılan ad ("Lady Serane") bir kez geçse de addır.
        if n >= (1 if " " in parca or parca in unvanli else esik):
            sayim[parca] = n
    # Eserin kendi dünyasına ait cins adlar çoğu kez küçük harfle yazılır ama arada bir büyük harfle de geçer
    # ("one of them Rangers" / "a ranger"): en sık yazımıyla, tekil biçimiyle aday olur.
    for parca in orta:
        if " " in parca or parca in sayim or len(parca) < 5 or not parca.isalpha():
            continue
        kok = parca.lower()[:-1] if parca.lower().endswith("s") else parca.lower()
        bicimler = re.findall(r"(?<![\w'’-])(" + re.escape(kok) + r")s?(?![\w-])", tum, re.I)
        if len(bicimler) >= max(esik, 3) and kok not in SIRADAN_BUYUK:
            sayim.setdefault(Counter(bicimler).most_common(1)[0][0], len(bicimler))
    # Başka bir adayın içinde geçmekten öteye gitmeyen tek sözcük ("Mount Doom" içindeki "Doom") aday değildir;
    # bütün sözcükleri zaten ayrı ayrı aday olan dizi ("Frodo Baggins") de ayrıca sorulmaz.
    diziler = [p for p in sayim if " " in p]
    for parca in [p for p in sayim if " " not in p]:
        icinde = sum(sayim[d] for d in diziler if re.search(_sinir(parca), d))
        if icinde and sayim[parca] - icinde < 2:
            del sayim[parca]
    for dizi in diziler:
        sozcukler = dizi.split()
        if all(s in sayim and s[:1].isupper() for s in sozcukler):
            del sayim[dizi]
    cogullar = {}
    for parca in [p for p in sayim if " " not in p]:      # çoğul biçim tekilin altında toplanır: Vardy + Vardies
        for cogul in (parca + "s", parca + "es", parca[:-1] + "ies" if parca.endswith("y") else ""):
            if cogul and cogul in sayim and cogul != parca:
                cogullar[cogul] = parca
    for cogul, tekil in cogullar.items():
        if tekil in sayim and cogul in sayim:
            sayim[tekil] += sayim.pop(cogul)
    return sayim, cogullar, unvanlar, ciftler, onler


def _japonca_adaylar(metin):
    """Japonca kaynakta ad izi taşıyan yazımlar. aday -> (sayı, öteki yazımları) döndürür."""
    adaylar = {}
    for m in OKUNUSLU.finditer(metin):                  # okunuşu yanında yazılmış sözcük: üç yazımı tek girdi
        asil = f"{m.group(1)}({m.group(2)})"
        sayi, bicimler = adaylar.get(asil, (0, set()))
        adaylar[asil] = (sayi + 1, bicimler | {m.group(1), m.group(2)})
    for asil in [a for a, (sayi, _) in adaylar.items() if sayi < 2 and re.search(r"[(][ぁ-ん]+[)]$", a)]:
        del adaylar[asil]                               # bir kez geçen hiragana okunuş: sıradan okuma yardımı
    okunuslu = {b for _, bicimler in adaylar.values() for b in bicimler}
    for m in AYRACLI.finditer(metin):
        adaylar[m.group(0)] = (adaylar.get(m.group(0), (0, set()))[0] + 1, set())
    duz = OKUNUSLU.sub(" ", metin)
    sayim = Counter(KATAKANA_AD.findall(duz))
    etiketler = Counter(e.strip() for e in JP_ETIKET.findall(metin))
    kanji_ad = Counter(JP_EKLI_KANJI.findall(duz)) + Counter(JP_EKLI_HIRAGANA.findall(duz))
    roller = Counter(JP_ROL.findall(re.sub(r"（[^）]*）", " ", duz)))
    for parca, n in sayim.items():
        if n >= 2 and parca not in okunuslu:
            adaylar.setdefault(parca, (n, set()))
    for parca, n in etiketler.items():                  # konuşmacı etiketi: yalnızca ad biçimindekiler
        if parca not in okunuslu and (KATAKANA_AD.fullmatch(parca) or (n >= 2 and re.fullmatch(r"[一-龥々]{2,4}", parca))):
            adaylar.setdefault(parca, (n + sayim.get(parca, 0), set()))
    for parca, n in list(kanji_ad.items()) + roller.most_common(20):
        if (n >= 2 or parca in roller) and parca not in okunuslu:
            adaylar.setdefault(parca, (n, set()))
    return adaylar


def _ekli_sozcukler(metin, esik=3):
    """Sıradan sözcük + hitap eki, en az "esik" kez: yazım -> (sayı, uzatılmış öteki yazımlar, sözcük). Konuşmacı
    etiketindeki adlar (（ロイド）) ve okunuşu yanında yazılmış sözcükler (黄昏(たそがれ): kod adı) aday olmaz; kalanların
    ad mı sözcük mü olduğuna model karar verir, adlı çıkan sözcüğün eklisi sözlüğe girmez (terimleri_cikar)."""
    duz = re.sub(r"（[^（）]*）", " ", OKUNUSLU.sub(" ", metin))
    adlar = {e.strip() for e in JP_ETIKET.findall(metin)} | {y for m in OKUNUSLU.finditer(metin) for y in m.groups()}
    gruplar = {}
    for m in JP_EKLI_SOZ.finditer(duz):
        if m.group(1) in adlar or m.group(1).lstrip("おご") in adlar:
            continue
        ek = re.sub(r"[ーあう〜～]", "", m.group(2))
        gruplar.setdefault((m.group(1), ek), Counter())[m.group(0)] += 1
    sonuc = {}
    for (kok, ek), yazimlar in gruplar.items():
        if sum(yazimlar.values()) >= esik:
            asil = kok + ek
            sonuc[asil] = (sum(yazimlar.values()), set(yazimlar) - {asil}, kok)
    return sonuc


def adaylari_bul(dosyalar, bilinen, esik=2, azami=320, unvanlar=None, ciftler=None, unvan_sor=False, tablo=()):
    """Bir yapımın bütün dosyalarından adayları toplar.

    dosyalar: (ad, satırlar, başlık) listesi; bilinen: hakkında karar verilmiş yazımların anahtarları.
    [{"yazim", "bicimler", "sayi", "ornek": [satır, satır], "ipucu"}] döndürür; sıra ve içerik belirlidir.
    unvanlar: bilinen unvan sözcükleri; ciftler verilirse bulunan (unvan, ad) çiftleri ona eklenir.
    unvan_sor: unvan kararını model de veriyorsa, "tablo"da olmayan unvanlar bir kez geçse de sorulur ve
    adların önünde duran, listede olmayan sözcükler ("Warden Hale") aday yapılır.
    """
    if unvanlar is None:
        unvanlar = {k for t in VARSAYILAN["unvanlar"].values() for k in t}
    # Ekran yazıları aday kaynağı değildir: Her Sözcüğü Büyük Harfle yazılan tabela, ad olmayan sözcükleri ad gösterir.
    konusma = [s for _, satirlar, _ in dosyalar for s in satirlar if not s.ekran]
    satir_metinleri, tum_satirlar = [], []
    for s in konusma:
        duz = temizlik.ETIKET.sub("", s.metin)
        tum_satirlar.append(duz.replace("\n", " / "))
        for satir in duz.split("\n"):
            satir = re.sub(r"^\s*[-–—]\s*", "", satir).strip()
            satir = re.sub(r"\b(Mr|Mrs|Ms|Dr)\.", r"\1", satir)        # "Mr. Frodo": nokta cümle sonu değil
            if satir and not satir.isupper():
                satir_metinleri.append(satir)
    metin = "\n".join(temizlik.ETIKET.sub("", s.metin) for s in konusma)
    latin, cogullar, unvan_sayim, bulunan, onler = _latin_adaylar(satir_metinleri, esik, unvanlar)
    if ciftler is not None:
        ciftler.update(bulunan)
    ciftli = {u for u, _ in bulunan}
    unvan_sayim = {u: n for u, n in unvan_sayim.items() if n >= 2 or (unvan_sor and u in ciftli and u not in tablo)}
    adaylar = [{"yazim": p, "bicimler": {c for c, t in cogullar.items() if t == p}, "sayi": n, "ipucu": ""}
               for p, n in latin.items()]
    adaylar += [{"yazim": u, "bicimler": set(), "sayi": n, "ipucu": "a title; if it serves as a character's NAME here it is an epithet"}
                for u, n in unvan_sayim.items()]
    if unvan_sor:
        adaylar += [{"yazim": o, "bicimler": set(), "sayi": n, "on": True,
                     "ipucu": "stands directly before names: a title or rank, or simply part of the name?"}
                    for o, n in onler.items() if n >= 2 and o not in latin and o not in unvanlar]
    adaylar += [{"yazim": p, "bicimler": b, "sayi": n,
                 "ipucu": 'the part in parentheses is how it is READ: "l" follows that reading (or the official English form, as for any item)' if b else ""}
                for p, (n, b) in _japonca_adaylar(metin).items()]
    adaylar += [{"yazim": p, "bicimler": b, "sayi": n, "ekli": kok, "ipucu": EKLI_SOZ_IPUCU}
                for p, (n, b, kok) in _ekli_sozcukler(metin, max(esik, 3)).items()]
    secilen = []
    for a in sorted(adaylar, key=lambda x: (-x["sayi"], x["yazim"])):
        if (_anahtar(a["yazim"]) in bilinen or (ekli_ad_mi(a["yazim"]) and not a.get("ekli"))
                or len(secilen) >= azami):
            continue
        gecen = [s for s in tum_satirlar if a["yazim"].split("(")[0] in s or a["yazim"] in s]
        if not gecen:
            gecen = [s for s in tum_satirlar if a["yazim"].lower() in s.lower()]
        a["ornek"] = [x[:110] for x in ([gecen[0], gecen[len(gecen) // 2]] if len(gecen) > 1 else gecen)]
        secilen.append(a)
    return secilen


# ---- 2. sınıflandırma ve keşif (model) --------------------------------------------------------------------
def _alanlar(dil, unvan=False, anlam=False):
    return f'''"t" - what it is in THIS work (the lines show how it is used):
- "name": an opaque proper name of a person, place, ship, organisation or thing (Harry, Hogwarts, Tatooine, ナルト).
- "descriptive": a proper name made of ordinary words, which a translator translates (Diagon Alley, the Forbidden Forest, the Leaky Cauldron, Keepers of Stonefall, 王立騎士団). An organisation, gang or order named with one ordinary word is this too, not "name" (a syndicate called 牙, "fang").
- "codename": a code name given by an organisation, or the name of an operation, mission or project (Nighthawk, 白鷺, Black Widow, Operation Overlord, プロジェクト〈ペガサス〉).
- "epithet": a nickname, epithet or by-name that describes its bearer (Erik the Red, the Boy Who Lived, the Kingslayer, the Lionheart), and a title or ordinary word used AS a character's name (a character everyone calls "the Professor").
- "title": a rank, office, role or form of address used with or instead of a name (Captain, Lord, Mr, 団長, 殿下).
- "people": a race, people, species or kind of creature, in the singular (centaur, house-elf, Wookiee, Na'vi).
- "term": an object, substance, technique, institution or concept that belongs to this work (lightsaber, horcrux, Polyjuice Potion, the Force), and a word for the special power, being or thing the story is built around, even when it is an ordinary word (the "gift" in a story where every hero is born with one, the "giants" in a story about fighting them, 霊力 as the energy its fighters use).
- "fictional": a word or phrase IN an invented language, quoted as such (Qapla', Dracarys, Alohomora). The NAME of a language is not fictional: it is "descriptive" and is translated (Klingon, Dothraki, Parseltongue).
- "common": an ordinary word, interjection, sound effect or fragment that is NOT a name or term of this work; this includes everyday loanwords for things, animals and jobs (Look, Monday, ドア, バカ, ニャー, タクシー, コーヒー, ナース, ポリス, ママ). Most doubtful candidates are this.
"l" - only when the candidate is not written in Latin letters: how it is written in Latin letters AS IT SOUNDS, because the viewers hear the original audio and must read the name they hear. When you recognise the work, use the romanised spelling of that same sound that the official release of THIS work uses, which is often not the letter-by-letter romanisation; otherwise the usual romanised spelling, and a name taken from a European language is written the way that language spells it (ケンジ -> Kenji, シャーロット -> Charlotte, ジョージ -> George). It is still the same name: a family name stays the family name, never the full name, the given name or another name of the same person. NEVER an English translation or localised name, even when the English release uses one: a character called ホシ is "Hoshi", never "Star"; 黒い牙 is "Kuroi Kiba", never "Black Fang".
"r" - only for a "name" written with kanji: how those characters are READ in the work's own audio, in hiragana (健二 -> けんじ, 黒い牙 -> くろいきば). Give it every time.
"e" - the ESTABLISHED {dil} form. Give it every time one exists: (1) for a work you recognise, the form its published {dil} translation uses, also when that form is a translation ([[tr|Diagon Alley -> Diagon Yolu, the Leaky Cauldron -> Çatlak Kazan, the Sorting Hat -> Seçmen Şapka, house-elf -> ev cini, Muggle -> Muggle||Diagon Alley, the Leaky Cauldron, the Sorting Hat, house-elf, Muggle]]); (2) for every real-world, historical or mythological person or place, its standard {dil} name, the same one an encyclopedia in {dil} uses ([[tr|Rome -> Roma, Hercules -> Herkül, Alexander the Great -> Büyük İskender, Athens -> Atina, Venice -> Venedik, Vienna -> Viyana||Rome, Hercules, Alexander the Great, Athens, Venice, Vienna]]). Leave "e" out only for a name that has no such form; never make one up.
"real" - true only for a real-world, historical or mythological person or place.
"tr" - only for "descriptive", "epithet", "title" and "people" that have no established form, and for a "term" made of ordinary words of the source language (賞金稼ぎ, shadow-walker), never for an invented word (horcrux, dilithium): one natural {dil} rendering[[tr| (Keepers of Stonefall -> Stonefall Bekçileri, 団長 -> Komutan, 賞金稼ぎ -> ödül avcısı)]]. A proper name inside it stays unchanged. Lower case unless it is part of a name.
"e" and "tr" render these same words; they are never the full name, real name, other name or family relation of whoever the item refers to (a nickname stays a nickname: "Ron" has no "e", never "Ronald Weasley").
Leave a field out when it is empty.''' + (f'''
"p" - for every "title", always: how the title is written together with a person's name in {dil}, with NAME standing for the name[[tr| (Captain -> "Kaptan NAME", Professor -> "Profesör NAME", 団長 -> "Komutan NAME")]]. Put NAME where {dil} puts it, before or after the title.''' if unvan else "") + ("""
"w" - for ANY item, a "name" included, whose spelling is ALSO an everyday word, a sound or a mimetic word of the source language, so that the subtitles may use it in that plain sense too: the plain sense in one to three English words (雷 -> "thunder", Hope -> "hope", ふわふわ -> "fluffy"). Leave it out when the spelling has no such everyday sense.
"m" - what the item refers to in THIS work, in at most 10 English words, for a translator who has not seen it ("the smuggler's alias in the guild", "the order that guards the wall", "a ghost bound to the old lighthouse"): for every "codename", "descriptive", "epithet", "title", "people" and "term", and for a "name" only when it has a "w". Facts only: never guess.""" if anlam else "")


def siniflandirma_istemi(ayar):
    dil = dil_adi(ayar)
    return dile_gore(f"""You classify names and terms found in the subtitles of one film or series, so that a translator into {dil} can render each of them the same way every time. You do NOT translate the subtitles. "title_hint" names the work. "candidates" were collected by a script: each has an id "i", its spelling ("s"), how often it occurs ("n"), one or two "lines" it occurs in, and sometimes a "hint" from the script. The list contains noise.

Return ONLY this JSON object, one item per candidate, with the same "i":
{{"work": "the title of the work if you recognise it, otherwise an empty string", "items": [{{"i": 1, "t": "name"}}, {{"i": 2, "t": "descriptive", "e": "..."}}, {{"i": 3, "t": "common"}}]}}

{_alanlar(dil, acik(ayar, "unvan", "model_karari"), ayar["sozluk"]["anlam_notu"])}""", hedef_kodu(ayar))


def kesif_istemi(ayar):
    dil = dil_adi(ayar)
    return f"""You read the subtitles of one film or episode and list the names and terms that a translator into {dil} must render the same way every time and that are NOT in "found" (a script already collected those). You do NOT translate the subtitles.

What the script cannot see, and you look for: names and code names written in kanji or hiragana (赤ひげ, かぐや姫), invented words and the story world's own creature or group words written in lower case (muggle, horcrux, wight, droid), the word for the special power or being the story is built around (霊力, gift, giant), offices and roles used instead of a name, recurring epithets. Only things that occur at least twice in these lines, copied exactly as the lines write them, in their shortest form. Never list ordinary words, honorifics, or anything already in "found". Usually there are fewer than 15; an empty list is a correct answer.

Return ONLY this JSON object:
{{"language": "ISO 639-1 code of the language of the lines", "work": "the title of the work if you recognise it, otherwise an empty string", "items": ["exact spelling", "exact spelling"]}}"""


# ---- 3. karşılığı belirleme (script) ----------------------------------------------------------------------
SES_ISTEMI = """You are given words from the subtitles of one film or series. Each is a Japanese word written only in kana that stands alone as a whole line; "line" shows one place where it occurs and "by" who says it.
Decide for each whether it is a SOUND or a WORD.
- SOUND: an animal's cry or a noise (ニャー, ガオー, ブーン), or a mimetic word that a character calls out as an exclamation or catchphrase (ルンルン, ドキドキ, キラーン). A translator may leave it as it sounds.
- WORD: anything with an ordinary meaning that has to be translated: answers and greetings (はい, もしもし, どうも), adverbs and adjectives (まだまだ, そろそろ, すごい, やばい), insults and orders (バカ, ダメ, こら), names, and laughter, sighs or gasps.
Answer with JSON only: {"items": [{"i": 1, "sound": true}, {"i": 2, "sound": false}]} - one item per word, same numbering."""


def ses_adaylari(dosyalar):
    """Yapımda sözü tek bir kana sözcüğü olan satırlar: sözcük -> {"sayi", "konusan", "yinelenen", "ornek", "ad_degil"}.

    ad_degil: sözcük en az iki kez, hep tek başına, hep aynı konuşmacıdan geliyor, cümle içinde hiç geçmiyor ve o
    konuşmacının etiketi bir ad değil bir tarif ("büyük beyaz köpek"). Ad başkalarının ağzından çıkar; böyle bir
    sözcüğü yalnızca sahibi söyler. Ad adayı olmaz: köpeğin havlaması (ボフ) dizinin köpeğinin adı ("Bond") sanılmıştı.
    Sözcüğün ses mi yoksa sıradan sözcük mü (どうも, バカ) olduğuna bu kanıt yetmez; onu model söyler."""
    sesler, satirlar = {}, []
    for _, dosya_satirlari, _ in dosyalar:
        for s in dosya_satirlari:
            if s.ekran or not s.metin.strip():
                continue
            ses = ses_satiri(s.metin)
            satirlar.append((s.metin, ses[0] if ses else None))
            if ses:
                k = sesler.setdefault(ses[0], {"sayi": 0, "konusanlar": set(), "yinelenen": False,
                                               "ornek": s.metin.replace("\n", " / ")[:80]})
                k["sayi"] += 1
                k["konusanlar"].add(ses[1])
                k["yinelenen"] |= ses[2]
    for soz, k in sesler.items():
        baska_yerde = any(soz in metin for metin, anahtar in satirlar if anahtar != soz)
        konusan = next(iter(k["konusanlar"])) if len(k["konusanlar"]) == 1 else None
        k["konusan"] = konusan or ""
        k["ad_degil"] = bool(k["sayi"] >= 2 and not baska_yerde and konusan and konusan != soz
                          and re.search(r"[一-龥々ぁ-ん]", konusan))
    return sesler


def hedef_belirle(yazim, cevap, taninan, latin_zorunlu=True, hedef_kodu="tr"):
    """Modelin söylediği türe kullanıcının kuralını uygular. (tür, hedef) döndürür.

    hedef == yazim: olduğu gibi kalır.  hedef == "": sözlüğe girmez (sıradan sözcük ya da karşılığı bulunamadı).
    "taninan": model eseri tanıdı mı. Tanımadığı eserde yerleşik karşılık ancak gerçek dünyadan bir ad için olur.
    """
    tur = cevap.get("t") if cevap.get("t") in TURLER else "common"
    def temiz(alan):
        deger = cevap.get(alan)
        deger = deger.strip().replace("’", "'") if isinstance(deger, str) else ""
        if alan == "l":                          # uzatma işaretsiz yazım (Dōberuman -> Doberuman)
            deger = deger.translate(MAKRONSUZ)
        return "" if (latin_zorunlu and temizlik.latin_disi(deger)) or len(deger) > 60 else deger
    latin, yerlesik = temiz("l"), temiz("e")
    yabanci = bool(temizlik.latin_disi(yazim))
    latin = tek_sozcuk_ad(yazim, latin)
    aynen = (latin if yabanci else yazim) if not (yabanci and latin_zorunlu and not latin) else ""
    # Yerleşik biçime yalnızca eser tanındıysa ya da ad gerçek dünyadansa güvenilir; yoksa uydurmadır.
    guvenilir = taninan or cevap.get("real") is True
    if tur == "common":                         # sıradan sözcük sözlüğe girmez
        return tur, ""
    if tur == "fictional":
        return tur, "" if yabanci and latin_zorunlu else yazim
    # Kaynak okunuşunu katakanayla vermiş: ad odur (東国(オスタニア) -> Ostania). Kişiyi niteleyen türlerde (lakap, unvan)
    # okunuş yalnızca söyleniştir, karşılık olmaz: 婆(ババア) "Baba" diye yazılmaz, aşağıdaki kuralla çevrilir.
    if OKUNUS_KATAKANA.search(yazim) and latin and tur not in ("epithet", "title"):
        return tur, latin
    if tur == "codename":                       # kod adı çevrilmez: özgün (ya da resmî İngilizce) yazımıyla kalır
        return tur, aynen
    if tur == "name":                           # uydurma ad çevrilmez; yalnızca yerleşik biçimi varsa o
        # Latin dışı kaynakta ad duyulduğu gibi yazılır: "yerleşik biçim" yalnız gerçek dünyadan adda (ローマ -> Roma)
        # kullanılır; kurgusal adın İngilizce yayındaki çevirisi ya da uyarlaması alınmaz. Yerleşik biçim adın
        # kendisine başka sözcük ekliyorsa (soyadı -> tam ad) o da alınmaz: ad aynı ad kalır.
        if yabanci and cevap.get("real") is not True:
            yerlesik = ""
        if yerlesik and aynen and len(yerlesik.split()) > len(aynen.split()) and re.search(
                r"(?<!\w)" + re.escape(temizlik.tr_kucuk(aynen)) + r"(?!\w)", temizlik.tr_kucuk(yerlesik)):
            yerlesik = ""
        return tur, (yerlesik if guvenilir else "") or aynen
    # descriptive, epithet, title, people, term: Türkçesini yalnızca karşılık isteği verir ("k"). Sınıflandırmanın
    # "e" ve "tr" alanları burada yalnızca Latin harfli yazımın çevrilip çevrilmeyeceğini söyler (_cevrilir).
    karsilik = temiz("k")
    # Latin dışı kaynakta İngilizce yazımla ("l") aynı karşılık çeviri değildir; hedef dil İngilizceyse odur.
    if karsilik and not (yabanci and latin and hedef_kodu != "en"
                         and temizlik.tr_kucuk(karsilik) == temizlik.tr_kucuk(latin)):
        if tur == "people" and " " not in karsilik and hedef_kodu == "tr":
            karsilik = temizlik.tr_kucuk(karsilik[:1]) + karsilik[1:]       # ırk adı küçük harfle (buçukluk)
        return tur, karsilik
    if yabanci:
        # Karşılığı gelmeyen kanji/hiragana yazımın "l" alanı İngilizce çevirisidir (爆弾犬 -> "bomb dog"), sıradan
        # anlamlı katakana İngilizce sözcüktür: sözlüğe girmez. Uydurma katakana terim yazımıyla kalır (ミスリル).
        siradan = isinstance(cevap.get("w"), str) and cevap["w"].strip()
        return tur, "" if re.search(r"[一-龥々ぁ-ん]", yazim) or siradan else aynen
    if _cevrilir(cevap, yazim):
        return tur, ""          # çevrilecek söz ama karşılığı gelmedi: sözlüğe girmez, çevirmen bağlama göre çevirir
    # Çevrilmeyen Latin yazım kalır (Vardy, Dúnedain, mithril); güvenilir yerleşik biçimin yalnızca harfi kazanır:
    # kaynak "Hobbit" yazsa da Türkçesi "hobbit"tir.
    return tur, yerlesik if guvenilir and yerlesik and temizlik.tr_kucuk(yerlesik) == temizlik.tr_kucuk(aynen) else aynen


def _cevrilir(cevap, yazim):
    """Latin harfli yazım çevrilecek bir söz mü: sınıflandırma kaynaktan farklı bir biçim (halfling -> buçukluk) ya da
    sıradan anlam ("ring") verdi. Özel ad gibi kalan (Vardy, Dúnedain, mithril) çevrilmez."""
    bicim = next((str(cevap.get(a)).strip() for a in ("tr", "e") if str(cevap.get(a) or "").strip()), "")
    siradan = isinstance(cevap.get("w"), str) and cevap["w"].strip()
    return bool(siradan) or (bool(bicim) and temizlik.tr_kucuk(bicim) != temizlik.tr_kucuk(yazim))


def anlam_notu(cevap, tur, latin_zorunlu=True):
    """Modelin "m" ve "w" alanları: (girdinin bu yapımda neyi kastettiği, yazımın sıradan sözcük olarak anlamı).
    Tek satır, kısa; Latin dışı yazı içeren (çıktı Latin harfliyse) ya da türü gerektirmeyen not boştur.
    Adın notu yalnızca sıradan anlamı da varsa tutulur (kişi notları ölçüldü, çeviriye katkısı çıkmadı)."""
    def temiz(alan, en_cok):
        m = " ".join(cevap.get(alan).split()).strip(" .\"'“”‘’") if isinstance(cevap.get(alan), str) else ""
        if not 2 <= len(m) <= en_cok or (latin_zorunlu and temizlik.latin_disi(m)):
            return ""
        return m.replace("[", "(").replace("]", ")").replace('"', "'")
    if tur in ("fictional", "common"):
        return "", ""
    siradan = temiz("w", 30)
    return (temiz("m", 110) if siradan or tur != "name" else ""), siradan


def unvan_kalibi(cevap, hedef, latin_zorunlu=True, turkce=True):
    """Unvanın adla birlikte yazılışı: modelin "p" alanı ("Muhafız NAME"), yoksa unvan adın önünde.
    "p" sınıflandırmadan, unvanın karşılığı (hedef) karşılık isteğinden gelir. Sözcükleri ayrışırsa kalıptan yalnız
    adın yeri alınır, sözcük hedeftir: yoksa unvan tek başına "Gardiyan", adla "Muhafız Hale" yazılır ve o satır
    unvan denetimiyle terim denetiminden aynı anda geçemez."""
    p = cevap.get("p").strip() if isinstance(cevap.get("p"), str) else ""
    if p.count("NAME") == 1 and len(p) <= 40 and p != "NAME" and not (latin_zorunlu and temizlik.latin_disi(p)):
        once, sonra = (x.strip() for x in p.split("NAME"))
        if hedef and not (once and sonra) and _oy_anahtari(once or sonra) != _oy_anahtari(hedef):
            buyuk = ("İ" if turkce and hedef[0] == "i" else hedef[0].upper()) + hedef[1:]
            p = f"{buyuk} NAME" if once else f"NAME {buyuk}"
        return p.replace("NAME", "{ad}")
    return f"{hedef} {{ad}}" if hedef else ""


def _mekanik_mi(yazim, latin):
    """Latin yazım, kaynaktaki kananın harf harf okunuşu mu (ステラ -> "Sutera", ロイド・フォージャー -> "Roido Foja")?"""
    kana = re.sub(r"[^ぁ-んァ-ヺー]", "", yazim.split("(")[-1] if "(" in yazim else yazim)
    okunus = temizlik.kana_latin(kana) if kana else None
    yalin = lambda m: re.sub(r"ou|oo|uu|aa|ii|ee", lambda x: x.group(0)[0], re.sub(r"[^a-z]", "", m.lower()))
    return bool(okunus) and yalin(okunus) == yalin(latin)


def _degerler(liste, alan, yazim=""):
    """Oylarda bir alana verilen değerler. Latin dışı kaynağın yazımını aynen geri veren karşılık (超能力者 -> "超能力者")
    karşılık değildir, oya girmez: girseydi üç ayrı oyda ilk gelen o olabiliyor, terim karşılıksız kalıyordu."""
    degerler = [c[alan].strip() for c in liste if isinstance(c.get(alan), str) and c[alan].strip()]
    kaynak_harfleri = set(temizlik.latin_disi(yazim) or "")
    if kaynak_harfleri and alan in ("e", "tr"):
        degerler = [d for d in degerler if not (temizlik.latin_disi(d) and set(temizlik.latin_disi(d)) <= kaynak_harfleri)]
    return degerler


KARSILIKLI = ("descriptive", "epithet", "title", "people", "term")
EK_OY = 2       # karşılık isteğinin cevapları birleşmediyse aynı soru kaç kez daha sorulur


def _karara_bagla(gruplar, partiler):
    """Her partinin oylarından karar: tür ve her alan için çoğunluğun dediği."""
    return [_cogunluk(grup, {n: a["yazim"] for n, a in enumerate(parti, 1)}) for grup, parti in zip(gruplar, partiler)]


def karsilik_istemi(ayar):
    dil = dil_adi(ayar)
    return dile_gore(f"""You render terms of one film or series into {dil}. "title_hint" names the work. Each item is a term from its subtitles, with what it refers to in this work ("m") and lines where it occurs. Give for each the one {dil} word or short phrase that names exactly what it is IN THIS WORK, as a careful professional {dil} translator of this work would write it every time; if the work has a published {dil} translation, use the form it uses. Be specific: when the work shows what the thing actually is or does, name that, not a vaguer general word (in a story where "the gifted" see the future, each of them is a seer[[tr|: "kâhin", not "yetenekli"||, not just someone gifted]]). Render the item's own words: a nickname, epithet or title stays one, never the name of whoever it refers to. When one item is built on another (a power and the people who have it, a group and its members, a craft and those who practise it), build its rendering on the other item's rendering, so that the two read as one family[[tr| ("büyü" / "büyücü", never two unrelated words)||]]. Never use a word that names a different kind of person or thing in this work (one who talks to spirits is not one who moves objects with the mind). Use what you know about the work as well as the lines, and follow an item's "hint". Write it in {dil}: {"" if hedef_kodu(ayar) == "en" else "never in English, "}never as a romanization of the original. Give the base form (singular, without case or possessive endings): the translation inflects it as each sentence needs. Lower case unless it is part of a name.
Return ONLY this JSON object: {{"items": [{{"i": 1, "tr": "..."}}]}}""", hedef_kodu(ayar))


def _karsilik_sorulacak(c, yazim):
    """Türkçesini karşılık isteğinin vereceği karar: karşılık alan bütün türler (halk adı, unvan, lakap, tanım, kavram).
    Bu türlerin Türkçesi yalnızca bu istekten gelir; sınıflandırmanın "yerleşik biçim" ya da karşılık alanı hedef
    olmaz (Spy'daki "esper" oradan geçiyordu). İki istisna: okunuşu katakanayla verilmiş yazımın karşılığı
    okunuşudur (星(ステラ) -> Stella); Latin harfli yazım ancak sınıflandırma çevrilir dediyse sorulur (halfling,
    kingsfoil), özel ad gibi kalan (Dúnedain, Vardy, mithril) yazımıyla kalır."""
    if c.get("t") not in KARSILIKLI:
        return False
    if OKUNUS_KATAKANA.search(yazim) and c.get("l") and c.get("t") not in ("epithet", "title"):
        return False
    return bool(temizlik.latin_disi(yazim)) or _cevrilir(c, yazim)


KARSILIK_PARTI = 24


def _karsilik_partileri(ogeler):
    """Karşılık öğelerini en çok KARSILIK_PARTI'lik partilere böler. Birbirine dayanan öğeler (biri ötekinin yazımını
    içeriyor ya da ortak bir sözcükleri var: "Ring" / "Ring of Power", 超能力 / 超能力者) aynı partide kalır: karşılıkları
    aynı aileden çıksın diye birlikte sorulmaları gerekir. Sıra korunur; hepsi tek partiye sığıyorsa istek eskisiyle
    aynıdır."""
    if len(ogeler) <= KARSILIK_PARTI:
        return [list(ogeler)]

    def sozcukler(yazim):
        return {x for x in re.findall(r"[^\W\d_]{4,}", yazim.lower())} if yazim.isascii() else set()
    kok = list(range(len(ogeler)))

    def bul(x):
        while kok[x] != x:
            kok[x] = kok[kok[x]]
            x = kok[x]
        return x
    anahtar = [(o["s"], sozcukler(o["s"])) for o in ogeler]
    for a in range(len(ogeler)):
        for b in range(a + 1, len(ogeler)):
            (ya, sa), (yb, sb) = anahtar[a], anahtar[b]
            if (sa & sb) or (len(ya) >= 2 and ya in yb) or (len(yb) >= 2 and yb in ya):
                kok[bul(b)] = bul(a)
    aileler = {}
    for n in range(len(ogeler)):
        aileler.setdefault(bul(n), []).append(n)
    partiler, simdiki = [], []
    for aile in sorted(aileler.values(), key=lambda l: l[0]):
        if simdiki and len(simdiki) + len(aile) > KARSILIK_PARTI:
            partiler.append(simdiki)
            simdiki = []
        simdiki = simdiki + aile
    if simdiki:
        partiler.append(simdiki)
    return [[ogeler[n] for n in sorted(p)] for p in partiler]


def _karsiliklari_tamamla(kararlar, partiler, sor, oy, isci, baslik="", ornek_bul=None, dil="", latin_zorunlu=True,
                          okunus_gecerli=False):
    """Karşılık alan kararların Türkçesi eserin adı, anlam notu ve en çok 6 satırla sorulur. Cevaplardan en az ikisi
    birleşmediyse yalnızca o terimler için aynı soru EK_OY kez daha sorulur ve hepsinin oyuyla karar verilir: en çok
    oyu alan (en az 2), beraberlikte önce verilen. Yine birleşmeyenin önerisi None'dır: kayda geçmez, sonraki bölümde
    yeniden sorulur (ilk cevap alınmaz: beş cevap da farklıysa model kararsızdır, yanlış sözcük her satıra yayılırdı).
    (kaç aday soruldu, öneriler, ek oy alan) döndürür. Öneriler yazım isteği bitince uygulanır (karsiliklari_uygula):
    o istek "l" alanını değiştirebilir ve Latin dışı kaynakta "l" ile aynı karşılık İngilizcedir.
    latin_zorunlu: çıktı Latin harfliyse başka yazıdaki cevap oya girmez; hedef dilin yazısı Latin değilse (Rusça,
    Japonca...) karşılık zaten o yazıdadır. okunus_gecerli: hedef dil İngilizceyse "l" ile aynı cevap karşılığın
    kendisidir (調査兵団 -> "Survey Corps"), elenmez."""
    sorulacak = [(c, partiler[k][c["i"] - 1]) for k, liste in enumerate(kararlar) for c in liste
                 if isinstance(c.get("i"), int) and 1 <= c["i"] <= len(partiler[k])
                 and _karsilik_sorulacak(c, partiler[k][c["i"] - 1]["yazim"])]
    if not sorulacak:
        return 0, [], 0
    # Sınıflandırmanın Latin yazımı şimdi alınır: yazım isteği aynı anda "l" alanını değiştiriyor olabilir.
    latinler = [str(c.get("l") or "").strip().lower() for c, _ in sorulacak]
    ogeler = [{"i": n, "s": a["yazim"], **({"m": c["m"]} if c.get("m") else {}),
               **({"hint": EKLI_KARSILIK_IPUCU.replace("TARGET language", dil or "the target language")}
                  if a.get("ekli") else {}),
               "lines": (ornek_bul(a["yazim"]) if ornek_bul else []) or a["ornek"]}
              for n, (c, a) in enumerate(sorulacak, 1)]

    def sorul(ogeler_, kez):
        """Öğeler KARSILIK_PARTI'lik partilere bölünür (aynı aileden olanlar aynı partide), bütün partiler ve oylar
        aynı anda sorulur: çok adlı yapımda (141 aday) tek büyük istek yarım dakika sürüyor, çeviri onu bekliyordu.
        Her oyun yanıtı, öğelerin bu çağrıdaki sıra numaralarıyla ("i") tek listede döner."""
        partiler_ = _karsilik_partileri(ogeler_)

        def bir(parti):
            yanit = sor({"title_hint": baslik, "items": [dict(o, i=n) for n, o in enumerate(parti, 1)]}) or {}
            return [dict(x, i=parti[x["i"] - 1]["i"]) for x in yanit.get("items") or []
                    if isinstance(x, dict) and isinstance(x.get("i"), int) and 1 <= x["i"] <= len(parti)]
        with ThreadPoolExecutor(max_workers=max(1, isci)) as havuz:
            turlar = [[havuz.submit(bir, p) for p in partiler_] for _ in range(kez)]
            return [{"items": [x for g in tur for x in g.result()]} for tur in turlar]

    def gecerli(n, yanitlar, yazim, latin, hepsi=None):
        # Latin harfli kaynakta yazımın kendisi de geçerli yanıttır ("hobbit"): çevrilmeyeceğini söyler.
        latin_kaynak = not temizlik.latin_disi(yazim)
        tum = [d for d in _degerler([x for y in yanitlar for x in y.get("items") or []
                                     if isinstance(x, dict) and x.get("i") == n], "tr", yazim)
               if not (latin_zorunlu and temizlik.latin_disi(d))]
        if hepsi is not None:
            hepsi.extend(tum)
        return [d for d in tum if latin_kaynak or okunus_gecerli or d.lower() != latin]

    yanitlar = sorul(ogeler, max(1, oy))
    esik = min(2, len(yanitlar))
    hepsi = [[] for _ in sorulacak]          # Latin yazımla aynı olanlar da: hepsi öyleyse terim yazımıyla kalır
    oylar = [gecerli(n, yanitlar, a["yazim"], latin, hepsi[n - 1])
             for n, ((_, a), latin) in enumerate(zip(sorulacak, latinler), 1)]
    bolunen = [k for k, o in enumerate(oylar) if _kazanan(o, esik) is None] if len(yanitlar) > 1 else []
    if bolunen:
        ek = sorul([dict(ogeler[k], i=m) for m, k in enumerate(bolunen, 1)], EK_OY)
        for m, k in enumerate(bolunen, 1):
            oylar[k] += gecerli(m, ek, sorulacak[k][1]["yazim"], latinler[k], hepsi[k])
    secilen = _aile_uyumu([a["yazim"] for _, a in sorulacak], [a.get("sayi") or 0 for _, a in sorulacak],
                          oylar, [_kazanan(o, esik) for o in oylar])
    oneriler = []
    for k, ((c, a), o) in enumerate(zip(sorulacak, oylar)):
        # Cevabı gelmeyen de (tek oyda ek oy sorulmaz) belirsizdir: kayda "karşılıksız" diye geçip bir daha sorulmaz olmasın.
        # Bütün cevaplar (en az 2) Latin yazımın kendisiyse model kararsız değildir: bu sözün hedef dildeki karşılığı
        # yazımının aynısıdır (ミスリル -> mithril). Karar "karşılıksız" diye kayda geçer, her bölümde yeniden sorulmaz;
        # hedefini hedef_belirle'nin eski kuralı verir (uydurma katakana terim yazımıyla kalır). Kanji ya da hiragana
        # yazımda "l" İngilizce çeviridir (調査兵団 -> Survey Corps): o belirsiz kalır, sonraki bölümde yeniden sorulur.
        ayni = (latinler[k] if o == [] and len(hepsi[k]) >= 2 and latinler[k]
                and all(_oy_anahtari(d) == _oy_anahtari(latinler[k]) for d in hepsi[k])
                and not re.search(r"[一-龥々ぁ-ん]", a["yazim"]) else "")
        oneriler.append((c, secilen[k], okunus_gecerli or not temizlik.latin_disi(a["yazim"]), ayni))
    return len(sorulacak), oneriler, len(bolunen)


def _aile_uyumu(yazimlar, sayilar, oylar, kazananlar):
    """Ortak sözcüğü olan terimlerin karşılıkları aynı aileden çıksın: oylar terim terim sayıldığından "Mount Doom"
    bir oylamadan "Hüküm Dağı", "Mountain of Doom" ötekinden "Kıyamet Dağı" alabiliyordu. En sık geçen terimin kazananı
    kalır; akrabası, modelin ona verdiği cevaplar içinde o karşılıkla daha çok sözcük paylaşan varsa onu alır. Yeni
    cevap uydurulmaz, kararsız kalan (None) da karara bağlanmaz. Yalnız Latin harfli kaynakta."""
    def sozcukler(yazim):
        return set(re.findall(r"[^\W\d_]{4,}", yazim.lower())) if yazim.isascii() else set()

    def kokler(deger):
        return {temizlik.tr_kucuk(x)[:4] for x in re.findall(r"[^\W\d_]{3,}", deger)}
    kaynak = [sozcukler(y) for y in yazimlar]
    secilen, kesin = list(kazananlar), []
    for k in sorted(range(len(yazimlar)), key=lambda n: (-sayilar[n], n)):
        if secilen[k] is None:
            continue
        akraba = [j for j in kesin if kaynak[k] & kaynak[j]]
        kesin.append(k)
        if not akraba:
            continue

        def uyum(deger):
            return sum(len(kokler(deger) & kokler(secilen[j])) for j in akraba)
        say = Counter(_oy_anahtari(d) for d in oylar[k])
        en_iyi = max(oylar[k], key=lambda d: (uyum(d), say[_oy_anahtari(d)]), default=None)
        if en_iyi is not None and uyum(en_iyi) > uyum(secilen[k]):
            secilen[k] = en_iyi
    return secilen


def _kazanan(oylar, esik):
    """En çok oyu alan cevap (yazım farkları tek oy); en az esik oy yoksa None. Beraberlikte önce verilen kazanır."""
    say = Counter(_oy_anahtari(d) for d in oylar)
    if not say or say.most_common(1)[0][1] < esik:
        return None
    kazanan = say.most_common(1)[0][0]
    return Counter(d for d in oylar if _oy_anahtari(d) == kazanan).most_common(1)[0][0]


def _oy_anahtari(deger):
    """Aynı cevabın yazım farkları tek oy sayılır: büyük/küçük harf, şapka, boşluk ve tire ("kral otu" / "kralotu",
    "askerî öğrenci" / "askeri öğrenci"). Kazananın en sık yazılan biçimi alınır. Noktalı ve noktasız i de tek harf
    sayılır: Türkçe küçük harf kuralı başka hedef dilde "Inspector" ile "inspector"ı ayrı cevap yapardı."""
    return re.sub(r"[\s-]+", "", temizlik.tr_kucuk(deger).translate(SAPKASIZ).replace("ı", "i"))


def karsiliklari_uygula(oneriler):
    """Karşılık önerilerini kararlara "k" alanı olarak yazar; Latin dışı kaynakta yazım isteğinden sonra gelen "l" ile
    aynı olan (İngilizce) alınmaz. Ek oyla da birleşmeyen karar "belirsiz" işaretlenir: kayda geçmez."""
    for c, deger, latin_kaynak, ayni in oneriler:
        if deger is None:
            # ayni: bütün cevaplar Latin yazımın kendisiydi; yazım isteği "l"yi değiştirmediyse karar budur
            if not (ayni and str(c.get("l") or "").strip().lower() == ayni):
                c["belirsiz"] = True
        elif latin_kaynak or deger.lower() != str(c.get("l") or "").strip().lower():
            c["k"] = deger


def yazim_istemi(ayar):
    return """Each item is a name or term written in Japanese script in the subtitles of one film or series ("title_hint" names it), with lines where it occurs. Give its spelling in Latin letters ("l") as the official English release of this work writes it. A word taken from English or another European language is written with that word's own spelling, not letter by letter (シャーロット -> Charlotte, ジョージ -> George); a Japanese word or name is written in plain Hepburn without macrons, the way it sounds. Never give an English translation or localised name, even when the English release uses one (ホシ is "Hoshi", never "Star"): the viewers hear the original audio and must read the name they hear.
Return ONLY this JSON object: {"items": [{"i": 1, "l": "..."}]}"""


YAZIMLI = ("name", "codename", "term")


def _yazimlari_tamamla(kararlar, partiler, sor, oy, isci, baslik):
    """Kana içeren ad, kod adı ve terimde seçilen Latin yazım harf harf okunuşsa (ステラ -> "Sutera", 黄昏(たそがれ) ->
    "Tasogare") ya da hiç yoksa yazım ayrıca sorulur. Oyların hepsi okunuşu verdiyse oylamanın "resmî yazım üstündür"
    kuralı işlemiyordu; istemdeki örnek de bu dizinin cevabı olduğu için ölçümü kendisi düzeltiyordu."""
    eksik = []
    for k, liste in enumerate(kararlar):
        for c in liste:
            if not (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(partiler[k])):
                continue
            yazim = partiler[k][c["i"] - 1]["yazim"]
            latin = str(c.get("l") or "").strip()
            if (c.get("t") in YAZIMLI and not (c.get("e") and c.get("t") != "codename") and re.search(r"[ぁ-んァ-ヺ]", yazim)
                    and (not latin or _mekanik_mi(yazim, latin))):
                eksik.append((c, partiler[k][c["i"] - 1]))
    if not eksik:
        return 0
    yuk = {"title_hint": baslik,
           "items": [{"i": n, "s": a["yazim"], "lines": a["ornek"]} for n, (_, a) in enumerate(eksik, 1)]}
    with ThreadPoolExecutor(max_workers=max(1, isci)) as havuz:
        yanitlar = [g.result() or {} for g in [havuz.submit(sor, yuk) for _ in range(max(1, oy))]]
    for n, (c, a) in enumerate(eksik, 1):
        degerler = [str(x.get("l") or "").strip().translate(MAKRONSUZ) for y in yanitlar for x in y.get("items") or []
                    if isinstance(x, dict) and x.get("i") == n]
        resmi = Counter(d for d in degerler if d and not temizlik.latin_disi(d) and len(d) <= 60
                        and not _mekanik_mi(a["yazim"], d))
        if resmi and resmi.most_common(1)[0][1] >= min(2, len(yanitlar)):
            c["l"] = resmi.most_common(1)[0][0]
    return len(eksik)


_KANJILI = re.compile(r"[一-龥々]")
_KANA_OKUNUS = re.compile(r"[ぁ-ゖァ-ヺー・\s]+")


def _ceviriyse_tanima_cevir(secilen, yazim, oylar):
    """Kanjiyle yazılmış "ad" için model okunuşu da verir ("r"). Latin yazım o okunuşun sesi değilse (爪: okunuş
    "tsume", yazım "Claw") model adı çevirmiştir: ad duyulduğu gibi yazılmaz, anlamı olan bir addır. Karar "tanım"a
    döner ve karşılığını hedef dilde karşılık isteği verir ("Pençe"); İngilizce çevirisi sözlüğe girmez. Okunuşa
    uyan yazım (鈴木 -> Suzuki) ve okunuşu verilmemiş ad olduğu gibi kalır."""
    latin = secilen.get("l")
    if not latin or not _KANJILI.search(yazim) or "(" in yazim or "（" in yazim:
        return
    okunuslar = [c["r"].strip() for c in oylar if isinstance(c.get("r"), str) and _KANA_OKUNUS.fullmatch(c["r"].strip())]
    okunus = temizlik.kana_latin(re.sub(r"[・\s]", "", Counter(okunuslar).most_common(1)[0][0])) if okunuslar else ""
    if okunus and _iskelet_yakinligi(okunus, latin) < 0.5:
        secilen["t"] = "descriptive"
        secilen.pop("l", None)
        secilen.pop("e", None)
        secilen.setdefault("m", f'a name that means "{latin}"')


def _cogunluk(yanitlar, yazimlar=None):
    """Aynı adaylar için verilmiş birkaç yanıtı tek yanıta indirir: tür ve her alan için çoğunluğun dediği.
    Yerleşik biçim ("e") ancak yanıtların yarısından çoğu bir yerleşik biçim verdiyse alınır."""
    oylar = {}
    for yanit in yanitlar:
        for c in yanit.get("items") or []:
            if isinstance(c, dict) and isinstance(c.get("i"), int):
                oylar.setdefault(c["i"], []).append(c)
    sonuc = []
    for i, liste in oylar.items():
        tur = Counter(c.get("t") if isinstance(c.get("t"), str) else None for c in liste).most_common(1)[0][0]
        secilen = {"i": i, "t": tur}
        for alan in ("l", "e", "tr", "p", "w"):
            degerler = _degerler(liste, alan, (yazimlar or {}).get(i, ""))
            if alan == "l" and yazimlar and i in yazimlar and len(set(degerler)) > 1:
                # Oylar ayrılmışsa resmî yazım harf harf okunuşa üstün gelir ("Stella" / "Sutera")
                resmi = [d for d in degerler if not _mekanik_mi(yazimlar[i], d)]
                mekanik = Counter(d for d in degerler if d not in resmi)
                if resmi and Counter(resmi).most_common(1)[0][1] >= max(mekanik.values(), default=0):
                    degerler = resmi
            # yerleşik biçim ve sıradan anlam ancak yanıtların çoğu verdiyse alınır
            if degerler and (alan not in ("e", "w") or len(degerler) * 2 > len(yanitlar)):
                secilen[alan] = Counter(degerler).most_common(1)[0][0]
        if tur == "name" and yazimlar and i in yazimlar and re.fullmatch(r"[ァ-ヺー]{2,}", yazimlar[i]) and (
                not secilen.get("l") or _mekanik_mi(yazimlar[i], secilen["l"])):
            # İngilizce sözcükten gelen katakana ad (ファルコン: oylardan biri sıradan anlamı "falcon" diye verdi):
            # yazım o sözcüktür, harf harf okunuş ("Farukon") değil.
            okunus = temizlik.kana_latin(yazimlar[i]) or ""
            sozcukler = [c["w"].strip() for c in liste if isinstance(c.get("w"), str)
                         and re.fullmatch(r"[A-Za-z]{3,}", c["w"].strip())]
            uyan = [w for w in sozcukler if okunus and _iskelet_yakinligi(okunus, w) >= 0.7]
            if uyan:
                secilen["l"] = Counter(uyan).most_common(1)[0][0].capitalize()
        if tur == "name" and yazimlar and i in yazimlar:
            _ceviriyse_tanima_cevir(secilen, yazimlar[i], liste)
        if sum(1 for c in liste if c.get("real") is True) * 2 > len(yanitlar):
            secilen["real"] = True
        # Anlam notu serbest metindir, oylanamaz: yanıtların en az yarısı not verdiyse çoğunluğun türünü
        # söyleyenlerden ilki alınır.
        notlar = [c["m"].strip() for c in liste if c.get("t") == tur and isinstance(c.get("m"), str) and c["m"].strip()]
        if notlar and len(notlar) * 2 >= len(yanitlar):
            secilen["m"] = notlar[0]
        sonuc.append(secilen)
    return sonuc


def _iste(model, sistem, yuk, ayar, gunluk, dosya, tur, dusunme=None):
    """JSON döndüren isteği yeniden denemeli çalıştırır; olmazsa None."""
    dusunme = dusunme or ayar["sozluk"]["dusunme_seviyesi"] or None
    for deneme in range(1, ayar["kontrol"]["deneme_sayisi"] + 1):
        try:
            ham = model.iste(sistem, json.dumps(yuk, ensure_ascii=False), dusunme)
            gunluk.ham(dosya, {"tur": tur, "deneme": deneme, "giden": yuk, "gelen": ham})
            veri = json_coz(ham)
            if isinstance(veri, dict) and isinstance(veri.get("items") or [], list):
                return veri
            gunluk.hata(dosya, f"{tur} yanıtı beklenen biçimde değil (JSON nesnesi, \"items\" listesi), deneme {deneme}")
        except Ret as e:
            gunluk.hata(dosya, f"{tur} reddedildi ({e}), deneme {deneme}")
        except ValueError as e:
            gunluk.hata(dosya, f"{tur} yanıtı bozuk ({e}), deneme {deneme}")
    return None


def takma_ad_mi(hedef, yazim, tum_metin):
    """Karşılık aynı altyazıda başka bir ad olarak geçen bir sözcük içeriyor mu? ("Khazad-dûm" -> "Moria",
    "Imladris" -> "Rivendell", "Pip" -> "Pippin Took"): kaynak her o adı söylediğinde çeviriye öteki ad yazılırdı.
    Altyazıda geçmeyen yerleşik karşılık (Rivendell -> Ayrıkvadi, Athens -> Atina) takma ad değildir. Başka yazıdaki
    ad (モブ -> Mob) metinde Latin harfle geçse de aynı addır."""
    if not hedef or hedef == yazim or temizlik.latin_disi(yazim):
        return False
    kaynakta = {s.lower() for s in re.findall(r"\w+", yazim)}
    return any(s.lower() not in kaynakta and re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", tum_metin)
               for s in re.findall(r"\w{3,}", hedef))


def baglam_sayisi_sil(hedef, terim, ornekler):
    """Karşılığa terimin kendisinde olmayan, geçtiği cümleden gelen sayı girmişse silinir ("Cadet Corps Class" ->
    "Harbiyeli Birliği Sınıfı", "104. Harbiyeli…" değil): sözlük her geçtiği yerde aynı biçimi dayatır, sayı ise
    cümleden cümleye değişir. Terimin yazımında da olan sayı ("District 9") ya da cümlede rakamla geçmeyen sayı
    ("Seventh Squad" -> "7. Takım") kalır."""
    baglam = set(re.findall(r"\d+", " ".join(ornekler))) - set(re.findall(r"\d+", terim))
    yabanci = [s for s in re.findall(r"\d+", hedef) if s in baglam]
    for s in yabanci:
        hedef = re.sub(rf"(?<!\w){s}(?:\.|'?\w{{0,4}})?(?!\w)", "", hedef)
    return " ".join(hedef.split()).strip(" ,.-") if yabanci else hedef


DIL_ISTEMI = 'Name the language of these subtitle lines. Return ONLY this JSON object: {"language": "ISO 639-1 code"}'


def dil_sor(satirlar, ayar, model, gunluk, dosya):
    """Kaynak dil, keşif vermediyse (kapalı ya da yanıtsız): dosyaya yayılmış en çok 20 satırla küçük bir istek."""
    metinler = [s.metin for s in satirlar if s.metin.strip()]
    adim = max(1, len(metinler) // 20)
    yanit = _iste(model, DIL_ISTEMI, {"lines": metinler[adim // 2::adim][:20]}, ayar, gunluk, dosya, "dil") or {}
    return yanit["language"].strip().lower()[:8] if isinstance(yanit.get("language"), str) else ""


def _adla_gecen_unvanlar(sozluk, ciftler, bilinen_unvan, tum_metin):
    """Bu dosyalarda bir adın yanında geçen unvan sözcükleri. Karşılıkları burada verilmez: çeviride
    Sozluk.gorunum tablodan ya da modelin kararından alır."""
    bulunan = set()
    # Model "Warden Hale" dizisinin tamamına unvan dediyse ikiye ayrılır: "Warden" unvan, "Hale" ad. Yoksa dizi
    # sözlüğe "Warden Hale -> Muhafız" diye girer ve çeviride ad düşer.
    for yazim, g in list(sozluk.girdiler.items()):
        sozcukler = yazim.split()
        if (g.get("tur") == "title" and len(sozcukler) == 2 and all(x[:1].isupper() for x in sozcukler)
                and not temizlik.latin_disi(yazim) and (g.get("hedef") or g.get("kalip"))
                and sozcukler[1] not in bilinen_unvan
                and (sozluk.girdiler.get(sozcukler[1]) or {}).get("tur") in ("name", None)):
            unvan, ad = sozcukler
            kalip = g.get("kalip") or ""
            hedef = kalip.replace("{ad}", "").strip() or g.get("hedef")
            if not kalip and hedef:                     # "Uncle Bilbo" -> "Bilbo Amca": unvanın karşılığı adsız "Amca"
                hedef = re.sub(r"(?<!\w)" + re.escape(ad) + r"(?!\w)", "", hedef).strip() or hedef
            sozluk.terimden_cikar(yazim)
            if not (sozluk.girdiler.get(unvan) or {"hedef": "x"}).get("hedef"):
                sozluk.girdi_sil(unvan)                 # tek başına sorulduğunda "sıradan sözcük" denmişti
            sozluk.girdi_ekle(unvan, "title", hedef, (), g.get("karar", "model"), g.get("sayi", 0), g.get("ornek", ""),
                              kalip, terim=unvan not in bilinen_unvan)
            if _anahtar(ad) not in sozluk.karar_verilenler():
                sozluk.girdi_ekle(ad, "name", ad, (), "unvan", g.get("sayi", 0), "")

    # Japonca "ad + unvan" (神室会長) tek terim olarak girmişse çeviride ad düşer ("başkan"). Baştaki parça bilinen bir
    # adsa (sözlükte ad olarak var ya da konuşmacı etiketinde okunuşuyla yazılmış) girdi terimlerden çıkarılır; unvan
    # kendi girdisini alır ve karşılığını adla birlikte unvan kaydı yazar.
    jp_bilinen = {y for k, g in sozluk.girdiler.items() if g.get("tur") in ("name", "codename", "epithet") and g.get("hedef")
                  for y in [k] + list(g.get("yazimlar") or [])
                  if temizlik.latin_disi(y) and "(" not in y and not okunus_yazimi(k, y)}
    for etiket in re.findall(r"（([^（）]{1,30})）", tum_metin):
        jp_bilinen.update(re.findall(r"([一-龥々]{1,4})[(（][ぁ-ん]{2,}[)）]", etiket))
    for yazim, g in list(sozluk.girdiler.items()):
        ad = next((a for a in sorted(jp_bilinen, key=len, reverse=True)
                   if yazim.startswith(a) and 1 <= len(yazim) - len(a) <= 3), None)
        if g.get("tur") != "title" or not g.get("hedef") or ad is None or not temizlik.latin_disi(yazim):
            continue
        unvan = yazim[len(ad):]
        kalip = g.get("kalip") or g["hedef"][:1].upper() + g["hedef"][1:] + " {ad}"
        hedef = kalip.replace("{ad}", "").strip()
        sozluk.terimden_cikar(yazim)
        if unvan not in sozluk.girdiler:
            sozluk.girdi_ekle(unvan, "title", hedef, (), g.get("karar", "model"), g.get("sayi", 0), g.get("ornek", ""),
                              kalip, terim=False)
        bulunan.add(unvan)

    def tur(ad):
        return next((g.get("tur") for k, g in sozluk.girdiler.items() if k.lower() == ad.lower()), None)
    for (unvan, ad), _ in ciftler.items():              # bilinen unvan + ad: script aday ararken ayırdı
        if tur(ad) not in ("common", "people", "title"):            # "Master Dwarf": ad değil
            bulunan.add(unvan)
    jp_adlar = [y for k, g in sozluk.girdiler.items() if g.get("tur") in ("name", "codename", "epithet")
                for y in [k] + list(g.get("yazimlar") or [])
                if temizlik.latin_disi(y) and "(" not in y and not okunus_yazimi(k, y)]
    jp_ad = "(?:" + "|".join([KATAKANA + "{2,}"] + sorted(map(re.escape, jp_adlar), key=len, reverse=True)) + ")"
    model_unvanlari = {k for k, g in sozluk.girdiler.items() if g.get("tur") == "title" and g.get("kalip")}
    for unvan in sorted((model_unvanlari | bilinen_unvan) - bulunan):
        if ekli_ad_mi(unvan):                           # さん, 先生...: unvan değil hitap eki, kendi ayarı var
            continue
        if temizlik.latin_disi(unvan):                  # Japonca: unvan adın arkasında, bitişik (リヴァイ隊長)
            if re.search(jp_ad + re.escape(unvan), tum_metin):
                bulunan.add(unvan)
        elif unvan not in bilinen_unvan and re.search(r"(?<![\w'’-])" + re.escape(unvan) + r"\.? [A-ZÀ-Þ]", tum_metin):
            dizili = [k for k in sozluk.girdiler if k.startswith(unvan + " ") and k[len(unvan) + 1:][:1].isupper()]
            ardindan = {re.sub(r"['’]s?$", "", x) for x in re.findall(
                r"(?<![\w'’-])" + re.escape(unvan) + r"\.? ([A-ZÀ-Þ][\w'’-]*)", tum_metin)}
            # Model bir sıfata unvan demiş olabilir ("Dark Lord", "Dark Days"): ardından gelenlerin hiçbiri ad değilse
            # unvan sayılmaz. Yoksa "Dark Days" bölünür, "Days" ad olur ve "Dark Lord" karşılığını yitirir.
            adli = [y for y in dizili if sozluk.girdiler[y].get("tur") in ("name", "title")]
            if not adli and not any(tur(x) == "name" for x in ardindan):
                continue
            bulunan.add(unvan)
            # "Warden Hale" tek ad sanılıp sözlüğe girdiyse çıkarılır; ad kısmı kendi girdisini alır.
            for yazim in adli:
                sozluk.terimden_cikar(yazim)
                ad = yazim[len(unvan) + 1:]
                if _anahtar(ad) not in sozluk.karar_verilenler():
                    sozluk.girdi_ekle(ad, "name", ad, (), "unvan", sozluk.girdiler[yazim].get("sayi", 0), "")
    return bulunan


def _kesifleri_birlestir(yanitlar):
    """Aynı dosyanın keşif yanıtları: dil ve eser ilk dolu olandan, yazımlar birleşim (ilk gelen kalır)."""
    sonuc, gorulen = {"items": []}, set()
    for y in yanitlar:
        if not isinstance(y, dict):
            continue
        for alan in ("language", "work"):
            if alan not in sonuc and isinstance(y.get(alan), str) and y[alan].strip():
                sonuc[alan] = y[alan]
        for c in y.get("items") or []:
            yazim = _kesif_yazimi(c)
            if yazim and yazim not in gorulen:
                gorulen.add(yazim)
                sonuc["items"].append(yazim)
    return sonuc


def _kesif_yazimi(c):
    """Keşif öğesi: düz yazım; eski biçim ({"s": ...}) de okunur."""
    c = c.get("s") if isinstance(c, dict) else c
    return c.strip() if isinstance(c, str) else ""


def ortusenleri_esitle(sozluk):
    """"Dark Lord" ve "Dark Lord Sauron" ayrı ayrı sorulur; ikincisi birincinin karşılığı + ad olarak yazılır ki aynı
    lakap iki biçimde çıkmasın ("Karanlıklar Efendisi" / "Karanlık Lord Sauron")."""
    for uzun in [k for k in sozluk.terimler if " " in k and k.isascii()]:
        for kisa, hedef in list(sozluk.terimler.items()):
            kalan = uzun[len(kisa) + 1:] if uzun.startswith(kisa + " ") else ""
            if not kalan or hedef == kisa or " " in kalan or kalan not in sozluk.degismez:
                continue
            yeni = f"{hedef} {kalan}"
            if sozluk.terimler[uzun] != yeni and uzun in sozluk.girdiler:
                sozluk.girdi_yaz(uzun, hedef=yeni)
            break


ETIKET_OKUNUSU = re.compile(r"([一-龥々]{1,4})[(（]([ぁ-ゖー]{2,})[)）]")


def okunuslu_adlari_isle(sozluk, metin):
    """Konuşmacı etiketinde okunuşuyla yazılmış ad (（峯岸(みねぎし)）) nasıl okunacağını kaynağın kendisi söyler:
    Latin yazımı o okunuştur (Minegishi). Model okunuşu verilmiş adı yanlış yazabiliyordu ("Minagishi", "Kumikawa");
    ad sözlükte yoksa diyalogda geçtiği satırlarda (峯岸！) hiç yol gösterilmiyordu. Sözlükte okunuşa uymayan tek
    sözcüklü yazım düzeltilir, olmayan ad eklenir; etiket -> ad kaydı da aynı yazımı alır. Düzeltilenleri döndürür."""
    ciftler = {}
    for etiket in re.findall(r"（([^（）]{1,30})）", metin):
        for kanji, okunus in ETIKET_OKUNUSU.findall(etiket):
            ciftler.setdefault(kanji, okunus)
    duzelen = []
    for kanji, okunus in ciftler.items():
        latin, tam = temizlik.kana_latin(okunus), f"{kanji}({okunus})"
        if not latin:
            continue
        anahtar = next((k for k in (tam, kanji) if (sozluk.girdiler.get(k) or {}).get("hedef")), None)
        mevcut = sozluk.girdiler[anahtar]["hedef"] if anahtar else ""
        if anahtar and sozluk.girdiler[anahtar].get("tur") not in ("name", "codename"):
            continue                              # unvan, terim: ad değil, karşılığı okunuşla ölçülmez
        if not mevcut:
            # Dört kanjilik etiket çoğunlukla soyadı + addır (okunuşu tek parça yazılır): script bölemez, modele kalır
            if len(kanji) <= 3:
                sozluk.girdi_yaz(kanji, tur="name", hedef=latin, karar="okunuş", sayi=metin.count(kanji))
        elif " " not in mevcut.strip() and not _mekanik_mi(tam, mevcut):
            sozluk.girdi_yaz(anahtar, hedef=latin, karar="okunuş")
            duzelen.append(f"{kanji}: {mevcut} -> {latin}")
        for etiket, ad in list(sozluk.adlar.items()):
            if etiket.strip() in (tam, kanji) and " " not in ad.strip() and not _mekanik_mi(tam, ad):
                sozluk.adlar[etiket] = latin
                duzelen.append(f"{kanji}: {ad} -> {latin}")
    return list(dict.fromkeys(duzelen))


def unvanlari_bul(dosyalar, ayar, sozluk):
    """Terim adımı kapalıyken ([sozluk] terimler = false) unvan tablosu yine çalışsın diye: adın yanında geçen
    bilinen unvanları yalnızca script bulur, modele istek gitmez."""
    bilinen, ciftler = unvan_sozcukleri(ayar), Counter()
    adaylari_bul(dosyalar, set(), unvanlar=bilinen, ciftler=ciftler)
    tum_metin = "\n".join(x.metin for _, satirlar, _ in dosyalar for x in satirlar)
    sozluk.unvanlar = sorted(set(sozluk.unvanlar) | _adla_gecen_unvanlar(sozluk, ciftler, bilinen, tum_metin))


def terimleri_cikar(dosyalar, ayar, model, sozluk, gunluk):
    """Bir yapımın terim sözlüğünü kurar ve sözlüğe işler. dosya -> kaynak dil kodu sözlüğü döndürür."""
    s = ayar["sozluk"]
    latin_zorunlu = ayar["ceviri"]["cikti_alfabesi"] == "latin"
    kod = hedef_kodu(ayar)
    ilk = dosyalar[0][0]
    baslik = "; ".join(sorted({b or d for d, _, b in dosyalar}))[:300]
    bilinen_unvan, ciftler = unvan_sozcukleri(ayar), Counter()
    unvan_sor = acik(ayar, "unvan", "model_karari")      # tabloda olmayan unvanın karşılığını model versin mi
    adaylar = adaylari_bul(dosyalar, sozluk.karar_verilenler(), s["aday_esigi"], unvanlar=bilinen_unvan,
                           ciftler=ciftler, unvan_sor=unvan_sor, tablo=unvan_tablosu(ayar))
    # Tek başına duran kana sözcükleri: kaynağın kanıtına göre ad olamayacak olan ad adayı olmaz; eski bir çalıştırma
    # onu ad diye sözlüğe yazdıysa o karar geri alınır. Sesler Latin harfle yazılacaksa ([modlar] ses_sozcukleri =
    # "orijinal") sözcüğün ses mi sıradan sözcük mü olduğu modele sorulur; sorulan bir daha sorulmaz.
    sesler = ses_adaylari(dosyalar)
    adaylar = [a for a in adaylar if not (sesler.get(a["yazim"]) or {}).get("ad_degil")]
    for soz, k in sesler.items():
        girdi = sozluk.girdiler.get(soz)
        if k["ad_degil"] and girdi and girdi.get("tur") not in ("common", "sound") and girdi.get("karar") in ("model", "keşif"):
            sozluk.terimden_cikar(soz)
            sozluk.girdi_yaz(soz, tur="common")
    ses_sorulacak = [soz for soz, k in sesler.items()
                     if (k["ad_degil"] or k["yinelenen"] or k["sayi"] >= 2)
                     and (sozluk.girdiler.get(soz) or {"tur": "common"}).get("tur") == "common"
                     and "ses" not in (sozluk.girdiler.get(soz) or {})] if ayar["modlar"]["ses_sozcukleri"] == "orijinal" else []
    partiler = [adaylar[i:i + PARTI] for i in range(0, len(adaylar), PARTI)]

    def ses_sor():
        yuk = {"title_hint": baslik, "words": [{"i": n, "s": soz, "line": sesler[soz]["ornek"], "by": sesler[soz]["konusan"]}
                                               for n, soz in enumerate(ses_sorulacak, 1)]}
        return _iste(model, SES_ISTEMI, yuk, ayar, gunluk, ilk, "ses") or {}

    def siniflandir(parti):
        yuk = {"title_hint": baslik, "candidates": [
            {"i": n, "s": a["yazim"], "n": a["sayi"], "lines": a["ornek"],
             **({"hint": a["ipucu"]} if a["ipucu"] else {})}
            for n, a in enumerate(parti, 1)]}
        return _iste(model, siniflandirma_istemi(ayar), yuk, ayar, gunluk, ilk, "terim") or {}

    def kesfet(dosya):
        ad, satirlar, dosya_basligi = dosya
        yuk = {"title_hint": dosya_basligi or ad, "found": bulunan[:400],
               "lines": [x.metin.replace("\n", " / ") for x in satirlar if x.metin.strip() and not x.ekran]}
        return _iste(model, kesif_istemi(ayar), yuk, ayar, gunluk, ad, "keşif") or {}

    # Keşfe "zaten bulundu" diye giden yazımlar: bu çalıştırmanın adayları ve önceki bölümlerden sözlükte olanlar.
    # Yalnızca adaylar gitseydi ikinci bölümde liste boş kalır, keşif bilinen adlarla dolardı.
    bulunan = list(dict.fromkeys([a["yazim"] for a in adaylar] + sorted(sozluk.terimler) + list(sozluk.degismez)))
    oy = max(1, int(s["oy"]))            # her sınıflandırma isteği kaç kez sorulsun (çoğunluğun dediği olur)
    with ThreadPoolExecutor(max_workers=max(1, ayar["model"]["es_zamanli_istek"])) as havuz:
        # Keşif de oylanır, ama çoğunlukla değil birleşimle: küçük harfli dünya sözcüklerini (wraith, ranger) model
        # her seferinde bulmuyor; bir kez bulunması yeter, gerçekten iki kez geçtiğini script zaten doğruluyor.
        kesif_gelecek = [[havuz.submit(kesfet, d) for _ in range(oy)] for d in dosyalar] if s["kesif"] else []
        oylar = [[havuz.submit(siniflandir, p) for _ in range(oy)] for p in partiler]
        ses_oylari = [havuz.submit(ses_sor) for _ in range(oy)] if ses_sorulacak else []
        oylar = [[g.result() for g in grup] for grup in oylar]
        ses_oylari = [g.result() for g in ses_oylari]
        kesifler = [_kesifleri_birlestir([g.result() for g in grup]) for grup in kesif_gelecek]

    eserler = [str(y.get("work") or "").strip() for y in [o for grup in oylar for o in grup] + kesifler]
    taninan = sum(1 for e in eserler if e) * 2 > len(eserler)       # isteklerin çoğu eseri tanıdıysa
    isci = ayar["model"]["es_zamanli_istek"]

    def karsilik_sor(yuk):
        return _iste(model, karsilik_istemi(ayar), yuk, ayar, gunluk, ilk, "karşılık", ayar["sozluk"]["karsilik_dusunme"]) or {}

    def yazim_sor(yuk):
        return _iste(model, yazim_istemi(ayar), yuk, ayar, gunluk, ilk, "yazım", ayar["sozluk"]["karsilik_dusunme"]) or {}
    tum_satirlar = [x.metin.replace("\n", " / ") for _, st, _ in dosyalar for x in st if x.metin.strip() and not x.ekran]

    def ornek_bul(yazim, en_cok=6):
        """Terimin geçtiği satırlardan dosya boyunca yayılmış en çok 6 tanesi (okunuşlu yazımda sözcük de aranır)."""
        bicimler = {yazim, re.sub(r"[(（].*?[)）]", "", yazim).strip()} - {""}
        bulunan = [x[:110] for x in tum_satirlar if any(b in x for b in bicimler)]
        adim = max(1, len(bulunan) // en_cok)
        return bulunan[::adim][:en_cok]

    def kararlari_kur(oylar_, partiler_):
        """Sınıflandırma oylarından karar; ardından karşılık ve yazım istekleri aynı anda gider (biri "k", öteki "l"
        alanını tamamlar). (kararlar, karşılığı sorulan, yazımı sorulan)."""
        kararlar_ = _karara_bagla(oylar_, partiler_)
        with ThreadPoolExecutor(max_workers=2) as h:
            k = h.submit(_karsiliklari_tamamla, kararlar_, partiler_, karsilik_sor, oy, isci, baslik, ornek_bul,
                         dil_adi(ayar), latin_zorunlu, kod == "en")
            y = h.submit(_yazimlari_tamamla, kararlar_, partiler_, yazim_sor, oy, isci, baslik)
            (sorulan_karsilik, oneriler, ek_oylu), sorulan_yazim = k.result(), y.result()
        karsiliklari_uygula(oneriler)              # yazım isteği "l"yi değiştirmiş olabilir: ondan sonra
        return kararlar_, sorulan_karsilik, sorulan_yazim, ek_oylu

    # Keşfin bulduğu, kaynakta gerçekten en az iki kez geçen ve henüz karara bağlanmamış yazımlar. Bu aşamadaki
    # adaylar ve öteki yazımları zaten soruluyor: keşif zinciri ana zincirle aynı anda yürüyebilsin diye onlar
    # burada elenir (keşif yalnızca yazımı bulur; ne olduğuna öteki adaylar gibi oylanan sınıflandırma karar verir).
    tum_metin = "\n".join(x.metin for _, satirlar, _ in dosyalar for x in satirlar)
    diller, yeni = {}, {}
    sorulan = {_anahtar(y) for a in adaylar for y in [a["yazim"], *a["bicimler"]]}
    karar_verilen = sozluk.karar_verilenler()
    for (ad, _, _), yanit in zip(dosyalar, kesifler):
        if isinstance(yanit.get("language"), str):
            diller[ad] = yanit["language"].strip().lower()[:8]
        for c in yanit.get("items") or []:
            yazim = _kesif_yazimi(c)
            sayi = (len(re.findall(_sinir(yazim), tum_metin)) if yazim.isascii() else tum_metin.count(yazim)) if yazim else 0
            if (sayi < 2 or len(yazim) > 40 or yazim in yeni or _anahtar(yazim) in karar_verilen
                    or _anahtar(yazim) in sorulan or ekli_ad_mi(yazim)):
                continue
            ornek = [x.metin.replace("\n", " / ")[:110] for _, st, _ in dosyalar for x in st if yazim in x.metin][:2]
            yeni[yazim] = {"yazim": yazim, "bicimler": set(), "sayi": sayi, "ornek": ornek, "ipucu": ""}
    kesif_partileri = [list(yeni.values())[i:i + PARTI] for i in range(0, len(yeni), PARTI)]

    def kesif_zinciri():
        if not kesif_partileri:
            return [], 0, 0, 0
        with ThreadPoolExecutor(max_workers=max(1, isci)) as havuz:
            gelecek = [[havuz.submit(siniflandir, p) for _ in range(oy)] for p in kesif_partileri]
            oylar_ = [[g.result() for g in grup] for grup in gelecek]
        return kararlari_kur(oylar_, kesif_partileri)

    with ThreadPoolExecutor(max_workers=2) as zincir:         # iki zincir aynı anda: süreyi uzun olanı belirler
        ana, kes = zincir.submit(kararlari_kur, oylar, partiler), zincir.submit(kesif_zinciri)
        kararlar, karsiliksiz, yazimsiz, ek_oylu = ana.result()
        kesif_kararlari, karsiliksiz_k, yazimsiz_k, ek_oylu_k = kes.result()
    karsiliksiz, yazimsiz, ek_oylu = karsiliksiz + karsiliksiz_k, yazimsiz + yazimsiz_k, ek_oylu + ek_oylu_k
    yanitlar = [{"items": k} for k in kararlar]
    eklenen = Counter()

    def isle(yazim, bicimler, sayi, ornekler, cevap, karar):
        if cevap.get("belirsiz"):           # ek oyla da birleşmedi: kayda geçmez, sonraki bölümde yeniden sorulur
            gunluk.uyari(ilk, f"terim: {yazim!r} için karşılık belirlenemedi (cevaplar birleşmedi); sözlüğe girmedi, "
                              "sonraki bölümde yeniden sorulur")
            return
        tur, hedef = hedef_belirle(yazim, cevap, taninan, latin_zorunlu, kod)
        if tur in ("name", "codename", "epithet") and takma_ad_mi(hedef, yazim, tum_metin):
            # "yerleşik biçim" ya da karşılık diye verilen, aynı altyazıda geçen başka bir ad: takma addır
            tur, hedef = hedef_belirle(yazim, {k: v for k, v in cevap.items() if k not in ("e", "k")}, taninan,
                                       latin_zorunlu, kod)
        if re.search(r"\d", hedef):        # sayı, karşılık isteğine giden satırların hepsinde aranır (ornekler: ilk ikisi)
            hedef = baglam_sayisi_sil(hedef, yazim + " " + str(cevap.get("l") or ""), ornekler + ornek_bul(yazim))
        ornek = ornekler[0] if ornekler else ""
        # Bilinen unvan sözcüğü ("Captain") başka yerde sıradan sözcüktür: karar saklanır ama terim olmaz.
        sozluk.girdi_ekle(yazim, tur, hedef, sorted(bicimler), karar, sayi, ornek,
                          unvan_kalibi(cevap, hedef, latin_zorunlu, kod == "tr") if tur == "title" and unvan_sor else "",
                          terim=not (tur == "title" and yazim in bilinen_unvan),
                          **(dict(zip(("anlam", "siradan"), anlam_notu(cevap, tur, latin_zorunlu)))
                             if s["anlam_notu"] else {}))
        eklenen[tur] += 1

    sirali = [(a, {c.get("i"): c for c in yanit.get("items") or [] if isinstance(c, dict)}.get(n))
              for parti, yanit in zip(partiler, yanitlar) for n, a in enumerate(parti, 1)]
    sirali.sort(key=lambda x: bool(x[0].get("ekli")))     # eklilerden önce sözcüğün kendi kararı yazılsın
    adli = None
    for a, cevap in sirali:
        if cevap is None:                   # karar gelmeyen aday kayda geçmez: sonraki çalıştırmada yeniden sorulur
            gunluk.uyari(ilk, f"terim: {a['yazim']!r} için karar gelmedi")
            continue
        if a.get("on") and cevap.get("t") != "title":     # yalnızca unvan mı diye soruldu: değilse sözlüğe girmez
            cevap = {"t": "common"}
        if a.get("ekli") and not cevap.get("belirsiz"):  # sözcük + hitap eki: ekisiz tek karşılık ya da sözlüğe girmez
            if adli is None:       # sözlükte ad, kod adı, unvan... olan yazımlar: onların eklisi addır (黄昏さん)
                adli = {y for k, g in sozluk.girdiler.items() if g.get("tur") not in ("common", "sound", None)
                        for y in [k] + list(g.get("yazimlar") or [])}
            karsilik = cevap.get("k").strip() if isinstance(cevap.get("k"), str) else ""
            sozcuk_ad = a["ekli"] in adli or a["ekli"].lstrip("おご") in adli
            if kod == "tr":        # Türkçede cins ad küçük harfle; başka dilde modelin yazımı kalır (Almanca: "Hündchen")
                karsilik = temizlik.tr_kucuk(karsilik[:1]) + karsilik[1:]
            cevap = ({"t": "epithet", "k": karsilik,
                      **({"m": cevap["m"]} if cevap.get("m") else {})}
                     if cevap.get("t") == "epithet" and karsilik and not EK_LATIN.search(karsilik) and not sozcuk_ad
                     else {"t": "common"})
        isle(a["yazim"], a["bicimler"], a["sayi"], a["ornek"], cevap, "model")
    for parti, kararlar_ in zip(kesif_partileri, kesif_kararlari):
        cevaplar = {c.get("i"): c for c in kararlar_}
        for n, a in enumerate(parti, 1):
            if n in cevaplar:
                isle(a["yazim"], set(), a["sayi"], a["ornek"], cevaplar[n], "keşif")
    # Ses kararları: ses ise girdi "sound" olur (terim değildir; yazımı girdinin "hedef"idir),
    # değilse girdiye "ses": false yazılır ki bir daha sorulmasın.
    ses_oylari = [y for y in ses_oylari if y.get("items")]
    if not ses_oylari:
        ses_sorulacak = []              # yanıt gelmedi: karar yazılmaz, sonraki çalıştırmada yeniden sorulur
    aday_yazimlari = {a["yazim"] for a in adaylar}
    ses_evet = Counter(c.get("i") for y in ses_oylari for c in y.get("items") or []
                       if isinstance(c, dict) and c.get("sound") is True and isinstance(c.get("i"), int))
    for n, soz in enumerate(ses_sorulacak, 1):
        if ses_evet[n] * 2 <= len(ses_oylari):
            if soz not in sozluk.girdiler and soz in aday_yazimlari:
                continue                    # sınıflandırması gelmedi: karar yazılmaz, sonra yeniden sorulur
            if soz not in sozluk.girdiler:
                sozluk.girdi_yaz(soz, sayi=sesler[soz]["sayi"], ornek=sesler[soz]["ornek"])
            sozluk.girdi_yaz(soz, ses=False)
        elif ses_latin(soz):
            sozluk.girdi_sil(soz)
            sozluk.girdi_yaz(soz, tur="sound", hedef=ses_latin(soz).capitalize(), sayi=sesler[soz]["sayi"],
                             ornek=sesler[soz]["ornek"])
            eklenen["sound"] += 1
    sozluk.unvanlar = sorted(set(sozluk.unvanlar) | _adla_gecen_unvanlar(sozluk, ciftler, bilinen_unvan, tum_metin))
    okunusla_duzelen = okunuslu_adlari_isle(sozluk, tum_metin)
    if okunusla_duzelen:
        gunluk.uyari(ilk, "okunuşu etikette yazan adın yazımı okunuşa göre düzeltildi: " + ", ".join(okunusla_duzelen))
    ortusenleri_esitle(sozluk)
    gunluk.uyari(ilk, f"terim sözlüğü: {len(adaylar)} aday, {len(partiler)} sınıflandırma isteği"
                      + (f", {len(kesifler)} keşif isteği ({len(yeni)} yeni yazım)" if kesifler else "")
                      + (f", {karsiliksiz} aday için karşılık isteği" if karsiliksiz else "")
                      + (f" (cevapları birleşmeyen {ek_oylu} aday için {EK_OY} ek oy)" if ek_oylu else "")
                      + (f", yazımı okunuş olan {yazimsiz} aday için yazım isteği" if yazimsiz else "")
                      + f"; eser {'tanındı' if taninan else 'tanınmadı'}; "
                      + ", ".join(f"{t} {n}" for t, n in sorted(eklenen.items()))
                      + (f"; adla geçen unvan: {', '.join(sozluk.unvanlar)}" if sozluk.unvanlar else ""))
    return diller
