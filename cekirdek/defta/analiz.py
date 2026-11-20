"""İlk geçiş: çeviriden önce bir yapımın dosyalarından sözlüğü çıkarır.

Dört iş aynı anda yürür; her biri kendi ayarıyla kapatılabilir ([sozluk] bölümü):
  - terim sözlüğü (terim.py): adayı script bulur, türünü model söyler, karşılığı kurala göre script belirler
  - karakter adları (ve [hitap] tablo açıksa hitap tablosu): dosyanın tamamını gören istek
  - sahne özeti, iç ses/anlatıcı, kurgu dili: dosyanın dilimlerini gören istekler
"""
import json
import os
import re
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor

from . import hitap, terim
from .ayar import acik, dil_adi, hedef_kodu, unvan_sozcukleri
from .kaynak import KOSELI_NOT, token_tahmini
from .model import Ret, json_coz


def iliski_istemi(ayar, sozluk):
    """Karakterler ve hitap: dosyanın tamamını gören, yalnızca bu soruya bakan ayrı istek."""
    dil = dil_adi(ayar)
    if not acik(ayar, "hitap", "tablo"):        # hitap tablosu kapalı: yalnızca karakterler sorulur
        return f"""You prepare subtitles for translation into {dil}. You do NOT translate the lines. You read the whole file and report, as JSON, who the speaking characters are.

Return ONLY this JSON object:
{{"characters": [{{"name": "A", "source": "..."}}]}}

# characters
The main speaking characters, at most 12: "name" as one short name in Latin letters, "source" as the name exactly as the lines write it when that differs (the spelling in a speaker label: "ナルト").

If there is nothing to report, return the list empty."""
    kanit = ('\n"evidence" in the input lists what a script read directly from these lines: who addresses whom by '
             'title, and polite or plain verb forms in Japanese (names as the lines write them). An item with a named '
             'speaker is certain: give that pair exactly that form. For "an unlabelled speaker", work out from the '
             'lines who actually says those lines (usually one character, not everyone who talks to that person) and '
             'give only that direction that form. Judge the other pairs so that they fit these.'
             if acik(ayar, "hitap", "kanit") else "")
    return f"""You prepare subtitles for translation into {dil}. You do NOT translate the lines. You read the whole file and report, as JSON, who the speaking characters are and how they address one another. Nobody will correct your output afterwards, so it must be right as delivered.

Return ONLY this JSON object:
{{"characters": [{{"name": "A", "source": "...", "note": "..."}}],
 "address": [{{"from": "A", "to": "B", "form": "informal", "why": "..."}}]}}

# characters
The main speaking characters, at most 20: "name" as one short name in Latin letters, the same one used in "address", "source" as the name exactly as the lines write it when that differs (the spelling in a speaker label: "ナルト"), "note" as one short English line with role, relation to the others and manner of speech ("Mei: small child, Satsuki's little sister; childish broken grammar, speaks of herself in the third person").

# address
Pairs of characters who speak to each other, and whether "from" addresses "to" informally or formally in {dil}{" (as in sen/siz)" if hedef_kodu(ayar) == "tr" else ""}, judged from rank, age, closeness and hostility; someone who calls the other by title and name ("Mr. Potter") is formal to them. Decide each direction on its own, from how THAT speaker talks in the lines, and never copy it from the opposite direction: a butler may be formal to the boy he serves while the boy is informal to him. A character who speaks politely to everyone in the source, even to a child, as a trait of character, stays formal; a small child speaking to a parent is informal. Only pairs you can actually name and are reasonably sure about, at most 25. Every pair you leave out is translated informally, so include every pair in which one side speaks formally to the other. Write each character with one short name in Latin letters, the same way every time ("Kenji", "Mei", "Harry"). "why" is a few words naming what in the lines shows it ("always polite forms, even to the children", "calls him by title", "childhood friends, plain speech"); if you cannot name anything, leave the pair out.{kanit}

If a list has nothing to report, return it empty.{_bilinen_iliski(sozluk)}"""


# Sahne isteği: her bölüm ayrı bir ayarla açılıp kapanır (sahne_ozeti, kurgu_tespiti).
SAHNE_ISTEMI = 'You prepare subtitles for translation. You do NOT translate the lines. You receive one stretch of a subtitle file ("title_hint" names the work) and report as JSON. Be brief.\n\nReturn ONLY this JSON object:\n{{sema}}\n\n{govde}\n\nIf a list has nothing to report, return it empty.'
SAHNE_BOLUMLERI = (
    ('"scenes": [{"from": 1, "to": 40, "summary": "..."}]',
     '# scenes\nSplit these lines into scenes, covering every line in order: a new scene at every change of place or time, every flashback, and every song or narration (roughly one scene per 30-60 lines). "from"/"to" are line ids. "summary" is ONE short English sentence: who is speaking to whom, where, what is happening and what is at stake. State only what these lines show: never guess a place, a relationship or an event they do not show (when the place is not clear, leave it out). Name a group ("the palace guards", "the rival crew") when speakers are unnamed. These summaries are what lets a translator understand short, ambiguous lines, so say what the scene is about, not just that people talk. "breaks", when given, are ids where the subtitles switch between italic text (a narrator or off-screen voice) and normal text (people speaking in the scene): start a new scene there whenever the speaker changes, and name who speaks to whom in each ("the narrator recalls ...", "in the flashback, the old captain tells the boy ...").'),
    ('"fictional": [{"id": 12, "text": "..."}]',
     '# fictional\nEvery passage in an INVENTED language (Klingon, Dothraki, High Valyrian, made-up incantations). Real languages, including Latin, Japanese, Russian or French, never count. "id" is the line id and "text" is the exact passage copied character for character from that line; when only part of a line is invented language, give only that part.{roma}'),
)


def sahne_istemi(ayar):
    """Sahne özetleri ve kurgu dili parçaları: dosyanın bir bölümünü gören, aynı anda çalışan istekler."""
    latin = ayar["ceviri"]["cikti_alfabesi"] == "latin"
    roma = (' If the passage is not written in Latin letters, also give "romanized".' if latin else "")
    s = ayar["sozluk"]
    bolumler = [b for anahtar, b in zip(("sahne_ozeti", "kurgu_tespiti"), SAHNE_BOLUMLERI) if s[anahtar]]
    sema = ",\n ".join(b[0] for b in bolumler)
    govde = "\n\n".join(b[1] for b in bolumler).replace("{roma}", roma)
    return SAHNE_ISTEMI.replace("{sema}", sema).replace("{govde}", govde)


def _bilinen_iliski(sozluk):
    """İlişki isteği için: daha önce karara bağlanmış karakterler ve hitaplar."""
    if sozluk is None or not (sozluk.hitap or sozluk.karakterler):
        return ""
    satirlar = [f"- address: {k} = {'informal' if v == 'samimi' else 'formal'}" for k, v in sozluk.hitap.items()]
    yazimi_var = set(sozluk.adlar.values())
    satirlar += [f"- character: {k}" + ("" if k in yazimi_var else ' ("source" spelling still unknown: list this one '
                                        'again in "characters" with its "source" if the lines write it differently)')
                 for k in sozluk.karakterler]
    return ("\n\n# Already decided for this series (final: never change these, do not list them again, "
            "use the same names, and stay consistent with them)\n" + "\n".join(satirlar))


def _ayristir(ham):
    veri = json_coz(ham)
    if not isinstance(veri, dict):
        raise ValueError("JSON nesnesi değil")
    for alan in ("characters", "address", "scenes", "fictional"):
        if not isinstance(veri.get(alan) or [], list):
            raise ValueError(f'"{alan}" liste değil')
    return veri


def _iste(model, sistem, yuk, ayar, gunluk, dosya, tur):
    """JSON döndüren tek bir isteği yeniden denemeli çalıştırır; olmazsa None döner."""
    hitap_acik = acik(ayar, "hitap", "tablo")         # kapalıyken ilişki isteği küçük bir karakter listesidir
    dusunme = ayar["sozluk"][{"sahne": "sahne_dusunme", "ilişki": "hitap_dusunme" if hitap_acik else "sahne_dusunme"}
                             .get(tur, "dusunme_seviyesi")] or None
    for deneme in range(1, ayar["kontrol"]["deneme_sayisi"] + 1):
        try:
            ham = model.iste(sistem, yuk, dusunme)
            gunluk.ham(dosya, {"tur": tur, "deneme": deneme, "gelen": ham})
            return _ayristir(ham)
        except Ret as e:
            gunluk.hata(dosya, f"{tur} reddedildi ({e}), deneme {deneme}")
        except ValueError as e:
            gunluk.hata(dosya, f"{tur} yanıtı bozuk ({e}), deneme {deneme}")
    return None


def italik_gecisleri(parca, en_az=3):
    """Anlatım (italik) ile sahne içi konuşma (düz) arasındaki geçişlerin satır numaraları.

    Gürültü olmasın diye geçişin iki yanında da en az 'en_az' satırlık aynı türden bir dizi aranır.
    """
    def italik(metin):
        metin = metin.strip()
        return metin.startswith("<i>") and metin.endswith("</i>")

    tur = [italik(x["text"]) for x in parca]
    gecisler = []
    for k in range(en_az, len(parca) - en_az + 1):
        if tur[k] != tur[k - 1] and len(set(tur[k - en_az:k])) == 1 and len(set(tur[k:k + en_az])) == 1:
            gecisler.append(parca[k]["id"])
    return gecisler


KISA_SAHNE = 5      # bundan az satırlık "sahne" komşusuna katılır


def kisa_sahneleri_birlestir(sahneler):
    """Birkaç satırlık sahne özetleri parçalı ve çoğu kez yanlıştı ("In the Shire" diyen 2 satırlık sahne yol
    üstündeyken; LOTR 517-518): KISA_SAHNE'den kısa sahne önceki sahneye (ilkse sonrakine) katılır, özeti o sahnenin."""
    gecerli = sorted((dict(x) for x in sahneler if isinstance(x, dict) and isinstance(x.get("from"), int)
                      and isinstance(x.get("to"), int) and x["to"] >= x["from"]), key=lambda x: x["from"])

    def kisa(x):
        return x["to"] - x["from"] + 1 < KISA_SAHNE
    sonuc = []
    for x in gecerli:
        if sonuc and kisa(x):
            sonuc[-1]["to"] = max(sonuc[-1]["to"], x["to"])
        else:
            sonuc.append(x)
    if len(sonuc) > 1 and kisa(sonuc[0]):
        ilk = sonuc.pop(0)
        sonuc[0]["from"] = ilk["from"]
    return sonuc


def adlari_sozluge_uydur(sozluk, karakterler, hitaplar, sahne):
    """Karakter, hitap ve sahne özetlerindeki adlar sözlükteki yazımla yazılır: özet ve hitap "Lloyd" derken sözlük
    "Loid" diyordu (SPY x FAMILY), model aynı kişi için iki ad görüyordu. Eşleme karakterin kaynaktaki yazımından
    ("ロイド") sözlüğün ad karşılığına gider."""
    esle = {}
    for k in karakterler:
        if not isinstance(k, dict) or not isinstance(k.get("name"), str):
            continue
        girdi = sozluk.girdiler.get(k.get("source") or "") or sozluk.girdiler.get(k["name"]) or {}
        hedef = girdi.get("hedef") if girdi.get("tur") in ("name", "codename") else ""
        ad = k["name"].strip()
        if hedef and " " in hedef and " " not in ad:
            # Kaynak tam ad ("ヨル・ブライア" -> "Yor Briar"), karakter adı kısa ("Yor"): kısa ad kısa kalır, yalnız
            # yazımı sözlüğe uyar. Tam ad yazılınca konuşmacı etiketi ("ヨル") hitap tablosuna bağlanamıyordu.
            puan, hedef = max((SequenceMatcher(None, ad.lower(), s.lower()).ratio(), s) for s in hedef.split())
            if puan < 0.6:
                continue
        if hedef and hedef != ad and " " not in ad:
            esle[ad] = hedef
    if not esle:
        return karakterler, hitaplar, sahne
    desen = re.compile(r"(?<![\w'’])(" + "|".join(re.escape(a) for a in sorted(esle, key=len, reverse=True)) + r")(?![\w])")

    def yaz(metin):
        return desen.sub(lambda m: esle[m.group(1)], metin) if isinstance(metin, str) else metin
    karakterler = [{**k, "name": yaz(k.get("name")), "note": yaz(k.get("note"))} if isinstance(k, dict) else k
                   for k in karakterler]
    hitaplar = [{**h, "from": yaz(h.get("from")), "to": yaz(h.get("to"))} if isinstance(h, dict) else h for h in hitaplar]
    sahne = {d: [{**x, "summary": yaz(x.get("summary"))} for x in liste] for d, liste in sahne.items()}
    return karakterler, hitaplar, sahne


def dizi_analiz(dosyalar, ayar, model, sozluk, gunluk):
    """Bir yapımın dosyalarını aynı anda okutur ve sonuçları sözlüğe işler.

    dosyalar: (göreli ad, satırlar, başlık ipucu) listesi. Hata olursa uyarıp devam eder.
    """
    s = ayar["sozluk"]
    hitap_acik = acik(ayar, "hitap", "tablo")
    koseli_sil = ayar["modlar"]["koseli_not"] == "sil"
    if getattr(sozluk, "bozuk", ""):         # sessizce yeniden kurulmasın: eski kararlar o dosyada kaldı
        gunluk.hata(dosyalar[0][0], "sözlük dosyası okunamadı, sözlük baştan kuruluyor; bozuk dosya saklandı: "
                                    + os.path.basename(sozluk.bozuk))
        sozluk.bozuk = ""

    def bol(satirlar, hedef):
        """Satırları yaklaşık "hedef" tokenlik parçalara böler: [{"id", "text"}, ...] listeleri."""
        parcalar, parca, toplam = [], [], 0
        for i, satir in enumerate(satirlar):
            metin = KOSELI_NOT.sub("", satir.metin).strip() if koseli_sil else satir.metin   # silinen not gitmez
            if not metin.strip():
                continue
            parca.append({"id": i + 1, "text": metin})
            toplam += token_tahmini(metin)
            if toplam >= hedef:
                parcalar.append(parca)
                parca, toplam = [], 0
        if parca:
            if parcalar and len(parca) < 15:     # çok kısa kuyruk: önceki parçaya eklenir
                parcalar[-1].extend(parca)
            else:
                parcalar.append(parca)
        return parcalar

    def isler(hedef):
        return [(dosya, baslik, p) for dosya, satirlar, baslik in dosyalar for p in bol(satirlar, hedef)]

    # Sen/siz kanıtı: script'in kaynaktan kesin okudukları (dosya -> (kanıt, nedenler)); tablo isteği oylanır.
    kanit_acik = hitap_acik and acik(ayar, "hitap", "kanit")
    kanitlar = ({dosya: hitap.kanit_topla(satirlar, unvan_sozcukleri(ayar)) for dosya, satirlar, _ in dosyalar}
                if kanit_acik else {})
    oy = max(1, int(ayar["hitap"]["oy"])) if hitap_acik else 1
    if oy > 2 and sozluk.kurulmus() and sozluk.hitap:
        oy -= 1                          # hitap tablosu önceki bölümlerden belli: ilk karar kalır, bir oy az
    # Kapatılan iş için istek hiç gitmez.
    iliski_isleri = isler(s["analiz_token"]) if s["karakterler"] or hitap_acik else []
    sahne_isleri = isler(s["sahne_token"]) if s["sahne_ozeti"] or s["kurgu_tespiti"] else []
    sistem_iliski, sistem_sahne = iliski_istemi(ayar, sozluk), sahne_istemi(ayar)

    def iliski_calis(is_):
        dosya, baslik, p = is_
        yuk = {"title_hint": baslik or dosya}
        if kanitlar.get(dosya, ({},))[0]:
            yuk["evidence"] = hitap.kanit_metni(*kanitlar[dosya])
        yuk = json.dumps({**yuk, "lines": p}, ensure_ascii=False)
        sonuc = _iste(model, sistem_iliski, yuk, ayar, gunluk, dosya, "ilişki")
        if sonuc is None:
            gunluk.hata(dosya, f"satır {p[0]['id']}-{p[-1]['id']}: karakter ve hitap çıkarılamadı")
        return sonuc or {}

    def sahne_calis(is_):
        dosya, baslik, p = is_
        yuk = {"title_hint": baslik or dosya}
        gecisler = italik_gecisleri(p)
        if gecisler and s["sahne_ozeti"]:
            yuk["breaks"] = gecisler
        yuk["lines"] = p
        sonuc = _iste(model, sistem_sahne, json.dumps(yuk, ensure_ascii=False), ayar, gunluk, dosya, "sahne")
        if sonuc is None:
            gunluk.hata(dosya, f"satır {p[0]['id']}-{p[-1]['id']}: sahne bilgisi çıkarılamadı")
        return sonuc or {}

    with ThreadPoolExecutor(max_workers=max(1, ayar["model"]["es_zamanli_istek"])) as havuz:
        # Üç iş aynı anda gider; toplam süreyi en yavaş olanı belirler.
        iliski_gelecek = [[havuz.submit(iliski_calis, x) for _ in range(oy)] for x in iliski_isleri]
        sahne_gelecek = [havuz.submit(sahne_calis, x) for x in sahne_isleri]
        diller = terim.terimleri_cikar(dosyalar, ayar, model, sozluk, gunluk) if s["terimler"] else {}
        if not s["terimler"] and ayar["unvan"]["acik"]:
            terim.unvanlari_bul(dosyalar, ayar, sozluk)
        sahne_sonuclari = [g.result() for g in sahne_gelecek]
        # Yanıt alınamayan istek (ret, bozuk yanıt) oy değildir: sayılırsa tek geçerli yanıtın çiftleri azınlıkta kalır.
        iliski_sonuclari = [hitap.oylari_birlestir([y for y in (g.result() for g in grup) if y])
                            for grup in iliski_gelecek]

    def topla(isler_, sonuclar, alan):
        """dosya -> o dosyanın isteklerinden gelen "alan" listelerinin birleşimi."""
        toplam = {dosya: [] for dosya, _, _ in dosyalar}
        for (dosya, _, _), sonuc in zip(isler_, sonuclar):
            if isinstance(sonuc.get(alan), list):
                toplam[dosya].extend(sonuc[alan])
        return toplam

    karakterler = [k for liste in topla(iliski_isleri, iliski_sonuclari, "characters").values() for k in liste]
    hitaplar = ([h for liste in topla(iliski_isleri, iliski_sonuclari, "address").values() for h in liste]
                if hitap_acik else [])           # hitap modele bırakıldıysa sözlüğe hitap yazılmaz
    if kanit_acik:       # script'in kesin gördüğü çiftler başa konur: ilk karar kaldığı için model değiştiremez
        bilinen = [{"name": k} for k in sozluk.karakterler] + [{"name": v, "source": k} for k, v in sozluk.adlar.items()]
        kesin = hitap.kesin_ciftler([k for k, _ in kanitlar.values()], bilinen + karakterler)
        ciftler = {(h["from"], h["to"]) for h in kesin}
        hitaplar = kesin + [h for h in hitaplar if isinstance(h, dict) and (h.get("from"), h.get("to")) not in ciftler]
        if kesin:
            gunluk.uyari(dosyalar[0][0], "hitap: script'in kaynaktan okuduğu çiftler: " + ", ".join(
                f"{h['from']} -> {h['to']}: {'resmi' if h['form'] == 'formal' else 'samimi'}" for h in kesin))
    kurgu = topla(sahne_isleri, sahne_sonuclari, "fictional")
    sahne = {dosya: kisa_sahneleri_birlestir(liste) for dosya, liste in topla(sahne_isleri, sahne_sonuclari, "scenes").items()}
    karakterler, hitaplar, sahne = adlari_sozluge_uydur(sozluk, karakterler, hitaplar, sahne)
    latin = ayar["ceviri"]["cikti_alfabesi"] == "latin"
    for dosya, satirlar, _ in dosyalar:
        sozluk.birlestir(dosya, {"characters": karakterler, "address": hitaplar, "fictional": kurgu[dosya],
                                 "scenes": sahne[dosya],
                                 "language": diller.get(dosya, "")}, satirlar, latin, gunluk)
    sozluk.kaydet()
