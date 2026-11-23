"""Kaynak metnin hazırlanması ve çıktının son temizliği. Hepsi sabit kural, model yok."""
import html
import re
import unicodedata

# Sıfır genişlikli ayırıcı ve birleştirici (U+200C, U+200D) Arap ve Hint yazılarında yazımın parçasıdır (Farsça
# "می\u200cروم"): o yazıların iki harfi arasındaysa kalır; başka her yerde öteki görünmez karakterler gibi atılır.
_BITISEN = "\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufefc\u0900-\u0dff"
GORUNMEZ = re.compile("[\u200b\u200e\u200f\u202a-\u202e\u2060\ufeff\u00ad]"
                      "|[\u200c\u200d](?![" + _BITISEN + "])|(?<![" + _BITISEN + "])[\u200c\u200d]")
ASS_KOD = re.compile(r"\{[^{}]*\}")
USTTE = re.compile(r"\{[^{}]*\\an[789][^{}]*\}")
I_AC = re.compile(r"<\s*i\s*>", re.I)
I_KAPA = re.compile(r"<\s*/\s*i\s*>", re.I)
DIGER_ETIKET = re.compile(r"</?(?!i>)[a-zA-Z][^<>]*>")
ETIKET = re.compile(r"<[^<>]+>")
TIRE = r"[-–—]"
ISIM = re.compile(
    r"^(" + TIRE + r"\s*)?[A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ0-9 .'’#&-]{1,24}:\s*")
# Büyük harfle yazılıp iki noktayla biten ama konuşan adı olmayan başlıklar: ekran yazısının parçasıdır, silinmez.
DUYURU = {"NOT", "NOTE", "UYARI", "DİKKAT", "WARNING", "CAUTION", "ATTENTION", "NOTICE", "PS", "P.S", "P.S.", "NB",
          "N.B", "N.B."}


def duyuru_etiketi(ad):
    return ad.strip().rstrip(":").strip().upper() in DUYURU


_CJK = {"〜": "~", "～": "~", "「": '"', "」": '"', "『": '"', "』": '"', "《": '"', "》": '"',
        "〈": '"', "〉": '"', "【": '"', "】": '"', "、": ", ", "。": ". ", "・": " ", "\u3000": " ",
        "―": "—"}
_CJK.update({chr(k): chr(k - 0xFEE0) for k in range(0xFF01, 0xFF5F)})
CJK_TABLO = str.maketrans(_CJK)


def noktalama_latin(metin):
    """Tam genişlikli ve Doğu Asya noktalama işaretlerini Latin karşılıklarına çevirir."""
    return metin.translate(CJK_TABLO)


# ---- kana -> Latin (sade Hepburn): tamamı kana olan şarkı satırları modele gitmeden yazılır -------------
_KANA_SES = ("a i u e o ka ki ku ke ko sa shi su se so ta chi tsu te to na ni nu ne no ha hi fu he ho "
             "ma mi mu me mo ya yu yo ra ri ru re ro wa o n ga gi gu ge go za ji zu ze zo da ji zu de do "
             "ba bi bu be bo pa pi pu pe po").split()
_KANA_HARF = ("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
              "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ")
_KANA = dict(zip(_KANA_HARF, _KANA_SES))
_KANA.update({"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゔ": "vu"})
_KUCUK_Y = {"ゃ": "ya", "ゅ": "yu", "ょ": "yo"}


def kana_latin(metin):
    """Tamamı kana (hiragana/katakana) olan metni Latin harfleriyle yazar; başka yazı (kanji...) varsa None."""
    cikti, i = [], 0
    hira = "".join(chr(ord(k) - 0x60) if "ァ" <= k <= "ヶ" else k for k in metin.replace("・", " "))
    while i < len(hira):
        k = hira[i]
        sonraki = hira[i + 1] if i + 1 < len(hira) else ""
        if k in _KANA and sonraki in _KUCUK_Y:
            ses = _KANA[k]
            cikti.append((ses[:-1] if ses[:-1] in ("sh", "ch", "j") else ses[:-1] + "y") + _KUCUK_Y[sonraki][1:])
            i += 2
            continue
        if k in _KANA and sonraki in "ぁぃぅぇぉ" and sonraki and k not in "ぁぃぅぇぉ":
            # küçük ünlü önceki sesin ünlüsünün yerine geçer: ファ fa, ティ ti, ヴィ vi, シェ she, ウェ we, トゥ tu
            on = _KANA[k][:-1] or ("w" if k == "う" else "y")
            cikti.append(on + _KANA[sonraki])
            i += 2
            continue
        if k == "っ":
            if sonraki in _KANA:
                cikti.append(_KANA[sonraki][0])
        elif k == "ー":
            if cikti and cikti[-1][-1:] in "aiueo":
                cikti.append(cikti[-1][-1])
        elif k in _KANA:
            cikti.append(_KANA[k])
        elif k in _KUCUK_Y:
            cikti.append(_KUCUK_Y[k])
        elif "\u3040" <= k <= "\u30ff" or "\u4e00" <= k <= "\u9fff" or "\uac00" <= k <= "\ud7af":
            return None
        else:
            cikti.append(k)
        i += 1
    yazi = noktalama_latin("".join(cikti))
    if latin_disi(yazi):
        return None
    # her satırın ilk harfi büyük
    return "\n".join(re.sub(r"[a-z]", lambda m: m.group(0).upper(), satir, count=1) for satir in yazi.split("\n"))


def _bosluk(metin):
    satirlar = []
    for s in metin.split("\n"):
        s = re.sub(r"[ \t]+", " ", s).strip()
        s = re.sub(r" +([,;:!?…])", r"\1", s)
        s = re.sub(r"(?<=\w) +\.(?!\.)", ".", s)
        if s:
            satirlar.append(s)
    return "\n".join(satirlar)


# Teletext ve kapalı altyazıdan kalan yön / konuşmacı işaretleri ("Tabii. >", "< Burası mı?", ">> Hello"): tek başına
# duran < ve > işaretleri. Etiketin parçası olanlar (<i>) boşlukla çevrili olmadığı için eşleşmez.
YON_ISARETI = re.compile(r"(?m)^[ \t]*[<>]{1,3}[ \t]+|[ \t]+[<>]{1,3}[ \t]*$|(?<=[ \t])[<>]{1,3}(?=[ \t])")
# Japon TV altyazısının işaretleri: ➡ cümlenin sonraki altyazıda sürdüğünü, 🔊 / 📱 sesin hoparlörden ya da telefondan
# geldiğini gösterir. Söz değildir; kalırsa çeviriye geçiyor ("Kaçın, kaçın! ➡"). Süren cümleyi script zaten
# noktalamadan anlar.
# Ok, yayına göre değişir (➡ ⇒ ➨ ➜ →): Unicode'un ok işaretlerinin hepsi aynı sayılır (S56; Black Clover TV kaydında ➨
# 61 satırın 13'ünde çeviride kalıyordu). ⸨ ⸩ ve ｟ ｠ ekran dışı sesin ayraçlarıdır: japonca_duzelt italiğe çevirir.
OK = "\u2192\u21d2\u2794-\u27be\u2b95"
YAYIN_ISARETI = re.compile(r"[ \t]*[" + OK + r"][ \t]*|[🔊📱📞☎📺][ \t]*")


def _ortak(metin):
    metin = html.unescape(metin).replace("\xa0", " ")
    metin = GORUNMEZ.sub("", metin)
    metin = metin.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    metin = ASS_KOD.sub("", metin)
    metin = I_KAPA.sub("</i>", I_AC.sub("<i>", metin))
    metin = DIGER_ETIKET.sub("", metin)
    metin = re.sub(r"</?i(?=[^>])", "", metin)            # yarım kalmış etiket: "<iBir" -> "Bir"
    metin = re.sub(r"(<i>\s*)+<i>", "<i>", metin)          # çift açılış
    metin = re.sub(r"</i>(\s*</i>)+", "</i>", metin)       # çift kapanış
    metin = YON_ISARETI.sub(" ", metin)
    metin = YAYIN_ISARETI.sub("", metin)
    return metin


def tr_kucuk(metin):
    """Türkçe kurallarıyla küçük harf (I -> ı, İ -> i)."""
    return metin.replace("I", "ı").replace("İ", "i").lower()


# Japonca hitap ekinden sonra gelen Türkçe hâl eki ekin okunuşuna uyar: son ünlüsünün dar ve geniş karşılığı,
# ünlüyle bitiyorsa kaynaştırma harfi (y, n). Sensei / Senpai istemdeki örnekler gibi "i" ile biter ("Sensei'ye").
_EK_SESI = {"kun": ("u", "a", False), "san": ("ı", "a", False), "chan": ("ı", "a", False), "tan": ("ı", "a", False),
            "sama": ("ı", "a", True), "dono": ("u", "a", True), "jou": ("u", "a", True), "shi": ("i", "e", True),
            "sensei": ("i", "e", True), "senpai": ("i", "e", True), "sempai": ("i", "e", True)}
_EKLI_HAL = re.compile(
    r"((?:-(?:" + "|".join(_EK_SESI) + r"))|(?<![\w-])(?:Sensei|Senpai|Sempai))(['’])"
    r"(n?[ıiuü]n|[dt][ae]n?|y?l[ae]|y?[ıiuü]|y?[ae])(?![a-zçğıöşü])", re.I)


# Gerçek din, mezhep ve halk adları Türkçede büyük harfle yazılır; model cümle içinde küçük yazabiliyordu
# ("müslümanlar", "tanrı'nın": gerçek test F/G, Kingdom of Heaven).
_TR_BUYUK = re.compile(r"(?<![\wçğıöşü])(müslüman|hristiyan|hıristiyan|yahudi|musevi|arap(?!saç)|islam|budist|katolik|"
                       r"protestan|ortodoks|haçlı|yunan|ispanyol|kızılderili|türk(?!ü)|japon|ingiliz|fransız|italyan|kelt|sakson|viking|"
                       r"(?:pers|iskoç|aztek|inka|rus|"
                       # "alman" fiil de olabilir ("yüzüğü senin alman", "bugüne alman gerekirse"): ardından gelen
                       # gerek/lazım/için… ya da önündeki "senin" milliyet değildir (S29).
                       r"(?<!senin )alman(?! (?:gerek|lazım|şart|için|d[ae]\b|m[ıi]\b|mümkün|yeter|doğru|iyi))|moğol|kürt|ermeni|gürcü|arnavut|macar|fars|portekiz|latin|"
                       r"çin|hint|amerikan?|afrika|avrupa|asya)(?=l[ae]r|l[ıi]|[cç][ae]|[^\wçğıöşü]|$))(?=[a-zçğıöşü'’]|[^\w]|$)")


def tr_buyuk_harf(metin):
    """ "müslümanlar" -> "Müslümanlar", "tanrı'nın" -> "Tanrı'nın" (kesme işareti yalnız özel adda olur). Küçük harfli
    sözcüğün sonundaki büyük I/İ küçülür: "İnandI" -> "İnandı" (modelin yanıtı, Kodi 1.0.16 SPY E02 401; S29)."""
    metin = _TR_BUYUK.sub(lambda m: ("İ" if m.group(1)[0] == "i" else m.group(1)[0].upper()) + m.group(1)[1:], metin)
    metin = re.sub(r"(?<![\w'’])([A-ZÇĞİÖŞÜ]?[a-zçğıöşüâîû]{2,})([Iİ])(?![\w'’])",
                   lambda m: m.group(1) + ("ı" if m.group(2) == "I" else "i"), metin)
    return re.sub(r"(?<![\wçğıöşü])tanrı(?=['’])", "Tanrı", metin)


def satir_basi_kucult(eski, yeni):
    """Yeniden yazılan çeviride satır sonundan sonra süren cümlenin büyük harfle başlaması geri alınır: eski çeviride
    o satır küçük harfle başlıyorsa, önceki satır cümleyi bitirmiyorsa ve sözcük eski çeviride büyük harfle hiç
    geçmiyorsa (ad değilse). "Yok, gerçekten\nDört gözle bekliyordum." (söz eşitleme yanıtı, Kodi 1.0.16 SPY E02 310;
    S29)."""
    es, ys = eski.split("\n"), yeni.split("\n")
    for k in range(1, len(ys)):
        if k >= len(es):
            break
        m = re.match(r"(\s*(?:<i>)?\s*)([A-ZÇĞİÖŞÜ])([a-zçğıöşüâîû]+)", ys[k])
        e = re.match(r"\s*(?:<i>)?\s*([a-zçğıöşüâîû])", es[k])
        if not (m and e) or re.search(r"[.!?…:\"”»]\s*(?:</i>)?\s*$", ys[k - 1]):
            continue
        sozcuk = m.group(2) + m.group(3)
        if re.search(r"(?<![\w])" + re.escape(sozcuk) + r"(?![\w])", eski):
            continue
        ys[k] = m.group(1) + ("ı" if m.group(2) == "I" else "i" if m.group(2) == "İ" else m.group(2).lower()) \
            + ys[k][m.end(2):]
    return "\n".join(ys)


_YUMUSA = {"p": "b", "ç": "c", "t": "d", "k": "ğ"}


_T_YUMUSAYAN = {"geçit", "üstat", "üstad", "velet", "kanat", "umut", "ümit", "armut", "kilit", "kağıt", "kâğıt", "damat",
                "murat", "yoğurt", "inat"}


def temizlik_t_yumusar(kok):
    """Sonu t olan çok heceli sözcük ünlüyle başlayan ekte d'ye yumuşar mı? Yalnız bilinen sözcüklerde."""
    return kok.lower().split()[-1] in _T_YUMUSAYAN


def kucuk_terim_kesmesi(metin, sozcukler):
    """Türkçede kesme işareti yalnız özel adda olur: sözlükteki küçük harfli terim ("baba", Anya'nın babasına
    dediği; "elf") ek alırken kesme almaz: "baba'nın" -> "babanın", "Baba'yla" -> "Babayla". Model sözlükte gördüğü
    sözcüğü ad sanıp kesme koyuyordu (gerçek test SPY x FAMILY, LOTR "bir elf'in"). Çok heceli sözcüğün sonundaki
    p, ç, t, k ünlüyle başlayan ekte yumuşar ("babacık'ı" -> "babacığı"). Cümle içinde büyük harfle yazılan
    (ad gibi kullanılan: "Köpekçik'in düşüncesi") dokunulmaz."""
    for soz in sozcukler:
        def birlestir(m):
            kok, ek = m.group(1), m.group(2)
            # Cümle içinde büyük harfle yazılmışsa model onu ad olarak kullanıyor (köpeğe ad gibi "Köpekçik"): kalır
            # (cümle başı: satır başı, tire, tırnak, "!", "?" ya da tek nokta; üç nokta cümleyi bitirmez)
            if kok[0].isupper() and not re.search(r"(?:^|(?<![.…])\.\s+|[!?]\s+|[\n\-–—\"“]\s*)$",
                                                  m.string[:m.start()]):
                return m.group(0)
            son = kok[-1].lower()
            # Çok heceli sözcükte p, ç, k ünlüyle başlayan ekte yumuşar; t çoğunlukla yumuşamaz (hobbitin, laneti,
            # cumhuriyetin): yalnız yumuşadığı bilinen sözcüklerde d olur (geçidi, üstadı, veledi). Kayıtlardaki 46
            # kesmeli t ekinin 44'ü yumuşamayan sözcüktü, "hobbidin" diye yazılıyordu (S51).
            t_yumusar = temizlik_t_yumusar(kok)
            if (son in _YUMUSA and ek[:1].lower() in "aeıioöuü" and (son != "t" or t_yumusar)
                    and len(re.findall(r"[aeıioöuüâîû]", kok.lower())) > 1):
                yumusak = "g" if son == "k" and kok[-2:-1].lower() == "n" else _YUMUSA[son]
                kok = kok[:-1] + (yumusak.upper() if kok[-1].isupper() else yumusak)
            return kok + ek
        buyuk = "İ" if soz[0] == "i" else "I" if soz[0] == "ı" else soz[0].upper()
        desen = rf"(?<![\wçğıöşü'’])((?:{re.escape(soz[0])}|{re.escape(buyuk)}){re.escape(soz[1:])})['’]([a-zçğıöşü]+)"
        metin = re.sub(desen, birlestir, metin)
    return metin


_AKRABA_IYELIK = re.compile(
    r"(?<![\wçğıöşü'’])((?:[Aa]nne|[Bb]aba|[Aa]bla|[Aa]mca|[Dd]ayı|[Tt]eyze|[Hh]ala|[Dd]ede|[Nn]ine|[Kk]oca|[Kk]arı|"
    r"[Aa]bi|[Bb]abaanne|[Aa]nneanne)(?:m|n|mız|miz|nız|niz)|(?:[Aa]ğabey|[Kk]ardeş|[Ee]ş)(?:im|in|imiz|iniz)|"
    r"[Oo]ğl(?:um|un|umuz|unuz)|[Kk]ız(?:ım|ın|ımız|ınız))['’]([a-zçğıöşü]+)")


def akraba_kesmesi(metin):
    """Birinci ya da ikinci kişi iyelik eki almış akrabalık adı ad değildir, ek alırken kesme almaz: "Annen'e" ->
    "Annene", "Babam'a" -> "Babama" (gerçek test 69, Odyssey 350 / 1509). Üçüncü kişi iyeliği ("Yüzük Kardeşliği'nin")
    ve eksiz yazılan ad ("Nine'la", "Baba'yla") dokunulmaz. 473 kayıtlı çeviride 8 kez (S49)."""
    return _AKRABA_IYELIK.sub(r"\1\2", metin)


def kana_latinle(metin):
    """Metindeki kana öbeklerini okunuşuyla Latin harfe çevirir (ヴォルケン・エ・レーゲン -> "Voruken e Reegen");
    model Latin harfli çıktıda kaynağın kanasını aynen bırakınca satır silinmesin diye: ses yine okunur."""
    if re.search(r"[一-龥々]", metin):
        return metin                     # kanjili metin okunamaz: yalnız kanalı kalanı harf harf yazmak anlamsız olur
    def cevir(m):
        latin = kana_latin(m.group(0))
        return latin if latin else m.group(0)
    sonuc = re.sub(r"[ぁ-ゖァ-ヺー][ぁ-ゖァ-ヺー・]*", cevir, metin)
    return re.sub(r"(^|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), sonuc)


def tr_ek_uyumu(metin):
    """ "Mob-kun'ın" -> "Mob-kun'un", "Yor-san'e" -> "Yor-san'a", "Anya-sama'a" -> "Anya-sama'ya",
    "Sensei'a" -> "Sensei'ye": model ünlü uyumunu ve kaynaştırma harfini Japonca ekte şaşırabiliyor."""
    def duzelt(m):
        # Desen harf büyüklüğüne bakmaz ve "i" Türkçe İ / ı ile de eşleşir ("SENSEİ'YE"): tablo ona göre aranır.
        ses = _EK_SESI.get(m.group(1).lstrip("-").replace("İ", "i").replace("ı", "i").lower())
        if ses is None:
            return m.group(0)
        dar, genis, unlu = ses
        ek = tr_kucuk(m.group(3))
        y, n = ("y", "n") if unlu else ("", "")
        if ek[0] in "dt":                                  # bulunma / ayrılma: ek sesli ya da "n" ile biter
            yeni = "d" + genis + ("n" if ek.endswith("n") else "")
        elif ek.endswith("n"):                             # ilgi
            yeni = n + dar + "n"
        elif "l" in ek:                                    # vasıta
            yeni = y + "l" + genis
        elif ek[-1] in "ıiuü":                             # belirtme
            yeni = y + dar
        else:                                              # yönelme
            yeni = y + genis
        if m.group(3).isupper():                           # bağıran replik: ek de büyük harfle kalır
            yeni = yeni.replace("i", "İ").upper()
        return m.group(1) + m.group(2) + yeni
    return _EKLI_HAL.sub(duzelt, metin)


# Künye ve reklam. Kesin olanlar dosyanın her yerinde aranır; konuşmada da geçebilenler ("Translated from Elvish,
# it means friend.", "It's called TheFacebook.com.") yalnızca künyenin durduğu yerde, dosyanın başında ve sonunda.
REKLAM = re.compile(
    r"https?://|\bwww\.\w|opensubtitles|subscene|addic7ed|podnapisi|advertise your product|support us and become"
    r"|^\s*(?:çeviri|çeviren|çevirmen|altyazı|altyazi|senkron|redaksiyon)\s*:|字幕\s*[:：]|翻訳\s*[:：]"
    # Çince fansub künyesi (Japonca altyazıya karışmış: 诸神字幕组, 翻译:, 校对:, 时间轴)
    r"|字幕[组組]|翻[译譯]\s*[:：]|校[对對]\s*[:：]|时间轴|時間軸|仅供交流|僅供交流|压制\s*[:：]|壓制\s*[:：]", re.I | re.M)
KENAR_REKLAM = re.compile(
    r"\b[\w-]+\.(?:com|net|org|tv|io|info|xyz)\b|\b(?:yify|YIFY|yts|YTS)\b"
    r"|^(?:<i>|\W)*(?i:subtitles?|subs|subtitled|captions?|captioning|sync(?:ed|hronized)?|corrected|translated|translation|"
    r"encoded|ripped|downloaded)\s+(?:and\s+\w+\s+)?(?i:by|from)\b", re.M)


def reklam_mi(metin, kenarda=True):
    """Site adresi ya da altyazı künyesi/reklamı: konuşma değildir, çevrilmez. kenarda: altyazı dosyanın başında ya da
    sonunda mı (konuşmada da geçebilen kalıplar yalnızca orada künye sayılır)."""
    duz = ETIKET.sub("", metin)
    return bool(REKLAM.search(duz) or (kenarda and KENAR_REKLAM.search(duz)))


def kaynak_hazirla(metin):
    """Modele gitmeden önce: kodları ayıklar, üstte gösterme bilgisini saklar."""
    ustte = bool(USTTE.search(metin))
    # Sondaki ➡ cümlenin sonraki altyazıda sürdüğünü söyler: Japonca altyazının sürdürme çizgisine (—) çevrilir ki
    # bölünmüş cümle tanınsın (cumle.SURDURME); öteki yayın işaretleri _ortak'ta atılır.
    # Cümle bitmişse ("ルフィ～！➡": konuşan sürüyor, cümle değil) işaret yalnız atılır.
    metin = re.sub(r"(?<![。！？!?…])[ \t]*[" + OK + r"][ \t]*$", "—", metin.rstrip())
    return _bosluk(japonca_duzelt(_ortak(metin))), ustte


_SURDURME_SON = re.compile(r"[—―]\s*((?:</i>)?)\s*$")
_CEVIRI_CIZGI = re.compile(r"\s*[—―–-]+\s*((?:</i>)?)\s*$")


def surdurme_cizgisi_sil(kaynak, ceviri):
    """Latin dışı kaynakta altyazı sonundaki — cümlenin sonraki altyazıda sürdüğünü gösteren yazım işaretidir (Japonca
    altyazı; yayın oku ➡ ➨ da buna çevrilir). Söz değildir; model bazen çeviriye taşıyor ("Senin yeteneklerinle—",
    Black Clover'da bölüm başına 7-15 altyazı). Kaynak böyle bitiyorsa çevirinin sonundaki çizgi silinir (S58).
    Latin harfli kaynakta "--" yarıda kesilen sözdür: dokunulmaz."""
    if not ceviri or not latin_disi(kaynak) or not _SURDURME_SON.search(kaynak.rstrip()):
        return ceviri
    yeni = _CEVIRI_CIZGI.sub(lambda m: m.group(1), ceviri.rstrip())
    return yeni if re.search(r"[^\W\d_]", ETIKET.sub("", yeni)) else ceviri


_JP_HARF = re.compile(r"[ぁ-ゖァ-ヺ一-龥ｦ-ﾟ]")
_YARIM_KANA = re.compile(r"[｡-ﾟ]+")
# Satır başındaki yarım genişlikli konuşmacı etiketi ((ワポル), [ｽﾋﾟｰｶ](ワポル), ≪(水夫)): tam genişlikliye çevrilip
# satırın başına alınır; yoksa etiket tanınmıyor, çeviriye "(Wapol)" ya da "Wapol:" diye giriyordu (One Piece, TV kaydı).
_YARIM_ETIKET = re.compile(r"^((?:\[[^\[\]]{1,12}\]|[≪《])*)\(([^()（）]{1,24})\)")


_IKI_NOKTA_ETIKET = re.compile(r"^((?:<i>|≪|《)*)([ァ-ヺー一-龥々0-9０-９]{1,12})：")


def japonca_duzelt(metin):
    """Japonca altyazının yayın biçimini tek biçime indirir:
    - yarım genişlikli katakana (ﾓﾙｶﾞﾝｽﾞ) tam genişlikli olur;
    - satır başındaki yarım parantezli etiket (ワポル) tam parantezli olur ve öndeki işaretlerden önce gelir;
    - 《…》 (iç ses) ve satır başındaki ≪ (ekran dışı ses) italik olur: çeviride düşünce ve ekran dışı ses hep aynı
      biçimde (italik) yazılır; 《》 tırnağa çevriliyor ya da düşüyordu."""
    if not _JP_HARF.search(metin):
        return metin
    metin = _YARIM_KANA.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), metin)
    satirlar = []
    for satir in metin.split("\n"):
        m = _YARIM_ETIKET.match(satir)
        if m and _JP_HARF.search(m.group(2)) and not re.search(r"[音声]$", m.group(2)):
            satir = f"（{m.group(2)}）{m.group(1)}{satir[m.end():]}"
        # ⸨ / ｟ satır başında ekran dışı sesi açar (≪ gibi), ⸩ / ｠ kapatır: söz değildir (S56)
        satir = re.sub(r"^((?:<i>)?)[⸨｟]", r"\1≪", satir)
        satir = re.sub(r"[⸨⸩｟｠]", "", satir)
        # "ダンテ：あぁ…" / "<i>ナハト：…</i>" / "≪アシェラッド：…": adı iki noktayla veren konuşmacı etiketi parantezli
        # etikete çevrilir, öndeki işaretlerden önce gelir (S57; çeviriye "Dante: Ah..." diye giriyordu). Ad hiragana
        # içermez (katakana, kanji, sayı + 人) ve yalnız rakam değildir ("１０：３０" saat kalır).
        m = _IKI_NOKTA_ETIKET.match(satir)
        if m and not re.fullmatch(r"[0-9０-９]+", m.group(2)) and satir[m.end():].strip(" \t</i>"):
            satir = f"（{m.group(2)}）{m.group(1)}{satir[m.end():]}"
        on = re.match(r"(（[^（）]{1,24}）)?(\s*)≪", satir)
        if on:
            ic = satir[on.end():].replace("≫", "").strip()
            satir = f"{on.group(1) or ''}<i>{ic}</i>" if ic else (on.group(1) or "")
        satirlar.append(satir)
    metin = "\n".join(satirlar)
    if "《" in metin or "》" in metin:
        # Birden çok satıra ya da iki altyazıya bölünmüş düşünce: her satır kendi içinde açılıp kapanır
        duzelen = []
        for satir in metin.replace("《", "<i>").replace("》", "</i>").split("\n"):
            ac, kapa = satir.count("<i>"), satir.count("</i>")
            if ac > kapa:
                satir += "</i>"
            elif kapa > ac:
                e = re.match(r"（[^（）]{1,24}）", satir)
                satir = (e.group(0) + "<i>" + satir[e.end():]) if e else "<i>" + satir
            duzelen.append(satir)
        satirlar = duzelen
    return re.sub(r"<i>\s*</i>", "", "\n".join(satirlar))


def _italik_onar(metin):
    ac, kapa = metin.count("<i>"), metin.count("</i>")
    if ac == kapa:
        return metin
    if ac == kapa + 1:
        return metin + "</i>"
    if kapa == ac + 1:
        return "<i>" + metin
    return metin.replace("<i>", "").replace("</i>", "")


def _bos_satir_mi(satir):
    duz = ETIKET.sub("", satir).strip()
    return not duz or all(k in "-–—♪♫# " for k in duz)


def isaretsiz(metin):
    """♪ / ♫ işaretleri kaldırılır, her satırdaki boşluklar toparlanır (satırlar korunur)."""
    return "\n".join(" ".join(re.sub(r"[♪♫]", "", s).split()) for s in metin.split("\n"))


def son_temizlik(metin, modlar, latin=False):
    """Çıktıya yazılmadan önceki son geçiş. Boş dönerse altyazı silinir."""
    metin = _ortak(metin).replace("⟦", "").replace("⟧", "")
    metin = re.sub(r"[ \t]*\|[ \t]*", " ", metin)          # kesme işareti hiçbir koşulda ekrana çıkmaz
    if latin:
        metin = noktalama_latin(metin)
    if modlar["koseli_not"] == "sil":
        metin = re.sub(r"\[[^\[\]]*\]", "", metin)
    if modlar["sarki_isareti"] == "sil":
        metin = re.sub(r"[♪♫]", "", metin)
        metin = "\n".join(re.sub(r"^\s*#+\s*|\s*#+\s*$", "", s) for s in metin.split("\n"))
    if modlar["italik"] == "sil":
        metin = metin.replace("<i>", "").replace("</i>", "")
    else:
        metin = _italik_onar(metin)
    satirlar = []
    for s in metin.split("\n"):
        m = re.match(r"\s*(<i>)?\s*", s)
        on, govde = m.group(1) or "", s[m.end():]
        if modlar["isim_etiketi"] == "sil":
            govde = ISIM.sub(lambda e: e.group(0) if duyuru_etiketi(e.group(0)[len(e.group(1) or ""):])
                             else e.group(1) or "", govde)
        if modlar["konusmaci_tiresi"] == "sil":
            govde = re.sub(r"^" + TIRE + r"\s*", "", govde)
        govde = re.sub(r"^[:;,]\s*", "", govde)
        satirlar.append(on + govde)
    metin = "\n".join(satirlar)
    metin = re.sub(r"\(\s*\)|\[\s*\]", "", metin)
    metin = re.sub(r"<i>\s*</i>", "", metin)
    metin = _bosluk(metin)
    metin = "\n".join(s for s in metin.split("\n") if not _bos_satir_mi(s))
    if modlar["italik"] != "sil":
        metin = _italik_onar(metin)
    return metin


def kalinti_tara(metin):
    """İzinli <i> dışında kalan şüpheli işaretleri döndürür."""
    duz = metin.replace("<i>", "").replace("</i>", "")
    return "".join(sorted(set(re.findall(r"[<>{}\\⟦⟧]", duz))))


KALINTI = re.compile(r"[{}\\]|<(?!/?i>)|(?<!<i)(?<!</i)>")


def kalinti_isaretleri(metin, kaynak=""):
    """Çeviride kalmış kod ya da JSON parçası: { } \\ ve <i> dışındaki < >. Kaynakta da geçen işaret sayılmaz. Korunan
    parçanın ⟦ ⟧ işaretleri sayılmaz (modelin yanıtında olağan, sonra silinir)."""
    return "".join(sorted({k for k in KALINTI.findall(metin) if k not in kaynak}))


def kalinti_sil(metin, kaynak=""):
    """Yeniden sorulduğunda da kalıntı işaretli dönen çeviriden işaretleri ve yanlarındaki tırnak/virgül artığını
    siler ("Şey, o...“},{" -> "Şey, o..."; gerçek test 69, SPY E02 410)."""
    isaret = kalinti_isaretleri(metin, kaynak)
    if not isaret:
        return metin
    metin = re.sub(r"</?(?!i>)[^<>\s]{0,10}>", " ", metin)       # bozuk etiket: "Sağa!</>" (Interstellar 1565)
    sinif = "".join(re.escape(k) for k in isaret)
    metin = re.sub(r"[\"“”,:\s]*[" + sinif + r"]+[\"“”,:]*", " ", metin)
    return "\n".join(" ".join(x.split()) for x in metin.split("\n")).strip()


_ILK_HARF = re.compile(r"^((?:<[^>]+>|[-–—♪♫\s\"“'‘(\[¿¡])*)([^\W\d_])")
_CUMLE_SONU = re.compile(r"(?<!\.\.)[.!?][\"”’')\]]*(?:</i>)?\s*$")


def cumle_basi_buyut(onceki_kaynak, kaynak, ceviri, tr=True, onceki_ceviri=None):
    """Kaynakta yeni cümleyle (önceki altyazı . ! ? ile bitmiş, bu altyazı büyük harfle başlıyor) başlayan altyazının
    çevirisi küçük harfle başlıyorsa ilk harfi büyür ("lembas." -> "Lembas."; gerçek test 69, LOTR 1408). Diyalogda
    her konuşmacının satırı ayrı bakılır (tire yeni söz demektir). Üç noktayla biten ya da süren cümleye dokunulmaz;
    önceki altyazının çevirisi de cümleyi bitirmiş olmalı (model iki kaynak cümlesini tek cümle yapmış olabilir:
    "En hızlı gemini alıp / en iyi tayfanı da yanına."; Odyssey 233-234)."""
    def buyut(m):
        h = m.group(2)
        if tr and h == "i":
            # Kaynaktaki ad Latin I ile yazılıyorsa öyle kalır: "isildur'un" -> "Isildur'un" ("İsildur" değil)
            soz = re.match(r"[^\W\d_]+", m.string[m.end(2) - 1:]).group(0)
            if re.search(r"(?<![^\W\d_])I" + re.escape(soz[1:]), kaynak):
                return m.group(1) + "I"
        return m.group(1) + (("İ" if h == "i" else "I" if h == "ı" else h.upper()) if tr else h.upper())

    def buyuk_mu(metin):
        m = _ILK_HARF.match(metin.strip())
        return bool(m) and m.group(2).isupper() and not re.match(r"\s*(?:\.\.\.|…)", re.sub(r"<[^>]+>", "", metin))

    k_satir = [x for x in kaynak.split("\n") if x.strip()]
    c_satir = ceviri.split("\n")
    diyalog = (len(k_satir) == len(c_satir) > 1 and all(re.match(r"\s*(?:<[^>]+>)*\s*[-–—]", x) for x in k_satir)
               and all(re.match(r"\s*(?:<[^>]+>)*\s*[-–—]", x) for x in c_satir))
    # İlk satır yalnız önceki cümle bittiyse yeni cümledir (diyalogda da: "- Than you could ever be." önceki
    # altyazının cümlesini sürdürüyor; House of the Dragon 634). Diyalogun sonraki satırları yeni konuşmacıdır.
    yeni_cumle = ((onceki_ceviri is None or not onceki_ceviri.strip() or _CUMLE_SONU.search(onceki_ceviri))
                  and (not onceki_kaynak.strip() or _CUMLE_SONU.search(onceki_kaynak)))
    if diyalog:
        return "\n".join(_ILK_HARF.sub(buyut, c, count=1) if buyuk_mu(k) and (n or yeni_cumle) else c
                         for n, (k, c) in enumerate(zip(k_satir, c_satir)))
    if yeni_cumle and k_satir and buyuk_mu(k_satir[0]):
        return _ILK_HARF.sub(buyut, ceviri, count=1)
    return ceviri


def latin_disi(metin):
    """Latin alfabesi dışındaki harfleri döndürür (boşsa sorun yok)."""
    return "".join(sorted({k for k in metin
                           if (k.isalpha() and unicodedata.category(k) != "Lm"
                               and "LATIN" not in unicodedata.name(k, ""))
                           or 0x3000 <= ord(k) <= 0x30FF or 0xFF00 <= ord(k) <= 0xFFEF}))


def latin_disi_sil(metin):
    kotu = set(latin_disi(metin))
    return "".join(k for k in metin if k not in kotu)


def gorunen_uzunluk(satir):
    return len(ETIKET.sub("", satir))


ONE_BAGLANAN = {"bir", "bu", "şu", "o", "her", "en", "çok", "daha", "pek", "ne", "hiç", "tek", "son", "ilk",
                "the", "a", "an", "of", "to", "in", "and", "der", "die", "das", "le", "la", "el", "de"}


def _bolme_cezasi(kelimeler, i):
    """Satırın i. sözcükten önce bölünmesi ne kadar kötü? Dengeli ama anlamı bölen yer yerine doğal yer seçilsin."""
    onceki, sonraki = ETIKET.sub("", kelimeler[i - 1]), ETIKET.sub("", kelimeler[i])
    if onceki[-1:] in ",.!?;:…":
        return -6                                   # noktalamadan sonra bölmek en doğalı
    ceza = 0
    if onceki[:1].isupper() and sonraki[:1].isupper() and i >= 2:
        ceza += 14                                  # özel adın ortası: "Kıyamet / Dağı", "Orta / Dünya"
    if onceki.lower() in ONE_BAGLANAN:
        ceza += 8                                   # sonraki sözcüğe bağlanan kısa sözcük satır sonunda kalmasın
    if sonraki[-1:] in ".!?…" and i < len(kelimeler) - 1:
        ceza += 10                                  # cümlenin son sözcüğü alt satıra tek başına düşmesin ("yolculuk / et.")
    return ceza


def sar(metin, azami):
    """Uzun satırı en fazla iki dengeli satıra böler. Tireli ve şarkılı satırlara dokunmaz."""
    satirlar = metin.split("\n")
    if not metin or (len(satirlar) <= 2 and all(gorunen_uzunluk(s) <= azami for s in satirlar)):
        return metin
    if "♪" in metin or any(re.match(r"\s*(<i>)?\s*(" + TIRE + r"|\()", s) for s in satirlar):
        return metin
    tek = " ".join(satirlar)
    if gorunen_uzunluk(tek) <= azami:
        return tek
    kelimeler = tek.split(" ")
    en_iyi, en_puan = tek, None
    for i in range(1, len(kelimeler)):
        ust, alt = " ".join(kelimeler[:i]), " ".join(kelimeler[i:])
        puan = abs(gorunen_uzunluk(ust) - gorunen_uzunluk(alt)) + _bolme_cezasi(kelimeler, i)
        puan += 100 * (gorunen_uzunluk(ust) > azami) + 100 * (gorunen_uzunluk(alt) > azami)
        if en_puan is None or puan < en_puan:
            en_iyi, en_puan = ust + "\n" + alt, puan
    return en_iyi
