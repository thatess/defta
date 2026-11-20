"""Terim sözlüğünün kurulması (2. sürüm).

Üç iş birbirinden ayrıdır:
  1. ADAYI SCRIPT BULUR  (adaylari_bul): aynı dosya her zaman aynı aday listesini verir.
  2. TÜRÜNÜ MODEL SÖYLER (siniflandir): kapalı liste; her adayın ne olduğu, Latin yazımı, yerleşik karşılığı.
     Scriptin göremediği adlar (kanji ile yazılmış kod adı, küçük harfli uydurma sözcük) için dosyayı okuyan
     ayrı ve dar bir istek aynı anda gider (kesfet).
  3. KARŞILIĞI SCRIPT BELİRLER (hedef_belirle): kullanıcının kuralları tür başına burada uygulanır; model bir
     kuralı çiğneyemez (kod adı çevrilmez, uydurma ad çevrilmez, sıradan sözcük sözlüğe girmez...).

Sonuç Sozluk.girdi_ekle ile saklanır: tür, karşılık, yazım bicimleri, geçiş sayısı ve örnek satırla birlikte.
"""
import difflib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import temizlik
from .ayar import VARSAYILAN, acik, dil_adi, hedef_kodu, unvan_sozcukleri, unvan_tablosu
from .istem import dile_gore
from .kaynak import EK as EK_LATIN, ciglik_mi, konusmaci_parcalari, okunus_yazimi, ses_latin, ses_satiri
from .model import Ret, json_coz
from .sozluk import UNVAN as HITAP_EKI, _anahtar, _iskelet_yakinligi, ekli_ad_mi, tek_sozcuk_ad

MAKRONSUZ = str.maketrans("āēīōūĀĒĪŌŪ", "aeiouAEIOU")
SAPKASIZ = str.maketrans("âîû", "aiu")
TURLER = ("name", "descriptive", "codename", "epithet", "title", "people", "term", "fictional", "common")
PARTI = 40                      # bir sınıflandırma isteğindeki aday sayısı (istekler aynı anda gider)

# ---- 1. aday bulma (script) -------------------------------------------------------------------------------
BUYUK = "[A-ZÀ-ÖØ-Þ" + "".join(ch for ch in map(chr, range(0x100, 0x180)) if ch.isupper()) + r"][\w'’\-]*"
AD_DIZISI = re.compile(rf"{BUYUK}(?:\s+(?:of|the|de|la|del|von|van|al)\s+{BUYUK}|\s+(?:al|el|ad|an|ar|as|at|ash|ul|bin|ibn)-{BUYUK}"
                       rf"|\s+{BUYUK})*")                    # "Lisan al-Gaib": tireli Arapça tanım edatı adın parçasıdır
# Cümle ortasında büyük harfle yazılsa da ad olmayan sözcükler
ZAMIRLER = {"I", "I'm", "I'll", "I've", "I'd", "OK", "Okay", "God"}
# İyelik sözcüğü unvan değildir: "Your Grace", "His Majesty" -> "Your" unvan sanılıp "Your -> Majesteleri" terimi
# olmuş, sıradan "your" geçen her satıra "Majesteleri" yazdırıyordu (Game of Thrones, gerçek test M).
IYELIK = {"Your", "His", "Her", "Their", "Our", "My", "Its", "Thy"}
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
EKLI_KARSILIK_IPUCU = ('an ordinary word with a Japanese honorific attached, used to call or name someone or something. '
                       'Answer with a TARGET language word, never Japanese and never a romanization: the one a native '
                       'speaker of TARGET language would use for it every time, WITHOUT any honorific, in the same tone: '
                       'a cute or affectionate one is the pet or diminutive form a child would use (クマさん -> the TARGET '
                       'language word for "little bear", never the plain word for "bear"), a polite one for a trade or '
                       'role (魚屋さん) is the plain polite word')


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
            while sozcukler and (sozcukler[-1] in ZAMIRLER or sozcukler[-1] in ("of", "the", "de", "la", "del", "von", "van", "al")):
                sozcukler.pop()                                                 # "Tell Mr. Pell I am busy" -> Pell
                                                                                # "Hand of God" -> "Hand" ("of" kalmaz)
            while len(sozcukler) > 1 and sozcukler[0] in bilinen_unvanlar:    # "Captain Venn" -> Captain + Venn
                unvan = sozcukler.pop(0)
                unvanlar[unvan] += 1
                if not sozcukler[0][:1].isupper():                              # "Lord of Mordor": çift değil
                    while sozcukler and not sozcukler[0][:1].isupper():
                        sozcukler.pop(0)
                    break
                ciftler[(unvan, re.sub(r"['’]s?$", "", " ".join(sozcukler)))] += 1
            if (len(sozcukler) > 1 and sozcukler[1][:1].isupper() and sozcukler[0].isalpha()
                    and sozcukler[0] not in IYELIK):
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
    # ("one of them Rangers" / "a ranger"): en sık yazımıyla, tekil bicimiyle aday olur.
    for parca in orta:
        if " " in parca or parca in sayim or len(parca) < 5 or not parca.isalpha():
            continue
        kok = parca.lower()[:-1] if parca.lower().endswith("s") else parca.lower()
        bicimler = re.findall(r"(?<![\w'’-])(" + re.escape(kok) + r")s?(?![\w-])", tum, re.I)
        if len(bicimler) >= max(esik, 3) and kok not in SIRADAN_BUYUK:
            sayim.setdefault(Counter(bicimler).most_common(1)[0][0], len(bicimler))
    # Tek başına bir cümle olan büyük harfli sözcük ("Strider!") seslenmedir; cümle başında atıldığı ve özensiz
    # altyazı adları cümle içinde küçük harfle yazdığı için ("known as strider") yukarıda düşer. Tek başına en az iki
    # kez geçer ve cümle içindeki küçük harfli hali bunun iki katını aşmazsa (What, Friend aşar) sorulur: ad mı
    # sıradan sözcük mü, model karar verir.
    yalniz = Counter(m.group(1) for satir in satir_metinleri for cumle in re.split(r"(?<=[.!?…])\s+", satir)
                     for m in [re.fullmatch(r"([^\W\d_][^\W\d_'’-]{3,})[.!?…,]*", cumle.strip(" -–—\"“”"))]
                     if m and m.group(1)[0].isupper() and m.group(1)[1:].islower())
    for soz, n in yalniz.items():
        if n < 2 or soz in sayim or soz in ZAMIRLER or soz.lower() in SIRADAN_BUYUK or soz in bilinen_unvanlar:
            continue
        kucuk = len(re.findall(_sinir(soz.lower()), tum))
        if kucuk <= 2 * n:
            sayim[soz] = n + kucuk
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
    for parca in [p for p in sayim if " " not in p]:      # çoğul bicim tekilin altında toplanır: Vardy + Vardies
        for cogul in (parca + "s", parca + "es", parca[:-1] + "ies" if parca.endswith("y") else ""):
            if cogul and cogul in sayim and cogul != parca:
                cogullar[cogul] = parca
    for cogul, tekil in cogullar.items():
        if tekil in sayim and cogul in sayim:
            sayim[tekil] += sayim.pop(cogul)
    return sayim, cogullar, unvanlar, ciftler, onler


def asil_kanji(yazim):
    """Okunuşlu yazımın kanji kısmı: 黄昏(たそがれ) -> 黄昏."""
    return yazim.split("(")[0]


def _japonca_adaylar(metin):
    """Japonca kaynakta ad izi taşıyan yazımlar. aday -> (sayı, öteki yazımları) döndürür."""
    adaylar = {}
    for m in OKUNUSLU.finditer(metin):                  # okunuşu yanında yazılmış sözcük: üç yazımı tek girdi
        asil = f"{m.group(1)}({m.group(2)})"
        sayi, bicimler = adaylar.get(asil, (0, set()))
        adaylar[asil] = (sayi + 1, bicimler | {m.group(1), m.group(2)})
    # Bir kez geçen hiragana okunuş, sözcük metinde okunuşsuz da geçiyorsa yalnız okuma yardımıdır (霊幻(れいげん) ilk
    # geçişte; sözcüğün kendisi zaten aday). Hiç başka geçmiyorsa adaydır: çoğu sıradan sözcüktür (熾烈(しれつ)) ve oyla
    # elenir, ama anlatıcının bir kez söylediği kod adı da böyle yazılır (黄昏(たそがれ)); sözlükte olmayınca model
    # İngilizce yayındaki çevirisini ("Twilight") yazıyordu.
    okunussuz = OKUNUSLU.sub(" ", metin)
    for asil in [a for a, (sayi, _) in adaylar.items() if sayi < 2 and re.search(r"[(][ぁ-ん]+[)]$", a)
                 and asil_kanji(a) in okunussuz]:
        del adaylar[asil]
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
    for parca, n in etiketler.items():                  # konuşmacı etiketi: yalnızca ad bicimindekiler
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


# Sayının hemen ardından gelen katakana sözcük bir para birimi ya da ölçü birimidir (１ダルク, 10ペント, 300万ダルク).
# Hikâyenin uydurma para birimi bir bölümde çoğu kez bir kez geçer, eşik onu kaçırıyordu: her bölüm başka yazılıyordu
# ("dalk", "dalc", "pent", "peni"; SPY x FAMILY). Gerçek birimleri (ドル, メートル) sınıflandırma "common" der.
BIRIM = re.compile(rf"[0-9０-９][0-9０-９,，.]*\s*[万千百億]?\s*({KATAKANA}{{2,8}})")
BIRIM_IPUCU = ("comes right after a number: a currency or unit. An invented currency or unit of the story world is a "
               "\"name\"; a real one (dollar, metre, percent) is \"common\"")


def birim_adaylari(metin):
    """Sayıdan sonra gelen katakana sözcükler: sözcük -> kaç kez (bir kez geçen de aday)."""
    return Counter(m.group(1) for m in BIRIM.finditer(OKUNUSLU.sub(" ", metin)))


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
                 "ipucu": 'the part in parentheses is how it is READ: "l" is that reading in Latin letters (the official spelling of that same sound), never a translation of the word' if b else ""}
                for p, (n, b) in _japonca_adaylar(metin).items()]
    adaylar += [{"yazim": p, "bicimler": b, "sayi": n, "ekli": kok, "ipucu": EKLI_SOZ_IPUCU}
                for p, (n, b, kok) in _ekli_sozcukler(metin, max(esik, 3)).items()]
    olanlar = {a["yazim"] for a in adaylar}
    adaylar += [{"yazim": p, "bicimler": set(), "sayi": n, "ipucu": BIRIM_IPUCU}
                for p, n in birim_adaylari(metin).items() if p not in olanlar]
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
OZGUN_ALANI = '''
"o" - only when the candidate is written in Japanese and you recognise the work: true for a named technique, attack or spell, and for a term, rank, race or creature that the work's fans and its official subtitles keep in its original Japanese sound instead of translating it (卍解 Bankai, 月牙天衝 Getsuga Tensho, 死神 Shinigami, 火影 Hokage, 写輪眼 Sharingan, 影分身 Kage Bunshin, 電伝虫 Den Den Mushi, 五老星 Gorosei, 霊圧 reiatsu), together with its "l". A technique, spell or attack whose name the subtitles write with its reading in parentheses (無量空処(むりょうくうしょ), 円舞(えんぶ)) is a named technique: true. An ordinary rank, office, institution or thing that they translate (国王 king, 海軍 the Navy, 王女 princess, 隊長 captain, 学校 school) is not, nor is an ordinary Japanese word for a kind of being or thing, even when the story is built on it (悪魔 devil, 鬼 demon, 巨人 giant, 魔法 magic, 呪い curse), nor a name made of such words (血の悪魔 the Blood Devil, 十二鬼月).'''


def _alanlar(dil, unvan=False, anlam=False, ozgun=False):
    return f'''"t" - what it is in THIS work (the lines show how it is used):
- "name": an opaque proper name of a person, place, ship, organisation or thing, including the own name or model name of a ship, vehicle, robot or machine even when it is also an English word, a short form of a character's name, and an invented currency or unit of the story world (Harry, Hogwarts, Tatooine, ナルト, the Endurance, a spacecraft called the Ranger, TARS, Cat for Catelyn, galleon).
- "descriptive": a proper name made of ordinary words, which a translator translates (Diagon Alley, the Forbidden Forest, the Leaky Cauldron, Keepers of Stonefall, 王立騎士団). An organisation, gang or order named with one ordinary word is this too, not "name" (a syndicate called 牙, "fang").
- "codename": a code name given by an organisation, the name a masked or secret persona goes by (Zero, Kira, V), or the name of an operation, mission or project (Nighthawk, 白鷺, Black Widow, Operation Overlord, プロジェクト〈ペガサス〉).
- "epithet": a nickname, epithet or by-name that describes its bearer (Erik the Red, the Boy Who Lived, the Kingslayer, the Lionheart), a title or ordinary word used AS a character's name (a character everyone calls "the Professor"), and the special word one character always uses to call another (a child's own word for her father).
- "title": a rank, office, role or form of address used with or instead of a name (Captain, Lord, Mr, 団長, 殿下).
- "people": a race, people, species or kind of creature, in the singular (centaur, house-elf, Wookiee, Na'vi).
- "term": an object, substance, technique, institution or concept that belongs to this work (lightsaber, horcrux, Polyjuice Potion, the Force), and a word for the special power, being or thing the story is built around, even when it is an ordinary word (the "gift" in a story where every hero is born with one, the "giants" in a story about fighting them, 霊力 as the energy its fighters use).
- "fictional": a word or phrase IN an invented language, quoted as such (Qapla', Dracarys, Alohomora), including the invented creole, slang or dialect of a story world (the Belters' beltalowda, kopeng, sasa ke?), even when the subtitles explain it. The NAME of a language is not fictional: it is "descriptive" and is translated (Klingon, Dothraki, Parseltongue).
- "common": an ordinary word, interjection, sound effect or fragment that is NOT a name or term of this work; this includes everyday loanwords for things, animals and jobs (Look, Monday, ドア, バカ, ニャー, タクシー, コーヒー, ナース, ポリス, ママ). Most doubtful candidates are this.
"l" - only when the candidate is not written in Latin letters: how it is written in Latin letters AS IT SOUNDS, because the viewers hear the original audio and must read the name they hear. When you recognise the work, use the romanised spelling of that same sound that the official release of THIS work uses, which is often not the letter-by-letter romanisation; otherwise the usual romanised spelling, and a name taken from a European language is written the way that language spells it (ケンジ -> Kenji, シャーロット -> Charlotte, ジョージ -> George). It is still the same name: a family name stays the family name, never the full name, the given name or another name of the same person. NEVER an English translation or localised name, even when the English release uses one: a character called ホシ is "Hoshi", never "Star"; 黒い牙 is "Kuroi Kiba", never "Black Fang".
"r" - only for a "name" or "codename" written with kanji: how those characters are READ in the work's own audio, in hiragana (健二 -> けんじ, 黒い牙 -> くろいきば). Give it every time.
"e" - the ESTABLISHED {dil} form, only for a real-world or historical person or place (a mythological one by the next rule): its standard {dil} name, the same one an encyclopedia in {dil} uses ([[tr|Rome -> Roma, Hercules -> Herkül, Alexander the Great -> Büyük İskender, Athens -> Atina, Venice -> Venedik, Vienna -> Viyana||Rome, Hercules, Alexander the Great, Athens, Venice, Vienna]]). A god, character, creature or place of an ancient epic, myth or classical text has an established form only when that form is in everyday {dil} use, even if an encyclopedia or a scholarly translation respells it[[tr| (Herkül, Truva, Zeus have one; Telemachus, Circe, Hector, Persephone, Delphi have none, so never "Telemakhos", "Kirke", "Hektor", "Persefoni", "Delfi")]]: otherwise the source spelling stays. Leave "e" out for a name of an invented world and for a name that has no such form; never make one up.
"real" - true only for a real-world, historical or mythological person or place (a real country or city included: England, Paris), and for a real people, nation or religion or its followers (Arab, Jew, Muslim, Christian, Islam, Templar, Scot, Persian).
"myth" - true only for a god, hero, creature or place of ancient myth, epic or legend (Zeus, Achilles, Medusa, Troy, Olympus).
"biz" - true only in a work set in our own world (never in an invented world such as Middle-earth or Westeros), for the invented name of a town, street, building, company, shop, restaurant, bar, hotel, school, brand or product, even when it is made of ordinary words (Eagle Point, Motel America, Central Perk, Buffalo Burger): the audience hears that name, so it is kept as it is.''' + (OZGUN_ALANI if ozgun else '') + f'''
"tr" - only for "descriptive", "epithet", "title" and "people" that have no established form, and for a "term" made of ordinary words of the source language, scientific and technical terms included (賞金稼ぎ, shadow-walker, wormhole, black hole, Murphy's Law), never for an invented word (horcrux, dilithium): one natural {dil} rendering[[tr| (Keepers of Stonefall -> Stonefall Bekçileri, 団長 -> Komutan, 賞金稼ぎ -> ödül avcısı, wormhole -> solucan deliği)]]. A proper name inside it stays unchanged. Capitalised as a name for "descriptive" and "epithet" (they are names) for a real people, nation, religion or language[[tr| (İskoç, Farsça, Müslüman)]], and for a term that names one unique thing of the story (the Force, the Ring), lower case otherwise.
"e" and "tr" render these same words; they are never the full name, real name, other name or family relation of whoever the item refers to (a nickname stays a nickname: "Ron" has no "e", never "Ronald Weasley").
Leave a field out when it is empty.''' + (f'''
"p" - for every "title", always: how the title is written together with a person's name in {dil}, with NAME standing for the name[[tr| (Captain -> "Kaptan NAME", Professor -> "Profesör NAME", 団長 -> "Komutan NAME")]]. Put NAME where {dil} puts it, before or after the title.''' if unvan else "") + ("""
"w" - for ANY item, a "name" included, whose spelling is ALSO an everyday word, a sound or a mimetic word of the source language, so that the subtitles may use it in that plain sense too: the plain sense in one to three English words (雷 -> "thunder", Hope -> "hope", ふわふわ -> "fluffy"). Leave it out when the spelling has no such everyday sense.
"m" - what the item refers to in THIS work, in at most 10 English words, for a translator who has not seen it ("the smuggler's alias in the guild", "the order that guards the wall", "a ghost bound to the old lighthouse"): for every "codename", "descriptive", "epithet", "title", "people" and "term", and for a "name" only when it has a "w". Facts only: never guess.""" if anlam else "")


def ozgun_mu(ayar):
    """Japonca teknik, rütbe ve ırk adları özgün sesiyle mi kalır (anime ekleri "orijinal")."""
    return ayar["modlar"]["anime_ekleri"] == "orijinal"


def siniflandirma_istemi(ayar):
    dil = dil_adi(ayar)
    return dile_gore(f"""You classify names and terms found in the subtitles of one film or series, so that a translator into {dil} can render each of them the same way every time. You do NOT translate the subtitles. "title_hint" names the work. "candidates" were collected by a script: each has an id "i", its spelling ("s"), how often it occurs ("n"), one or two "lines" it occurs in, and sometimes a "hint" from the script. The list contains noise.

Return ONLY this JSON object, one item per candidate, with the same "i":
{{"work": "the title of the work if you recognise it, otherwise an empty string", "items": [{{"i": 1, "t": "name"}}, {{"i": 2, "t": "descriptive", "e": "..."}}, {{"i": 3, "t": "common"}}]}}

{_alanlar(dil, acik(ayar, "unvan", "model_karari"), ayar["sozluk"]["anlam_notu"], ozgun_mu(ayar))}""", hedef_kodu(ayar))


def kesif_istemi(ayar):
    dil = dil_adi(ayar)
    return f"""You read the subtitles of one film or episode and list the names and terms that a translator into {dil} must render the same way every time and that are NOT in "found" (a script already collected those). You do NOT translate the subtitles.

What the script cannot see, and you look for: names and code names written in kanji or hiragana (赤ひげ, かぐや姫), invented words and the story world's own creature or group words written in lower case (muggle, horcrux, wight, droid), the word for the special power or being the story is built around (霊力, gift, giant), ordinary words the story uses as its own name for a group, a role or a kind of person or being and repeats in that sense, even when they look like everyday words (the "company" that sets out together, the "watchers" of an order, the "wardens" who roam its borders, "shades" for its ghosts), offices and roles used instead of a name, recurring epithets, the story world's own currency and units (galleons, credits of an invented state), and names that a careless subtitle writes wholly in lower case, which the script misses (winterfell, king's landing, the iron throne). Only things that occur at least twice in these lines (a currency or unit: even once), copied exactly as the lines write them, in their shortest form. Never list ordinary words, honorifics, or anything already in "found" (in any letter case: careless subtitles write names in lower case too). List the story world's own lower-case words first, then names. List every one you find, however many; an empty list is a correct answer.

Return ONLY this JSON object:
{{"language": "ISO 639-1 code of the language of the lines", "work": "the title of the work if you recognise it, otherwise an empty string", "items": ["exact spelling", "exact spelling"]}}"""


# ---- 3. karşılığı belirleme (script) ----------------------------------------------------------------------
SES_ISTEMI = """You are given words from the subtitles of one film or series. Each is a Japanese word written only in kana that stands alone as a whole line or as one speaker's whole part of a line; "line" shows one place where it occurs and "by" who says it.
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
            # Birden çok konuşmacılı satırda her konuşmacının sözü ayrı bakılır: （ヨル）ガーン da tek başına bir sestir.
            butun = ses_satiri(s.metin)
            parcalar = [butun] if butun else [x for x in map(ses_satiri, konusmaci_parcalari(s.metin)) if x]
            satirlar.append((s.metin, parcalar[0][0] if len(parcalar) == 1 and butun else None))
            for ses in parcalar:
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


# Bir karakterin hep tek başına söylediği kana sözcük: o karakterin birine seslenme biçimi olabilir (Anya'nın
# babasına ちち demesi). Sıradan sözcük gibi göründüğü için aday olmuyor, her satırda başka çevriliyordu
# ("Babacık", "Babacığım", "Baba"; SPY x FAMILY). Sınıflandırma "epithet" derse sözlüğe girer, tek biçimde yazılır.
HITAP_SOZ_IPUCU = ("a word one character keeps saying on its own: if it is that character's own way of calling or "
                   "naming someone (a child's own word for her father), it is an \"epithet\"; otherwise \"common\"")
HITAP_SOZ_ESIGI = 3


def hitap_sozcugu_adaylari(sesler, olanlar, bilinen, dosyalar):
    """Tek başına en az HITAP_SOZ_ESIGI kez söylenen, hep aynı (adı bilinen) konuşmacıdan gelen kana sözcükler."""
    tum = [temizlik.ETIKET.sub("", s.metin).replace("\n", " / ") for _, satirlar, _ in dosyalar
           for s in satirlar if not s.ekran]
    sonuc = []
    for soz, k in sorted(sesler.items(), key=lambda x: (-x[1]["sayi"], x[0])):
        konusanlar = {x for x in k["konusanlar"] if x}
        if (k["sayi"] < HITAP_SOZ_ESIGI or len(konusanlar) != 1 or k["ad_degil"] or soz in olanlar
                or _anahtar(soz) in bilinen or not re.fullmatch(r"[ぁ-んァ-ヺー]{2,4}", soz)):
            continue
        # örnek: tek başına söylendiği bir satır ve cümle içinde geçtiği bir satır (varsa)
        icinde = [x for x in tum if soz in x and x != k["ornek"] and len(x) > len(soz) + 8]
        sonuc.append({"yazim": soz, "bicimler": set(), "sayi": k["sayi"], "ipucu": HITAP_SOZ_IPUCU,
                      "ornek": [k["ornek"]] + [x[:110] for x in icinde[:1]]})
    return sonuc


# Kod adının genel sözcüğü (Operation Strix, Project Apple): ad duyulduğu gibi kalır, genel sözcüğü karşılık isteği
# hedef dile, o dilin söz dizimiyle çevirir ("Strix Operasyonu"); cevapta ad aynen yoksa yazım olduğu gibi kalır
# (kullanıcının kararı, 18.11.2026: kalıp dile özgü yazılmaz, hedef dilden gelir).
KOD_ADI_GENEL = re.compile(r"(?i)(operation|project|mission|plan|protocol|program(?:me)?|code\s*name)[\s:]+(.+)")
KOD_ADI_IPUCU = ('the name of an operation, project or mission made of a generic word and a name: translate ONLY the '
                 'generic word into TARGET language and put the two together in the word order and form natural in '
                 'TARGET language; the name "NAME" stays exactly as written')


def kod_adi_adi(latin):
    """"Operation Strix" -> "Strix": kod adının genel sözcükten sonraki adı; kalıba uymuyorsa boş."""
    m = KOD_ADI_GENEL.fullmatch((latin or "").strip())
    return m.group(2).strip() if m else ""


# Katakanayla yazılmış genel sözcük (オペレーション〈梟〉): Latin yazım onu düşürmüşse ("Strix") geri konur.
KATAKANA_GENEL = {"オペレーション": "Operation", "プロジェクト": "Project", "ミッション": "Mission", "プラン": "Plan",
                  "プロトコル": "Protocol", "プログラム": "Program"}


def kod_adi_latin(yazim, latin):
    """Kod adının genel sözcüklü Latin yazımı: kaynakta katakana genel sözcük varsa ve yazım onu taşımıyorsa eklenir."""
    latin = (latin or "").strip()
    genel = next((v for k, v in KATAKANA_GENEL.items() if yazim.startswith(k)), "")
    if genel and latin and not kod_adi_adi(latin):
        return f"{genel} {latin}"
    return latin


# Halk, ırk ve yaratık türü adını küçük harfle yazan diller (Türkçe "hobbit, elf, cüce, ork"; Almanca adları büyük
# yazar, listede yok). Gerçek dünyadan bir halk ("Japon") büyük harfli kalır. Kullanıcının kararı, 18.11.2026.
HALK_KUCUK_HARF = {"tr", "az", "en", "fr", "es", "it", "pt", "nl", "sv", "no", "nb", "da", "fi", "pl", "cs", "sk",
                   "ro", "hu", "hr", "sr", "bs", "sl", "ru", "uk", "bg", "el", "id", "ms", "vi", "ca", "et", "lv", "lt"}


def halk_harfi(hedef, hedef_kodu="tr"):
    """Tek ya da çok sözcüklü kurgusal halk adının her sözcüğü küçük harfle ("Hobbit" -> "hobbit"); hedef dil
    listede yoksa olduğu gibi."""
    if hedef_kodu not in HALK_KUCUK_HARF or not hedef:
        return hedef
    kucuk = temizlik.tr_kucuk if hedef_kodu in ("tr", "az") else str.lower
    sozcukler = hedef.split(" ")
    # Kesmeyle ek almış sözcük özel addır, önündeki büyük harfli sözcüklerle birlikte korunur ("Tabr Sietch'inden
    # fedaykin": gerçek test Z'de "tabr sietch'inden" yazılıyordu)
    koru = set()
    for i, w in enumerate(sozcukler):
        if len(sozcukler) > 1 and re.search(r"\w['’]\w", w) and w[:1].isupper():
            j = i
            while j >= 0 and sozcukler[j][:1].isupper():
                koru.add(j)
                j -= 1
    return " ".join(w if i in koru else kucuk(w[:1]) + w[1:] for i, w in enumerate(sozcukler))


def siradan_odunc(cevap, latin):
    """Okunuşu verilmiş terim İngilizcenin sıradan bir sözcüğü mü (警戒強度(カテゴリー) -> "Category", sıradan anlamı
    "category")? Öyleyse hedef dile çevrilir ("kategori"): İngilizce bırakılınca çeviride "Category 4" kalıyordu."""
    w = cevap.get("w").strip() if isinstance(cevap.get("w"), str) else ""
    return (cevap.get("t") in ("term", "people") and bool(w) and isinstance(latin, str) and " " not in latin.strip()
            and _oy_anahtari(w) == _oy_anahtari(latin.strip()))


def okunus_adi_mi(yazim, latin, siradan=False):
    """Kaynak okunuşu katakanayla vermiş (東国(オスタニア)) ve Latin yazım o okunuşun sesi: ad odur ("Ostania"). Sesi
    tutmayan yazım uydurma ya da İngilizce uyarlamadır (婆(ババア) -> "Dash Baba"): okunuş adı sayılmaz."""
    m = OKUNUS_KATAKANA.search(yazim)
    if not m or not isinstance(latin, str) or not latin.strip():
        return False
    parcalar = [p for p in re.split(r"[・=＝]", re.sub(r"[()（）]", "", m.group(0))) if p]
    if len(latin.split()) != len(parcalar):         # okunuşta olmayan sözcük eklenmiş ("Dash Baba")
        return False
    okunus = temizlik.kana_latin(re.sub(r"[^ァ-ヺー]", "", m.group(0))) or ""
    if siradan and _mekanik_mi(yazim, latin):       # sıradan sözcüğün harf harf okunuşu ad değildir (婆(ババア) "Baba")
        return False
    return not okunus or _iskelet_yakinligi(okunus, latin) >= 0.5


def odunc_okunus_mu(yazim, latin):
    """Unvan ya da lakabın katakana okunuşu Avrupa dilinden ödünç bir sözcük mü (管理官(ハンドラー) -> "Handler")? Latin
    yazım okunuşun harf harf karşılığı değilse ödünçtür ve duyulduğu gibi yazılır (kullanıcının kararı, 18.11.2026);
    harf harf karşılığıysa Japonca sözcüktür (婆(ババア) -> "Babaa") ve çevrilir."""
    return okunus_adi_mi(yazim, latin) and not _mekanik_mi(yazim, latin)


def _harf_iskeleti(ad):
    """Yunanca/Latince adın yazım farklarından arınmış iskeleti: Circe ve Kirke, Athena ve Atena, Menelaus ve Menelaos,
    Pylos ve Pilos, Sparta ve Isparta aynı iskeleti verir; Troy ve Truva, Hercules ve Herkül vermez."""
    s = unicodedata.normalize("NFKD", ad.replace("ı", "i").replace("İ", "i").lower())
    s = re.sub(r"[^a-z]", "", "".join(k for k in s if not unicodedata.combining(k)))
    for eski, yeni in (("ph", "f"), ("th", "t"), ("kh", "h"), ("ch", "h"), ("ae", "e"), ("oe", "e"), ("ou", "u"),
                       ("c", "k"), ("y", "i"), ("x", "ks"), ("w", "v")):
        s = s.replace(eski, yeni)
    s = re.sub(r"(.)\1+", r"\1", s)                  # Odysseus / Odiseus
    s = re.sub(r"^i(?=s[^aeiou])", "", s)            # Sparta / Isparta
    s = re.sub(r"(?<=.{3})e$", "i", s)                # Persephone / Persefoni
    return re.sub(r"(?<=.{3})(us|os)$", "", s)       # Menelaus / Menelaos, Pylos / Pilos


def harf_cevirisi_mi(kaynak, hedef):
    """Hedef, kaynaktaki adın yalnızca başka bir harf çevirisi mi (yeni bir ad değil)?"""
    return _harf_iskeleti(kaynak) == _harf_iskeleti(hedef) and len(_harf_iskeleti(kaynak)) >= 3


def turkce_harfsiz(metin):
    """ "Şinjuku" -> "shinjuku": Türkçe harflerin özgün yazımdaki karşılığı (ş sh, ç ch, c j, ı i ...)."""
    s = temizlik.tr_kucuk(metin.strip())
    for eski, yeni in (("ş", "sh"), ("ç", "ch"), ("c", "j"), ("ğ", "g"), ("ı", "i"), ("ö", "o"), ("ü", "u"), ("â", "a"),
                       ("î", "i"), ("û", "u")):
        s = s.replace(eski, yeni)
    return s


def uydurma_katakana(cevap, yazim):
    """Katakanayla yazılmış uydurma terim (ギアス "Geass"): sıradan anlamı ("w") ve sıradan sözcüklerden kurulduğunu
    söyleyen karşılığı ("tr") yok, Latin yazımı harf harf okunuş değil (eserin kendi yazımı). Uydurma terim
    çevrilmez; karşılık isteği "mutlak itaat gücü" gibi bir açıklama yazıyordu (Code Geass, gerçek test K).
    Harf harf okunuşu verilen (チャクラ "Chakura") gerçek bir sözcüğün okunuşu olabilir: o yine sorulur."""
    latin = cevap.get("l").strip() if isinstance(cevap.get("l"), str) else ""
    return (cevap.get("t") in ("term", "people") and bool(re.fullmatch(r"[ァ-ヺー・]{2,}", yazim)) and bool(latin)
            and not temizlik.latin_disi(latin) and not str(cevap.get("w") or "").strip()
            and not str(cevap.get("tr") or "").strip() and not _mekanik_mi(yazim, latin))


def ozgun_kalir(yazim, cevap, ozgun):
    """Animenin tanınmış teknik, rütbe ve ırk adı (卍解 Bankai, 火影 Hokage, 死神 Shinigami) Japonca sesiyle kalır:
    çevirisi ("Nihai salım", "Ateş Gölgesi") hayranın tanıdığı adı siliyordu (gerçek test F, 18.11.2026)."""
    latin = cevap.get("l").strip().translate(MAKRONSUZ) if isinstance(cevap.get("l"), str) else ""
    # uzun ünlü ikilenmez (Tenshou -> Tensho, Koushun -> Koshun): çeviri isteminin yazım kuralı da budur
    latin = re.sub(r"(?i)(?<=[bcdfghjkmnprstwyz])(?:ou|oo)", "o", re.sub(r"(?i)(?<=[bcdfghjkmnprstwyz])uu", "u", latin))
    # Yalnız katakanadan oluşan yazım yabancı kökenli sözcüktür (チャクラ): okunuş ve karşılık kuralı onundur; harf harf
    # okunuşu ("Chakura") yazılmaz.
    # Animede okunuşu hiraganayla verilmiş kanji terim (獄門疆(ごくもんきょう), 円舞(えんぶ)) adlandırılmış tekniktir:
    # model "o" demese de okunuşuyla kalır (oylar her çalıştırmada değişiyordu: bir turda "Gokumonkyo", ötekinde
    # "Hapishane Diyarı").
    m = re.fullmatch(r"[一-龥々]{1,8}[(（]([ぁ-ゖー]{2,})[)）]", yazim)
    if ozgun and m and cevap.get("t") == "term":
        if not latin or temizlik.latin_disi(latin):
            latin = temizlik.kana_latin(m.group(1)) or ""
            latin = re.sub(r"(?<=[bcdfghjkmnprstwyz])(?:ou|oo)", "o", re.sub(r"(?<=[bcdfghjkmnprstwyz])uu", "u", latin))
        return (latin[:1].upper() + latin[1:]) if latin and len(latin) <= 40 else ""
    return (latin if ozgun and cevap.get("o") is True and cevap.get("t") in KARSILIKLI
            and re.search(r"[一-龥々ぁ-ゖ]", re.sub(r"[(（].*?[)）]", "", yazim)) and latin and not temizlik.latin_disi(latin) and len(latin) <= 40 else "")


def hedef_belirle(yazim, cevap, taninan, latin_zorunlu=True, hedef_kodu="tr", ozgun=False):
    """Kararın türü ve hedefi; kurgusal halk adı hedef dilin yazımıyla küçük harfli (halk_harfi)."""
    if ozgun_kalir(yazim, cevap, ozgun):
        return cevap["t"], ozgun_kalir(yazim, cevap, ozgun)
    tur, hedef = _hedef_ham(yazim, cevap, taninan, latin_zorunlu, hedef_kodu)
    # İngilizcede halk ve dil aynı sözcük ("Portuguese", "Chinese", "English"): sözlüğe dil adı girerse ("Portekizce")
    # halkı anlatan her satır terimi göstermiyor sayılır, yeniden sorulur (gerçek test U, Shogun'da 8 satır). Anlamı
    # bağlam belirler; sözlüğe girmez.
    # Model dil adını "descriptive" de sayabiliyor (Shogun, gerçek test X: "Portuguese" -> descriptive "Portekizce").
    if (tur in ("people", "descriptive") and hedef and hedef_kodu in ("tr", "az") and not temizlik.latin_disi(yazim)
            and re.fullmatch(r"[A-Z][a-z]+(?:ese|ish|an|ch|sh)", yazim.strip())
            and re.search(r"[cç][ae]$", hedef.strip())):
        return tur, ""
    # Mitoloji ve destan adı altyazıdaki gibi kalır; yalnızca gündelik dilde başka bir sözcük olarak yerleşmiş biçim
    # (Troy -> Truva) yazılır, ansiklopedinin harf çevirisi (Circe -> Kirke, Sparta -> Isparta) yazılmaz (kullanıcının
    # kararı, 18.11.2026). Model bunu istemle ayırt edemiyordu; harf çevirisi olduğunu script ölçer.
    if (tur == "name" and hedef and hedef != yazim and cevap.get("myth") is True
            and not temizlik.latin_disi(yazim) and harf_cevirisi_mi(yazim, hedef)):
        hedef = yazim
    # Tekil kaynağın halk adı tekil yazılır: "Scottish" -> "İskoçlar", "Welsh" -> "gallerliler" geliyordu (gerçek test
    # T); sözlükte çoğul kalınca "İskoç ordusu" satırı terimi göstermiyor sayılıyordu.
    if (tur == "people" and hedef and hedef_kodu in ("tr", "az") and " " not in hedef.strip()
            and not temizlik.latin_disi(yazim) and not yazim.strip().endswith("s")
            and re.search(r"[^\W\d_]{3,}l[ae]r$", hedef.strip())):
        hedef = hedef.strip()[:-3]
    if tur == "people" and hedef and cevap.get("real") is not True:
        hedef = halk_harfi(hedef, hedef_kodu)
    # Gerçek halk, din ve mensubu büyük harfle (Müslüman, Hristiyan, Arap, İslam); karşılık isteği küçük yazıyordu.
    if (hedef and cevap.get("real") is True and hedef_kodu in ("tr", "az")
            and (tur == "people" or (tur == "term" and " " not in hedef.strip()))):
        hedef = " ".join(("İ" if w[:1] == "i" else w[:1].upper()) + w[1:] for w in hedef.split(" "))
    if hedef and hedef_kodu == "tr" and tur in ("people", "term", "descriptive"):
        hedef = temizlik.tr_buyuk_harf(hedef)
    # Bir yerin adından kurulan halk adı ve dil adı Türkçede büyük harfle yazılır (Truvalı, Spartalı, Romalı, Galyalı,
    # Valyrialı; Farsça, Portekizce); kurgusal tür adı (hobbit, elf, cüce) küçük kalır. Model gerçek halkı her zaman "real" saymıyordu.
    if (hedef and hedef_kodu in ("tr", "az") and tur == "people" and " " not in hedef.strip()
            and re.search(r"[^\W\d_]{2,}(?:l[ıiuü]|[cç][ae])$", hedef.strip()) and not temizlik.latin_disi(yazim)
            and yazim.strip()[:1].isupper()):
        hedef = ("İ" if hedef[0] == "i" else hedef[0].upper()) + hedef[1:]
    # Gerçek yer adının yalnızca Türkçe harflerle yazılışı (新宿 "Şinjuku", "Şibuya") yerleşik biçim değildir:
    # Türkçede de özgün yazımıyla geçer (Shinjuku). Başka sözcük olan yerleşik biçim (Japonya, Moskova) kalır.
    kaynak_yazimi = (cevap.get("l") if isinstance(cevap.get("l"), str) else "") if temizlik.latin_disi(yazim) else yazim
    if (tur == "name" and hedef and kaynak_yazimi and hedef != kaynak_yazimi
            and turkce_harfsiz(hedef) == temizlik.tr_kucuk(kaynak_yazimi.strip().translate(MAKRONSUZ))):
        hedef = kaynak_yazimi.strip().translate(MAKRONSUZ)
    if tur == "name" and hedef and cevap.get("real") is not True:
        hedef = duyulan_sira(yazim, hedef, [cevap])     # yerleşik biçim Batı sırasıyla gelse de duyulan sıra
    # Çevrilmeden kalan Latin harfli uydurma terim (mithril, athelas) halk adı gibi küçük harfle yazılır (kullanıcının
    # kararı, 18.11.2026); kısaltma (TARDIS) ve içinde büyük harf olan yazım (PolyJuice) olduğu gibi kalır.
    # Kaynak büyük harfle yazıyorsa (the Matrix, Alpha) tek nesnenin adıdır, büyük harf kalır (gerçek test Z: "matrix").
    if (tur == "term" and hedef and not temizlik.latin_disi(yazim) and cevap.get("real") is not True
            and _oy_anahtari(hedef) == _oy_anahtari(yazim) and " " not in hedef.strip()
            and not (yazim.strip()[:1].isupper() and not yazim.strip().isupper())
            and not any(k.isupper() for w in hedef.split() for k in w[1:])):
        hedef = halk_harfi(hedef, hedef_kodu)
    return tur, hedef


def _hedef_ham(yazim, cevap, taninan, latin_zorunlu=True, hedef_kodu="tr"):
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
    # Yerleşik bicim yalnızca gerçek dünyadan ya da söylenceden adda güvenilir; kurgusal adda sınıflandırmanın "e"si
    # uydurma olabiliyordu.
    guvenilir = cevap.get("real") is True or cevap.get("myth") is True
    if tur == "common":                         # sıradan sözcük sözlüğe girmez
        return tur, ""
    # Tek sözcüklük kurgusal özel ad kaynaktaki gibi (kaynaktaki_gibi_kalir); küçük harfle yazan kaynakta büyük harfle.
    if cevap.get("unvanli_ad") and not yabanci:
        return tur, cevap["unvanli_ad"]
    if cevap.get("kaynak") is True and not yabanci:
        ad = yazim.strip()
        return tur, ad[:1].upper() + ad[1:]
    if tur == "fictional":
        return tur, "" if yabanci and latin_zorunlu else yazim
    # Kaynak okunuşunu katakanayla vermiş: ad odur (東国(オスタニア) -> Ostania). Kişiyi niteleyen türlerde (lakap, unvan)
    # okunuş yalnızca söyleniştir, karşılık olmaz: 婆(ババア) "Baba" diye yazılmaz, aşağıdaki kuralla çevrilir.
    if (okunus_adi_mi(yazim, latin, bool(isinstance(cevap.get("w"), str) and cevap["w"].strip()))
            and (tur not in ("epithet", "title") or odunc_okunus_mu(yazim, latin)) and not siradan_odunc(cevap, latin)):
        return tur, latin
    if tur == "codename":                       # kod adı çevrilmez, duyulduğu gibi kalır; yalnız "Operation" gibi
        if yabanci and aynen:                   # okunuşu tutmayan yazım çeviridir: okunuş yazılır (Twilight -> Tasogare)
            duzeltilen = {"l": aynen}
            _kod_adi_duyuldugu_gibi(duzeltilen, yazim, [cevap])
            aynen = duzeltilen["l"]
        aynen = kod_adi_latin(yazim, aynen) if yabanci else aynen
        ad, karsilik = kod_adi_adi(aynen), temiz("k")   # genel sözcüğü karşılık isteği hedef dile çevirir
        if ad and karsilik and temizlik.tr_kucuk(ad) in temizlik.tr_kucuk(karsilik):
            return tur, karsilik
        return tur, aynen
    if tur == "name" and cevap.get("anlamli_ad") and not yabanci and temiz("k"):
        return tur, temiz("k")                  # çok sözcüklü anlamlı ad: karşılık isteği (Bag End -> Çıkın Çıkmazı)
    if tur == "name":                           # uydurma ad çevrilmez; yalnızca yerleşik bicimi varsa o
        # Latin dışı kaynakta ad duyulduğu gibi yazılır: "yerleşik bicim" yalnız gerçek dünyadan adda (ローマ -> Roma)
        # kullanılır; kurgusal adın İngilizce yayındaki çevirisi ya da uyarlaması alınmaz. Yerleşik bicim adın
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
        return tur, karsilik
    if yabanci:
        # Karşılığı gelmeyen kanji/hiragana yazımın "l" alanı İngilizce çevirisidir (爆弾犬 -> "bomb dog"), sıradan
        # anlamlı katakana İngilizce sözcüktür: sözlüğe girmez. Uydurma katakana terim yazımıyla kalır (ミスリル).
        siradan = isinstance(cevap.get("w"), str) and cevap["w"].strip()
        return tur, "" if re.search(r"[一-龥々ぁ-ん]", yazim) or siradan else aynen
    if _cevrilir(cevap, yazim):
        return tur, ""          # çevrilecek söz ama karşılığı gelmedi: sözlüğe girmez, çevirmen bağlama göre çevirir
    # Çevrilmeyen Latin yazım kalır (Vardy, Dúnedain, mithril); güvenilir yerleşik bicimin yalnızca harfi kazanır:
    # kaynak "Hobbit" yazsa da Türkçesi "hobbit"tir.
    # yalnız harf büyüklüğü (Almanca hedefte "Elf"): tanınan eserde de alınır
    return tur, (yerlesik if (taninan or guvenilir) and yerlesik and temizlik.tr_kucuk(yerlesik) == temizlik.tr_kucuk(aynen)
                 else aynen)


def _cevrilir(cevap, yazim):
    """Latin harfli yazım çevrilecek bir söz mü: sınıflandırma kaynaktan farklı bir bicim (halfling -> buçukluk) ya da
    sıradan anlam ("ring") verdi, ya da oylardan biri çevirdi (_ceviri_oyu). Özel ad gibi kalan (Vardy, Dúnedain,
    mithril) çevrilmez."""
    bicim = next((str(cevap.get(a)).strip() for a in ("tr", "e") if str(cevap.get(a) or "").strip()), "")
    siradan = isinstance(cevap.get("w"), str) and cevap["w"].strip()
    return (bool(siradan) or cevap.get("ceviri_oyu") is True
            or (bool(bicim) and temizlik.tr_kucuk(bicim) != temizlik.tr_kucuk(yazim)))


def _ceviri_oyu(oylar, tur, yazim):
    """Çoğunluğun türünü veren oylardan biri Latin harfli yazımı çevirdi mi: kaynaktan farklı bir "tr"/"e" ya da
    unvan kalıbı ("p": "Majesteleri NAME"). Yerleşik bicim ancak oyların çoğu verirse alınır; tek oyun çevirisi de
    karşılığın sorulmasına yeter (gerçek test Q: "His Grace" üç oyda da "Majesteleri NAME", "e" tek oyda; sorulmadı,
    sözlüğe İngilizcesi girdi. "Citadel" de öyle)."""
    if temizlik.latin_disi(yazim):
        return False
    kaynak = temizlik.tr_kucuk(yazim.strip())
    for c in oylar:
        if c.get("t") != tur:
            continue
        bicimler = [c.get(a) for a in ("tr", "e")]
        p = c.get("p") if isinstance(c.get("p"), str) else ""
        if p.count("NAME") == 1:
            bicimler.append(" ".join(p.replace("NAME", " ").split()).strip(" ,"))
        if any(isinstance(b, str) and b.strip() and temizlik.tr_kucuk(b.strip()) != kaynak for b in bicimler):
            return True
    return False


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
    if not hedef:
        return ""
    return f"{('İ' if turkce and hedef[0] == 'i' else hedef[0].upper()) + hedef[1:]} {{ad}}"


def _mekanik_mi(yazim, latin):
    """Latin yazım, kaynaktaki kananın harf harf okunuşu mu (ステラ -> "Sutera", ロイド・フォージャー -> "Roido Foja")?"""
    kana = re.sub(r"[^ぁ-んァ-ヺー]", "", yazim.split("(")[-1] if "(" in yazim else yazim)
    okunus = temizlik.kana_latin(kana) if kana else None
    # Uzun ünlü işareti harfin kendisidir ("Furankī" = "Furanki"): silinirse harf harf okunuş fark edilmez.
    duz = lambda m: "".join(k for k in unicodedata.normalize("NFKD", m.lower()) if not unicodedata.combining(k))
    # "iya" kayma sesi de harf harf okunuştur (ブライア -> "Buraiya" = "Buraia")
    yalin = lambda m: re.sub(r"ou|oo|uu|aa|ii|ee", lambda x: x.group(0)[0],
                             re.sub(r"iy(?=[aeou])", "i", re.sub(r"[^a-z]", "", duz(m))))
    return bool(okunus) and yalin(okunus) == yalin(latin)


def _sesi_tutan_yerlesik(yazim, yerlesikler, oy_sayisi):
    """Oyların yarısından çoğunun verdiği yerleşik bicim, kaynaktaki katakananın sesini tutuyorsa (her parça
    kendi parçasına, ünsüz iskeletiyle) o bicim; yoksa boş. "Sylvia Sherwood" シルヴィア・シャーウッド'nın sesidir,
    "Star" ホシ'nin değildir."""
    if not yerlesikler or not re.fullmatch(r"[ァ-ヺー・=＝ ]+", yazim):
        return ""
    anahtar, kac = Counter(_oy_anahtari(d) for d in yerlesikler).most_common(1)[0]
    if kac * 2 <= oy_sayisi:
        return ""
    bicim = Counter(d for d in yerlesikler if _oy_anahtari(d) == anahtar).most_common(1)[0][0]
    kaynak = [p for p in re.split(r"[・=＝ ]+", yazim) if p]
    hedef = bicim.split()
    if len(kaynak) != len(hedef) or temizlik.latin_disi(bicim) or _mekanik_mi(yazim, bicim):
        return ""
    benzerlik = [_iskelet_yakinligi(temizlik.kana_latin(k) or "", h) for k, h in zip(kaynak, hedef)]
    return bicim if sum(benzerlik) / len(benzerlik) >= 0.5 else ""


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
    return dile_gore(f"""You render terms of one film or series into {dil}. "title_hint" names the work. Each item is a term from its subtitles, with what it refers to in this work ("m") and lines where it occurs. Give for each the one {dil} word or short phrase that names exactly what it is IN THIS WORK, as a careful professional {dil} translator of this work would write it every time; if the work has a published {dil} translation, use the form it uses. Be specific: when the work shows what the thing actually is or does, name that, not a vaguer general word (in a story where "the gifted" see the future, each of them is a seer[[tr|: "kâhin", not "yetenekli"||, not just someone gifted]]). Render the item's own words: a nickname, epithet or title stays one, never the name of whoever it refers to. "names" lists names that occur inside the item, with their fixed spelling: each stays exactly as given, only the rest is rendered; a word derived from one of them (an adjective or demonym, "Trojan" from "Troy") is built on its given spelling. When one item is built on another (a power and the people who have it, a group and its members, a craft and those who practise it), build its rendering on the other item's rendering, so that the two read as one family[[tr| ("büyü" / "büyücü", never two unrelated words)||]]. Never use a word that names a different kind of person or thing in this work (one who talks to spirits is not one who moves objects with the mind). Use what you know about the work as well as the lines, and follow an item's "hint". Write every rendering in {dil} ("target_language"), even when an item or its lines are in some other language: {"" if hedef_kodu(ayar) == "en" else "never in English, "}never in the original language or script, never as a romanization of the original; the only exceptions are the two below (an invented word, and a name the published translation keeps). Give the base form (singular, without case or possessive endings): the translation inflects it as each sentence needs; a people is named in the singular even when the source word stands for all of them[[tr| ("the Scottish" -> "İskoç", never "İskoçlar")||]]. An invented word of the story that is not built from ordinary words (mithril, beskar, droid) is given unchanged. A name of a ship, place or building made of ordinary words is rendered whole[[tr| ("the Black Pearl" -> "Kara İnci", never "Kara Pearl")||]], or kept whole where the published translation keeps it. A proper name made of ordinary words and a nickname or epithet are names: capitalise them as names; so are a real people, nation, religion and language[[tr| ("İskoç", "Farsça", "Müslüman")||]]. The word for a KIND of invented being (elf, titan, devil) is lower case, but the name of one particular being, place, group or unique thing made of such words ("the Blood Devil", "the Force") is a name. Everything else is lower case.
Return ONLY this JSON object: {{"items": [{{"i": 1, "tr": "..."}}]}}""", hedef_kodu(ayar))


def _karsilik_sorulacak(c, yazim):
    """Türkçesini karşılık isteğinin vereceği karar: karşılık alan bütün türler (halk adı, unvan, lakap, tanım, kavram).
    Bu türlerin Türkçesi yalnızca bu istekten gelir; sınıflandırmanın "yerleşik bicim" ya da karşılık alanı hedef
    olmaz (Spy'daki "esper" oradan geçiyordu). İki istisna: okunuşu katakanayla verilmiş yazımın karşılığı
    okunuşudur (星(ステラ) -> Stella); Latin harfli yazım ancak sınıflandırma çevrilir dediyse sorulur (halfling,
    kingsfoil), özel ad gibi kalan (Dúnedain, Vardy, mithril) yazımıyla kalır."""
    if c.get("kaynak") or c.get("unvanli_ad"):  # kaynaktaki gibi kalan ad; unvan + kalan ad
        return False
    if c.get("anlamli_ad"):                     # anlamlı sözcüklerden kurulan çok sözcüklü ad (Bag End, Middle-earth)
        return True
    if c.get("t") == "codename":
        return bool(kod_adi_adi(kod_adi_latin(yazim, c.get("l")) if temizlik.latin_disi(yazim) else yazim))
    # "o" yalnız anime ekleri özgünken istenir: oyu alan terimin karşılığı sorulmaz (okunuşlu terim kuralı ayara bağlı
    # olduğundan onun karşılığı yine sorulur, ayar kapalıysa o kullanılır)
    if c.get("t") not in KARSILIKLI or (c.get("o") is True and ozgun_kalir(yazim, c, True)):
        return False
    if uydurma_katakana(c, yazim):
        return False
    # "Geralt of Rivia", "Eddard of House Stark": bağlaçla kurulan ad tanımdır, Türkçede kurulur ("Rivyalı Geralt");
    # sınıflandırma "tr" vermese de sorulur (İngilizce kalıyordu, gerçek test M)
    if c.get("t") == "descriptive" and re.search(r"\s(?:of|de|del|von|van)\s", yazim):
        return True
    # Latin harfli kaynakta küçük harfli terim sıradan söz olabilir (wormhole, black hole, time slippage: gerçek test S'de
    # sınıflandırma sıradan anlamını vermedi, terimler "olduğu gibi yazılacak adlar" listesine girdi). Sorulur; uydurma
    # sözcükse (mithril, beskar) karşılık isteği yazımın kendisini verir, öyle kalır. İyelikli kalıp ("Murphy's Law")
    # da kurulur.
    if (c.get("t") == "term" and re.fullmatch(r"[a-z][a-z'’ -]*[a-z]", yazim.strip())) or (
            c.get("t") in ("term", "descriptive") and re.search(r"\w['’]s\s+\w", yazim)):
        return True
    # Yerden kurulan halk adı ("Valyrian", "Spartan", "Belter", "Japanese") Türkçede kurulur (Valyrialı); sınıflandırma
    # çevirmese de sorulur (gerçek test Q: "valyrian" kaldı)
    if (c.get("t") == "people" and not temizlik.latin_disi(yazim)
            and re.fullmatch(r"[A-Z][a-z]+(?:an|ese|ish|er)s?", yazim.strip())):
        return True
    if (okunus_adi_mi(yazim, c.get("l"), bool(isinstance(c.get("w"), str) and c["w"].strip()))
            and (c.get("t") not in ("epithet", "title") or odunc_okunus_mu(yazim, c.get("l")))
            and not siradan_odunc(c, c.get("l"))):
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


def _ad_turemis_mi(ad, yazim):
    """Adın türemiş bicimi terimde geçiyor mu (Troy -> "Trojan War"): kök adın son harfi atılmış hali."""
    return (len(ad) >= 4 and " " not in ad and not temizlik.latin_disi(ad)
            and any(w != ad and w.startswith(ad[:-1]) for w in re.findall(r"[A-Za-z]+", yazim)))


def _terimdeki_ad(ad, yazim):
    """Ad terimin içinde geçiyor ya da terimde ondan türemiş bir sözcük var."""
    return ad != yazim and (ad in yazim or _ad_turemis_mi(ad, yazim))


def _adlara_uyan(cevaplar, yazim, adlar, kokler=None):
    """Karşılık cevaplarından terimde geçmeyen bir adı taşıyanlar atılır; terimde geçen (ya da türemiş bicimi geçen:
    Troy / "Trojan War") adı taşıyanlar varsa yalnız onlar kalır (yoksa hepsi kalır). Türemiş bicimde ad Türkçe ekle
    bitişebilir ("Truvalı"). "kokler": yalnızca türetmede aranan Latin harfli adlar (Troy -> Truva); dışarıdaki ad
    sayılmazlar."""
    def gecer(ad, metin, bitisik=False):
        return re.search(r"(?<!\w)" + re.escape(temizlik.tr_kucuk(ad)) + ("" if bitisik else r"(?!\w)"),
                         temizlik.tr_kucuk(metin))
    disari = [h for y, h in adlar.items() if y != yazim and not _terimdeki_ad(y, yazim) and not gecer(h, yazim)]
    kalan = [d for d in cevaplar if not any(gecer(h, d) for h in disari)]
    ic = [(h, ad in yazim) for ad, h in {**(kokler or {}), **adlar}.items() if _terimdeki_ad(ad, yazim)]
    tutan = [d for d in kalan if all(gecer(h, d, bitisik=not aynen) for h, aynen in ic)]
    return tutan if ic and tutan else kalan


def _karsiliklari_tamamla(kararlar, partiler, sor, oy, isci, baslik="", ornek_bul=None, dil="", latin_zorunlu=True,
                          okunus_gecerli=False, bilinen_adlar=None):
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
    # Bilinen adlar (sözlükteki ve bu turda "ad" denenler): terimin içinde geçen ad aynen kalır (ハニー姫 -> "Prenses
    # Honey", "Bal Prensesi" değil); terimde geçmeyen bir adı taşıyan cevap oya girmez (部長 -> "Kulüp başkanı Musashi").
    adlar = dict(bilinen_adlar or {})
    for k, liste in enumerate(kararlar):
        for c in liste:
            if (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(partiler[k]) and c.get("t") == "name"
                    and isinstance(c.get("l"), str) and c["l"].strip()):
                # gerçek dünyadan adın hedef dildeki yerleşik biçimi (Troy -> "Troya") sözlüğe giren yazımdır
                yerlesik = c.get("e") if c.get("real") is True and isinstance(c.get("e"), str) and c["e"].strip() else ""
                adlar.setdefault(partiler[k][c["i"] - 1]["yazim"], (yerlesik or c["l"]).strip())
    adlar = {y: h for y, h in adlar.items() if len(y) >= 2 and len(h) >= 3}

    # Latin harfli kaynağın adının "l"si yoktur: türeyen sözcüğün (Trojan) kökü olarak adın yerleşik bicimi (söylence ya
    # da gerçek dünyadan: Troy -> "Truva") ya da yazımı verilir; yoksa karşılık "Troyalı" çıkıyordu (gerçek test R)
    kokler = {}
    for k, liste in enumerate(kararlar):
        for c in liste:
            if not (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(partiler[k]) and c.get("t") == "name"):
                continue
            yazim_ = partiler[k][c["i"] - 1]["yazim"]
            if temizlik.latin_disi(yazim_) or yazim_ in adlar or len(yazim_) < 4:
                continue
            # Yerleşik biçim adın sözlükteki yazımıdır (England -> İngiltere; model "real" demese de: gerçek test S'de
            # "England Kilisesi" çıktı); söylence adının harf çevirisi (Sparta -> Isparta) alınmaz, hedef_belirle gibi.
            e = c.get("e").strip() if isinstance(c.get("e"), str) else ""
            if e and c.get("myth") is True and harf_cevirisi_mi(yazim_, e):
                e = ""
            kokler[yazim_] = e or yazim_
    icindeki = [{y: h for y, h in {**kokler, **adlar}.items() if _terimdeki_ad(y, a["yazim"])} for _, a in sorulacak]
    ogeler = [{"i": n, "s": a["yazim"], **({"m": c["m"]} if c.get("m") else {}),
               **({"hint": EKLI_KARSILIK_IPUCU.replace("TARGET language", dil or "the target language")}
                  if a.get("ekli") else {}),
               **({"hint": KOD_ADI_IPUCU.replace("TARGET language", dil or "the target language").replace(
                   "NAME", kod_adi_adi(kod_adi_latin(a["yazim"], c.get("l")) if temizlik.latin_disi(a["yazim"])
                                       else a["yazim"]))}
                  if c.get("t") == "codename" else {}),
               **({"names": icindeki[n - 1]} if icindeki[n - 1] else {}),
               **({"hint": COGUL_IPUCU} if c.get("t") == "people" and cogul_gibi(a["yazim"]) and not a.get("ekli") else {}),
               "lines": (ornek_bul(a["yazim"]) if ornek_bul else []) or a["ornek"]}
              for n, (c, a) in enumerate(sorulacak, 1)]

    def sorul(ogeler_, kez):
        """Öğeler KARSILIK_PARTI'lik partilere bölünür (aynı aileden olanlar aynı partide), bütün partiler ve oylar
        aynı anda sorulur: çok adlı yapımda (141 aday) tek büyük istek yarım dakika sürüyor, çeviri onu bekliyordu.
        Her oyun yanıtı, öğelerin bu çağrıdaki sıra numaralarıyla ("i") tek listede döner."""
        partiler_ = _karsilik_partileri(ogeler_)

        def bir(parti):
            yanit = sor({"title_hint": baslik, "target_language": dil or "",
                         "items": [dict(o, i=n) for n, o in enumerate(parti, 1)]}) or {}
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
        tum = _adlara_uyan(tum, yazim, adlar, kokler)
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
    "askerî öğrenci" / "askeri öğrenci"). Kazananın en sık yazılan bicimi alınır. Noktalı ve noktasız i de tek harf
    sayılır: Türkçe küçük harf kuralı başka hedef dilde "Inspector" ile "inspector"ı ayrı cevap yapardı."""
    return re.sub(r"[\s-]+", "", temizlik.tr_kucuk(deger).translate(SAPKASIZ).replace("ı", "i"))


def _yazimi_yenile(deger, degisen):
    """Karşılıktaki eski ad yazımları yenisiyle ("Gandamu pilotu" -> "Gundam pilotu"); tam sözcük, harf büyüklüğü
    gözetilmeden."""
    if not isinstance(deger, str):
        return deger
    for eski, yeni in sorted(degisen.items(), key=lambda x: -len(x[0])):
        deger = re.sub(r"(?<![\w])" + re.escape(eski) + r"(?![\w])", yeni, deger, flags=re.I)
    return deger


def karsiliklari_uygula(oneriler):
    """Karşılık önerilerini kararlara "k" alanı olarak yazar; Latin dışı kaynakta yazım isteğinden sonra gelen "l" ile
    aynı olan (İngilizce) alınmaz. Ek oyla da birleşmeyen karar "belirsiz" işaretlenir: kayda geçmez."""
    for c, deger, latin_kaynak, ayni in oneriler:
        if deger is None:
            # ayni: bütün cevaplar Latin yazımın kendisiydi; yazım isteği "l"yi değiştirmediyse karar budur.
            # Kod adı belirsiz kalmaz: karşılığı birleşmezse duyulduğu gibi yazımıyla girer.
            # Çok sözcüklü anlamlı ad da: karşılığı birleşmezse kaynaktaki gibi kalır (minas tirith).
            if (c.get("t") != "codename" and not c.get("anlamli_ad")
                    and not (ayni and str(c.get("l") or "").strip().lower() == ayni)):
                c["belirsiz"] = True
        elif latin_kaynak or deger.lower() != str(c.get("l") or "").strip().lower():
            c["k"] = deger


# Belirleyici: önündeyse söz bir şeyin adı değil anlamlı bir sözdür ("four farthings", "my precious"). "the" yalnız
# lakapta sayılır (the Enemy): yer adı da "the" alır (the Shire, the Southfarthing, the Anduin; LOTR'da Southfarthing
# böyle çevrilip Westfarthing kalıyordu). "her" ve "that" adın önünde de geçer ("tell her Strider..."), sayılmaz.
BELIRLEYICI = re.compile(r"(?:^|[^\w'’])(?:a|an|this|these|those|my|your|his|our|their|its|one|two|three|four|five|"
                         r"six|seven|eight|nine|ten|eleven|twelve|\d+)\s+$", re.I)
TANIMLIK = re.compile(r"(?:^|[^\w'’])the\s+$", re.I)
KAYNAKTA_KALAN_TURLER = ("name", "epithet", "descriptive", "codename")


def anlamli_cok_sozcuklu_ad(c, yazim, tek_adlar):
    """Latin harfli kaynakta birden çok ya da tireyle birleşmiş sözcükten kurulan kurgusal ad anlamlı sözdür, karşılığı
    sorulur (Bag End -> Çıkın Çıkmazı, Middle-earth -> Orta Dünya; kullanıcının kararı, 19.11.2026: birden çok
    sözcüklü anlamlı söz çevrilir). Sınıflandırma bunlara "tr" vermiyor, ad karşılık isteğine gitmiyordu: S4'te
    "Bag End", "Middle-Earth" kaldı. Sözcüklerinden biri kendi başına ad ya da unvan olan kişi adı (Frodo Baggins,
    Warden Hale, Sackville-Baggins) sorulmaz."""
    yazim = (yazim or "").strip()
    if (c.get("t") != "name" or c.get("real") is True or c.get("myth") is True or temizlik.latin_disi(yazim)
            or not re.fullmatch(r"[^\W\d_]+(?:[ -][^\W\d_]+)+", yazim)):
        return False
    sozcukler = [w.lower() for w in re.split(r"[ -]", yazim) if len(w) >= 3]
    return bool(sozcukler) and all(w not in tek_adlar for w in sozcukler)


def isletme_adi(c, yazim):
    """Gerçek dünyada geçen eserde uydurma yer, işletme ya da marka adı kaynaktaki gibi kalır (Eagle Point, Motel
    America, Buffalo Burger): izleyici o adı duyar, tabelada görür. "Çok sözcüklü anlamlı ad çevrilir" kuralı bunları
    da çeviriyor, model türü koşudan koşuya değiştirdiği için karşılık da oynuyordu (American Gods, 9 koşu: Eagle
    Point 5 kez kaldı, 4 kez "Kartal Noktası"; Buffalo Burger dört ayrı karşılık). Kararı model "biz" alanıyla verir
    (oyların çoğu); uydurma dünyadaki anlamlı adlar değişmez (Bag End, Middle-earth)."""
    return (c.get("biz") is True and c.get("t") in ("name", "descriptive") and c.get("real") is not True
            and c.get("myth") is not True and not temizlik.latin_disi((yazim or "").strip())
            and bool(re.search(r"[A-Z]", yazim or "")))


def kaynaktaki_gibi_kalir(c, yazim, metin):
    """Latin harfli kaynakta tek sözcüklük kurgusal özel ad kaynaktaki gibi kalır (Strider, Rivendell, Mirkwood,
    Westfarthing): izleyici filmde o adı duyar; kitap çevirisinin adı (Yolgezer, Ayrıkvadi) yazılmaz, karşılığı
    sorulmaz (kullanıcının kararı, 19.11.2026). Birden çok ya da tireyle birleşmiş sözcükten kurulan anlamlı söz
    çevrilir (dark lord -> Karanlıklar Efendisi, Middle-earth -> Orta Dünya). Kaynakta çoğunlukla belirleyiciyle
    geçen tek sözcük de anlamlı sözdür (four farthings; lakapta the Enemy): çevrilir. Gerçek dünyadan ve söylenceden
    ad bugünkü kuralla (Troy -> Truva); türün kararını model koşudan koşuya değiştirdiği için (Mirkwood bir koşuda ad,
    öbüründe tanım) karar sözcük sayısına ve kaynağa bağlıdır."""
    yazim = (yazim or "").strip()
    if (c.get("t") not in KAYNAKTA_KALAN_TURLER or c.get("real") is True or c.get("myth") is True
            or not re.fullmatch(r"[^\W\d_]{2,}", yazim) or temizlik.latin_disi(yazim)):
        return False
    toplam = belirli = 0
    for m in re.finditer(r"(?<![\w'’-])" + re.escape(yazim) + r"(?![\w-])", metin, re.I):
        toplam += 1
        once = metin[max(0, m.start() - 20):m.start()]
        belirli += bool(BELIRLEYICI.search(once) or (c.get("t") == "epithet" and TANIMLIK.search(once)))
    return belirli * 2 < toplam or toplam == 0


def yazim_istemi(ayar):
    return """Each item is a name or term written in Japanese script in the subtitles of one film or series ("title_hint" names it), with lines where it occurs. Give its spelling in Latin letters ("l") as the official English release of this work writes it. A word taken from English or another European language is written with that word's own spelling, not letter by letter (シャーロット -> Charlotte, ジョージ -> George); a Japanese word or name is written in plain Hepburn without macrons, the way it sounds, and a Japanese full name in the order it is heard, family name first (a non-Japanese name written in katakana, such as ロイド・フォージャー, keeps the order of its parts: "Loid Forger"). Never give an English translation or localised name, even when the English release uses one (ホシ is "Hoshi", never "Star"): the viewers hear the original audio and must read the name they hear.
Return ONLY this JSON object: {"items": [{"i": 1, "l": "..."}]}"""


YAZIMLI = ("name", "codename", "term")


def _bilesik_adlari_esitle(kararlar, partiler, sozluk):
    """Ortası noktalı katakana tam adın (ロイド・フォージャー) parçaları sözlükte ya da bu turda ayrı ad olarak resmî
    yazımla karara bağlandıysa tam ad o parçalardan kurulur: üç oyun üçü "Roid Fōjā" dese de parçalar "Loid" ve
    "Forger" ise ad "Loid Forger"dır. Parçası bilinmeyen tam ad olduğu gibi kalır."""
    # Parça tek sözcüktür: ユーリ için yazım isteği "Yuri Briar" dediyse o parça yazımı sayılmaz ("Yuri Briar Briar" çıktı)
    bilinen = {y: g["hedef"] for y, g in sozluk.girdiler.items() if g.get("tur") == "name" and g.get("hedef")
               and not temizlik.latin_disi(g["hedef"]) and len(g["hedef"].split()) == 1}
    for k, liste in enumerate(kararlar):
        for c in liste:
            if (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(partiler[k]) and c.get("t") == "name"
                    and isinstance(c.get("l"), str) and len(c["l"].split()) == 1
                    and not _mekanik_mi(partiler[k][c["i"] - 1]["yazim"], c["l"])):
                bilinen.setdefault(partiler[k][c["i"] - 1]["yazim"], c["l"].strip().translate(MAKRONSUZ))
    for k, liste in enumerate(kararlar):
        for c in liste:
            if not (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(partiler[k]) and c.get("t") == "name"):
                continue
            yazim = partiler[k][c["i"] - 1]["yazim"]
            parcalar = [p for p in re.split(r"[・=＝]", yazim) if p]
            if len(parcalar) < 2 or not all(re.fullmatch(r"[ァ-ヺー]+", p) for p in parcalar):
                continue
            eski = str(c.get("l") or "").translate(MAKRONSUZ).split()
            yeni = []
            for n, parca in enumerate(parcalar):
                eski_parca = eski[n] if len(eski) == len(parcalar) else ""
                if parca in bilinen and (not eski_parca or _iskelet_yakinligi(temizlik.kana_latin(parca) or "",
                                                                             eski_parca) >= 0.5):
                    yeni.append(bilinen[parca])
                elif eski_parca:
                    yeni.append(eski_parca)
                else:
                    yeni = []
                    break
            if yeni and " ".join(yeni) != " ".join(eski):
                c["l"] = " ".join(yeni)


def kaynak_sirasi(yazim, latin):
    """Ortası noktalı katakana tam adın (ヨル・ブライア) Latin yazımı kaynaktaki parça sırasına dizilir: oylardan biri
    "Yor Briar", öteki "Briar Yor" derse ikisi aynı oydur (gerçek test 10'da oy birleşmedi, ad "Yoru Buraia" kaldı).
    Sözcükler parçaların sesine göre eşlenir; eşleşme kesin değilse yazım olduğu gibi kalır."""
    parcalar = [p for p in re.split(r"[・=＝]", yazim) if p]
    sozcukler = latin.split()
    if (len(parcalar) < 2 or len(parcalar) != len(sozcukler) or len(parcalar) > 4
            or not all(re.fullmatch(r"[ァ-ヺー]+", p) for p in parcalar)):
        return latin
    okunuslar = [temizlik.kana_latin(p) or "" for p in parcalar]
    from itertools import permutations
    puan = lambda sira: sum(_iskelet_yakinligi(o, w) for o, w in zip(okunuslar, sira))
    en_iyi = max(permutations(sozcukler), key=puan)
    return " ".join(en_iyi) if puan(en_iyi) > puan(sozcukler) else latin


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
            # Yerleşik biçimi olan gerçek ad (アメリカ -> Amerika) sorulmaz; kurgusal adın "e"si sözlüğe girmez (izleyici
            # özgün sesi duyar), yazımı yine sorulur: ブリタニア'nın "e"si "Britanya" iken yazımı "Buritania" kaldı
            # (gerçek test W, Code Geass)
            if ((c.get("t") in YAZIMLI and not (c.get("e") and c.get("t") != "codename" and c.get("real") is True)
                 and re.search(r"[ぁ-んァ-ヺ]", yazim)
                    or c.get("t") in ("title", "epithet") and OKUNUS_KATAKANA.search(yazim))
                    and (not latin or _mekanik_mi(yazim, latin))):
                # okunuşu katakanayla verilmiş unvanın yazımı da sorulur: ödünç sözcükse (ハンドラー) duyulduğu gibi kalır
                eksik.append((c, partiler[k][c["i"] - 1]))
    if not eksik:
        return 0
    yuk = {"title_hint": baslik,
           "items": [{"i": n, "s": a["yazim"], "lines": a["ornek"]} for n, (_, a) in enumerate(eksik, 1)]}
    with ThreadPoolExecutor(max_workers=max(1, isci)) as havuz:
        yanitlar = [g.result() or {} for g in [havuz.submit(sor, yuk) for _ in range(max(1, oy))]]
    for n, (c, a) in enumerate(eksik, 1):
        degerler = [kaynak_sirasi(a["yazim"], str(x.get("l") or "").strip().translate(MAKRONSUZ))
                    for y in yanitlar for x in y.get("items") or [] if isinstance(x, dict) and x.get("i") == n]
        # Noktasız tek katakana ad tek sözcüktür: ユーリ -> "Yuri Briar" (tam adı vermiş) parçanın yazımı değildir.
        tek_ad = c.get("t") == "name" and re.fullmatch(r"[ァ-ヺー]+", a["yazim"])
        resmi = Counter(d for d in degerler if d and not temizlik.latin_disi(d) and len(d) <= 60
                        and not _mekanik_mi(a["yazim"], d) and not (tek_ad and " " in d))
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
    if latin and _KANJILI.search(yazim) and OKUNUS_KATAKANA.search(yazim) and not okunus_adi_mi(yazim, latin):
        pass                    # okunuşu katakanayla verilmiş ad: yazım o okunuş değilse (婆(ババア) -> "Dash Baba") uyarlamadır
    elif not latin or not _KANJILI.search(yazim) or "(" in yazim or "（" in yazim:
        return
    else:
        okunuslar = [c["r"].strip() for c in oylar if isinstance(c.get("r"), str) and _KANA_OKUNUS.fullmatch(c["r"].strip())]
        okunus = temizlik.kana_latin(re.sub(r"[・\s]", "", Counter(okunuslar).most_common(1)[0][0])) if okunuslar else ""
        if not okunus or _iskelet_yakinligi(okunus, latin) >= 0.5:
            return
    secilen["t"] = "descriptive"
    secilen.pop("l", None)
    secilen.pop("e", None)
    secilen.setdefault("m", f'a name that means "{latin}"')


def _kod_adi_duyuldugu_gibi(secilen, yazim, oylar):
    """Kanji ya da hiraganayla yazılmış kod adı duyulduğu gibi yazılır (kullanıcının kararı, 18.11.2026): Latin yazım
    okunuşun sesi değilse İngilizce yayındaki çevirisidir (黄昏(たそがれ) -> "Twilight"); yerine okunuş yazılır
    ("Tasogare"). Okunuş kaynakta parantezle verilmişse o, verilmemişse modelin okunuşu ("r") alınır."""
    latin = secilen.get("l")
    if (not latin or not re.search(r"[一-龥々ぁ-ん]", yazim.split("(")[0].split("（")[0])
            or re.search(r"[(（][ァ-ヺー・]+[)）]", yazim) or kod_adi_adi(kod_adi_latin(yazim, latin))):
        return              # okunuşu katakanayla verilmiş (梟(ストリクス)) ya da genel sözcüklü kod adı: yazımı kalır
    m = re.search(r"[(（]([ぁ-ゖー]+)[)）]", yazim)
    if m:
        kana = m.group(1)
    else:
        okunuslar = [c["r"].strip() for c in oylar if isinstance(c.get("r"), str) and _KANA_OKUNUS.fullmatch(c["r"].strip())]
        kana = re.sub(r"[・\s]", "", Counter(okunuslar).most_common(1)[0][0]) if okunuslar else ""
        if kana and re.search(r"[ぁ-ん]", yazim) and not _KANJILI.search(yazim):
            kana = yazim                # yalnız hiragana: okunuş yazımın kendisi
    okunus = temizlik.kana_latin(kana) if kana else ""
    if okunus and _iskelet_yakinligi(okunus, latin) < 0.5:
        secilen["l"] = okunus[:1].upper() + okunus[1:]


def _ses_anahtari(metin):
    """Ad sırası karşılaştırması için kaba ses anahtarı: uzun ünlü yazımları teklenir (kyouhei = Kyōhei = Kyohei)."""
    m = re.sub(r"[^a-z]", "", metin.translate(MAKRONSUZ).lower())
    for uzun, kisa in (("ou", "o"), ("oo", "o"), ("uu", "u"), ("aa", "a"), ("ii", "i"), ("ee", "e")):
        m = m.replace(uzun, kisa)
    return m


def _ad_sirasi_duyuldugu_gibi(secilen, yazim, oylar):
    """Kanjiyle yazılmış Japonca tam ad duyulduğu sırayla yazılır (kullanıcının kararı, 18.11.2026): önce soyadı
    ("Kageyama Shigeo", "Kamuro Shinji"). Oylar adı Batı sırasıyla verdiyse ("Shigeo Kageyama") ve iki sözcüğün
    ters sırası okunuşun sesiyse sıra çevrilir. Okunuş modelin "r" alanından, yoksa kaynaktaki parantezden alınır;
    katakana Batılı adlar (ロイド・フォージャー) kendi sırasında kalır."""
    if isinstance(secilen.get("l"), str):
        secilen["l"] = duyulan_sira(yazim, secilen["l"], oylar)


def duyulan_sira(yazim, latin, oylar):
    """_ad_sirasi_duyuldugu_gibi'nin yazım üzerinde çalışan hali; yerleşik biçimden gelen hedefe de uygulanır."""
    taban = yazim.split("(")[0].split("（")[0]
    if len(latin.split()) != 2 or not _KANJILI.search(taban):
        return latin
    okunuslar = [c["r"].strip() for c in oylar if isinstance(c.get("r"), str) and _KANA_OKUNUS.fullmatch(c["r"].strip())]
    kana = re.sub(r"[・\s]", "", Counter(okunuslar).most_common(1)[0][0]) if okunuslar else ""
    if not kana:
        m = re.search(r"[(（]([ぁ-ゖー]+)[)）]", yazim)
        kana = m.group(1) if m else ""
    okunus = _ses_anahtari(temizlik.kana_latin(kana) or "") if kana else ""
    once, sonra = latin.split()
    if okunus and okunus == _ses_anahtari(sonra + once) != _ses_anahtari(once + sonra):
        return f"{sonra} {once}"
    return latin


def japon_ad_sirasi(sozluk):
    """Okunuşu bilinmeyen kanjili tam ad (神室真司) iki parçası sözlükte ayrı ad olarak varsa (神室 Kamuro, 真司
    Shinji) kaynaktaki sırayla o parçalardan yazılır. Değişenleri döndürür."""
    adlar = {y.split("(")[0].split("（")[0]: g["hedef"] for y, g in sozluk.girdiler.items()
             if g.get("tur") == "name" and g.get("hedef") and len(g["hedef"].split()) == 1
             and _KANJILI.search(y) and not temizlik.latin_disi(g["hedef"])}
    degisen = []
    for yazim, g in list(sozluk.girdiler.items()):
        taban = yazim.split("(")[0].split("（")[0]
        if (g.get("tur") != "name" or not g.get("hedef") or len(g["hedef"].split()) != 2 or not _KANJILI.search(taban)
                or g.get("karar") == "elle"):
            continue
        sirali = duyulan_sira(yazim, g["hedef"], [])          # okunuşu parantezde verilmiş tam ad
        if sirali != g["hedef"]:
            sozluk.girdi_yaz(yazim, hedef=sirali)
            degisen.append(yazim)
            continue
        for n in range(1, len(taban)):
            on, son = taban[:n], taban[n:]
            if on in adlar and son in adlar and {adlar[on].lower(), adlar[son].lower()} == {w.lower() for w in g["hedef"].split()}:
                yeni = f"{adlar[on]} {adlar[son]}"
                if yeni != g["hedef"]:
                    sozluk.girdi_yaz(yazim, hedef=yeni)
                    degisen.append(yazim)
                break
    return degisen


def _cogunluk(yanitlar, yazimlar=None):
    """Aynı adaylar için verilmiş birkaç yanıtı tek yanıta indirir: tür ve her alan için çoğunluğun dediği.
    Yerleşik bicim ("e") ancak yanıtların yarısından çoğu bir yerleşik bicim verdiyse alınır."""
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
                # Oyların çoğu yerleşik bicimi ("e") aynı sesin resmî yazımı diye verdiyse o alınır: üç oyun ikisi
                # "Furankī Furankurin" dese de üçü "e" alanında "Franky Franklin" diyorsa ad budur. Ses tutmayan
                # yerleşik bicim çeviridir (ホシ -> "Star"), alınmaz.
                yerlesik = _sesi_tutan_yerlesik(yazimlar[i], _degerler(liste, "e", yazimlar[i]), len(yanitlar))
                # Resmî yazımların çoğunluğu açıksa ("Loid", "Loid" / "Roido") yerleşik biçim onu değiştirmez.
                en_cok = Counter(resmi).most_common(1)[0][1] if resmi else 0
                if yerlesik and any(_oy_anahtari(d) == _oy_anahtari(yerlesik) for d in resmi):
                    degerler = [d for d in resmi if _oy_anahtari(d) == _oy_anahtari(yerlesik)]
                elif resmi and en_cok > max(mekanik.values(), default=0):
                    degerler = resmi
                elif yerlesik:
                    degerler = [yerlesik]
                elif resmi and en_cok >= max(mekanik.values(), default=0):
                    degerler = resmi
            # yerleşik bicim ve sıradan anlam ancak yanıtların çoğu verdiyse alınır
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
        if tur == "codename" and yazimlar and i in yazimlar:
            _kod_adi_duyuldugu_gibi(secilen, yazimlar[i], liste)
        if (tur == "name" and yazimlar and i in yazimlar and not temizlik.latin_disi(yazimlar[i])
                and re.search(r"[A-Z][\w'’-]*\s+(?:[a-z]+\s+)+[A-Z]", yazimlar[i])
                and sum(1 for c in liste if c.get("real") is True) * 2 <= len(yanitlar)):
            # Sıradan bir bağlaçla kurulan ad ("Boromir of Gondor", "Elrond of Rivendell") opak değildir: içindeki adlar
            # kalır, gerisi çevrilir ("Gondorlu Boromir"). Gerçek kişinin adı (Leonardo da Vinci) olduğu gibi kalır.
            secilen["t"] = tur = "descriptive"
        if tur == "name" and yazimlar and i in yazimlar:
            _ceviriyse_tanima_cevir(secilen, yazimlar[i], liste)
            _ad_sirasi_duyuldugu_gibi(secilen, yazimlar[i], liste)
        if sum(1 for c in liste if c.get("real") is True) * 2 > len(yanitlar):
            secilen["real"] = True
        if sum(1 for c in liste if c.get("myth") is True) * 2 > len(yanitlar):
            secilen["myth"] = True
        if sum(1 for c in liste if c.get("biz") is True) * 2 > len(yanitlar):
            secilen["biz"] = True
        if sum(1 for c in liste if c.get("o") is True) * 2 > len(yanitlar):
            secilen["o"] = True
        # Anlam notu serbest metindir, oylanamaz: yanıtların en az yarısı not verdiyse çoğunluğun türünü
        # söyleyenlerden ilki alınır.
        if tur in KARSILIKLI and yazimlar and i in yazimlar and _ceviri_oyu(liste, tur, yazimlar[i]):
            secilen["ceviri_oyu"] = True
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
            gunluk.hata(dosya, f"{tur} yanıtı beklenen bicimde değil (JSON nesnesi, \"items\" listesi), deneme {deneme}")
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
    "Harbiyeli Birliği Sınıfı", "104. Harbiyeli…" değil): sözlük her geçtiği yerde aynı bicimi dayatır, sayı ise
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


def _latin_ad(sozluk, ad, metin=""):
    """Kaynaktaki adın sözlükteki Latin yazımı (girdi ya da konuşmacı etiketi), yoksa metinde okunuşuyla yazıldığı
    yerden okunuşu; o da yoksa boş."""
    g = sozluk.girdiler.get(ad) or {}
    bulunan = (g.get("hedef") if g.get("tur") in ("name", "codename", "epithet") else "") or sozluk.adlar.get(ad, "")
    if not bulunan:                 # etikette okunuşuyla yazılmış ad (郷田(ごうだ)武蔵(むさし)): okunuşu
        for etiket in list(sozluk.adlar) + [metin]:
            m = re.search(re.escape(ad) + r"[(（]([ぁ-ゖー]+)[)）]", etiket)
            if m and temizlik.kana_latin(m.group(1)):
                okunus = temizlik.kana_latin(m.group(1))
                return okunus[:1].upper() + okunus[1:]
    return bulunan


def _addan_kalip(hedef, latin_ad):
    """"ad + unvan" teriminin karşılığından unvan kalıbı: karşılık adı da taşıyorsa ad yerine {ad} konur ("Başkan
    Musashi" -> "Başkan {ad}", "Prenses Anya" -> "Prenses {ad}"); taşımıyorsa unvan addan önce sayılır."""
    if latin_ad:
        m = re.search(r"(?<!\w)" + re.escape(latin_ad) + r"(?!\w)", hedef, re.I)
        if m:
            kalan = (hedef[:m.start()] + "{ad}" + hedef[m.end():]).strip()
            if kalan != "{ad}":
                return kalan[:1].upper() + kalan[1:]
    return hedef[:1].upper() + hedef[1:] + " {ad}"


def _kaliptan_adi_cikar(kalip, latin_ad):
    """Kalıp adın kendisini de taşıyorsa ("Prenses Anya {ad}": karşılık "Prenses Anya" idi) ad çıkarılır."""
    if not latin_ad:
        return kalip
    temiz = " ".join(re.sub(r"(?<!\w)" + re.escape(latin_ad) + r"(?!\w)", " ", kalip, flags=re.I).split())
    if temiz == " ".join(kalip.split()) and "{ad}" in kalip:
        # ad kalıpta başka harflerle yazılmış ("Marki Baltho {ad}", sözlükte "Barto": gerçek test V): sesi aynı sözcük
        # adın kendisidir, çıkarılır
        temiz = " ".join(w for w in kalip.split()
                         if w == "{ad}" or not (w[:1].isupper() and len(w) >= 3
                                                and _iskelet_yakinligi(latin_ad.lower(), w.lower()) >= 0.75))
    return temiz if temiz.replace("{ad}", "").strip() else kalip


UNVAN_NITELEYICI = {"High", "Grand", "Great", "Chief", "Deputy", "Vice", "Head", "Senior", "Junior", "Supreme", "Royal",
                    "Imperial", "Honored", "Honoured", "Honorable", "Honourable", "Provincial", "Acting", "Assistant",
                    "First", "Second", "Under"}


def _kalip_ikinciyi_tasir(kalip, ikinci):
    """İki sözcüklü unvan girdisinin kalıbı ikinci sözcüğü (ya da onun çevirisini) unvanın parçası sayıyor mu."""
    if not kalip:
        return False
    if "{ad}" in kalip and re.search(r"(?<!\w)" + re.escape(ikinci) + r"(?!\w)", kalip):
        return True
    govde = kalip.replace("{ad}", " ").split()
    return "{ad}" in kalip and len(govde) > 1


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
                and sozcukler[1] not in bilinen_unvan and sozcukler[0] not in IYELIK     # "Your Grace" hitaptır
                # ikinci sözcük metinde küçük harfle de geçiyorsa ad değil sıradan sözcüktür: "Grand Maester"
                # (maesters), "Red Leader" (leader) unvan + ad diye bölünmez
                and not re.search(r"(?<![\w'’-])" + re.escape(sozcukler[1].lower()) + r"(?:s|es)?(?![\w'’-])", tum_metin)
                and (sozluk.girdiler.get(sozcukler[1]) or {}).get("tur") in ("name", None)
                # "High Counsellor", "Grand Maester", "Chief Inspector": baştaki niteleyici tek başına unvan değildir
                and sozcukler[0] not in UNVAN_NITELEYICI
                # kalıp ikinci sözcüğü de taşıyorsa ("Saygıdeğer Diplomat {ad}", "Büyükelçi Xandem {ad}") unvan iki
                # sözcüğün tamamıdır; adsız kalıp birden çok sözcükse ikinci sözcük çevrilmiştir ("Kardeş Gün {ad}"):
                # bölünmez (gerçek test S, Foundation)
                and not _kalip_ikinciyi_tasir(g.get("kalip") or g.get("hedef") or "", sozcukler[1])):
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
        latin_ad = _latin_ad(sozluk, ad, tum_metin)
        kalip = _kaliptan_adi_cikar(g.get("kalip"), latin_ad) if g.get("kalip") else _addan_kalip(g["hedef"], latin_ad)
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


def harf_ciftlerini_birlestir(yazimlar, metin):
    """Yalnız harf büyüklüğü farklı yazımlardan ("Rivendell" / "rivendell") biri kalır: kaynakta harfi harfine en çok
    geçen, eşitse büyük harfle başlayan. Adları küçük harfle yazan kaynakta keşif bir yanıtta kaynaktaki yazımı,
    ötekinde düzeltilmişini veriyordu; ikisi ayrı girdi oluyor, karşılıkları çelişiyordu ("Barad-dur" / "Barad-Dur",
    "Ringwraiths" -> "yüzük tayfı" / "ringwraiths" -> "ringwraiths"; gerçek test 28 ve 33, LOTR: 175 girdinin 37'si
    çift). Sıra korunur (ilk geçtiği yer)."""
    gruplar = {}
    for y in yazimlar:
        gruplar.setdefault(_anahtar(y), []).append(y)

    def puan(y):
        return (len(re.findall(r"(?<![\w'’-])" + re.escape(y) + r"(?![\w-])", metin)), y[:1].isupper())
    return [max(grup, key=puan) for grup in gruplar.values()]


def _kesif_sayisi(yazim, metin):
    """Keşfin bulduğu yazım kaynakta kaç kez geçiyor. Latin harflide büyük-küçük harf ayrılmaz ve küçük harfli tek
    sözcüğün çoğulu da sayılır: model en kısa bicimi veriyor ("wraith"), kaynak 1 kez tekil 2 kez çoğul yazınca
    tam eşleşme 1 sayıyor, sözcük "en az iki kez" koşulunda eleniyordu."""
    if not yazim:
        return 0
    if not yazim.isascii():
        return metin.count(yazim)
    desen = re.escape(yazim) + (r"(?:e?s)?" if re.fullmatch(r"[a-z][a-z'-]*", yazim) else "")
    return len(re.findall(r"(?<![\w'’-])" + desen + r"(?![\w-])", metin, re.I))


def _kesif_yazimi(c):
    """Keşif öğesi: düz yazım; eski bicim ({"s": ...}) de okunur."""
    c = c.get("s") if isinstance(c, dict) else c
    return c.strip() if isinstance(c, str) else ""


def ortusenleri_esitle(sozluk):
    """"Dark Lord" ve "Dark Lord Sauron" ayrı ayrı sorulur; ikincisi birincinin karşılığı + ad olarak yazılır ki aynı
    lakap iki bicimde çıkmasın ("Karanlıklar Efendisi" / "Karanlık Lord Sauron")."""
    for uzun in [k for k in sozluk.terimler if " " in k and k.isascii()]:
        for kisa, hedef in list(sozluk.terimler.items()):
            kalan = uzun[len(kisa) + 1:] if uzun.startswith(kisa + " ") else ""
            if (not kalan or hedef == kisa or " " in kalan or kalan not in sozluk.degismez
                    or (sozluk.girdiler.get(uzun) or {}).get("karar") == "elle"):
                continue
            yeni = f"{hedef} {kalan}"
            if sozluk.terimler[uzun] != yeni and uzun in sozluk.girdiler:
                sozluk.girdi_yaz(uzun, hedef=yeni)
            break


def _tekil_bicimleri(soz):
    """Latin harfli sözcüğün olası tekilleri (İngilizce çoğul ekleri): dwarves -> dwarf/dwarfe, men -> man, ponies ->
    pony, wraiths -> wraith."""
    s = soz.lower()
    aday = []
    if s.endswith("ves"):
        aday += [s[:-3] + "f", s[:-3] + "fe"]
    if s.endswith("ies"):
        aday.append(s[:-3] + "y")
    if s.endswith("men"):
        aday.append(s[:-3] + "man")
    if s.endswith("es"):
        aday.append(s[:-2])
    if s.endswith("s") and not s.endswith("ss"):
        aday.append(s[:-1])
    return [a for a in aday if len(a) >= 2]


COGUL_IPUCU = ("the item is written in the plural here: give the SINGULAR base form of its rendering, the form used for "
               "one of them; the translation adds the plural ending itself")


def cogul_gibi(yazim):
    """Latin harfli tek sözcük İngilizce çoğul görünüyor mu (dwarves, elves, orcs, men)? Halk adının karşılığı tekil
    istensin diye; "Mithrandir" gibi -s ile bitmeyen ya da "Gandalf" gibi bitmeyen yazım dokunulmaz."""
    return (bool(re.fullmatch(r"[a-z][a-z'-]+", yazim)) and len(yazim) > 3
            and (yazim.endswith(("ves", "ies", "men")) or (yazim.endswith("s") and not yazim.endswith(("ss", "us", "is")))))


def cogullari_esitle(sozluk):
    """Latin harfli kaynakta çoğul ayrı girdi olmuşsa (dwarves, wraiths) karşılığı tekilinkiyle aynı olur: çeviri çoğulu
    kendisi yapar; yoksa aynı halk "cüce" ve "cüceler" diye iki karşılık alıyordu. Yalnız halk, kavram, tanım ve lakapta."""
    turler = ("people", "term", "descriptive", "epithet")
    kucuk = {k.lower(): k for k, g in sozluk.girdiler.items()
             if g.get("tur") in turler and g.get("hedef") and not temizlik.latin_disi(k)}
    for k, g in list(sozluk.girdiler.items()):
        if g.get("tur") not in turler or not g.get("hedef") or temizlik.latin_disi(k) or g.get("karar") == "elle":
            continue
        tekil = next((kucuk[t] for t in _tekil_bicimleri(k) if t in kucuk and kucuk[t] != k), None)
        if tekil and sozluk.girdiler[tekil].get("tur") == g.get("tur") and sozluk.girdiler[tekil]["hedef"] != g["hedef"]:
            sozluk.girdi_yaz(k, hedef=sozluk.girdiler[tekil]["hedef"])


def ad_ekli_lakaplari_esitle(sozluk):
    """Bilinen bir adla bir katakana ekten kurulan lakap (ロイドマン = ロイド + マン), eki aynı biçimde taşıyan başka bir ad
    (ボンドマン -> Bondman) sözlükteyse o kalıpla kurulur: "Loidman". Esprinin kaynağı o addır; ek çevrilince ("Loid
    Adam") bağ kopuyordu. Eki taşıyan başka ad yoksa modelin kararı kalır. Dilden bağımsızdır: Latin yazımlar
    sözlükteki adlardan gelir. Değişenleri döndürür."""
    adlar = {y: g["hedef"] for y, g in sozluk.girdiler.items()
             if g.get("tur") == "name" and g.get("hedef") and re.fullmatch(r"[ァ-ヺー]{2,}", y)
             and not temizlik.latin_disi(g["hedef"]) and " " not in g["hedef"]}
    degisen = []
    for yazim, g in list(sozluk.girdiler.items()):
        if (g.get("tur") not in ("epithet", "name", "descriptive") or not re.fullmatch(r"[ァ-ヺー]{4,}", yazim)
                or g.get("karar") == "elle"):
            continue
        for ad in sorted((a for a in adlar if yazim.startswith(a) and a != yazim), key=len, reverse=True):
            ek = yazim[len(ad):]
            ek_latin = (temizlik.kana_latin(ek) or "").lower()
            if len(ek) < 2 or not ek_latin:
                continue
            ornek = next((h for a, h in adlar.items() if a.endswith(ek) and len(a) > len(ek) and a != yazim
                          and h.lower().endswith(ek_latin) and len(h) > len(ek_latin)), None)
            if ornek:
                yeni = adlar[ad] + ornek[-len(ek_latin):]
                if g.get("hedef") != yeni:
                    sozluk.girdi_yaz(yazim, hedef=yeni)
                    degisen.append(yazim)
                break
    return degisen


def baslik_terimi(yazim, cevap, baslik, hedef=""):
    """Eserin adındaki uydurma sözcük (The Witcher -> "witcher") çevrilmez, adındaki yazımıyla kalır: karşılık isteği
    "mutant canavar avcısı" yazıyordu. Sıradan sözcük bu kurala girmez: sıradan anlamı verilmiş ("w": Gladiator,
    Dune), yerleşik karşılığı başka bir sözcük ("e": dragon -> ejderha), ya da karşılığı tek sözcük (samurai ->
    samuray, hobbit -> hobbit): uydurma sözcüğü çevirmen ancak açıklayarak (birkaç sözcükle) karşılayabilir.
    "House of the Dragon"da "Dragon kokuyor" yazılmıştı. Kalacak yazımı döndürür, yoksa boş."""
    if temizlik.latin_disi(yazim) or str(cevap.get("w") or "").strip() or cevap.get("t") not in KARSILIKLI:
        return ""
    yerlesik = str(cevap.get("e") or "").strip().lower()
    if yerlesik and yerlesik not in (yazim.strip().lower(), re.sub(r"(?:es|s)$", "", yazim.strip().lower())):
        return ""
    if hedef and len(hedef.split()) < 2:
        return ""
    kok = re.sub(r"(?:es|s)$", "", yazim.strip().lower()) if " " not in yazim.strip() else yazim.strip().lower()
    for soz in re.findall(r"[A-Za-z][A-Za-z'’-]{3,}", baslik or ""):
        if soz.lower() in (yazim.strip().lower(), kok):
            return soz
    return ""


def kisaltma_adlari(sozluk):
    """Bir adın kısaltması (Cat -> Catelyn, Ned -> Eddard değil, yalnız baş harfleri tutanlar) addır: "Cat" lakap
    sanılıp "Kedi" diye çevriliyordu (Game of Thrones, gerçek test M). Sözlükteki bir adın başı olan ve çevrilmiş
    tek sözcüklü lakap/tanım adın kendisine döner. Değişenleri döndürür."""
    adlar = [y for y, g in sozluk.girdiler.items() if g.get("tur") == "name" and not temizlik.latin_disi(y)]
    degisen = []
    for yazim, g in list(sozluk.girdiler.items()):
        if (g.get("tur") not in ("epithet", "descriptive") or g.get("karar") == "elle" or " " in yazim
                or temizlik.latin_disi(yazim) or len(yazim) < 2 or g.get("hedef") in ("", yazim)):
            continue
        if any(ad != yazim and ad.startswith(yazim) and len(ad) > len(yazim) and " " not in ad for ad in adlar):
            sozluk.girdi_yaz(yazim, tur="name", hedef=yazim)
            degisen.append(yazim)
    return degisen


def katakana_parcalarini_esitle(sozluk):
    """Katakanayla yazılmış terim, onu içeren girdinin karşılığında okunuşuyla geçiyorsa o yazımı alır: ヒノカミ神楽
    "Hinokami Kagura" iken ヒノカミ "Ateş Tanrısı" kalıyordu (çeviri iki ad arasında gidip geliyordu). Değişenleri
    döndürür."""
    uzun = lambda m: re.sub(r"([aeiou])\1|ou", lambda x: x.group(0)[0], m.lower())
    degisen = []
    for yazim, g in list(sozluk.girdiler.items()):
        if (not re.fullmatch(r"[ァ-ヺー]{2,}", yazim) or g.get("karar") == "elle"
                or g.get("tur") not in ("term", "people", "descriptive", "title")):
            continue
        okunus = uzun(temizlik.kana_latin(yazim) or "")
        for baska, b in sozluk.girdiler.items():
            if baska == yazim or yazim not in baska or not b.get("hedef"):
                continue
            uyan = [w for w in re.findall(r"[A-Za-z]+", b["hedef"]) if okunus and uzun(w) == okunus]
            if uyan and g.get("hedef") != uyan[0]:
                sozluk.girdi_yaz(yazim, hedef=uyan[0])
                degisen.append(yazim)
                break
    return degisen


def tam_ad_parcalarini_esitle(sozluk):
    """Tek başına geçen katakana ad parçası (フォージャー), sözlükteki tam adların (ロイド・フォージャー -> Loid Forger)
    o parçasıyla aynı yazılır. Oylar parçayı ayrıca sorunca bir turda harf harf okunuşa gidebiliyordu ("Foja"), tam
    adlar "Forger" derken. Tam adlar parça için birden çok yazım veriyorsa çoğunluğu alınır, eşitlikte dokunulmaz.
    Elle verilen karar kalır. Harf harf okunuşta kalmış tam ad ("Yoru Buraia") parçanın resmî yazımını ("Yor")
    ezmez; parçaların hepsi resmî yazımla biliniyorsa tam ad onlardan kurulur. Değişenleri döndürür."""
    oylar, mekanik_tamlar = {}, []
    for yazim, g in sozluk.girdiler.items():
        parcalar = [x for x in re.split(r"[・=＝]", yazim) if x]
        hedef = g.get("hedef") or ""
        if (len(parcalar) < 2 or g.get("tur") != "name" or not all(re.fullmatch(r"[ァ-ヺー]{2,}", x) for x in parcalar)
                or temizlik.latin_disi(hedef) or len(hedef.split()) != len(parcalar)
                or not all(re.fullmatch(r"[^\W\d_][\w'’-]*", w) for w in hedef.split())):
            continue                         # "エース・サボ・ルフィ" -> "Ace, Sabo, Luffy" tam ad değil, ad listesidir
        if _mekanik_mi(yazim, hedef):
            mekanik_tamlar.append((yazim, parcalar))
            continue                         # harf harf okunuş kalmış tam ad ("Yoru Buraia") parçayı ezmez
        for parca, latin in zip(parcalar, hedef.split()):
            oylar.setdefault(parca, Counter())[latin] += 1
    degisen = []
    for yazim, parcalar in mekanik_tamlar:
        # Parçalarının hepsi sözlükte resmî yazımla varsa (ヨル "Yor", ブライア "Briar") tam ad onlardan kurulur.
        g = sozluk.girdiler[yazim]
        bilinen = [(sozluk.girdiler.get(p) or {}).get("hedef") or "" for p in parcalar]
        if (g.get("karar") != "elle" and all(b and len(b.split()) == 1 and not temizlik.latin_disi(b)
                                              and not _mekanik_mi(p, b) for p, b in zip(parcalar, bilinen))
                and all((sozluk.girdiler.get(p) or {}).get("tur") == "name" for p in parcalar)):
            sozluk.girdi_yaz(yazim, hedef=" ".join(bilinen))
            degisen.append(yazim)
    for parca, sayim in oylar.items():
        g = sozluk.girdiler.get(parca)
        if not g or g.get("tur") != "name" or g.get("karar") == "elle" or not g.get("hedef"):
            continue
        sira = sayim.most_common(2)
        if len(sira) > 1 and sira[0][1] == sira[1][1]:
            continue
        if g["hedef"] != sira[0][0]:
            sozluk.girdi_yaz(parca, hedef=sira[0][0])
            degisen.append(parca)
    return degisen


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
        if anahtar and (sozluk.girdiler[anahtar].get("tur") not in ("name", "codename")
                        or sozluk.girdiler[anahtar].get("karar") == "elle"):
            continue                              # unvan, terim: ad değil, karşılığı okunuşla ölçülmez; elle karar kalır
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


def unvan_govdesi(yazim):
    """"Our Lord", "my Lady" -> "Lord", "Lady": bilinen unvanın iyelikli hitabı da unvandır, terim olmaz (Kingdom of
    Heaven'da "Our Lord -> Rab" girdisi Tanrı satırlarına sızıp "Rab" ile "Tanrı"yı karıştırıyordu)."""
    return re.sub(r"(?i)^(?:our|my|your|the)\s+", "", yazim.strip())


def unvanlari_bul(dosyalar, ayar, sozluk):
    """Terim adımı kapalıyken ([sozluk] terimler = false) unvan tablosu yine çalışsın diye: adın yanında geçen
    bilinen unvanları yalnızca script bulur, modele istek gitmez."""
    bilinen, ciftler = unvan_sozcukleri(ayar), Counter()
    adaylari_bul(dosyalar, set(), unvanlar=bilinen, ciftler=ciftler)
    tum_metin = "\n".join(x.metin for _, satirlar, _ in dosyalar for x in satirlar)
    sozluk.unvanlar = sorted(set(sozluk.unvanlar) | _adla_gecen_unvanlar(sozluk, ciftler, bilinen, tum_metin))


_IFADE_KANJI = r"[一-龯々]"
_IFADE_ATILAN = re.compile(r"[“”「」『』〝〟\"…‥！？!?、。・〜♪～]")


def tekrar_eden_ifadeler(metinler, haric=()):
    """Japonca kaynakta birbirinden uzak iki üç satırda geçen ortak ifade (parçalar ayrı çevrildiği için iki ayrı
    karşılık alabiliyordu: 懇親会 "davetler" / "veli toplantısı", 鬼ごっこ "ebeleme" / "yakalamaca", aynı selam
    kalıbı iki ayrı biçimde; gerçek test 21, SPY x FAMILY 1. bölüm). Çeviriden sonra denetimde birleştirilir
    (Denetimler._ortak_ifadeler). metinler: {satır no: etiketsiz kaynak metin}.
    Adaylar: 2+ kanjili öbek (önünde katakana olabilir), iki satırda ortak 10+ harflik parça, dosyada iki kez ve
    aralarında 30+ satır olan kanji + kana sözcük. İki üç satırda geçmeyen, satırları 3'ten yakın, satırları
    birebir aynı (seçim adımının işi) ya da sözlükteki bir yazımın parçası olan aday alınmaz. {ifade: [satır no]}"""
    temiz = {i: re.sub(r"\s+", "", _IFADE_ATILAN.sub(" ", t)) for i, t in metinler.items()}
    temiz = {i: t for i, t in temiz.items() if t}
    hepsi = "\n".join(temiz[i] for i in sorted(temiz))
    adaylar = set()
    for t in temiz.values():
        adaylar.update(m.group() for m in re.finditer(r"[ァ-ヺー]*" + _IFADE_KANJI + "{2,}", t))
        for a in range(len(t)):
            for uzun in range(4, 7):
                parca = t[a:a + uzun]
                if len(parca) == uzun and re.fullmatch(_IFADE_KANJI + r"+[ぁ-ゖ]+", parca):
                    adaylar.add(("uzak", parca))
    on = defaultdict(set)
    for i, t in temiz.items():
        for a in range(len(t) - 9):
            on[t[a:a + 10]].add(i)
    for parca, idler in on.items():
        if 2 <= len(idler) <= 3:
            a, b = sorted(idler)[:2]
            m = difflib.SequenceMatcher(None, temiz[a], temiz[b], autojunk=False).find_longest_match(
                0, len(temiz[a]), 0, len(temiz[b]))
            if m.size >= 10:
                adaylar.add(temiz[a][m.a:m.a + m.size])
    haric = [re.sub(r"\s+", "", h) for h in haric if h]
    secilen = {}
    for aday in adaylar:
        uzak, ifade = aday if isinstance(aday, tuple) else (False, aday)
        idler = {i for i, t in temiz.items() if ifade in t}
        if not (2 <= len(idler) <= 3 and hepsi.count(ifade) == len(idler) and max(idler) - min(idler) >= 3):
            continue
        if uzak and not (len(idler) == 2 and max(idler) - min(idler) >= 30):
            continue
        if len({temiz[i] for i in idler}) == 1 or any(ifade in h for h in haric):
            continue
        secilen[ifade] = idler
    sonuc = {}
    for ifade, idler in sorted(secilen.items(), key=lambda kv: -len(kv[0])):
        if not any(ifade in u and idler <= v for u, v in sonuc.items()):
            sonuc[ifade] = idler
    # Kaynakta boşlukla yazılmışsa ("こんにちは あるいは こんばんは") yazım kaynaktaki gibi alınır
    yazimli = {}
    for ifade, idler in sonuc.items():
        desen = r"\s*".join(map(re.escape, ifade))
        m = re.search(desen, metinler[min(idler)])
        yazimli[" ".join(m.group().split()) if m else ifade] = sorted(idler)
    return yazimli


_LATIN_SOZ = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


def tekrar_eden_latin_satirlar(metinler, haric=()):
    """Latin harfli kaynakta sözleri uzak bir satırda yeniden geçen satır çifti (sonradan hatırlanan söz, döngüde
    yinelenen replik: "What should I call you if I were so inclined?" / "And if I was inclined, what might I call
    you?"). Parçalar ayrı çevrildiği için iki ayrı biçimde çevrilebiliyordu (gerçek test 26, American Gods 313/321).
    Aday: dosyada yalnız bu iki satırda geçen 5+ harflik bir sözcükleri var, satırlar 3+ satır uzakta, ikisi de 6+
    sözcüklük, en az 5 ve kısa satırın sözcüklerinin %60'ı ortak; birebir aynı satırlar (seçim adımının işi) ve
    sözlükteki yazımlar alınmaz. {ortak sözcükler (ilk satırdaki sırayla): [satır no, satır no]}"""
    sozler = {i: [w.lower() for w in _LATIN_SOZ.findall(t)] for i, t in metinler.items()}
    gectigi = defaultdict(set)
    for i, ws in sozler.items():
        for w in set(ws):
            gectigi[w].add(i)
    haric = {h.lower() for h in haric if h}
    sonuc = {}
    for w, idler in gectigi.items():
        if len(idler) != 2 or len(w) < 5 or w in haric:
            continue
        a, b = sorted(idler)
        A, B = set(sozler[a]), set(sozler[b])
        ortak = A & B
        if (b - a < 3 or len(sozler[a]) < 6 or len(sozler[b]) < 6 or len(ortak) < 5
                or len(ortak) < 0.6 * min(len(A), len(B)) or sozler[a] == sozler[b]):
            continue
        sonuc[" ".join(dict.fromkeys(x for x in sozler[a] if x in ortak))] = [a, b]
    return sonuc


def terimleri_cikar(dosyalar, ayar, model, sozluk, gunluk):
    """Bir yapımın terim sözlüğünü kurar ve sözlüğe işler. dosya -> kaynak dil kodu sözlüğü döndürür."""
    s = ayar["sozluk"]
    latin_zorunlu = ayar["ceviri"]["cikti_alfabesi"] == "latin"
    kod = hedef_kodu(ayar)
    ozgun = ozgun_mu(ayar)
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
    adaylar += hitap_sozcugu_adaylari(sesler, {a["yazim"] for a in adaylar}, sozluk.karar_verilenler(),
                                      dosyalar)
    for soz, k in sesler.items():
        girdi = sozluk.girdiler.get(soz)
        if k["ad_degil"] and girdi and girdi.get("tur") not in ("common", "sound") and girdi.get("karar") in ("model", "keşif"):
            sozluk.terimden_cikar(soz)
            sozluk.girdi_yaz(soz, tur="common")
    # Çığlık ve inleme sorulmaz: script tanır, duyulduğu gibi yazılır (daha önce "ses değil" denmiş olsa da).
    ciglik_eklenen = 0
    if ayar["modlar"]["ses_sozcukleri"] == "orijinal":
        for soz, k in sesler.items():
            girdi = sozluk.girdiler.get(soz) or {}
            if ciglik_mi(soz) and ses_latin(soz) and girdi.get("tur") in (None, "common"):
                sozluk.girdi_sil(soz)
                sozluk.girdi_yaz(soz, tur="sound", hedef=ses_latin(soz).capitalize(), sayi=k["sayi"], ornek=k["ornek"])
                ciglik_eklenen += 1
    ses_sorulacak = [soz for soz, k in sesler.items()
                     # bir kez geçen kısa katakana sözcük de sorulur (ガーン): yansıma sesi çoğu kez bir kez geçer;
                     # sıradan sözcük olduğuna (チワワ) model karar verir
                     if (k["ad_degil"] or k["yinelenen"] or k["sayi"] >= 2 or re.fullmatch(r"[ァ-ヺー]{2,5}", soz))
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
    if oy > 2 and sozluk.kurulmus():
        oy -= 1                          # kurulmuş sözlük: yeni adaylar azdır, bir oy az (süre ve maliyet)
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

    kaynak_metni = "\n".join(x.metin for _, satirlar, _ in dosyalar for x in satirlar)
    _harf = [k for k in kaynak_metni if k.isalpha()]
    latin_disi_kaynak = sum(not k.isascii() for k in _harf) > 0.3 * max(1, len(_harf))

    def kararlari_kur(oylar_, partiler_):
        """Sınıflandırma oylarından karar; ardından aynı anda yazım isteği ve karşılık isteği ("l" ve "k" alanları).
        (kararlar, karşılığı sorulan, yazımı sorulan)."""
        kararlar_ = _karara_bagla(oylar_, partiler_)
        _bilesik_adlari_esitle(kararlar_, partiler_, sozluk)
        # Özgün sesiyle kalan terim, onu içeren terimin karşılığında da öyle kalır (死神 Shinigami -> 死神代行 "vekil
        # Shinigami"; "vekil ölüm tanrısı" yazılıyordu).
        ozgunler = {a["yazim"]: ozgun_kalir(a["yazim"], c, ozgun) for parti, kar in zip(partiler_, kararlar_)
                    for c in kar if isinstance(c.get("i"), int) and 1 <= c["i"] <= len(parti)
                    for a in [parti[c["i"] - 1]] if ozgun_kalir(a["yazim"], c, ozgun)}
        # Tek sözcüklük kurgusal özel ad kaynaktaki gibi kalır: karşılığı sorulmaz
        if not latin_disi_kaynak:
            for parti, kar in zip(partiler_, kararlar_):
                for c in kar:
                    if (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(parti)
                            and (kaynaktaki_gibi_kalir(c, parti[c["i"] - 1]["yazim"], kaynak_metni)
                                 or isletme_adi(c, parti[c["i"] - 1]["yazim"]))):
                        c["kaynak"] = True
            tek_adlar = {parti[c["i"] - 1]["yazim"].strip().lower() for parti, kar in zip(partiler_, kararlar_)
                         for c in kar if isinstance(c.get("i"), int) and 1 <= c["i"] <= len(parti)
                         and c.get("t") in ("name", "epithet", "title") and " " not in parti[c["i"] - 1]["yazim"].strip()}
            tek_adlar |= {y.lower() for y, g in sozluk.girdiler.items() if g.get("tur") in ("name", "title") and " " not in y}
            tek_adlar |= {u.lower() for u in sozluk.unvanlar} | {u.lower() for u in unvan_sozcukleri(ayar)}
            # Scriptin bulduğu tek sözcüklük adaylar da (öteki zincirin adları): keşif zinciri ana zincirdeki "Shadow",
            # "Moon" adlarını görmüyordu, "Shadow Moon" -> "Gölge Ay" çevrildi (gerçek test 25, American Gods).
            tek_adlar |= {a["yazim"].strip().lower() for a in adaylar if re.fullmatch(r"[^\W\d_]+", a["yazim"].strip())}
            tablo = unvan_tablosu(ayar)
            for parti, kar in zip(partiler_, kararlar_):
                for c in kar:
                    if (isinstance(c.get("i"), int) and 1 <= c["i"] <= len(parti)
                            and anlamli_cok_sozcuklu_ad(c, parti[c["i"] - 1]["yazim"], tek_adlar)):
                        c["anlamli_ad"] = True
                    # Unvan + kaynaktaki gibi kalan ad: unvan tablodan çevrilir, ad kalır ("Mr. Wednesday" ->
                    # "Bay Wednesday"; karşılık isteği "Bay Çarşamba" veriyordu, gerçek test 25).
                    m = (re.fullmatch(r"([A-Z][a-z]+)\.? ([A-Z][\w'’-]+)", parti[c["i"] - 1]["yazim"].strip())
                         if isinstance(c.get("i"), int) and 1 <= c["i"] <= len(parti) and not latin_disi_kaynak else None)
                    if (m and m.group(1) in tablo and m.group(2).lower() in tek_adlar
                            and c.get("real") is not True and c.get("t") in ("name", "epithet", "descriptive", "title")):
                        c["unvanli_ad"] = tablo[m.group(1)].format(ad=m.group(2))
                        c.pop("anlamli_ad", None)
        eski_l = [(c, c["l"].strip()) for kar in kararlar_ for c in kar
                  if c.get("t") in YAZIMLI and isinstance(c.get("l"), str) and len(c["l"].strip()) >= 3]
        with ThreadPoolExecutor(max_workers=2) as h:
            y = h.submit(_yazimlari_tamamla, kararlar_, partiler_, yazim_sor, oy, isci, baslik)
            k = h.submit(_karsiliklari_tamamla, kararlar_, partiler_, karsilik_sor, oy, isci, baslik, ornek_bul,
                         dil_adi(ayar), latin_zorunlu, kod == "en",
                         {**sozluk.adlar,
                          **{y: g["hedef"] for y, g in sozluk.girdiler.items() if g.get("tur") == "name" and g.get("hedef")},
                          **ozgunler})
            (sorulan_karsilik, oneriler, ek_oylu), sorulan_yazim = k.result(), y.result()
        # Yazım isteği parçayı düzeltmiş olabilir (ヨル -> "Yor"), tam ad oyları birleşmeyip harf harf okunuşta
        # kalmışken ("Yoru Buraia"): tam ad bilinen parçalardan yeniden kurulur.
        _bilesik_adlari_esitle(kararlar_, partiler_, sozluk)
        # Karşılık isteği adın yazım isteğinden önceki harf harf okunuşunu almış olabilir: ガンダムマイスター ->
        # "Gandamu pilotu", ad sonra "Gundam" oldu (gerçek test Z). Karşılıktaki eski yazım yenisiyle değişir.
        degisen = {e: c["l"].strip() for c, e in eski_l if isinstance(c.get("l"), str) and c["l"].strip() != e}
        if degisen:
            oneriler = [(c, _yazimi_yenile(deger, degisen), lk, ayni) for c, deger, lk, ayni in oneriler]
        karsiliklari_uygula(oneriler)              # yazım isteği "l"yi değiştirmiş olabilir: ondan sonra
        return kararlar_, sorulan_karsilik, sorulan_yazim, ek_oylu

    # Keşfin bulduğu, kaynakta gerçekten en az iki kez geçen ve henüz karara bağlanmamış yazımlar. Bu aşamadaki
    # adaylar ve öteki yazımları zaten soruluyor: keşif zinciri ana zincirle aynı anda yürüyebilsin diye onlar
    # burada elenir (keşif yalnızca yazımı bulur; ne olduğuna öteki adaylar gibi oylanan sınıflandırma karar verir).
    tum_metin = "\n".join(x.metin for _, satirlar, _ in dosyalar for x in satirlar)
    diller, yeni, yeni_anahtarlar = {}, {}, set()
    harfler = [k for k in temizlik.ETIKET.sub("", tum_metin) if k.isalpha() and k.isascii()]
    buyuk_harfli = len(harfler) > 200 and sum(k.isupper() for k in harfler) > 0.8 * len(harfler)
    sorulan = {_anahtar(y) for a in adaylar for y in [a["yazim"], *a["bicimler"]]}
    karar_verilen = sozluk.karar_verilenler()
    for (ad, _, _), yanit in zip(dosyalar, kesifler):
        if isinstance(yanit.get("language"), str):
            diller[ad] = yanit["language"].strip().lower()[:8]
        for yazim in harf_ciftlerini_birlestir([_kesif_yazimi(c) for c in yanit.get("items") or []], tum_metin):
            sayi = _kesif_sayisi(yazim, tum_metin)
            # Katakanayla yazılmış ad tek geçse de alınır (バーリント: yer adı bir kez geçti, sözlükte olmayınca model
            # "Burlington" yazdı; ドノバン・デズモンド). Öteki keşifler kaynakta en az iki kez geçmeli.
            # Tanınan eserde tek geçen tek sözcüklük Latin ad da alınır: sözlükte kaynaktaki gibi kalır, karşılığı
            # sorulmaz. Sözlükte olmayınca çevirmen kendisi çeviriyordu (Mirkwood -> "Kara Orman", southfarthing ->
            # "Güney Mahallesi"; gerçek test 20, S4).
            if (sayi < (1 if re.fullmatch(r"[ァ-ヺー・]{3,}", yazim) or (
                    taninan and re.fullmatch(r"[^\W\d_]{3,}", yazim) and not temizlik.latin_disi(yazim)) else 2)
                    or len(yazim) > 40 or _anahtar(yazim) in yeni_anahtarlar
                    or _anahtar(yazim) in karar_verilen
                    or _anahtar(yazim) in sorulan or ekli_ad_mi(yazim)):
                continue
            ornek = [x.metin.replace("\n", " / ")[:110] for _, st, _ in dosyalar for x in st if yazim in x.metin][:2]
            yeni[yazim] = {"yazim": yazim, "bicimler": set(), "sayi": sayi, "ornek": ornek, "ipucu": ""}
            yeni_anahtarlar.add(_anahtar(yazim))        # birden çok dosyanın keşfi aynı adı iki harfle verebilir
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
        tur, hedef = hedef_belirle(yazim, cevap, taninan, latin_zorunlu, kod, ozgun)
        if hedef and baslik_terimi(yazim, cevap, baslik, hedef):
            hedef = baslik_terimi(yazim, cevap, baslik, hedef)
        if buyuk_harfli and hedef and hedef == hedef.upper() and re.search(r"[A-Z]{2}", hedef):
            # Tamamı büyük harfle yazılmış altyazıda (AZOG, SHIRE) ad sözlüğe olağan harfle girer: kısaltma ile ad
            # ayırt edilemez, büyük harf dosyanın yazım biçimidir
            hedef = re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1] + m.group(0)[1:].lower(), hedef)
        if tur in ("name", "codename", "epithet") and takma_ad_mi(hedef, yazim, tum_metin):
            # "yerleşik bicim" ya da karşılık diye verilen, aynı altyazıda geçen başka bir ad: takma addır
            tur, hedef = hedef_belirle(yazim, {k: v for k, v in cevap.items() if k not in ("e", "k")}, taninan,
                                       latin_zorunlu, kod, ozgun)
        if tur in ("name", "codename") and hedef and not re.search(r"[A-ZÀ-ÞĞİŞ]", hedef) and re.search(r"[a-zß-ÿğış]", hedef):
            # Kaynak adı küçük harfle yazmış ("mordor", "isildur": özensiz altyazı): özel ad baş harfi büyük yazılır.
            # Yoksa ad sözlüğe "kaynaktaki gibi kalır" diye küçük harfle giriyor, çeviri de öyle yazıyordu.
            hedef = re.sub(r"(?<![\w'’])([a-zß-ÿğış])", lambda m: m.group(1).upper(), hedef)
        if re.search(r"\d", hedef):        # sayı, karşılık isteğine giden satırların hepsinde aranır (ornekler: ilk ikisi)
            hedef = baglam_sayisi_sil(hedef, yazim + " " + str(cevap.get("l") or ""), ornekler + ornek_bul(yazim))
        ornek = ornekler[0] if ornekler else ""
        # Bilinen unvan sözcüğü ("Captain") başka yerde sıradan sözcüktür: karar saklanır ama terim olmaz.
        sozluk.girdi_ekle(yazim, tur, hedef, sorted(bicimler), karar, sayi, ornek,
                          unvan_kalibi(cevap, hedef, latin_zorunlu, kod == "tr") if tur == "title" and unvan_sor else "",
                          terim=not (tur == "title" and unvan_govdesi(yazim) in bilinen_unvan),
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
    eklenen["sound"] += ciglik_eklenen
    sozluk.unvanlar = sorted(set(sozluk.unvanlar) | _adla_gecen_unvanlar(sozluk, ciftler, bilinen_unvan, tum_metin))
    okunusla_duzelen = okunuslu_adlari_isle(sozluk, tum_metin)
    if okunusla_duzelen:
        gunluk.uyari(ilk, "okunuşu etikette yazan adın yazımı okunuşa göre düzeltildi: " + ", ".join(okunusla_duzelen))
    ortusenleri_esitle(sozluk)
    cogullari_esitle(sozluk)
    ad_ekli_lakaplari_esitle(sozluk)
    tam_ad_parcalarini_esitle(sozluk)
    katakana_parcalarini_esitle(sozluk)
    kisaltma_adlari(sozluk)
    japon_ad_sirasi(sozluk)
    for sorun in sozluk.tutarsizliklar()[:20]:
        gunluk.uyari(ilk, "sözlükte çelişki: " + sorun)
    gunluk.uyari(ilk, f"terim sözlüğü: {len(adaylar)} aday, {len(partiler)} sınıflandırma isteği"
                      + (f", {len(kesifler)} keşif isteği ({len(yeni)} yeni yazım)" if kesifler else "")
                      + (f", {karsiliksiz} aday için karşılık isteği" if karsiliksiz else "")
                      + (f" (cevapları birleşmeyen {ek_oylu} aday için {EK_OY} ek oy)" if ek_oylu else "")
                      + (f", yazımı okunuş olan {yazimsiz} aday için yazım isteği" if yazimsiz else "")
                      + f"; eser {'tanındı' if taninan else 'tanınmadı'}; "
                      + ", ".join(f"{t} {n}" for t, n in sorted(eklenen.items()))
                      + (f"; adla geçen unvan: {', '.join(sozluk.unvanlar)}" if sozluk.unvanlar else ""))
    return diller
