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
YAYIN_ISARETI = re.compile(r"[ \t]*[➡⇒][ \t]*|[🔊📱📞☎📺][ \t]*")


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
                       r"(?:pers|iskoç|aztek|inka|rus|alman|moğol|kürt|ermeni|gürcü|arnavut|macar|fars|portekiz|latin|"
                       r"çin|hint|amerikan?|afrika|avrupa|asya)(?=l[ae]r|l[ıi]|[cç][ae]|[^\wçğıöşü]|$))(?=[a-zçğıöşü'’]|[^\w]|$)")


def tr_buyuk_harf(metin):
    """ "müslümanlar" -> "Müslümanlar", "tanrı'nın" -> "Tanrı'nın" (kesme işareti yalnız özel adda olur)."""
    metin = _TR_BUYUK.sub(lambda m: ("İ" if m.group(1)[0] == "i" else m.group(1)[0].upper()) + m.group(1)[1:], metin)
    return re.sub(r"(?<![\wçğıöşü])tanrı(?=['’])", "Tanrı", metin)


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
    metin = re.sub(r"(?<![。！？!?…])[ \t]*[➡⇒][ \t]*$", "—", metin.rstrip())
    return _bosluk(japonca_duzelt(_ortak(metin))), ustte


_JP_HARF = re.compile(r"[ぁ-ゖァ-ヺ一-龥ｦ-ﾟ]")
_YARIM_KANA = re.compile(r"[｡-ﾟ]+")
# Satır başındaki yarım genişlikli konuşmacı etiketi ((ワポル), [ｽﾋﾟｰｶ](ワポル), ≪(水夫)): tam genişlikliye çevrilip
# satırın başına alınır; yoksa etiket tanınmıyor, çeviriye "(Wapol)" ya da "Wapol:" diye giriyordu (One Piece, TV kaydı).
_YARIM_ETIKET = re.compile(r"^((?:\[[^\[\]]{1,12}\]|[≪《])*)\(([^()（）]{1,24})\)")


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
