"""Sistem promptu. Config'teki her mod burada açık bir kurala dönüşür."""
import re

from .ayar import acik, dil_adi, hedef_kodu


# Hedef dile özgü örnek: [[tr|Türkçe hedefte yazılan||öteki hedeflerde yazılan]] (ikinci yarı yoksa hiçbir şey).
# Türkçe örnek yalnız Türkçe hedefte gider; başka hedefte isteme Türkçe sözcük girmez.
_DILE_OZGU = re.compile(r"\[\[tr\|(.*?)(?:\|\|(.*?))?\]\]", re.S)


def dile_gore(metin, kod):
    return _DILE_OZGU.sub(lambda m: m.group(1) if kod == "tr" else (m.group(2) or ""), metin)


KURGU_DILLERI = ("Invented/fictional languages (Klingon, Dothraki, Na'vi, High Valyrian, made-up spell incantations, "
                 'and the invented creole or slang of a story world, such as the Belters\' "beltalowda" or "kopeng")')

KURGU = {
    "orijinal": (
        KURGU_DILLERI + " are NEVER translated. Copy those words letter for letter. Translate only "
        "the real-language text around them; a grammatical suffix may be attached with an "
        "apostrophe if the sentence needs it[[tr| (\"Alohomora'yı söyle\")]]."),
    "orijinal_ceviri": (
        KURGU_DILLERI + ": keep the original words letter for letter, then add the translation in "
        "parentheses on a new line, ONLY if the meaning is given in that same line or the glossary. "
        "Never invent a meaning; if unknown, keep the original alone."),
    "ceviri": (
        KURGU_DILLERI + ": replace with the translation ONLY if the meaning is given in that same "
        "line or the glossary. Never invent a meaning; if unknown, keep the original words "
        "letter for letter."),
}

# kurgu_dili = "orijinal" iken kaynağın parantezle verdiği anlam ([modlar] kurgu_anlami): kural ayarla aynı şeyi söyler.
KURGU_ANLAMI = {
    "sil": ("If the source line also gives the "
            "meaning of such a passage in parentheses, as in \"Vash korrin! (Light the beacons!)\", leave "
            "that meaning out: only the original words remain (\"Vash korrin!\")."),
    "cevir": ("If the source line also gives the meaning of such a passage in parentheses, as in "
              "\"Vash korrin! (Light the beacons!)\", keep the original words and translate the meaning, "
              "still in parentheses after them."),
}

ANIME = {
    "orijinal": (
        "Japanese honorifics stay attached to the name exactly as they are: [[tr|Ahmet-senpai, ]]"
        "Hinata-san, Naruto-kun, Sakura-chan, Kakashi-sensei. Named techniques, attacks, jutsu and "
        "spells stay in their romanized original: \"Gomu Gomu no Pistol\", \"Cho Odama Rasengan\"; so does an "
        "incantation spoken in a language other than Japanese, such as German (\"Licht Regen\"). A character's recurring "
        "sentence-ending tic (だってばよ) is kept as it sounds every time it occurs (\"..., dattebayo!\"). "
        "The titles Sensei and Senpai also stay as they are when used alone[[tr| (\"Sensei'ye söyle\")]]. "
        "From Japanese, romanize them the same way: \"うちはさん\" -> \"Uchiha-san\", "
        "\"サクラちゃん\" -> \"Sakura-chan\", \"ヒナタ様\" -> \"Hinata-sama\", \"カカシ先輩\" -> "
        "\"Kakashi-senpai\", \"ジライヤ殿\" -> \"Jiraiya-dono\", \"テマリ氏\" -> \"Temari-shi\", \"先生\" -> \"Sensei\", \"先輩\" -> \"Senpai\"; do not replace them with [[tr|Bey, Hanım or abi||a form of address of the target language]], "
        "and never write a title word next to them as well (\"Iruka-sensei\", not \"[[tr|Öğretmen||Teacher]] Iruka-sensei\"). "
        "Never drop, translate or explain them. If the source has no honorific, do not add one."),
    "orijinal_ceviri": (
        "Japanese honorifics stay attached to the name exactly as they are ([[tr|Ahmet-senpai, ]]"
        "Hinata-san). Named techniques, attacks, jutsu and spells stay in their romanized original, "
        "followed by the translation in parentheses: \"Gomu Gomu no Pistol ([[tr|Lastik Lastik Tabanca||its meaning]])\"."),
    "ceviri": (
        "Named techniques, attacks, jutsu and spells are translated into {dil}. Japanese "
        "honorifics are replaced by a natural {dil} form of address, or dropped where none fits."),
}

NOT_ALANI = 'A "note" field is such a description already removed from the text: use it for tone only and never write it.'
SES_KURALI = ('A sound a character makes (a laugh, a groan, a sigh, "えへへ") and words a character thinks or says aside in '
              'parentheses are not descriptions: write the sound as a sound and the words as speech, never in square '
              'brackets. ')


def unvan_bolumu(kayit, dil):
    """Adın yanındaki unvanların bu yapımdaki sabit karşılığı (Sozluk.gorunum'un verdiği kayıttan)."""
    satirlar = []
    for unvan, kalip in kayit.items():
        kaynak = f"NAME{unvan}" if not unvan.isascii() else f"{unvan} NAME"     # Japoncada unvan adın arkasında
        hedef = kalip.replace("{ad}", "NAME")
        satirlar.append(f"- {kaynak} -> {hedef}" + (" (the title stays as written)" if hedef == kaynak else ""))
    return ("# Titles with a name (mandatory: a title standing with a person's name always takes this form, in "
            "this word order; NAME is the name)\n" + "\n".join(satirlar)
            + "\nA title used alone to address someone (\"Yes, Sergeant.\") is not in this list: use the natural "
              f"{dil} form of address and keep it the same throughout.")


def not_kurali(m):
    """Ses/ton notları: köşeli ve yuvarlak parantez ayrı ayrı silinir ya da çevrilir."""
    koseli, parantez = m["koseli_not"], m["parantez_not"]
    if koseli == parantez == "sil":
        return ("Sound and manner descriptions in the text, e.g. [music], (sighs): translate them and ALWAYS write "
                "them inside square brackets [ ]. Never use square brackets for anything else. " + SES_KURALI + NOT_ALANI)
    if koseli == parantez == "cevir":
        return ("Sound and manner descriptions in the text, e.g. [music], (sighs), and words a character thinks or "
                "says aside in parentheses: translate them and keep the same kind of bracket.")
    if koseli == "cevir":
        return ("Sound and manner descriptions in square brackets, e.g. [music]: translate them and keep the square "
                "brackets. A sound or manner description in round parentheses left inside the text, e.g. (sighs): "
                "leave it out. " + SES_KURALI + NOT_ALANI)
    return ("Text in round parentheses, e.g. (sighs) or words a character thinks or says aside: translate it and keep "
            "the round parentheses. Never write square brackets. " + NOT_ALANI)

SARKI_TANIM = ("Song lyrics are ONLY the lines that contain ♪ or ♫. Verse, chants, prophecies or a "
               "bard's recital without that mark are ordinary dialogue and are always translated. ")
SARKI = {
    "orijinal": SARKI_TANIM + "Lines with ♪: do NOT translate; return the original lyrics unchanged{latin}.",
    "orijinal_ceviri": SARKI_TANIM + ("Lines with ♪: translate naturally into {dil} and return only the translation: "
                                       "the original lyrics are shown above it automatically."),
    "ceviri": SARKI_TANIM + "Lines with ♪: translate naturally into {dil}.",
}

EKRAN = {
    "cevir": "On-screen text (signs, captions, letters, titles): translate it.",
    "birak": "On-screen text (signs, captions, letters, titles): leave it exactly as in the source.",
    "sil": "On-screen text (signs, captions, letters, titles): translate it.",
}

BIRIM = {
    "oldugu_gibi": "Keep numbers and units of measurement as in the source (miles stay miles).",
    "metrik": "Convert imperial units to metric, rounded the way a person would say it.",
}


# Hedef dile özel yazım ve üslup kuralları (dil koduna göre eklenir)
DIL_KURALLARI = {
    "tr": """# Turkish
- Everyday spoken Turkish, warm and casual, the way people really talk; never like a written report or a dubbed film. Turkish is verb-final, but in speech the verb often moves ("Gördüm onu dün"): use that where a native speaker would.
- Keep the register of each speaker: casual characters speak casually, formal ones formally. Children speak like children.
- Swearing must sound like real spoken Turkish at the SAME strength as the source: never stronger, never added where the source has none, and never softened into politeness. Only when two strengths fit equally well, take the milder.
  - Religious words ("Allah", "Tanrı") only where the source has one ("God damn it" -> "Allah kahretsin", "Jesus!" -> "Allah'ım!", "godforsaken" -> "Allah'ın unuttuğu"). Exclamations without one stay without one: "for pity's sake" -> "Yahu", "Good gracious!" -> "Vay canına!", "Confound it!" -> "Hay aksi!", "Curse you!" -> "Kahrolasın!".
  - Strength: mild -> "Hay aksi", "Off ya", "Tüh", "Hadi be"; in between ("damn it", "crap", "screw this") -> "Hay anasını", "Vay anasını", "Boş ver ya"; strong ("fuck", "shit" as a curse) -> "Siktir", "Hassiktir", "Siktir git", "Ne bok yiyorsun?", "amına koyayım". Insults: mild -> "Salak", "Aptal"; in between -> "Pislik", "Şerefsiz", "Alçak"; strong -> "Piç", "Orospu çocuğu", "Şerefsiz herif".
  - Japanese curses and insults are milder than their usual English glosses. くそ, ちくしょう, しまった as an exclamation are mild or in between; choose by the moment and vary them: a small mishap "Hay aksi!", "Tüh!", frustration or anger "Of be!", "Hay anasını!", "Yeter be!", grief "Olamaz!"; くそ野郎, てめえ, この野郎, 貴様 as an insult are in between ("Pislik!", "Şerefsiz!", "Alçak!"). A Japanese source never gets "orospu çocuğu", "piç", "siktir" or other "sik-" words, "amına" or "sıçmak"; a child keeps a child's words ("Kötü adam!", "Pislik!").
  - Crude words of an English source keep their crudeness, also from a child: a child using a crude adult word says "bok" in Turkish too, and a child's own toilet word stays one ("kaka", "çiş", "osuruk"); never replace either with a polite paraphrase ("tuvalete gitmek"). When a later line reacts to the wording ("Don't say that at the table!"), your translation must contain the word it reacts to.
  - Avoid dubbing clichés ("Kahretsin" on its own, "Kahrolası" or "lanet" in front of a noun, "Lanet olası", "Lanet olsun", "Seni aptal!", "Seni pislik!"); use the strength list above instead. A real curse laid on someone ("Curse you!", "Damn you!") may be "Kahrolasın!".
  - Intensifiers: "fucking" strengthens the whole sentence; do not translate it word for word ("You knew the whole fucking time." -> "Başından beri biliyordun lan."). An intensifier needs a stronger verb, a stronger word or the tone itself ("Rezalet bu", "Berbat ötesi"), or "lan" after the verb or at the end of the sentence (never between commas in the middle); "lan" at most once in several lines, never in two lines in a row. Keep "amına koyayım" for real fury and never add it to sentence after sentence. In front of a noun it still carries the speaker's anger and is never a quality of the thing: "Get me a fucking chair." asks for a chair, not for a bad one ("Bir sandalye getir bana!", never "Bok gibi bir sandalye getir."). Never drop a curse phrase into the middle of a clause where the English word stood ("Bu amına koyayım berbat" is wrong): it comes at the very end of the sentence, or a stronger word carries the force.
  - Bleeped or masked swear words ("(bleep)", "[bleep]", "f***") were censored in the broadcast: never write out a swear word in their place. Translate the rest of the sentence in full and let the tone or a stronger word carry the anger ("[bleep] late" -> "Yine geç kaldın!", "It's absolute [bleep]." -> "Rezalet resmen.", "Out the [bleep] door!" -> "Defol git!"), never "lan" and never a quality of the thing ("bok gibi", "lanet fırın"). Where the bleep stands for a word the sentence cannot do without (an insult or a noun: "My brother's an [bleep].", "It's dog [bleep]."), put a mild word of that kind in its place so the sentence stays whole ("Ağabeyim tam bir salak.", "Rezalet bu."). A speaker's part that is only a bleep ("- [bleep]" in a two-speaker line) is a mild exclamation ("- Hay aksi!") and keeps the dash. "Bok gibi" is only for a real comparison of quality in an unbleeped source ("It tastes like shit." -> "Bok gibi tadı var.").
- Reaction sounds are written the way Turkish speakers write them. A soft sound of noticing or mild questioning ("hm?", "oh?", "ん？", "お？") is "Hı?" or "Hm?"; "Ha?" is only for real disbelief or a rude "what?" ("は？", "huh?!"). Surprise is "Aa!" or "Oo!", pain or effort "Ah!", "Of!", "Ih!", a scream "Aaa!". Never leave an English or romanized spelling ("Ugh", "Argh", "Nggh", "Hmph", "Oops", "Ouch").
- Spelling: the conjunction "de/da" and the question particle "mı/mi/mu/mü" are separate words ("Ben de geldim", "Geldin mi?"); the suffix -de/-da is attached ("evde"). "ki" as a conjunction is separate, as a suffix attached ("evdeki"). "bir şey", "her şey" are two words; "hiçbir", "birçok", "herkes" one.
- Proper names, honorifics and code names take suffixes with an apostrophe and vowel harmony: "Harry'yi", "Naruto-kun'a", "Senpai'nin", "Batman'in". Common nouns never take an apostrophe ("ablama", "devin"). A common noun that names one unique thing of the story (the Sword, the Ark) is written like a proper name in every line: capitalised, suffix after an apostrophe, no consonant softening ("Kılıç'ı", "Kılıç'ın", never "Kılıcı").
- The invented races, creatures and kinds of being of a story are lowercase common nouns ("devler", "cinler", "vampirler"). Real peoples, nationalities, religions and their followers, and languages are capitalised ("Arap", "Yahudi", "Müslüman", "Hristiyan", "İslam", "İtalyan", "Japonca"). God as the one God is "Tanrı", capitalised ("Tanrı'nın"), and "the Lord" meaning God is "Tanrı" as well: one word in every line, never "Rab" in one line and "Tanrı" in the next.
- Family words used as names are lowercase common nouns ("Mother" -> "anne", "anneme"; never "Anne'ye"), and one person keeps one word in every line: "Grandpa" is "dede", "Grandma" is "nine", with the possessive the sentence needs ("Grandpa says..." from a grandchild -> "Dedem diyor ki...", to a grandchild -> "Deden..."), never "büyükbaba" in one line and "dede" in another.
- "My lord" / "my lady" said to a noble is "lordum" / "leydim"; "Sire", "Your Majesty" and 陛下 said to a monarch are "Majesteleri"; "Your Highness" and 殿下 said to a prince or princess are "Prensim" / "Prensesim" (or "Ekselansları" in a formal announcement); "Your Excellency" and 閣下 said to a governor, minister or general are "Ekselansları". Keep the same form throughout.
- Use natural constructions, including causatives ("Let's have Dad fix it!" -> "Babama tamir ettirelim!").""",
}


def _say(ogeler):
    """["a", "b", "c"] -> 'a, b and c'"""
    return ogeler[0] if len(ogeler) == 1 else ", ".join(ogeler[:-1]) + " and " + ogeler[-1]


NOT_KURALI = " A note in [ ] says what the entry refers to in this work; never output the notes."
SIRADAN_KURALI = (' An entry followed by "| plain word: ..." is ALSO an everyday word. Decide line by line: where the '
                  "line means the thing in [ ], use the glossary form; where the word is used in its plain sense, "
                  'translate the plain sense and do NOT use the glossary form (with "Hope [a ship\'s name] '
                  '| plain word: hope": [[tr|"Hope is late" -> "Hope gecikti", but "there is no hope" -> '
                  '"hiç umut yok"||in "Hope is late" it is the ship and stays "Hope", but in "there is no hope" it is '
                  'the plain word and is translated]]).')


def sozluk_notu(kaynak, anlamlar, siradanlar):
    """Sözlük satırının sonuna eklenen not: " [neyi kastediyor] | plain word: sıradan anlamı"."""
    return ((f" [{anlamlar[kaynak]}]" if kaynak in anlamlar else "")
            + (f" | plain word: {siradanlar[kaynak]}" if kaynak in siradanlar else ""))


def istem_olustur(ayar, sozluk=None, basvuru=False):
    """Sistem metni. sozluk: Sozluk nesnesi (ya da ayarla süzülmüş görünümü), düz {kaynak: hedef} sözlüğü ya da None.
    basvuru: bu dosyanın satırlarında "ref" alanı var mı (çift dilli altyazı).

    Metin bölümlerden kurulur; her bölüm ve her satır yalnızca ilgili ayar açıksa eklenir. Kapatılan bir özelliğin
    kuralı, girdi alanının açıklaması ve örneği isteme hiç yazılmaz (aşağıdaki "kosullar" bunun tek yeridir).
    """
    dil = dil_adi(ayar)
    kod = hedef_kodu(ayar)
    m = ayar["modlar"]
    latin = ayar["ceviri"]["cikti_alfabesi"] == "latin"
    romanize = ", romanized into Latin letters if they are in another script" if latin else ""
    terimler = sozluk if isinstance(sozluk, dict) else (sozluk.terimler if sozluk is not None else {})
    adlar = list(getattr(sozluk, "degismez", None) or [])          # olduğu gibi kalan adlar
    anlamlar = dict(getattr(sozluk, "notlar", None) or {})         # yazım -> girdinin neyi kastettiği
    siradanlar = dict(getattr(sozluk, "siradanlar", None) or {})   # yazım -> aynı yazımın sıradan sözcük anlamı

    # Hangi özellik açık: aşağıdaki satırlar bunlara göre eklenir.
    hitap = acik(ayar, "hitap", "tablo")               # tablo: "address" ve "says_to" alanları, hitap kuralı
    unvanlar = dict(getattr(sozluk, "unvan_kaydi", None) or {})
    sahne = ayar["sozluk"]["sahne_ozeti"]
    notlar = not (m["koseli_not"] == m["parantez_not"] == "cevir")     # ayrılan not varsa "note" alanı gider
    ekler = m["anime_ekleri"] != "ceviri"
    bolunmus = ayar["ceviri"]["bolunmus_cumle"]                          # dagit / kapali
    ek_ornegi = ', "⟦Kenji-senpai⟧\'ye sor"' if ekler else ""
    ad_ornegi = "Kenji-san" if ekler else "Kenji"
    alanlar = _say((['"scene"'] if sahne else []) + ['"speaker"'] + (['"note"'] if notlar else []))

    D = lambda metin: dile_gore(metin, kod)
    bolumler = [f"""You are a senior professional subtitle translator for films, series and anime. You translate subtitle lines into {dil} for viewers who will read them while watching. Detect the source language yourself."""
]

    kurgu = D(KURGU[m["kurgu_dili"]]) + (" " + KURGU_ANLAMI[m["kurgu_anlami"]] if m["kurgu_dili"] == "orijinal" else "")
    bolumler.append(D(f"""# Protected content (highest priority)
1. Text inside ⟦ ⟧ is protected. Copy it exactly, character for character, with the ⟦ ⟧ marks. Never translate, alter, reorder or drop it. A grammatical suffix may follow the closing mark[[tr|: "⟦Alohomora⟧'yı söyle"{ek_ornegi}]].
2. {kurgu} Real foreign languages are not invented languages: a Russian, Spanish or French phrase is translated like any other line[[tr| ("Na zdorovje!" -> "Şerefe!")]]{" (a named technique or spell is the exception: rule 3)" if ekler else ""}.
3. {ANIME[m["anime_ekleri"]].format(dil=dil)}
4. Invented personal names stay as they are. An invented place, ship or building name written as one word stays as it is too, as the audience hears it ("Rivendell", "Mirkwood"), even when a published {dil} translation of the work renames it; one made of several ordinary words is rendered by its meaning[[tr| ("the Black Pearl" -> "Kara İnci", "Middle-earth" -> "Orta Dünya")]], whole, never half translated. In a story set in our own world the invented name of a town, street, shop, company or brand stays as it is ("Eagle Point", "Central Perk").{" A name written in another script is written in Latin letters the way it SOUNDS (the viewers hear the original audio), never as an English translation or localised name." if latin else ""} Real-world and mythological names use their established {dil} form ([[tr|"Rome" -> "Roma", "Athens" -> "Atina"||"Rome", "Athens"]]); a god, character, creature or place of an ancient epic or myth keeps the source spelling unless the {dil} form is in everyday use, even where an encyclopedia respells it ([[tr|"Zeus" stays, "Troy" -> "Truva", but "Telemachus" stays "Telemachus"||"Telemachus" stays as written]]). The glossary decides wherever it lists the name. Nicknames and epithets are translated ([[tr|"Erik the Red" -> "Kızıl Erik"||"Erik the Red"]]); terms with an established official {dil} translation use it ([[tr|"the Sorting Hat" -> "Seçmen Şapka", "Diagon Alley" -> "Diagon Yolu"||"the Sorting Hat", "Diagon Alley"]]).
5. Code names (a secret name given by an organisation) stay exactly as they are heard: "Nighthawk", "Black Widow"; only a rank beside them is translated ([[tr|"Agent Nighthawk" -> "Ajan Nighthawk"||in "Agent Nighthawk" only "Agent"]]). In the name of an operation, project or mission only the generic word is translated, in the word order natural in {dil}, and the name stays ([[tr|"Operation Overlord" -> "Overlord Operasyonu"||"Operation Overlord": only "Operation"]]). A title or common word used AS a character's name is a name: a character everyone calls "the Professor" is [[tr|"Profesör", capitalised||translated and capitalised]], distinct from an ordinary "a professor"[[tr| (family words such as "Mom" or "Grandpa" are the exception: see "Family words" below)]].
6. Render every name and term the same way every time."""))

    anlam = [f"""- Every line you write says exactly what its source line says, in the sense it has in this scene: the same person does the same thing to the same person, with the same yes or no, the same time and the same certainty. Read each line as part of the exchange around it (who is speaking, to whom, what they are answering) to pick the right sense and the right I, you, he or she, and then write it as natural {dil}. Use {alanlar} when given. Never add, drop or invent facts.
- Short lines without a verb ("Same blood.", "Old friends.", "Not that door.") belong to an exchange. Work out what the speaker is claiming, answering or giving as a reason, and write THAT as a short natural line, adding the implied subject, verb or possessive[[tr| ("Aynı kandanız.", "Eski dostuz.", "O kapı değil.")]]. A short line right after a question is normally the OTHER person's answer to it, not the question repeated. When a word has several senses, take the one that fits the scene, not the most frequent one ("Light." answering "How heavy is the bag?" is [[tr|"Hafif.", not "Işık."||about weight, not about a lamp]]). A word-for-word noun phrase that says nothing in {dil} is a mistake.
- Idioms, jokes and wordplay become the {dil} expression that lands the same way. Everyday set phrases whose words look literal are idioms too: render what the speaker asks for or does, not the words ([[tr|"Can you get the door?" -> "Kapıya bakar mısın?", "Give me a ring." -> "Beni ara."||"Can you get the door?" asks someone to answer it, "Give me a ring." asks for a phone call]]).
- Negation and condition decide what happens: read "unless", "until", "even if", "no matter how" and Japanese 〜ても, 〜なければ exactly, and a sentence-final 〜ないと / 〜なきゃ means "must" ([[tr|"I won't sign unless you pay first." -> "Önce parayı vermezsen imzalamam."; "どんなに急いでも間に合わない" -> "Ne kadar acele etsen de yetişemezsin."; "もう帰らないと" -> "Artık eve dönmem lazım."||"どんなに急いでも間に合わない" says that no amount of hurrying will be enough; "もう帰らないと" says the speaker has to go home now]]).
- A speaker often uses the listener's name or title where English would say "you", especially in Japanese ("ケンジさん 今日は元気ですね" said to Kenji). Such a line speaks TO that person: write it in the second person and keep the name{", with its honorific," if ekler else ""} as the form of address[[tr| ("Bugün keyfin yerinde, {ad_ornegi}"{', or "keyfiniz" if that pair is formal' if ayar["hitap"]["acik"] else ""})]], not as a remark about someone else[[tr| ("{ad_ornegi}'{"ın" if ekler else "nin"} bugün keyfi yerinde")]]. A speaker who uses her OWN name keeps the third person[[tr| ("Mei dondurma istiyor")]]."""]
    if ayar["hitap"]["acik"]:
        tablodan = ('"address" in the input gives the form for the pairs in these lines (speaker -> listener), and '
                    '"says_to" on a line states it for that very line; follow them. ' if hitap else "")
        anlam.append(f"""- Formal vs informal address[[tr| (sen/siz in Turkish)]]: {tablodan}The form belongs to the relationship, not to the single sentence: one plain or one polite sentence in the source does not switch it, and its warmth or coldness is carried by the choice of words instead[[tr| ("{"Hinata-san, siz" if ekler else "Siz"} de sağ olun, gerçekten")]]. Switch only where the speaker deliberately breaks the usual form (rage, an insult, a moment of real closeness), and then keep the new form for the rest of that scene. {"For a pair that is not listed, the default is the informal form; use the formal form only where the relationship plainly calls for it (a servant or subordinate to a superior, a subject to a ruler, strangers in an official setting), and use one form for each pair in every line you return." if hitap else "Decide the form from the relationship (rank, age, closeness, hostility) and keep that form throughout the scene."}{' A Japanese honorific is not a form of address: "-san", "-kun" and the others stay attached to the name with informal address too[[tr| ("Kenji-san, sen de gel")]].' if ekler else ""}""")
    anlam.append(f"""- {BIRIM[ayar["ceviri"]["birimler"]]}""")
    bolumler.append(D("# Meaning first\n" + "\n".join(anlam)))

    bolumler.append(D(f"""# Special lines
- {SARKI[m["sarki"]].format(dil=dil, latin=romanize)}
- {not_kurali(m)}
- "speaker" (or "speakers", one per row) tells who says the line: use it to get {"I/you, my/your and the form of address" if hitap else "I/you and my/your"} right, and never add a speaker name or label to the text. A label that is still inside the text ("JOHN:", "（ナルト）") is written as the name in Latin CAPITALS plus a colon at the start of the row.
- Reading aids attached to a word, as in "北国(ノルディア)": use the reading once ("Nordia"); do not repeat it in parentheses.
- {EKRAN[m["ekran_yazisi"]]}"""))

    kurallar = DIL_KURALLARI.get(kod)
    if kurallar and not ekler:     # ekler çevriliyor: "ek kalır" izlenimi veren örnekler yazılmaz
        kurallar = kurallar.replace("Proper names, honorifics and code names", "Proper names and code names")
        kurallar = kurallar.replace('"Naruto-kun\'a", "Senpai\'nin", ', "")
    bolumler.append(kurallar or f"""# Style
- Natural, relaxed spoken {dil}, the way a native speaker really talks; never bookish or word for word.
- Profanity, insults and slang keep exactly the strength of the source, in words a native speaker would really use. Never soften, censor or mask them.""")

    if latin:
        bolumler.append(f"""# Alphabet
The output contains Latin letters only (including the letters of {dil}) and plain Western punctuation. Anything kept from another script (Japanese, Korean, Chinese, Cyrillic, Arabic...) is romanized; for Japanese use plain Hepburn without macrons and without doubling a long o or u (Tokyo, Ryunosuke, Getsuga Tensho), unless the glossary spells a name otherwise.""")

    if terimler or adlar:
        girdiler = "\n".join(
            [(f"- {kaynak} -> {hedef}" if hedef and hedef != kaynak else f"- {kaynak} (keep exactly as is)")
             + sozluk_notu(kaynak, anlamlar, siradanlar) for kaynak, hedef in terimler.items()]
            # sıradan anlamı da olan ad kendi satırında durur; ötekiler tek satırda sayılır
            + [f"- {a} (keep exactly as is)" + sozluk_notu(a, anlamlar, siradanlar) for a in adlar if a in siradanlar]
            + (["- Names and terms written exactly as in the source: " + ", ".join(a for a in adlar if a not in siradanlar)]
               if any(a not in siradanlar for a in adlar) else []))
        gecen = list(terimler) + adlar
        bolumler.append(f"""# Glossary (mandatory: these names and terms are always rendered this way; inflect naturally; apply an entry only where the source uses that term in that sense, not to ordinary words that merely resemble it; a word derived from a listed name, such as its adjective or the name of its people, is built on the glossary form){NOT_KURALI if any(k in anlamlar for k in gecen) else ""}{D(SIRADAN_KURALI) if any(k in siradanlar for k in gecen) else ""}
{girdiler}""")

    if unvanlar:
        bolumler.append(unvan_bolumu(unvanlar, dil))

    sesler = sozluk.sesler(ayar) if hasattr(sozluk, "sesler") else {}
    if sesler:              # [modlar] ses_sozcukleri = "orijinal": tek başına duranları script yazar, kalanı için kural
        bolumler.append("# Sound words (an animal's cry, a character's own exclamation)\n"
                        "When one of these is a whole utterance (the whole line, or one speaker's whole part of a "
                        "line), write it in Latin letters exactly like this and do not translate it: "
                        + ", ".join(f"{k} = {v}" for k, v in sesler.items())
                        + ". Any other mimetic word for a sound, a noise or a feeling that a character utters on its "
                        "own is written the same way, as it sounds, in Latin letters (ドキドキ -> Doki doki, シーン -> "
                        "Shiin), and so is a scream, groan or grunt of pain, fear or effort (ぎゃあ -> Gyaa, うおお -> "
                        "Uoo): this overrides the reaction-sound rule. A short sound that carries a meaning (noticing, "
                        f"doubt, agreement: えっ, あっ, ん？, はあ) is a reaction sound, written the way {dil} speakers "
                        "write it. Inside a sentence, translate it like any other word.")

    # Girdi alanları: (koşul, açıklama). Koşulu sağlanmayan alan isteklere de konmaz, burada da anlatılmaz.
    kosullar = [
        (True, """- "translate_to": the language every "text" you return is written in. Never leave a line in its source language, except text a rule here tells you to keep."""),
        (sahne, """- "scene": what is happening in the scene(s) these lines belong to, summarised before translation. Read it first, but it can be wrong: where it disagrees with the lines, the lines win."""),
        (True, """- "context_before" and "context_after": neighbouring lines, READ ONLY. Never translate or return them."""),
        (True, f"""- "lines": the lines to translate. Each has "id", "text" and "dur" (seconds on screen), and may have "speaker"/"speakers"{' and "note"' if notlar else ""}. A line with "skip": true is read only: never return it."""),
        (bolunmus == "dagit", f"""- A line with "parts": N is one sentence shown over N consecutive subtitles. Translate it as ONE sentence in natural {dil} word order, on a single row, with its normal punctuation, and put " | " at every place where a speaker could pause: after a clause and between phrases that each read on their own. Never mark inside a phrase[[tr| (such as "olana kadar", "çatı altında", "bir daha asla")]], and never split off a single word. Mark every such place and no others: a sentence without a natural pause gets no mark, however large N is. The subtitles are cut at the marks that fit their timing best. Never use " | " in any other line."""),
        (bolunmus != "kapali", """- A line with "continues": true stops in mid-sentence and the next id carries on: translate only this line's part and do not finish the sentence here."""),
        (basvuru,                    # çift dilli dosya: satırların ikinci dildeki karşılığı "ref" alanında gider
         '- "ref" on a line is the subtitle shown at the same moment in another language (a published translation of '
         'this very line). Translate from "text"; use "ref" to settle what "text" leaves open: the unstated subject or '
         'object of a sentence, who is spoken to, the sense of an ambiguous word, and the Latin spelling of a name that '
         'is not in the glossary. "ref" may cover more or less than this line and may be free or wrong: where it '
         'disagrees with "text", "text" wins. Never translate "ref" itself, and add nothing that only "ref" says.'),
        (hitap, """- "address": the forms of address for the characters in these lines."""),
        (hitap, """- "says_to": who this line is said to, with the form of address that pair uses ("Kenji -> Ms. Sato: formal")."""),
        (m["sarki"] == "orijinal" and latin, """- "romanize": true marks a song line that stays in its original language: write how it SOUNDS in Latin letters and do not translate it."""),
        (m["sarki"] == "orijinal_ceviri" and latin, """- "reading": true marks a song line written in another script: return its translation in "text" and, in an extra "reading" field, how the original SOUNDS in Latin letters (plain Hepburn), syllable for syllable."""),
        (True, """- "katakana_message": true marks a line that quotes Japanese words typed in katakana (a message sent on a device, a telegram, a child's writing, a chant). They are ordinary Japanese words, not names, foreign words or sounds: translate what they mean. Only a name or code name among them stays as it sounds. "hiragana" gives the same words in hiragana to make them easier to read."""),
        (True, """- "fix" lists ids whose previous answer was rejected or looks doubtful, and why. Correct exactly that and change nothing else. When a doubt turns out to be unfounded, return your previous text unchanged."""),
    ]
    ornek = '{"id": 12, "text": "..."}'
    bolumler.append("# Input and output format\nYou receive a JSON object:\n"
                    + "\n".join(D(satir) for kosul, satir in kosullar if kosul)
                    + "\n\nReturn ONLY a JSON object, no commentary:\n{\"lines\": [" + ornek + "]}\n\n"
                    + f"""Rules:
- Return exactly the ids of the non-skipped lines, each once. Never merge, drop, add or leave a line empty.
- Keep each line readable within its "dur": at most 2 rows of about {ayar["cikti"]["satir_karakter"]} characters{" (per subtitle when " + chr(34) + "parts" + chr(34) + " is given)" if bolunmus == "dagit" else ""}; prefer the more compact natural wording. Use "\\n" for a row break.
- Keep <i> and </i> tags around the corresponding words.
- Write names in normal capitalisation ("Sato", not "SATO"); only a speaker label kept at the start of a row is in capitals.
- When rows start with "- " (two speakers in one line), keep the same number of rows, each starting with "- ", each row in that speaker's own words.""")
    return "\n\n".join(bolumler)
