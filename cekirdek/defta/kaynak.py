"""Kaynak altyazı metnini okuyan yardımcılar: saygı ekleri, konuşmacı etiketleri, ses notları, şarkı, kurgu dili."""
import re

from . import temizlik

EK = re.compile(r"-(san|sama|kun|chan|tan|senpai|sempai|sensei|dono|shi|jou)\b", re.I)
CUMLE_SONU = re.compile(r"[.!?…\"”’')\]♪♫。！？」』）]$")
JP_EK = {"さん": "san", "ちゃん": "chan", "くん": "kun", "君": "kun", "さま": "sama", "様": "sama",
         "先輩": "senpai", "先生": "sensei", "殿": "dono", "どの": "dono", "氏": "shi", "嬢": "jou", "たん": "tan"}
# Uzatılarak söylenen ek de aynı ektir: アーニャさーん, ヨルちゃーん. 殿 / 氏 bileşik sözcükte ek değildir (殿下, 氏名).
JP_EK_DESEN = r"(さ[ーあ〜～]*ん|ちゃ[ーあ〜～]*ん|く[ーう〜～]*ん|た[ーあ〜～]*ん|君|さま|様|先輩|先生|殿(?![下様方])|どの|氏(?![名族])|嬢)"
JP_AD_EK = re.compile(r"([\u30A1-\u30FA\u30FC]{2,})" + JP_EK_DESEN)
# Okunuşu yanında yazılmış ad + ek (影山茂夫(しげお)君): okunuş adlara yazılır. さん / ちゃん sıradan sözcüklere de
# takıldığından (お母(かあ)さん, 赤(あか)ちゃん) yalnızca kişiye özgü ekler aranır.
OKUNUSLU_AD_EK = re.compile(r"[一-龥々][(（][ぁ-ん]{1,8}[)）](君|くん|先輩)")
UNVAN_EKI = {"先生", "先輩", "様", "さま", "殿", "氏", "嬢"}     # sıradan sözcüklere takılmayan, kişiye özgü ekler


def _ses_yalin(metin):
    """Romaji karşılaştırması için: küçük harf, yalnızca harfler, uzun ünlüler tek (tooi = toi = tōi)."""
    metin = temizlik.tr_kucuk(metin).translate(str.maketrans("āēīōūâîû", "aeiouaiu"))
    metin = re.sub(r"[^a-z]", "", metin)
    return re.sub(r"ou|oo|uu|aa|ii|ee", lambda m: m.group(0)[0], metin)


KANA_DIZI = re.compile(r"[\u3041-\u3096\u30A1-\u30FA\u30FC]{3,}")
YALNIZ_UNVAN = re.compile(r"(?<![\u30A1-\u30FA\u30FC\u4e00-\u9fff])(先生|先輩)")


def romaji_mi(kaynak, ceviri):
    """Latin harfleriyle yazılması istenen şarkı satırı gerçekten okunuşuyla mı yazılmış (çevrilmemiş mi)?
    Kaynaktaki kana dizilerinin okunuşu çıktıda aranır; en az yarısı bulunmalıdır."""
    diziler = [temizlik.kana_latin(m.group(0)) for m in KANA_DIZI.finditer(kaynak)]
    diziler = [_ses_yalin(d) for d in diziler if d]
    diziler = [d for d in diziler if len(d) >= 3]
    if not diziler:
        return True
    cikti = _ses_yalin(ceviri)
    return sum(1 for d in diziler if d in cikti) * 2 >= len(diziler)


def _ek_latin(ek):
    """Japonca hitap ekinin Latin karşılığı (uzatma işaretleri atılarak)."""
    return JP_EK[re.sub(r"[ーあう〜～]", "", ek)]


HIRAGANA_OKUNUS = re.compile(r"[ぁ-ゖー]+")
_KANJI = "一-龥々"
_TEK_KANJI = re.compile(f"[{_KANJI}]")
_KANJI_EK = "君様殿氏達"            # addan sonra gelebilen tek kanjili hitap ekleri (律君); 先生 / 先輩 ayrıca


def okunus_yazimi(kaynak, bicim):
    """bicim, kaynağın yanında yazılmış hiragana okunuşu mu ("律(りつ)" için "りつ")? Okunuş tek başına bir yazım
    sayılmaz: iki üç hecelik okunuş sıradan sözcüklerin içinde de geçer (すがりつく, 縛りつける) ve o satırlara ad
    yazdırıyordu ("Ritsu"). Katakana okunuş (東国(オスタニア)) ayrı bir sözcüktür, tek başına da geçer."""
    m = re.fullmatch(r"(.+?)[(（]([^()（）]+)[)）]", kaynak.strip())
    return bool(m and m.group(2).strip() == bicim.strip() and HIRAGANA_OKUNUS.fullmatch(bicim.strip()))


def _terim_bicimleri(kaynak):
    """Okunuşu yanında yazılmış terimin ("東国(オスタニア)") kaynakta geçebileceği yazımlar: tamamı, yalnız
    sözcük, yalnız okunuş. Kontrol hangisi geçerse geçsin çalışsın diye. Hiragana okunuş tek başına aranmaz
    (okunus_yazimi)."""
    m = re.fullmatch(r"(.+?)[(（]([^()（）]+)[)）]", kaynak.strip())
    if not m or kaynak.isascii():
        return [kaynak]
    return [kaynak] + [x for x in (m.group(1).strip(), m.group(2).strip())
                       if len(x) >= 2 and not okunus_yazimi(kaynak, x)]


def bicim_gecer(bicim, metin):
    """Terimin bu yazımı metinde geçiyor mu? Tek kanjili yazım (爪, 律, 兄) başka bir kanjiye bitişikse geçmiş
    sayılmaz: orada başka bir sözcüğün parçasıdır (爪痕 "iz", 法律 "yasa", 兄弟 "kardeşler"). Addan sonraki hitap
    eki (律君, 律先生) bitişiklik sayılmaz."""
    if len(bicim) == 1 and _TEK_KANJI.fullmatch(bicim):
        return bool(re.search(f"(?<![{_KANJI}])" + re.escape(bicim) + f"(?:(?![{_KANJI}])|(?=[{_KANJI_EK}]|先生|先輩))", metin))
    return bicim in metin


JP_ETIKET = re.compile(r"（([^（）]{1,24})）")
KATAKANA = re.compile(r"[\u30A1-\u30FA\u30FC]{2,}")
TIRELI = re.compile(r"^\s*(<i>)?\s*[-–—]")
ANLAMLI = re.compile(r"^(?P<soz>[^()]+?)\s*\((?P<anlam>[^()]+)\)[\s\"“”]*$", re.S)


def anlami_ayir(metin, parcalar):
    """Kurgu dilindeki söz ve kaynağın aynı altyazıda parantez içinde verdiği anlamı:
    "Tangado haid! (Hold positions!)" -> ("Tangado haid!", "Hold positions!", italik mi).
    Altyazı yalnızca bu ikisinden oluşuyorsa döndürür; yoksa None."""
    duz = temizlik.ETIKET.sub("", metin).strip()
    m = ANLAMLI.match(duz)
    if not m or not parcalar:
        return None
    soz, anlam = m.group("soz").strip(), m.group("anlam").strip().strip('"“”').strip()
    artan = soz
    for parca in parcalar:
        artan = artan.replace(parca, "")
    if any(k.isalnum() for k in artan) or not any(k.isalpha() for k in anlam):
        return None                       # sözün yanında gerçek dilde metin de var: model bütün olarak çevirir
    return soz, anlam, metin.strip().startswith("<i>")


KOSELI_NOT = re.compile(r"\[([^\[\]]{1,60})\]")
PARANTEZ_SATIR = re.compile(r"^(?:[(（]([^()（）]{1,40})[)）]|（((?:[^()（）]|\([^()（）]*\)){1,40})）)$")
JP_ETIKET_BAS = re.compile(r"^（([^（）]{1,24})）\s*")
# Japonca ses/müzik notunun sonu: （ドアが開く音）, （犬のうなり声）, （「SPY WARS」のテーマ）, （せきばらい）. Konuşmacı değildir.
JP_SES_NOTU = re.compile(r"(?:[音声曲歌]|テーマ|音楽|ＢＧＭ|BGM|拍手|ため息|笑い|すすり泣き|泣き|せきばらい|せき込み|せきこみ|"
                         r"咳払い|咳)$")
LATIN_ETIKET_BAS = re.compile(r"^(" + temizlik.TIRE + r"\s*)?([A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ0-9 .'’#&-]{1,24}):\s*")


# Küçük harfli konuşmacı etiketi ("Bilbo:", "Female voice:"): tek başına bakınca sıradan sözden ayrılmaz ("Unutma:"),
# o yüzden yalnız dosyada kendi satırında en az ETIKET_ESIGI kez geçen adlar etiket sayılır (latin_etiketleri).
ADLI_ETIKET_SATIRI = re.compile(r"^(?:" + temizlik.TIRE + r"\s*)?([A-ZÇĞİÖŞÜÂÎÛ][\w.'’-]*(?: [\w.'’#&-]+){0,2}):$")
ETIKET_ESIGI = 3


# Sözün başındaki etiket ("Pippin: Biraz da lahana."): kendi satırındaki etiketten daha çok sıradan söze benzer
# ("Look: ..."), o yüzden bir koşul daha var: etiketin hiçbir sözcüğü dosyada küçük harfle geçmez (özel ad hep büyük
# harfle yazılır; "look", "remember" cümle içinde küçük harfle de geçer).
ADLI_ETIKET_BAS = re.compile(r"^(?:" + temizlik.TIRE + r"\s*)?([A-ZÇĞİÖŞÜÂÎÛ][\w.'’-]*(?: [A-ZÇĞİÖŞÜÂÎÛ][\w.'’-]*){0,2}):\s+\S")


# Etiket düzeni kesin dosyada bir kez geçen etiket de etikettir ("Maggot: ...", "Arwen: ...", "Female: ...",
# "Voice of the ring: ...", OCR'lı "Sau Ron: ..."): eşik onları kaçırıyordu, çeviride "Maggot:" yazıyordu (LOTR).
# Biçim daha sıkı: büyük harfli sözcükler (arada of/the gibi bağlaçlar), sözün başında, ardından söz; sık cümle
# başları ("Look:", "Remember:") etiket sayılmaz.
TEKIL_ETIKET_BAS = re.compile(r"^(?:" + temizlik.TIRE + r"\s*)?(?!(?:A|An|The|This|That|My|Your|Our|His|Her|Their|One) )"
                              r"([A-ZÇĞİÖŞÜÂÎÛ][\w.'’-]*(?: (?:of|the|de|du|van|von|da|[A-ZÇĞİÖŞÜÂÎÛ][\w.'’-]*)){0,3}"
                              r"(?:(?<=the)|(?<=of))?(?:(?<= the)(?: [a-zçğıöşü][\w'’-]*)|(?<= of)(?: [a-zçğıöşü][\w'’-]*))?):\s+\S")
ETIKET_OLMAYAN = {"look", "listen", "remember", "note", "warning", "well", "so", "now", "yes", "no", "okay", "ok", "hey",
                  "wait", "answer", "question", "rule", "step", "chapter", "part", "reason", "problem", "truth", "fact",
                  "lesson", "example", "dear", "ps", "p.s", "p.s.", "nb", "item", "first", "second", "third", "finally",
                  "result", "results", "score", "total", "attention", "caution", "notice", "update", "breaking", "live",
                  "subject", "re", "to", "from", "date", "time", "place", "location", "translation", "subtitles"}


ETIKET_OLMAYAN_ILK = {"A", "An", "The", "This", "That", "My", "Your", "Our", "His", "Her", "Their", "One"}


def latin_etiketleri(metinler):
    """Dosyadaki küçük harfli konuşmacı etiketleri: kendi satırında, ardından söz gelen "Ad:" satırlarından ya da
    sözün başında duran "Ad: ..." etiketlerinden en az ETIKET_ESIGI kez geçenler (ikisi birlikte sayılır; sözün
    başındakiler ADLI_ETIKET_BAS'taki koşulla). Büyük harfli etiketler (GANDALF:) zaten tanınır; bu yalnız karışık
    yazımlılar içindir."""
    metinler = list(metinler)
    sayim, bastaki = {}, {}
    for metin in metinler:
        satirlar = [temizlik.ETIKET.sub("", x).strip() for x in metin.split("\n")]
        for sira, satir in enumerate(satirlar):
            m = ADLI_ETIKET_SATIRI.match(satir)
            if (m and sira < len(satirlar) - 1 and len(satir) <= 26 and any(satirlar[sira + 1:])
                    and not temizlik.duyuru_etiketi(m.group(1))):
                sayim[m.group(1)] = sayim.get(m.group(1), 0) + 1
                continue
            m = ADLI_ETIKET_BAS.match(satir)
            if m and len(m.group(1)) <= 24 and not temizlik.duyuru_etiketi(m.group(1)):
                bastaki[m.group(1)] = bastaki.get(m.group(1), 0) + 1
    if bastaki:
        kucukler = set(re.findall(r"\b[a-zçğıöşüâîû][\w'’-]*", "\n".join(temizlik.ETIKET.sub("", x) for x in metinler)))
        for ad, n in bastaki.items():
            if not any(soz.lower() in kucukler for soz in re.findall(r"[^\W\d_][\w'’-]*", ad)):
                sayim[ad] = sayim.get(ad, 0) + n
    etiketler = {ad for ad, n in sayim.items() if n >= ETIKET_ESIGI}
    # Dosyanın etiket düzeni kesinse (en az iki ad yukarıdaki koşullarla tanındı), sözün başında en az iki kez geçen
    # öbür etiketler de alınır: özensiz altyazı adları cümle içinde küçük harfle yazabiliyor ("bilbo" 15 kez, büyük
    # harfle hiç), küçük harf koşulu "Merry:", "Pippin:", "Bilbo:" etiketlerini reddediyor, adlar çeviride kalıyordu.
    if len(etiketler) >= 2:
        etiketler |= {ad for ad, n in bastaki.items() if n >= 2}
        for metin in metinler:
            for satir in (temizlik.ETIKET.sub("", x).strip() for x in metin.split("\n")):
                m = TEKIL_ETIKET_BAS.match(satir) or ADLI_ETIKET_SATIRI.match(satir)
                if (m and len(m.group(1)) <= 24 and m.group(1).lower().rstrip(".") not in ETIKET_OLMAYAN
                        and m.group(1).split()[0] not in ETIKET_OLMAYAN_ILK and not temizlik.duyuru_etiketi(m.group(1))):
                    etiketler.add(m.group(1))
    return etiketler


def ara_etiketleri_ayir(satir, adli_etiketler):
    """Aynı satırda ikinci konuşmacının etiketi ("Are we lost? Merry: No.", "Frodo: Go! Pippin: Hurry!"): cümle
    sonundan sonra gelen bilinen etiket yeni satır başlatır; satır başı etiketi olarak ayrılır ve tire alır."""
    if not adli_etiketler:
        return satir
    adlar = "|".join(re.escape(a) for a in sorted(adli_etiketler, key=len, reverse=True))
    return re.sub(r"(?<=[.!?…])\s+(?=(?:" + adlar + r"):\s+\S)", "\n", satir)


BIP = re.compile(r"\[\s*bleep(?:ed|ing)?\s*\]", re.I)


def not_ve_konusmaci_ayir(metin, koseli_ayir=True, parantez_ayir=True, etiket_ayir=True, adli_etiketler=()):
    """Ses/ton notlarını ve konuşmacı etiketlerini metinden ayırır.

    (kalan metin, notlar, satır başına konuşmacılar) döndürür. Model bunları bağlam olarak görür
    ama metinde olmadıkları için çeviriye yazamaz.
    """
    notlar, konusanlar, satirlar = [], [], []
    if etiket_ayir and adli_etiketler:
        metin = "\n".join(ara_etiketleri_ayir(x, adli_etiketler) for x in metin.split("\n"))
    ham = metin.split("\n")
    for sira, satir in enumerate(ham):
        ic = temizlik.ETIKET.sub("", satir).strip()
        m = PARANTEZ_SATIR.match(ic)
        if m and m.group(1) is None:        # etiketin içinde okunuş var: （ＷＩＳＥ(ワイズ)機関員）
            m = re.match(r"^（(.+)）$", ic)
        if m and parantez_ayir or (m and etiket_ayir and ic.startswith("（")):
            sonra = any(temizlik.ETIKET.sub("", x).strip() for x in ham[sira + 1:])
            icerik = m.group(1)
            ses = bool(JP_SES_NOTU.search(icerik))             # （ドアが開く音）, （犬のうなり声）: ses notu, konuşmacı değil
            # Sonraki satır kendi etiketiyle başlıyorsa （せきばらい） / （黄昏）失敬: konuşan odur, bu satır nottur.
            sonraki = next((y for y in (temizlik.ETIKET.sub("", x).strip() for x in ham[sira + 1:]) if y), "")
            m2 = JP_ETIKET_BAS.match(sonraki)
            sonraki_etiketli = bool(m2 and sonraki[m2.end():].strip())
            if ic.startswith("（") and sonra and etiket_ayir and not ses and not sonraki_etiketli:   # Japonca: kendi satırında konuşmacı adı
                konusanlar.append(icerik)
                continue
            # Not mu konuşma mı: Latin yazıda kısa ve noktalamasız olan; Japoncada boşluk olmadığı için karakter sayısıyla
            # (en çok 8) ya da ses notu ekiyle. （ここは慎重に行かねば。…） gibi iç ses konuşmadır, silinmez.
            if temizlik.latin_disi(icerik):
                not_mu = (len(icerik) <= 8 or ses) and not re.search(r"[。！？!?]", icerik)
            else:
                not_mu = len(icerik.split()) <= 5 and not re.search(r"[.!?]", icerik)
            if parantez_ayir and (not_mu or ses):
                notlar.append(icerik)
                continue
        konusan = ""
        if etiket_ayir and adli_etiketler:
            # "Bilbo:" kendi satırında ya da sözün başında ("- Gandalf: ..."): dosyanın etiket diye kullandığı ad
            on = re.match(r"\s*(<i>)?\s*", satir)
            m = re.match(r"(" + temizlik.TIRE + r"\s*)?([^:\n]{1,24}):\s*", satir[on.end():])
            if m and m.group(2).strip() in adli_etiketler:
                kalan_satir = satir[on.end() + m.end():]
                if not temizlik.ETIKET.sub("", kalan_satir).strip():
                    if any(temizlik.ETIKET.sub("", x).strip() for x in ham[sira + 1:]):
                        konusanlar.append(m.group(2).strip())      # etiket satırı: ad sonraki satırındır
                    # ardından söz gelmeyen yalnız etiket ("Gimli:"): altyazıda söz yok, etiket de yazılmaz
                    continue
                else:
                    konusan = m.group(2).strip()
                    satir = satir[:on.end()] + (m.group(1) or "") + kalan_satir
        if etiket_ayir and not konusan:
            m = JP_ETIKET_BAS.match(satir) or None
            if m and satir[m.end():].strip():
                konusan, satir = m.group(1), satir[m.end():]
            else:
                on = re.match(r"\s*(<i>)?\s*", satir)
                m = LATIN_ETIKET_BAS.match(satir[on.end():])
                if m and satir[on.end() + m.end():].strip() and not temizlik.duyuru_etiketi(m.group(2)):
                    konusan = m.group(2).strip()
                    satir = satir[:on.end()] + (m.group(1) or "") + satir[on.end() + m.end():]
        if koseli_ayir:
            # Cümlenin içindeki bip işareti ("the [bleep] sink") not değil, örtülmüş bir küfürdür: metinde kalır ki
            # model küfrü çevirebilsin (silinince küfür düşüyordu). Satırda ondan başka söz yoksa not gibi silinir.
            # İki konuşmacılı satırda yalnız bipten oluşan pay ("- [bleep]") o konuşmacının küfrüdür, not değil:
            # silinirse tire sayısı tutmuyor, konuşmacı düşüyordu.
            pay = (re.match(r"\s*[-–—]", ic) is not None
                   and sum(1 for x in ham if re.match(r"\s*[-–—]", temizlik.ETIKET.sub("", x))) >= 2)
            ortulu = bool(BIP.search(satir)) and (pay or bool(temizlik.ETIKET.sub("", KOSELI_NOT.sub("", satir)).strip(" -–—.,!?")))
            notlar.extend(n for n in KOSELI_NOT.findall(satir) if not (ortulu and BIP.fullmatch(f"[{n}]")))
            yeni = KOSELI_NOT.sub(lambda e: e.group(0) if ortulu and BIP.fullmatch(e.group(0)) else "", satir)
            if yeni != satir and not temizlik.ETIKET.sub("", yeni).strip(" -–—.,!?…"):
                yeni = ""                   # notu atılınca yalnız tire ve noktalama kalan satır ("- [bleep].") boştur
            satir = yeni
        if temizlik.ETIKET.sub("", satir).strip(" -\u2013\u2014"):
            satirlar.append(re.sub(r"[ \t]+", " ", satir).strip())
            if konusan or len(konusanlar) < len(satirlar):     # etiket satırından gelen ad bu satırındır
                konusanlar.append(konusan)
    konusanlar = konusanlar[:len(satirlar)] if any(konusanlar) else []
    kalan = "\n".join(satirlar)
    if metin.count("<i>") == metin.count("</i>"):
        # Atılan not ya da etiket satırında açılmış (kapanmış) italik yarım kalmasın: "<i>(SIGHS)" / "I can't.</i>".
        # Yarım etiket modele gidince çeviri italik denetiminden geçemiyor, satır boşuna yeniden soruluyordu.
        kalan = temizlik._italik_onar(kalan)
    return kalan, notlar, konusanlar


def sarki_mi(metin):
    return "♪" in metin or "♫" in metin


def sarki_bloklari(satirlar, azami_satir=40, azami_sure=180000):
    """Yalnız ilk dizesinin başında ve son dizesinin sonunda ♪ olan şarkının iç dizeleri (index kümesi).

    Açılış ve kapanış dizesinde ♪'den başka söz olmalı: tek başına "♪〜" müziğin başladığını söyler, söz değildir
    (arada konuşma olabilir). İç dizelerde ♪ ve iki konuşmacı olmaz; açılış italikse onlar da italiktir. Kapanış en
    çok 40 altyazı ve 3 dakika içinde gelmezse blok sayılmaz."""
    def cekirdek(m):
        return re.sub(r"^[\s♪♫～〜~]+|[\s♪♫～〜~]+$", "", m)

    def italik(m):
        c = cekirdek(m)
        return c.startswith("<i>") and c.endswith("</i>")

    def sozlu(m):
        return bool(re.search(r"\w", temizlik.ETIKET.sub("", cekirdek(m))))

    def isaret(m, uc):
        duz = temizlik.ETIKET.sub("", m).strip()
        return (duz[:1] if uc == "bas" else duz[-1:]) in ("♪", "♫")

    ic, i = set(), 0
    while i < len(satirlar):
        m = satirlar[i].metin
        if not (isaret(m, "bas") and not isaret(m, "son") and sozlu(m)):
            i += 1
            continue
        egik, j = italik(m), i + 1
        while (j < len(satirlar) and not sarki_mi(satirlar[j].metin) and sozlu(satirlar[j].metin)
               and not (egik and not italik(satirlar[j].metin))
               and sum(1 for r in satirlar[j].metin.split("\n") if TIRELI.match(r)) < 2):
            j += 1
        if (i + 1 < j < len(satirlar) and j - i <= azami_satir and satirlar[j].bas - satirlar[i].bas <= azami_sure
                and isaret(satirlar[j].metin, "son") and not isaret(satirlar[j].metin, "bas")
                and sozlu(satirlar[j].metin) and not (egik and not italik(satirlar[j].metin))):
            ic.update(range(i + 1, j))
            i = j
        i += 1
    return ic


def cumle_sonu_mu(metin):
    duz = temizlik.ETIKET.sub("", metin).strip()
    return not duz or bool(CUMLE_SONU.search(duz))


def token_tahmini(metin):
    """Kaba token tahmini: Latin harflerde ~3,5 karakter, Doğu Asya yazılarında karakter başına 1."""
    genis = sum(1 for k in metin if ord(k) > 0x2E7F)
    return int((len(metin) - genis) / 3.5 + genis) + 8


def _okunan_harf(metin):
    """İzleyicinin okuduğu karakter sayısı: etiketler, satır sonları ve parça işaretleri sayılmaz."""
    return len(re.sub(r"\s*\|\s*", " ", temizlik.ETIKET.sub("", metin)).replace("\n", ""))


def _yalin(metin):
    """Karşılaştırma için: harf ve rakam dışındaki her şey atılır, küçük harfe çevrilir."""
    return re.sub(r"[\W_]+", "", metin, flags=re.UNICODE).lower()


# ---- ses sözcükleri: hayvan sesi ya da karakterin kendine özgü seslenmesi (ボフ, ワンワン, わくわく) ----------
KANA_SOZCUK = re.compile(r"[ぁ-ゖァ-ヺー]+")
_SES_ARASI = re.compile(r"[\s…‥・、。，,.!?！？~～〜―—\-「」『』]*")
# Yalnızca ünlü, h sesi, n ve yarı ünlüden oluşan sözcük ünlemdir (はい, ええ, ハア, フフ, うわっ): ses sözcüğü sayılmaz.
_UNLEM = set("あいうえおぁぃぅぇぉはひふへほんわやゆよゃゅょっーアイウエオァィゥェォハヒフヘホンワヤユヨャュョッ")
# Çığlık, inleme, homurtu (acı, korku, çaba): anlamı olmayan ses; duyulduğu gibi yazılır (kullanıcı kararı, 20.11.2026).
# Ses mi sözcük mü diye modele sorulmaz: ぐあ için oylar koşudan koşuya değişiyordu (SPY x FAMILY S01E02). Anlam taşıyan
# kısa ünlemler (えっ, あっ, はい, うん) bu kalıba girmez, çevrilmeye devam eder.
_CIGLIK = re.compile(r"(?:ぐ[あわはふえお]|が[あは]|[ぎき]ゃ|う[わお]|ぬ[おあ]|どわ|おわ)[あぁえぇおぉー]*|ひ[いえぃぇ][いぃえぇー]*|ぐぬ[ぬー]*|う[ぐげ]ー*")


def ciglik_mi(soz):
    """Kana sözcük bir çığlık ya da inleme mi (ぐあ, うわあ, ヒイ, ギャア): katakana da hiragana gibi bakılır."""
    hira = "".join(chr(ord(k) - 0x60) if "ァ" <= k <= "ヶ" else k for k in soz or "")
    return bool(_CIGLIK.fullmatch(hira.rstrip("っー")))


def ses_satiri(metin):
    """Altyazının sözü tek bir kana sözcüğüyse (yinelenmiş olabilir: ハス ハス ハス) (sözcük, konuşan, yinelenmiş mi)
    döndürür, değilse None. Konuşmacı etiketi ve ses notları söz sayılmaz; şarkılar ve ünlemler dışarıdadır."""
    if sarki_mi(metin):
        return None
    konusanlar, sozcukler = set(), []
    for sira in metin.split("\n"):
        etiket = re.match(r"\s*（([^（）]{1,30})）", sira)
        if etiket:
            konusanlar.add(etiket.group(1).strip())
            sira = sira[etiket.end():]
        sira = re.sub(r"（[^（）]*）", "", temizlik.ETIKET.sub("", sira))
        if not _SES_ARASI.fullmatch(KANA_SOZCUK.sub("", sira)):
            return None
        sozcukler += [s.rstrip("っッー") for s in KANA_SOZCUK.findall(sira)]
    sozcukler = [s for s in sozcukler if s]
    if not sozcukler or len(set(sozcukler)) != 1 or len(konusanlar) > 1:
        return None
    soz, yari = sozcukler[0], len(sozcukler[0]) // 2
    if len(soz) < 2 or (set(soz) <= _UNLEM and not ciglik_mi(soz)):
        return None
    yinelenen = len(sozcukler) > 1 or (yari >= 2 and len(soz) % 2 == 0 and soz[:yari] == soz[yari:])
    return soz, (konusanlar.pop() if konusanlar else None), yinelenen


def konusmaci_parcalari(metin):
    """Birden çok konuşmacı etiketli satırı konuşmacı başına parçalara böler (（アーニャ）何でもいい / （ヨル）ガーン);
    tek konuşmacılıysa satırın kendisi."""
    parcalar = [p for p in re.split(r"(?=（[^（）]{1,30}）)", metin) if p.strip()]
    return parcalar if len(parcalar) > 1 else [metin]


def ses_latin(soz):
    """Ses sözcüğünün Latin harfli yazımı: ボフ -> "bofu"; kendi içinde yinelenen ayrı yazılır: わくわく -> "waku waku"."""
    yari = len(soz) // 2
    parcalar = [soz[:yari], soz[yari:]] if yari >= 2 and len(soz) % 2 == 0 and soz[:yari] == soz[yari:] else [soz]
    latin = [temizlik.kana_latin(p) for p in parcalar]
    return " ".join(x.lower() for x in latin) if all(latin) else ""


def ses_yaz(metin, soz, latin):
    """Ses satırını Latin harfleriyle yazar: etiket ve notlar atılır, yineleme ve noktalama kalır."""
    siralar = []
    for sira in metin.split("\n"):
        sira = re.sub(r"\s*（[^（）]*）", "", temizlik.ETIKET.sub("", sira))
        sira = KANA_SOZCUK.sub(lambda m: latin if m.group(0).rstrip("っッー") == soz else m.group(0), sira)
        sira = " ".join(temizlik.noktalama_latin(sira).split())
        if sira:
            siralar.append(sira[:1].upper() + sira[1:])
    return "\n".join(siralar)
