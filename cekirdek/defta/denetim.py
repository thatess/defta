"""Çeviriden sonraki denetimler: şüpheli satırın yeniden sorulması (kayma, klişe, eksik içerik), yinelenen satır.

Okumaya süresi yetmeyen çeviri kısalttırılmaz (kısalırken anlam gidiyordu): yazım aşaması o altyazının
ekranda kalma süresini sonraki altyazıya kadarki sessizliğe doğru uzatır, metne dokunmaz."""
import difflib
import json
import re

from . import temizlik
from .ayar import dil_adi, hedef_kodu
from .cumle import BIRLESTI
from .kaynak import KANA_SOZCUK, _yalin, ciglik_mi
from .model import Iptal, Model, Ret, json_coz

SECIM_ISTEMI = """A film subtitle repeats some of its lines (a flashback, a repeated scene, a line quoted or echoed later). Each such line was translated into {dil} in more than one way. For every item with "candidates", choose the candidate that renders "source" most accurately and naturally ("before" and "after", when given, are its neighbouring source lines), so that the line reads the same every time. Choose only among the candidates.
An item with "occurrences" is one line said again elsewhere; each occurrence shows its neighbouring source lines and which candidate it has now. If it is the same words said in the same sense (a memory, an echo, a catchphrase, a punchline repeated), choose one candidate. If the occurrences are said in different situations that need different renderings (said to different people, or meaning something else there), answer "pick": 0 and every occurrence keeps its own translation."""
IFADE_ISTEMI = """Each item is a Japanese expression ("expression") that occurs in two or three lines of one film. The lines were translated into {dil} separately ("lines": each source line and its current translation).
Make the translations match only where the expression names the same particular thing (a game, an event, a gathering, an object, a place) or is the same set phrase, greeting or line said again, and the translations render it with different words: choose the best rendering already used and rewrite only the words that render the expression in the other lines. Keep everything else in those lines exactly as it is (who does what, person, tense, names, punctuation).
Leave a line alone when the expression is already rendered the same way, when it is an ordinary word whose wording can naturally vary, or when it means something else there.
Return ONLY this JSON object: {{"lines": [{{"id": 12, "text": "..."}}]}} with only the lines you changed (an empty list is fine)."""
IFADE_OZENSIZ = 2          # düşünme sayısı bilinmezse: yanıt bu kadar satırı değiştirmeden yollarsa özensizdir (S22)
YANKI = re.compile(r'^["“]?(We|Me|I|You|Us|Him|Her|Them|Mine|Ours)["”]?\s*\?$', re.I)
ARDISIK_UZAKLIK = 3        # art arda yinelenen replik en çok kaç altyazı sonra aranır (araya giren konuşmalar)
ARDISIK_BOSLUK = 150       # ms: ilkinin bitişi ile ikincinin başlangıcı arasındaki en büyük boşluk


# Yinelenen satırı tanırken atılanlar: konuşmacı etiketi ve ses notu （カミラ）, satır başındaki "Ad:" etiketi,
# tırnaklar ve satır kırılması. Bir geçişte etiketli ("Gandalf: ...", "（カミラ）...") ya da başka yerden bölünmüş
# yazılan aynı söz öbür geçişiyle eşleşmiyordu (gerçek test SPY x FAMILY 2. bölüm 53/134, LOTR 1176/1592).
_TEKRAR_NOT = re.compile(r"（[^（）]{1,40}）|\([^()]{1,40}\)|\[[^\[\]]{1,40}\]")
_TEKRAR_ETIKET = re.compile(r"^\s*(?:[-–—]\s*)?[A-ZÇĞİÖŞÜ][\w'’.-]*(?: [A-ZÇĞİÖŞÜ][\w'’.-]*){0,2}:\s+(?=\S)", re.M)
_TEKRAR_TIRNAK = re.compile(r"[\"“”„«»「」『』〝〟]")
# Sözü süren satır: sonu ya da başı bağlaçla, virgülle, üç noktayla, tireyle kesik (yarım cümle).
_YARIM_SON = re.compile(r"[,、…‥—–\-:;]\s*$")
_YARIM_BAS = re.compile(r"^\s*(?:[…‥]|\.\.\.|[a-zçğıöşü])")


def tekrar_metni(metin):
    """Yinelenen satırı tanımak için sözün kendisi: etiket, not, tırnak ve satır kırılması olmadan (harf korunur)."""
    m = temizlik.ETIKET.sub("", metin)
    m = _TEKRAR_ETIKET.sub("", m)
    m = _TEKRAR_NOT.sub(" ", m)
    m = _TEKRAR_TIRNAK.sub("", m)
    m = re.sub(r"^\s*[-–—]\s*", "", m, flags=re.M)
    return " ".join(m.split())


def ardisik_mi(once, sonra):
    """İki altyazının süreleri bitişik mi (biri biterken öteki başlıyor)."""
    return -ARDISIK_BOSLUK <= sonra.bas - once.bit <= ARDISIK_BOSLUK


KLISE = {"tr": re.compile(r"(?<!allah )kahretsin|kahrolası(?![nc])|lanet olası|lanet olsun|seni aptal|seni pislik|"
                         r"(?<![\wçğıöşü])lanet(?=\s+(?!et|ed|oku|ol|"
                         # beddua anlamındaki ad: "Lanet değil.", "lanet mi okudun?", "bir lanet var" (Odyssey "Not a
                         # curse." / "You cursed them?": doğru çeviri klişe sayılıp "sövgü" yapılıyordu; S33)
                         r"(?:değil|mi|mı|mu|mü|var|yok|da|de|gibi|ki)(?![\wçğıöşü]))[\wçğıöşü])")}

# Bip'li pekiştiricinin nitelik gibi çevrilmesi ("[bleep] raw" -> "bok gibi çiğ"): bip bir sözcüğün önündeyse ve
# kaynakta benzetme ("like") yoksa çevirideki bu kalıp pekiştiriciyi niteliğe çevirmiştir.
BIP_ONUNDE = re.compile(r"[\[(]\s*bleep(?:ed|ing)?\s*[\])]\s+[a-z]", re.I)
NITELIK_KUFUR = {"tr": re.compile(r"bok gibi")}
# Cümlenin ortasına, İngilizce sözcüğün yerine düşmüş küfür kalıbı ("ortalığı amına koyayım savaş alanına çeviriyorsun").
ARAYA_KUFUR = {"tr": re.compile(r"[\wçğıöşü]\s+(amına koyayım|amk)\s+[\wçğıöşü]|[\wçğıöşü],\s*(lan),\s*[\wçğıöşü]")}

# Japonca kaynağa çok ağır gelen küfür (kural: Japonca küfür İngilizce karşılığından hafiftir). "やられたな" ->
# "Ağzıma sıçtı" (Death Note, gerçek test I). "sıçramak" ve "sıçan" küfür değildir: "kan sıçrarsa belli olur" doğru
# çeviriydi, yeniden sorulunca anlamı kayboldu (Kodi 1.0.12, SPY x FAMILY S01E03 256).
JP_AGIR = {"tr": re.compile(r"siktir|(?<![\wçğıöşü])sik(?:er|ti|iyim|eyim)|(?<![\wçğıöşü])sıç(?!ra|an)|amına|(?<![\wçğıöşü])amk(?![\wçğıöşü])|orospu|"
                           r"(?<![\wçğıöşü])piç|yarra|götveren")}
JAPONCA = re.compile(r"[ぁ-ゖァ-ヺ]")

# Hedef dilde yazılmamış tepki sesi (İngilizce ya da Japonca okunuşuyla kalmış): satırın tamamı ya da başı.
YABANCI_SES = {"tr": re.compile(r"(?<![\wçğıöşü])(u+gh+|a+r+gh+|u+wa+h*|n+g+h+|hmph|oops|ouch|ga+h|e+w+|whoa+|yikes|phew)(?![\wçğıöşü])")}


YER_TUTUCU = re.compile(r"(?:\.\.\.|…)['’][a-zçğıöşüA-ZÇĞİÖŞÜ]")
# Türkçede tek başına sözcük olan tek harf yalnız ünlüdür (o, e, a); tek ünsüz bozulmuş sözcüktür. Sayıdan sonraki
# birim (5 m, 10 g) ve kesmeli kısaltma sayılmaz.
TEK_HARF = {"tr": re.compile(r"(?<![\w'’.\-])(?<!\d )[bcçdfgğhjklmnprsştvyz](?![\w'’.\-])")}


def bolunmus_sozcuk_birlestir(metin, kod):
    """Modelin Japonca boşlukla (U+3000) böldüğü sözcüğü birleştirir ("n\u3000eredeyse" -> "neredeyse"; LOTR 360,
    gerçek test 68). Boşluk Latin noktalamaya çevrilince "n eredeyse" kalıyor, kopuk harf sorusuna İngilizce yanıt
    geliyordu ("nearly completely disappeared."). Yalnız kopuk tek ünsüzden sonra (TEK_HARF; "A grubu", "o gün"
    gibi gerçek tek harfli sözcüklere dokunulmaz). 546 ham kayıtta çeviride U+3000 bir kez geçiyor, o da bu (S46)."""
    tek = TEK_HARF.get(kod)
    if not tek or "\u3000" not in metin:
        return metin
    return re.sub("(" + tek.pattern + r")\u3000(?=[a-zçğıöşü])", r"\1", metin)


LATIN_KUCUK_SOZ = re.compile(r"(?<![^\W\d_])[a-z][a-z'’]{2,}(?![^\W\d_])")


def kaynak_sizintisi(kaynak, eski, yeni, kod):
    """Şüphe sorusunun yanıtı kaynak dile kaymışsa kaynaktan gelen küçük harfli sözcükleri döndürür: yanıtta olup
    önceki çeviride olmayan, kaynakta da küçük harfle geçen sözcükler ("nearly completely disappeared.", "frakking
    karıncalar", "Jack and Coke"). Adlar büyük harfle başladığı için sayılmaz (Sauron, Hari Seldon kaymayı düzeltirken
    geri konur). 1172 kabul edilmiş şüphe yanıtında 3 sızıntının 3'ünü yakalıyor, yanlış yakaladığı yok (S46)."""
    if kod == "en":
        return []
    kaynakta = set(LATIN_KUCUK_SOZ.findall(temizlik.ETIKET.sub("", kaynak)))
    if not kaynakta:
        return []
    oncede = {w.lower() for w in LATIN_KUCUK_SOZ.findall(eski.lower())}
    return [w for w in dict.fromkeys(LATIN_KUCUK_SOZ.findall(temizlik.ETIKET.sub("", yeni)))
            if w in kaynakta and w not in oncede]


def kopuk_harf_sil(metin, kod):
    """Sonraki sözcüğün ilk harfiyle aynı olan kopuk tek harfi siler ("ağaçların\nt tepelerinden" -> "ağaçların\n
    tepelerinden"): model sözcüğe başlayıp satırı kırmış, sonra sözcüğü baştan yazmış. Yeniden sormak gerekmez; soru
    satırın başka yerini de değiştiriyordu ("s size" -> "sana": siz -> sen; SPY 388). Kayıtlarda bu türden 8 örneğin
    8'inde silmek doğru sonucu veriyor (S41). Harf sonraki sözcüğün başı değilse ("s destan", "n eredeyse") dokunulmaz,
    o satır yine sorulur."""
    tek = TEK_HARF.get(kod)
    if not tek:
        return metin
    desen = re.compile("(" + tek.pattern + r")[ \t]+(?=(\S))")
    return desen.sub(lambda m: "" if temizlik.tr_kucuk(m.group(2)) == m.group(1) else m.group(0), metin)


def _bip_pekistirici(kaynak):
    """Bip bir sözcüğün önünde ve kaynakta benzetme yok: pekiştiricidir ("[bleep] raw", "the [bleep] grill")."""
    return bool(BIP_ONUNDE.search(kaynak)) and not re.search(r"\blike\b", kaynak, re.I)


def _duz(metin):
    return temizlik.ETIKET.sub("", metin).replace("⟦", "").replace("⟧", "")


def _son_parca(ceviri):
    """Bölünmüş cümlenin çevirisindeki son parça: son " | " işaretinden sonrası; işaret yoksa, birden çok cümle
    varsa son cümle; yoksa boş."""
    duz = ceviri.replace("\n", " ").strip()
    if "|" in duz:
        return duz.rsplit("|", 1)[1].strip()
    cumleler = re.split(r"(?<=[.!?…])\s+", duz)
    return cumleler[-1].strip() if len(cumleler) > 1 else ""


def icinde_yinelenir(x, y):
    """y'nin çevirisi x'in çevirisinin içinde (neredeyse) aynen geçiyor ve x ondan uzun mu? Kısa söz (20 harften az)
    sayılmaz: anlamı yineleyen kısa söz olağandır ("Ufacık bir şey yüzünden." / "Ufacık bir şey."; "Sadece biraz
    yiyecek lazım." / "Yiyecek lazım.")."""
    a, b = _yalin(x), _yalin(y)
    if len(b) < 20 or len(a) < len(b) + 5:
        return False
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size >= 0.9 * len(b)


def _kaynakta_yinelenir(once, sonra):
    """Sonraki altyazının kaynağının en az yarısı önceki cümlenin kaynağında da kesintisiz geçiyor mu?"""
    a, b = _yalin(once), _yalin(sonra)
    if not b:
        return True
    # Sözcüklerinin çoğu ötekinde de geçiyorsa (araya söz girmiş olsa da) kaynak sözü yineliyor demektir:
    # "We just need some food." / "We need food." (Odyssey 684; S50)
    sa, sb = set(re.findall(r"[^\W\d_]+", once.lower())), re.findall(r"[^\W\d_]+", sonra.lower())
    if len(sb) >= 2 and sum(1 for w in sb if w in sa) >= 0.6 * len(sb):
        return True
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size * 2 >= len(b)


class Denetimler:
    """Cevirmen'in parçası: script somut bir işaret görürse o satırı nedeniyle birlikte bir kez sorar."""

    def _kalip_supheleri(self, kaynak, kucuk, kod):
        """Çevirinin kendi sözlerinde ölçülen kalıp işaretleri: dublaj klişesi, cümle ortasına düşen küfür, bip'li
        pekiştiricinin nitelik gibi çevrilmesi. Şüphe üzerine gelen yanıt da bunlara bakılarak kabul edilir."""
        nedenler = []
        klise = KLISE.get(kod)
        m = klise.search(kucuk) if klise else None
        if m:
            neden = (f'"{m.group(0)}" is a dubbing cliché: use what a person would really say, at the same strength; '
                     "no religious word unless the source has one")
            if _bip_pekistirici(kaynak):
                neden += (f'; the bleeped word is an intensifier: drop "{m.group(0)}" entirely and let the tone or a '
                          "stronger word carry the anger")
            nedenler.append(neden)
        araya = ARAYA_KUFUR.get(kod)
        m = araya.search(kucuk) if araya else None
        if m:
            nedenler.append(f'"{m.group(1) or m.group(2)}" sits in the middle of a clause where the English word stood: put it at '
                            "the very end of the sentence, or let a stronger word or the tone carry the force")
        nitelik = NITELIK_KUFUR.get(kod)
        if nitelik and _bip_pekistirici(kaynak) and nitelik.search(kucuk):
            nedenler.append("the bleeped word in the source is an intensifier in front of a word: it carries the "
                            "speaker's anger, not a quality of the thing; let the tone or a stronger word carry the "
                            "force and translate the rest in full")
        return nedenler

    def _supheler(self, idler, kabul):
        """index -> modele sorulacak kısa neden. Yalnızca kesin ölçülebilen işaretlere bakar."""
        supheli, kod = {}, hedef_kodu(self.ayar)

        def ekle(i, neden):                  # bir satırın birden çok işareti varsa hepsi birlikte sorulur
            supheli[i] = f"{supheli[i]}; also {neden}" if i in supheli else neden

        for i in idler:
            kaynak, ceviri = self._kaynak(i), _duz(kabul[i])
            kucuk = temizlik.tr_kucuk(ceviri)
            for neden in self._kalip_supheleri(kaynak, kucuk, kod):
                ekle(i, neden)
            agir = JP_AGIR.get(kod)
            m = agir.search(kucuk) if agir and JAPONCA.search(kaynak) else None
            if m:
                ekle(i, f'"{m.group(0)}" is far too strong for a Japanese source: Japanese curses are milder than their '
                        "English glosses; use the milder word of that kind, or none if the source has none")
            # Model sözcüğün yerine yer tutucu bırakmış ("Seni ...'da bekliyor olacağım": yer sonraki altyazıda) ya da
            # sözcük bozulmuş, başka sözcüğe yapışmayan tek harf kalmış ("dillere s destan"; LOTR 462, 1117).
            if YER_TUTUCU.search(ceviri):
                ekle(i, "the translation has \"...\" with a suffix in place of a word: write the actual words here, "
                        "even if they are also said in the next subtitle")
            tek = TEK_HARF.get(kod)
            m = tek.search(ceviri) if tek else None
            if m:
                ekle(i, f'"{m.group(0).strip()}" is a stray letter, not a word: a word is broken or misspelt; write it correctly')
            ses = YABANCI_SES.get(kod)
            m = ses.search(kucuk) if ses else None
            # Kaynakta çığlık ya da inleme varsa okunuşu bilerek yazılmıştır (うわあ -> "Uwaa"): yabancı ses sayılmaz.
            if m and not any(ciglik_mi(s) for s in KANA_SOZCUK.findall(kaynak)):
                ekle(i, f'"{m.group(0)}" is not how {dil_adi(self.ayar)} speakers write this sound: write the sound '
                        f"in its {dil_adi(self.ayar)} spelling")
            # Bip'li küfür yayında duyulmaz: çeviride küfür aranmaz (yazıyla açılması istenmiyor; açılınca "lan" ve
            # "köpek boku" gibi yayından ağır çeviriler çıkıyordu, gerçek test F, 18.11.2026).
            if temizlik.latin_disi(kaynak):
                continue
            # eksik içerik şüphesi: çeviri kaynağa göre çok kısa. Çince, Japonca, Korece çeviride bakılmaz: orada bir
            # karakter birkaç Latin harfinin yerini tutar, eksiksiz çeviri de bu sınırın altında kalır.
            yalin_k = temizlik.ETIKET.sub("", kaynak)
            if (len(yalin_k) >= 45 and len(ceviri) < 0.35 * len(yalin_k)
                    and not any(ord(k) > 0x2E7F for k in ceviri)):
                ekle(i, "the translation is much shorter than the source: check that no clause was left out")
        if self.ayar["kontrol"]["kayma_kontrolu"]:
            for i, neden in self._kayma_supheleri(idler, kabul).items():
                ekle(i, neden)
        # zamiri soru olarak yineleyen replik ("We think you should stay." / "We?")
        for i in idler:
            if i - 1 not in kabul or i - 1 not in idler:
                continue
            ilk_sira = re.sub(r"^\s*[-–—]\s*", "", temizlik.ETIKET.sub("", self._kaynak(i)).split("\n")[0]).strip()
            m = YANKI.match(ilk_sira)
            if m and re.search(r"\b" + m.group(1) + r"\b", temizlik.ETIKET.sub("", self._kaynak(i - 1)), re.I):
                neden = (f'id {i + 1} echoes the word "{m.group(1)}" of id {i} as a question: both translations must '
                         f"use the same explicit pronoun so that the echo works")
                ekle(i - 1, neden)
                ekle(i, neden)
        return supheli

    def _adlari_kucuk_yazan_kaynak(self, kaynak_desen):
        """Dosya sözlükteki büyük harfli adları çoğunlukla küçük harfle mi yazıyor (en az 10 geçiş, yarıdan çoğu)?
        Dosya başına bir kez hesaplanır."""
        anahtar = (id(self.giden), len(self.giden), len(kaynak_desen))
        if getattr(self, "_kucuk_ad_kaydi", (None,))[0] != anahtar:
            kucuk = toplam = 0
            metin = "\n".join(temizlik.ETIKET.sub("", x) for x in self.giden.values())
            for a, d in kaynak_desen:
                if not a[:1].isupper():
                    continue
                for m in d.finditer(metin):
                    toplam += 1
                    kucuk += m.group(0)[:1].islower()
            self._kucuk_ad_kaydi = (anahtar, toplam >= 10 and kucuk * 2 > toplam)
        return self._kucuk_ad_kaydi[1]

    def _kayma_supheleri(self, idler, kabul):
        """Çevirinin yanlış altyazıya yazıldığını gösteren kesin işaretler: index -> neden.

        1) Komşu iki altyazının kaynağı farklı, çevirisi aynı.
        2) Kaynakta bir altyazıda geçen ad, çeviride onun yerine komşu altyazıda geçiyor.
        3) Kaynaktaki ad çeviride yok, yerinde kaynakta olmayan başka bir ad var ("Oh, Sam." -> "Ah, Frodo.").
        İşaretli satırların arasında kalanlar da sorulur: kayma tek satırda durmaz."""
        sira = [i for i in idler if i in kabul]
        adlar = [a for a in self.sozluk.degismez
                 if len(a) >= 3 and " " not in a and not temizlik.latin_disi(a)] if self.sozluk is not None else []
        # Kaynakta ad tam sözcük olarak aranır (İngilizce çoğul ve iyelik ekiyle): "hobbit" "hobbiton"un içinde
        # bulunuyordu, doğru çeviri "Baggins yok" -> "hiç hobbit yok" diye bozuldu (gerçek test 10-11, LOTR 363).
        # Çeviride Türkçe ek alır: büyük harfli ad kesmeyle (Frodo'nun), küçük harfli terim doğrudan (hobbitler).
        kaynak_desen = [(a, re.compile(r"(?<![\w'’])" + re.escape(a) + r"(?:'s|’s|s|es)?(?![\w])", re.I)) for a in adlar]
        # Küçük harfli sözlük adı çeviride cümle başında büyük harfle de geçer ("Hithlain'den", "Mithril."): ad yerinde
        # olduğu hâlde "eksik" sayılıyordu; kaynakta olmayan başka bir sözle birleşince doğru çeviri yeniden sorulup
        # bozuluyordu ("Hithlain'den elf ipi." -> "Hithlain ipi.", LOTR 1427, üç koşuda; S42).
        def ilk_harf(a):
            if not a[:1].islower():
                return re.escape(a)
            buyuk = {a[0].upper()} | ({"İ"} if a[0] == "i" else set())
            return "[" + re.escape(a[0] + "".join(buyuk)) + "]" + re.escape(a[1:])
        ceviri_desen = [(a, re.compile(r"(?<![\w'’])" + ilk_harf(a) + ("" if a[:1].islower() else r"(?![^\W\d_])")))
                        for a in adlar]

        def gecen(metin, desenler, buyuk_kucuk=True):
            return {a for a, d in desenler
                    if any(buyuk_kucuk is False or m.group(0)[:len(a)] == a for m in d.finditer(metin))}

        duz = {i: temizlik.ETIKET.sub("", kabul[i]) for i in sira}
        kaynak = {i: temizlik.ETIKET.sub("", self._kaynak(i)).replace("⟦", "").replace("⟧", "") for i in sira}
        # Eksik sayılan ad kaynakta harfi harfine geçendir; fazla sayılan ad kaynakta küçük harfle bile geçmeyendir
        # (OCR'lı kaynak adları küçük yazıyor: "bagginses", "gamgee").
        k_gevsek = {i: gecen(kaynak[i], kaynak_desen, False) for i in sira}
        # Adları küçük harfle yazan kaynakta (OCR'lı altyazı: "bagginses", "gondor") eksik ad da küçük harfle aranır:
        # "bagginses" -> "Sackville-Bagginses" yakalanmıyordu (gerçek test 14, LOTR 363). Olağan kaynakta harfi harfine
        # (sıradan sözcük "will" ad "Will" sayılmasın).
        kucuk = self._adlari_kucuk_yazan_kaynak(kaynak_desen)
        k_ad = k_gevsek if kucuk else {i: gecen(kaynak[i], kaynak_desen) for i in sira}
        c_ad = {i: gecen(duz[i], ceviri_desen, False) for i in sira}      # harf büyüklüğünü desen denetler
        kayan, neden = set(), {}
        for a, b in zip(sira, sira[1:]):
            if b - a > len(self.birlesik.get(a, {}).get("grup", [a])):
                continue                                    # arada başka (gönderilmeyen) altyazı var: komşu değiller
            if len(_yalin(duz[a])) >= 12 and _yalin(duz[a]) == _yalin(duz[b]) and _yalin(kaynak[a]) != _yalin(kaynak[b]):
                # Aynı çeviri iki kez yazıldıysa kayma daha önce başlamıştır: öncesindeki üç satır da sorulur (aşağıda).
                kayan.update((a, b))
            # Bölünmüş cümlenin son parçası sonraki altyazının çevirisiyle aynı, ama kaynakta böyle bir tekrar yok:
            # model parçayı sonraki altyazının içeriğiyle doldurmuş ("…kit in first class." -> "…veriyorlar. | Bunu
            # bilmiyor olabilirsin." / "Bunu bilmiyor olabilirsin."; gerçek test 26-32, American Gods 359-361: cümle
            # ekranda iki kez çıkıyor, bazı koşularda kayma buradan başlıyordu). Kaynakta sonraki altyazı cümlenin
            # sonunda da geçiyorsa ya da ona çok benziyorsa ("I don't like it." / "I don't like you.") tekrar gerçektir,
            # dokunulmaz.
            if a in self.birlesik:
                son = _son_parca(duz[a])
                if (len(_yalin(son)) >= 12 and len(_yalin(duz[b])) >= 12
                        and difflib.SequenceMatcher(None, _yalin(son), _yalin(duz[b])).ratio() >= 0.8
                        and not _kaynakta_yinelenir(kaynak[a], kaynak[b])):
                    kayan.update((a, b))
                    neden[a] = (f"the last part of this translation repeats the translation of id {b + 1}, which this "
                                f"line's source does not say: translate only this line's own source text")
            # Bölünmüş cümle olarak gönderilmemiş komşu iki altyazıdan birinin çevirisi ötekinin çevirisini de içeriyor,
            # kaynakta böyle bir tekrar yok: model cümlenin tamamını bir altyazıya yazıp ikinciye yinelemiş (gerçek
            # test 69, LOTR 936-937 "Yüzük, ... yok edilemez, Gléin'in oğlu Gimli..." / "Yüzük, ... yok edilemez."; S50).
            elif not _kaynakta_yinelenir(kaynak[a], kaynak[b]) and not _kaynakta_yinelenir(kaynak[b], kaynak[a]):
                for x, y in ((a, b), (b, a)):
                    if icinde_yinelenir(duz[x], duz[y]):
                        kayan.update((a, b))
                        neden[x] = (f"this translation also contains the translation of id {y + 1}, which this "
                                    f"line's source does not say: translate only this line's own source text")
                        break
            for x, y in ((a, b), (b, a)):                   # ad x'in kaynağında, ama çevirisi y'ye yazılmış
                if (k_ad[x] - c_ad[x]) & (c_ad[y] - k_gevsek[y]):
                    kayan.update((a, b))
        for i in sira:
            eksik, fazla = k_ad[i] - c_ad[i], c_ad[i] - k_gevsek[i]
            # Satır başında "Ad:" biçiminde geçen ad konuşanın etiketidir, sözün içindeki ad değil: çeviride olmaması
            # hata sayılmaz (etiket sanılmayıp "düzeltilince" sözdeki doğru adın yerine konuşanın adı yazılıyordu).
            eksik = {a for a in eksik
                     if not re.search(r"(?m)^\s*(?:[-–—]\s*)?" + re.escape(a) + r"\s*:", kaynak[i])}
            if eksik and fazla and i not in kayan and not temizlik.latin_disi(kaynak[i]):
                neden[i] = (f'the source names "{sorted(eksik)[0]}" but the translation names "{sorted(fazla)[0]}": '
                            f"keep the name the source has")
        if kayan:
            sirali = sorted(kayan)
            for a, b in zip(sirali, sirali[1:]):            # aradaki en çok iki satır da aynı kaymanın parçasıdır
                if b - a <= 3:
                    kayan.update(i for i in sira if a < i < b)
            # Kayma işaretli satırdan önce başlamış, sonrasına da taşmış olabilir: aynı parçadaki önceki ve sonraki
            # üç satır da birlikte sorulur. Yalnız işaretli satırlar sorulunca öncekiler (gerçek test 26, American Gods
            # 275-277) ve sonraki satır (gerçek test 28, American Gods 366) kaymış kaldı.
            isaretli = set(kayan)
            for i in sorted(isaretli):
                k = sira.index(i)
                if k and sira[k - 1] not in isaretli:
                    kayan.update(sira[max(0, k - 3):k])
                if k + 1 < len(sira) and sira[k + 1] not in isaretli:
                    kayan.update(sira[k + 1:k + 4])
            for i in kayan:                                 # kendi nedeni olan satır (değişen ad) onu korur
                neden.setdefault(i, "translations around this id are shifted against their source lines (the content of "
                                    "one id is written under a neighbouring id): translate every id from its own source text only")
        return neden

    def _sorgula(self, idler, kabul):
        """Şüpheli satırları nedenleriyle bir kez daha sorar; yanıt kontrolleri geçerse kullanılır."""
        if not self.ayar["kontrol"]["supheli_satir_kontrolu"]:
            return
        supheli = self._supheler(idler, kabul)
        if not supheli:
            return
        kalan, kod = sorted(supheli), hedef_kodu(self.ayar)
        yuk = self._yuk(kalan, {i: (kabul[i], supheli[i]) for i in kalan})
        try:
            ham = self.model.iste(self.sistem, yuk)
            self.gunluk.ham(self.etiket, {"tur": "supheli", "giden": json.loads(yuk), "gelen": ham})
            sonuc = self._ayristir(ham, kalan)
        except (Ret, ValueError) as e:
            self.gunluk.uyari(self.etiket, f"şüpheli satırlar sorulamadı ({e})")
            return
        except Iptal:
            return                          # durduruldu: kabul edilen satırlar olduğu gibi kalır
        gecerli = {i for i in kalan if i in sonuc and self._satir_kontrol(i, sonuc) is None}
        for i in kalan:
            if i not in gecerli:
                continue
            if _yalin(sonuc[i]) == _yalin(kabul[i]):
                continue
            yeni_kalip = self._kalip_supheleri(self._kaynak(i), temizlik.tr_kucuk(_duz(sonuc[i])), kod)
            if yeni_kalip:                  # yanıt düzeltirken yeni bir kalıp hatası getirdi: önceki çeviri kalır
                self.gunluk.uyari(self.etiket, f"satır {i + 1}: şüphe üzerine gelen yanıt kullanılmadı "
                                               f"({yeni_kalip[0][:90]}): {sonuc[i]!r}")
                continue
            sizan = kaynak_sizintisi(self._kaynak(i), kabul[i], sonuc[i], kod)
            if sizan:                       # yanıt kaynak dile kaydı: önceki çeviri kalır
                self.gunluk.uyari(self.etiket, f"satır {i + 1}: şüphe üzerine gelen yanıt kullanılmadı "
                                               f"(kaynak dilde sözcük: {', '.join(sizan)}): {sonuc[i]!r}")
                continue
            self.gunluk.uyari(self.etiket, f"satır {i + 1}: şüphe üzerine düzeltildi ({supheli[i][:90]}): "
                                           f"{kabul[i]!r} -> {sonuc[i]!r}")
            kabul[i] = sonuc[i]

    def _ardisik_yinelenen(self, sonuc):
        """Kaynak aynı repliği art arda iki altyazıya bölmüşse (araya başka bir konuşma girince altyazı yeniden
        yazılır: süreler bitişik, metin aynı) ikisi aynı okunur: ikinciye ilkinin çevirisi konur. Model sorulmaz.
        Yazım aşaması bu ikisini tek altyazı yapar (dosya.py)."""
        def norm(i):
            return " ".join(temizlik.ETIKET.sub("", self.satirlar[i].metin).lower().split())

        for i in sorted(self.giden):
            if i not in sonuc or i in self.uye or i in self.birlesik or not norm(i):
                continue
            for j in range(i + 1, min(i + 1 + ARDISIK_UZAKLIK, len(self.satirlar))):
                if (j in sonuc and j in self.giden and j not in self.uye and j not in self.birlesik
                        and ardisik_mi(self.satirlar[i], self.satirlar[j]) and norm(j) == norm(i)):
                    if sonuc[j] != sonuc[i]:
                        self.gunluk.uyari(self.etiket, f"satır {j + 1}: art arda yinelenen replik, satır {i + 1} ile "
                                                       f"aynı çeviri kullanıldı")
                        sonuc[j] = sonuc[i]
                    break

    def _ortak_ifadeler(self, sonuc):
        """Japonca kaynakta uzak satırlarda tekrar eden ifade (aynı oyun, toplantı, selam kalıbı) ayrı parçalarda ayrı
        çevriliyordu (gerçek test 21: 懇親会 "davetler" / "veli toplantısı", 鬼ごっこ "ebeleme" / "yakalamaca", selam
        kalıbının ikinci geçişi yarım). Çeviriden sonra ifade ve satırların çevirileri modele gider; model yalnız o
        ifadeyi karşılayan sözleri eşitler. Sözlüğe sorarak birleştirme denendi: oylar dağıldı, bir koşuda yanlış girdi
        oluştu (冒険 -> "SPY WARS"; gerçek test 22), bırakıldı. Yeniden yazılan satır eskisinden çok ayrılıyorsa ya da
        satır denetiminden geçmiyorsa kullanılmaz."""
        from .terim import tekrar_eden_ifadeler
        metinler = {}
        for i in sorted(self.giden):
            if i in sonuc and i not in self.uye and i not in self.birlesik and sonuc[i] != BIRLESTI:
                metinler[i] = tekrar_metni(self.giden.get(i) or self.satirlar[i].metin)
        harf = "".join(metinler.values())
        if not harf:
            return
        haric = list(self.sozluk.girdiler) if self.sozluk is not None else []
        japonca = sum(1 for k in harf if "\u3040" <= k <= "\u30ff" or "\u4e00" <= k <= "\u9fff") >= 0.3 * len(harf)
        # Latin harfli kaynakta satır çifti eşitlemesi kaldırıldı: 56 kayıtlı eşitlemede kazanç yoktu, satırlar
        # boşuna yeniden yazılıyor ya da bozuluyordu (American Gods 321 "ağzımdan çıkarsa", Odyssey 1536 "olmalı?"; S52).
        if not japonca:
            return
        gruplar, istem, anahtar = tekrar_eden_ifadeler(metinler, haric), IFADE_ISTEMI, "expression"
        if not gruplar:
            return
        duz = lambda i: temizlik.ETIKET.sub("", sonuc[i]).strip()
        ogeler = [{anahtar: ifade, "lines": [{"id": i + 1, "source": metinler[i], "translation": duz(i)}
                                             for i in idler]}
                  for ifade, idler in list(gruplar.items())[:80]]
        izinli = {i for idler in gruplar.values() for i in idler}
        # Modelin düşünmeden verdiği yanıt özensizdir: sıradan sözcüklerin satırlarını da yeniden yazıyor (300 "siz" ->
        # "sen", 366 "belediye" -> "devlet dairesi"). Gerçek test 51: düşünme sayısı 0 olan 12 yanıtın 10'u satırları
        # bozdu, 2'si boş döndü; düşünerek gelen 33 yanıtın hepsi özenliydi. Düşünme sayısı biliniyorsa ölçüt odur (S25):
        # S22'nin işareti (değiştirmeden yollanan 2+ satır) gerçek test 53'te düşünmeden gelen bir yanıtı kaçırdı (1 satır
        # aynen; 262 ve 432 bozuldu). Sunucu sayıyı vermezse S22'nin işaretine bakılır. Özensiz yanıt kullanılmaz,
        # istek bir kez daha sorulur; o da özensizse eşitleme yapılmaz (çeviriler olduğu gibi kalır).
        for deneme in range(2):
            try:
                ham = self.model.iste(istem.format(dil=dil_adi(self.ayar)),
                                      json.dumps({"items": ogeler}, ensure_ascii=False))
                dusunme = getattr(Model.yerel, "dusunme", None)       # ham kayda yazınca sıfırlanır
                self.gunluk.ham(self.etiket, {"tur": "ifade", "giden": {"items": ogeler}, "gelen": ham})
                cevap = json_coz(ham)
                yeni = {int(x["id"]) - 1: x["text"].strip() for x in (cevap.get("lines") or [])
                        if isinstance(x, dict) and isinstance(x.get("text"), str) and x["text"].strip()}
            except (Ret, ValueError, KeyError, TypeError, AttributeError) as e:
                self.gunluk.uyari(self.etiket, f"tekrar eden ifadeler eşitlenemedi ({e})")
                return
            aynen = sum(1 for i, metin in yeni.items() if i in izinli and metin == duz(i))
            if dusunme is not None:
                if dusunme > 0:
                    break
                neden = "model düşünmeden yanıtladı"
            elif aynen < IFADE_OZENSIZ:
                break
            else:
                neden = f"yanıt {aynen} satırı değiştirmeden yolladı"
            self.gunluk.uyari(self.etiket, f"tekrar eden ifadeler: {neden} (özensiz yanıt), "
                                           + ("yeniden soruldu" if deneme == 0 else "eşitleme yapılmadı"))
        else:
            return
        for i, metin in yeni.items():
            if i not in izinli or metin == duz(i):
                continue
            metin = temizlik.satir_basi_kucult(duz(i), metin)
            if metin == duz(i):
                continue
            if difflib.SequenceMatcher(None, duz(i), metin).ratio() < 0.6:
                self.gunluk.uyari(self.etiket, f"satır {i + 1}: ifade eşitlemesi çok değiştirdi, kullanılmadı: {metin!r}")
                continue
            denenen = {i: metin}
            sorun = self._satir_kontrol(i, denenen)
            if sorun:
                self.gunluk.uyari(self.etiket, f"satır {i + 1}: ifade eşitlemesi kullanılmadı ({sorun}): {metin!r}")
                continue
            kendi = self.satirlar[i].metin.strip()
            yaz = denenen[i]
            if kendi.startswith("<i>") and kendi.endswith("</i>") and not yaz.startswith("<i>"):
                yaz = "<i>" + yaz + "</i>"
            self.gunluk.uyari(self.etiket, f"satır {i + 1}: tekrar eden ifade eşitlendi: {sonuc[i]!r} -> {yaz!r}")
            sonuc[i] = yaz

    def _tekrar_eden_sahne(self, sonuc):
        """Yinelenen sahne her geçişinde aynı okunur. Aynı replik önceki ya da sonraki satırıyla birlikte yeniden
        geçiyorsa (geri dönüş, hatırlama) çeviriler farklıysa hangisinin kalacağını model seçer: yeni metin yazmaz,
        adaylardan birini işaret eder. Tek başına yeniden geçen replik birleştirilmez: aynı sözler başka bir
        bağlamda başka anlama gelir (tanıtımda yarım kesilen cümle ile sahnedeki tamamı); seçim doğru çeviriyi
        yanlışıyla değiştiriyordu. Art arda gelen aynı replik ayrıdır (_ardisik_yinelenen).
        Uzun replik başka bir geçişinde bölünmüş bir cümlenin parçasıysa dokunulmaz: o geçişin ayrı çevirisi yoktur.
        Eskiden tek başına geçen satır cümlenin çevirisindeki sözlerle yeniden yazılıyordu ("yankı"); 269 çalışmanın
        59'unda yarım ek ve cümle ortası parçası bıraktığı için kaldırıldı ("Kaçan güneşi kovalasak.", LOTR 328
        "durmadan uzayıp gidiyorsa"; S43).
        Tek başına yeniden geçen tam cümle (yarım değil, bölünmüş cümlenin parçası değil) de modele sorulur: aynı
        anlamdaysa bir aday seçer, bağlam başkaysa (başka birine, başka anlamda) "0" der ve ikisi de kalır. Bölüm
        sonunda yinelenen şaka ("Annem yok" / "Annen yok ki") ve hatırlanan söz başka çevriliyordu (SPY x FAMILY)."""
        def kaynak_metni(i):
            if not 0 <= i < len(self.satirlar):
                return ""
            # Modele giden metin etiketi ve notları atılmış haldir; yoksa (ya da bölünmüş cümlenin parçasıysa, giden
            # orada bütün cümledir) kaynak satırın kendisi
            giden = self.giden.get(i) if i not in self.uye and i not in self.birlesik else None
            return tekrar_metni(giden or self.satirlar[i].metin)

        def norm(i):
            return kaynak_metni(i).lower()

        def duz(i):
            return temizlik.ETIKET.sub("", sonuc[i]).strip()

        def uzun(i, kisa=False):             # baştaki tire, tırnak ve üç nokta, sondaki nokta ve virgül sayılmaz
            t = re.sub(r"^[\s\-–—….\"'“”‘’«»„]+|[\s.,;:…\"'“”‘’«»]+$", "", norm(i))
            # kisa: tek başına yinelenen tam cümle için ("はは 存在しない" 7 harf); seçimi model yapar, "0" diyebilir
            if temizlik.latin_disi(t):
                yeter = len(re.sub(r"\W", "", t)) >= (6 if kisa else 10)
            else:
                yeter = len(re.findall(r"\w+", t)) >= (3 if kisa else 4)
            return t if yeter else ""

        def tam(i):                          # yarım cümle değil: sonu ve başı kesik değil
            t = kaynak_metni(i)
            return bool(t) and not _YARIM_SON.search(t) and not _YARIM_BAS.search(t)

        gruplar, ciftler = {}, {}
        for i in sorted(self.giden):
            if i not in sonuc or i in self.uye or i in self.birlesik:
                continue
            if len(norm(i).split()) < 2 and len(norm(i)) < 6:
                continue
            # Sahne en az iki replik boyunca aynen yineleniyorsa tekrar sayılır: satır, önceki ya da sonraki
            # komşusuyla birlikte başka bir yerde de geçiyor olmalı (tek başına aynı replik rastlantı olabilir).
            for anahtar in (("önce", norm(i - 1), norm(i)), ("sonra", norm(i), norm(i + 1))):
                if all(anahtar):
                    ciftler.setdefault(anahtar, []).append(i)
        # Ortak satırı olan eşleşmeler tek grup olur ve tek karar alır: satır hem önceki hem sonraki komşusuyla
        # eşleşince iki ayrı gruba giriyor, iki seçimden sonraki öncekini eziyordu ("Ve... çek!" / "Ve... çekin!" /
        # "Haydi... çekin!" aynı sahnede karışık; Odyssey 671-706; S44).
        kumeler = []
        for idler in ciftler.values():
            if len(idler) < 2:
                continue
            kume = set(idler)
            for k in [k for k in kumeler if k & kume]:
                kume |= k
                kumeler.remove(k)
            kumeler.append(kume)

        def devam(i):                        # "...a fleet...": cümle önceki satırdan sürüyor
            return bool(re.match(r"\s*(?:\.\.\.|[…‥])", kaynak_metni(i)))

        def baslangic(i):                    # devam satırının cümlesinin başladığı satır ("" devam değilse)
            j = i
            while j > 0 and devam(j) and i - j < 8:
                j -= 1
            return norm(j) if j != i else ""

        # Devam satırı, cümlesinin başladığı satır da aynıysa eşitlenir: "...a fleet..." filmin başında "A face..."
        # cümlesinin, sonda "Sing, Muse, of a face..." cümlesinin parçası; Türkçede eki başka ("bir donanma..." /
        # "bir donanmadan..."). Eşitleme doğru eki siliyordu (kayıtlarda 217 değişikliğin 202'si; S44).
        for kume in kumeler:
            parcalar = {}
            for i in sorted(kume):
                parcalar.setdefault(baslangic(i), []).append(i)
            for idler in parcalar.values():
                if len(idler) > 1:
                    i0 = idler[0]
                    gruplar[(norm(i0 - 1), norm(i0), norm(i0 + 1), i0)] = idler
        sahnede = {i for v in gruplar.values() if len(v) > 1 for i in v}
        secilecek = [({"before": k[0], "source": k[1], "after": k[2]}, v, "tekrar eden sahne")
                     for k, v in gruplar.items() if len(v) > 1 and len({duz(i) for i in v}) > 1]
        tekli, parcali = {}, {}
        for i in sorted(self.giden):
            anahtar = uzun(i, kisa=not (i in self.uye or i in self.birlesik)) if i in sonuc and i not in sahnede else ""
            if not anahtar:
                continue
            if i in self.uye or i in self.birlesik:
                parcali.setdefault(anahtar, self.uye.get(i, i))
            else:
                tekli.setdefault(anahtar, []).append(i)
        for anahtar, idler in tekli.items():
            if (anahtar not in parcali and len(idler) > 1 and len({duz(i) for i in idler}) > 1
                    and all(tam(i) for i in idler)):
                secenek = list(dict.fromkeys(duz(i) for i in idler))
                gecisler = [{"before": kaynak_metni(i - 1), "after": kaynak_metni(i + 1),
                             "now": secenek.index(duz(i)) + 1} for i in idler]
                secilecek.append(({"source": kaynak_metni(idler[0]), "occurrences": gecisler}, idler,
                                  "tek başına yinelenen replik"))
        if not secilecek:
            return
        ogeler, adaylar = [], []
        for n, (oge, idler, _) in enumerate(secilecek, 1):
            secenek = list(dict.fromkeys(duz(i) for i in idler))
            adaylar.append(secenek)
            ogeler.append({"n": n, **oge, "candidates": {str(k): c for k, c in enumerate(secenek, 1)}})
        sistem = (SECIM_ISTEMI.format(dil=dil_adi(self.ayar))
                  + '\nReturn ONLY this JSON object: {"choices": [{"n": 1, "pick": 2}]} with one entry per item.')
        secim = {}
        try:
            ham = self.model.iste(sistem, json.dumps({"items": ogeler}, ensure_ascii=False))
            self.gunluk.ham(self.etiket, {"tur": "secim", "giden": {"items": ogeler}, "gelen": ham})
            cevap = json_coz(ham)
            for x in (cevap.get("choices") or []):
                secim[int(x["n"])] = int(x["pick"])
        except (Ret, ValueError, KeyError, TypeError, AttributeError) as e:
            self.gunluk.uyari(self.etiket, f"yinelenen satırlar için seçim yapılamadı ({e}); ilk çeviriler kullanıldı")

        def koy(i, metin, neden):
            kendi_metni = self.satirlar[i].metin.strip()
            if kendi_metni.startswith("<i>") and kendi_metni.endswith("</i>") and not metin.startswith("<i>"):
                metin = "<i>" + metin + "</i>"
            self.gunluk.uyari(self.etiket, f"satır {i + 1}: {neden}: {sonuc[i]!r} -> {metin!r}")
            sonuc[i] = metin

        for n, (_, idler, neden) in enumerate(secilecek, 1):
            secenek = adaylar[n - 1]
            tek = "occurrences" in ogeler[n - 1]
            k = secim.get(n, 0 if tek else 1)
            if tek and k == 0:              # bağlam başka ya da yanıt yok: her geçiş kendi çevirisini korur
                continue
            secilen = secenek[k - 1] if 1 <= k <= len(secenek) else secenek[0]
            for i in idler:
                if duz(i) != secilen:
                    koy(i, secilen, f"{neden}, seçilen çeviri kullanıldı")
