"""Testler: python -m unittest test_defta   (internet ve API anahtarı gerekmez)"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import tomllib
except ModuleNotFoundError:   # Python 3.10 ve öncesi (Kodi 21: 3.8): PC başlatıcısı ve config.toml testleri atlanır
    tomllib = None

from defta import srt, temizlik
from defta.ayar import ayar_birlestir
from defta.cevirmen import Cevirmen
from defta.cumle import BIRLESTI, dengele, kesme_adaylari, parcalara_bol, yerlestir
from defta.dosya import dosya_cevir
from defta.kaynak import not_ve_konusmaci_ayir
from defta.gunluk import Gunluk
from defta.istem import istem_olustur
from defta.analiz import dizi_analiz
from defta.model import Ret
from defta.sozluk import Sozluk, isaretle

ORNEK = """1
00:00:01,000 --> 00:00:03,000
<font color="#fff">Where is</font>

2
00:00:03,100 --> 00:00:05,000
he going?

3
00:00:09,000 --> 00:00:11,000
{\\an8}<i>I should have told her.</i>

4
00:00:11,500 --> 00:00:13,000
- Hello, Yor-san.
- Hi.

5
00:00:14,000 --> 00:00:16,000
♪ La la la ♪

6
00:00:17,000 --> 00:00:18,000
(sighs)

7
00:00:19,000 --> 00:00:20,000
JOHN: Run!
"""


class SahteModel:
    """Her satırın başına TR ekler; davranışı 'kural' işleviyle değiştirilebilir."""

    def __init__(self, kural=None, analiz=None, terim=None, kesif=None, eser=""):
        self.kural, self.cagri, self.analiz, self.gidenler = kural, 0, analiz or {}, []
        self.dusunme = []
        # terim: yazım -> sınıflandırma yanıtı ({"t": "name"}...); kesif: keşif isteğinin döndüreceği girdiler
        self.terim, self.kesif, self.eser, self.terim_istekleri = terim or {}, kesif or [], eser, []
        self.sesler = set()         # ses sorusuna "ses" denecek sözcükler

    def iste(self, sistem, kullanici, dusunme=None):
        self.dusunme.append(dusunme)
        if sistem.startswith("You classify names and terms"):                        # terim sınıflandırma
            adaylar = json.loads(kullanici)["candidates"]
            self.terim_istekleri.append([c["s"] for c in adaylar])
            return json.dumps({"work": self.eser, "items": [{"i": c["i"], **self.terim.get(c["s"], {"t": "common"})}
                                                            for c in adaylar]}, ensure_ascii=False)
        if sistem.startswith("You are given words from the subtitles"):              # ses sözcüğü mü
            return json.dumps({"items": [{"i": w["i"], "sound": w["s"] in self.sesler}
                                         for w in json.loads(kullanici)["words"]]}, ensure_ascii=False)
        if sistem.startswith("You render terms"):                                    # karşılık isteği
            # terim yanıtındaki "k" (yoksa yerleşik biçim, yoksa çeviri) karşılık isteğinin yanıtıdır
            ogeler = json.loads(kullanici)["items"]
            self.karsilik_istekleri = getattr(self, "karsilik_istekleri", []) + [ogeler]
            def cevap(t):
                k = t.get("k", t.get("e") or t.get("tr"))
                return (k.pop(0) if k else None) if isinstance(k, list) else k
            yanit = [{"i": x["i"], "tr": c} for x in ogeler for c in [cevap(self.terim.get(x["s"], {}))] if c]
            return json.dumps({"items": yanit}, ensure_ascii=False)
        if sistem.startswith(("Each item is a Japanese expression", "Each item holds two lines")):   # ifade eşitleme
            ogeler = json.loads(kullanici)["items"]
            self.ifade_istekleri = getattr(self, "ifade_istekleri", []) + [ogeler]
            ifade = getattr(self, "ifade", {})       # satır no -> yeni metin
            return json.dumps({"lines": [{"id": x["id"], "text": ifade[x["id"]]} for o in ogeler for x in o["lines"]
                                         if x["id"] in ifade]}, ensure_ascii=False)
        if sistem.startswith("Each item is a "):                                     # yazım isteği
            self.yazim_istekleri = getattr(self, "yazim_istekleri", []) + [[x["s"] for x in json.loads(kullanici)["items"]]]
            return json.dumps({"items": []})
        if sistem.startswith("You read the subtitles of one film"):                  # terim keşfi
            return json.dumps({"language": "en", "work": self.eser, "items": self.kesif}, ensure_ascii=False)
        if sistem.startswith("A film subtitle repeats"):                             # yinelenen satır seçimi
            ogeler = json.loads(kullanici)["items"]
            self.secim_istekleri = getattr(self, "secim_istekleri", []) + [ogeler]
            # varsayılan: tek başına yinelenen replik kendi çevirisiyle kalır (0), sahnede ilk aday
            return json.dumps({"choices": [{"n": x["n"], "pick": 0 if "occurrences" in x else 1}
                                           for x in ogeler if "candidates" in x]})
        if sistem.startswith("Name the language"):                                  # dil isteği (keşif dil vermediyse)
            self.dil_istekleri = getattr(self, "dil_istekleri", 0) + 1
            return json.dumps({"language": getattr(self, "dil", "en")})
        if sistem.startswith("You prepare subtitles for translation. You do NOT"):   # sahne isteği
            a = {} if callable(self.analiz) else self.analiz
            return json.dumps({"scenes": a.get("scenes", []), "fictional": a.get("fictional", [])},
                              ensure_ascii=False)
        if "who the speaking characters are" in sistem[:400]:                        # ilişki isteği
            a = {} if callable(self.analiz) else self.analiz
            return json.dumps({"characters": a.get("characters", []), "address": a.get("address", [])},
                              ensure_ascii=False)
        self.cagri += 1
        veri = json.loads(kullanici)
        self.gidenler.append(veri)
        satirlar = []
        for s in veri["lines"]:
            if s.get("skip"):
                continue
            metin = s["text"]
            if metin == "(sighs)":
                metin = "[iç çeker]"
            elif metin.startswith("JOHN: "):
                metin = "JOHN: TR " + metin[6:]
            elif metin.startswith("<i>"):
                metin = "<i>TR " + metin[3:]
            elif metin.startswith("- "):
                metin = "\n".join("- TR " + p[2:] for p in metin.split("\n"))
            else:
                metin = "TR " + metin
            satirlar.append({"id": s["id"], "text": metin})
        if self.kural:
            satirlar = self.kural(self.cagri, satirlar)
        return json.dumps({"lines": satirlar}, ensure_ascii=False)


class Testler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gunluk = Gunluk(os.path.join(self.tmp.name, "logs"))

    def _dosya_cevir(self, ayar, model, icerik=ORNEK):
        kaynak = os.path.join(self.tmp.name, "a.srt")
        hedef = os.path.join(self.tmp.name, "cikti", "a.tr.srt")
        with open(kaynak, "w", encoding="utf-8") as f:
            f.write(icerik)
        dosya_cevir(kaynak, hedef, "a.srt", ayar, model, "SISTEM", self.gunluk,
                    os.path.join(self.tmp.name, ".onbellek"))
        with open(hedef, encoding="utf-8") as f:
            return srt.srt_ayristir(f.read())

    def test_art_arda_yinelenen_replik_tek_altyazi_olur(self):
        # Kaynak aynı repliği art arda iki altyazıya bölmüş (araya başka konuşma girince yeniden yazılmış): süreler
        # bitişik, metin aynı. İkisi aynı çevrilir (model yeniden sorulmaz) ve çıktıda tek altyazı olur.
        icerik = ("1\n00:00:01,000 --> 00:00:03,000\nYou are in my way. Leave first.\n\n"
                  "2\n00:00:02,000 --> 00:00:02,900\nHow dare you!\n\n"
                  "3\n00:00:03,000 --> 00:00:03,800\nYou are in my way. Leave first.\n\n"
                  "4\n00:00:09,000 --> 00:00:11,000\nYou are in my way. Leave first.\n")
        ceviri = {1: "Ayağımın altında dolaşma. Önce sen git.", 2: "Bu ne cüret!", 3: "Yolumdan çekil. Önce sen git.",
                  4: "Engel oluyorsun. Önce sen ayrıl."}

        def kural(cagri, satirlar_):
            for x in satirlar_:
                x["text"] = ceviri[x["id"]]
            return satirlar_
        model = SahteModel(kural)
        cikti = self._dosya_cevir(ayar_birlestir({"sozluk": {"otomatik": False}}), model, icerik)
        self.assertEqual(model.cagri, 1)                                     # çeviri isteği bir tane
        # uzaktaki aynı replik modele sorulur (tek başına yinelenen tam cümle); "0" deyince kendi çevirisi kalır
        self.assertEqual([x["source"] for x in model.secim_istekleri[0]], ["You are in my way. Leave first."])
        self.assertEqual([(x.bas, x.bit, x.metin.replace("\n", " ")) for x in cikti], [
            (1000, 3800, "Ayağımın altında dolaşma. Önce sen git."), (2000, 2900, "Bu ne cüret!"),
            (9000, 11000, "Engel oluyorsun. Önce sen ayrıl.")])              # uzaktaki aynı replik kendi çevirisiyle

    def test_yalniz_secilenler_cevrilir_otekiler_baglam(self):
        # Kodi'nin hızlı çevirisi: aralık çevrilir, öncesi ve sonrası modele yalnız bağlam olarak gider, çıktıya girmez.
        # Sınırdan geçen bölünmüş cümlenin aralıktaki parçası kendi başına çevrilir; başı bağlamda görünür.
        icerik = "".join(f"{n}\n00:00:{2 * n:02d},000 --> 00:00:{2 * n + 1:02d},000\n{m}\n\n" for n, m in enumerate(
            ["Line one.", "We went to the", "market yesterday.", "Line four.", "Line five.", "Line six."], 1))
        kaynak = os.path.join(self.tmp.name, "a.srt")
        hedef = os.path.join(self.tmp.name, "cikti", "a.tr.srt")
        with open(kaynak, "w", encoding="utf-8") as f:
            f.write(icerik)
        model = SahteModel()
        dosya_cevir(kaynak, hedef, "a.srt", ayar_birlestir(), model, "SISTEM", self.gunluk,
                    os.path.join(self.tmp.name, ".onbellek"), cevrilecek={2, 3})
        self.assertEqual(len(model.gidenler), 1)
        giden = model.gidenler[0]
        self.assertEqual([(s["id"], s["text"]) for s in giden["lines"]], [(3, "market yesterday."), (4, "Line four.")])
        self.assertEqual(giden["context_before"], ["Line one.", "We went to the"])
        self.assertEqual(giden["context_after"], ["Line five.", "Line six."])
        with open(hedef, encoding="utf-8") as f:
            cikti = srt.srt_ayristir(f.read())
        self.assertEqual([s.metin for s in cikti], ["TR market yesterday.", "TR Line four."])
        self.assertEqual(cikti[0].bas, 6000)

    def test_kesme_yerini_script_secer(self):
        sec = lambda parcalar, sureler: yerlestir(*kesme_adaylari(parcalar), sureler, 25, 84)
        # Aday sayısı gerekenden çok: süreye en iyi uyanlar kullanılır, ötekiler birleşir.
        self.assertEqual(sec(["Feneri al,", "kilere in,", "üçüncü raftaki kırmızı defteri", "bana getir."],
                             [1000, 1500, 1800, 1400]),
                         (["Feneri al,", "kilere in,", "üçüncü raftaki kırmızı defteri", "bana getir."], [1, 1, 1, 1]))
        self.assertEqual(sec(["Kuyu zehirlendiği için", "bütün köy", "tepelere", "taşındı."], [1600, 3400]),
                         (["Kuyu zehirlendiği için", "bütün köy tepelere taşındı."], [1, 1]))
        # Tek sözcük kendi altyazısında kalacaksa kesilmez: iki altyazının süresi birleşir.
        self.assertEqual(sec(["Maren'e, bir daha geç dönerse", "gece nöbetinde ona yer olmayacağını", "söyledim."],
                             [3000, 2000, 1400]),
                         (["Maren'e, bir daha geç dönerse", "gece nöbetinde ona yer olmayacağını söyledim."], [1, 2]))
        # Duraklama yeri yok: cümle tek altyazı olur.
        self.assertEqual(sec(["Greyfold'dan neden ayrıldığını hiç söylemedi."], [900, 1800]),
                         (["Greyfold'dan neden ayrıldığını hiç söylemedi."], [2]))
        # Model işaretlemediyse noktalama da adaydır; birleşen parçalar arasında "... ..." izi kalmaz.
        self.assertEqual(sec(["Elbette şüpheleniyor, o bir tüccar değil."], [1500, 1500]),
                         (["Elbette şüpheleniyor,", "o bir tüccar değil."], [1, 1]))
        self.assertEqual(sec(["Zili çalıp...", "...durdular", "ve gittiler."], [2500, 1500])[0],
                         ["Zili çalıp durdular", "ve gittiler."])
        # İki satıra sığmayan tek parça yerleşmez: çağıran sözcük sınırından böler.
        self.assertIsNone(sec(["a " * 60], [1000, 1000]))

    def test_tasan_sozcuk_cumle_sonuna_cekilir(self):
        from defta.cumle import tasani_duzelt
        self.assertEqual(tasani_duzelt(["Müdürüm, barışın geleceği", "bağlı. Daha ciddi yaklaşın."]),
                         ["Müdürüm, barışın geleceği bağlı.", "Daha ciddi yaklaşın."])
        self.assertEqual(tasani_duzelt(["Bilgiyi sızdır. Onlar", "da sorun istemez."]),
                         ["Bilgiyi sızdır.", "Onlar da sorun istemez."])
        dar = ["Son zamanlarda anime parkına dönüştürüldüğünü", "duymuştum. Bu mu?"]
        self.assertEqual(tasani_duzelt(dar, [30, 40]), dar)        # okuma hızını aşacaksa kaydırılmaz
        for saglam in (["Evet, geldim.", "Tamam. Sonra görüşürüz."], ["Bugün Dr. Loid", "geldi mi?"],
                       ["Şehre vardığımızda", "seni arayacağım."]):
            self.assertEqual(tasani_duzelt(saglam), saglam)

    def test_az_parcali_cumlede_sureler_birlesir(self):
        icerik = ("1\n00:00:01,000 --> 00:00:03,000\nSince the peace of the world\n\n"
                  "2\n00:00:03,100 --> 00:00:05,000\ndepends on this operation\n\n"
                  "3\n00:00:05,100 --> 00:00:07,000\nplease take it seriously.\n\n"
                  "4\n00:00:09,000 --> 00:00:10,000\nFine.\n")

        class M:
            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                yanit = {1: "Dünya barışı bu operasyona bağlı, | lütfen biraz ciddiye alın.", 4: "Peki."}
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        cikti = self._dosya_cevir(ayar_birlestir(), M(), icerik)
        self.assertEqual([s.metin.replace("\n", " ") for s in cikti],
                         ["Dünya barışı bu operasyona bağlı,", "lütfen biraz ciddiye alın.", "Peki."])
        # ilk parça iki altyazının süresini kaplar; hiçbir metin başka repliğin süresine taşmaz
        self.assertEqual([(s.bas, s.bit) for s in cikti], [(1000, 5000), (5100, 7000), (9000, 10000)])

    def test_iki_konusmaci_satir_kaydirmada_karismaz(self):
        icerik = ("1\n00:00:01,000 --> 00:00:05,000\n- They're scattered, divided, leaderless.\n"
                  "- There is one who could unite them.\n")

        class M:
            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                return json.dumps({"lines": [{"id": veri["lines"][0]["id"], "text":
                                   "- Dağıldılar, bölündüler, başlarında kimse yok.\n- Onları birleştirebilecek biri var."}]},
                                  ensure_ascii=False)

        cikti = self._dosya_cevir(ayar_birlestir({"modlar": {"konusmaci_tiresi": "sil"}}), M(), icerik)
        self.assertEqual(cikti[0].metin, "Dağıldılar, bölündüler, başlarında kimse yok.\n"
                                         "Onları birleştirebilecek biri var.")

    def test_kucuk_harfli_dunya_terimi_aday_olur(self):
        from defta.terim import adaylari_bul
        metinler = ["He's one of them Rangers, they say.", "What's this, a ranger off his guard?",
                    "What would a ranger know?", "This is no mere ranger.", "Look at that.", "I said Look at him.",
                    "They look fine.", "We look away."]
        satirlar = [srt.Satir(n + 1, n * 1000, n * 1000 + 900, m) for n, m in enumerate(metinler)]
        adaylar = [a["yazim"] for a in adaylari_bul([("a.srt", satirlar, "")], set())]
        self.assertIn("ranger", adaylar)
        self.assertNotIn("look", [a.lower() for a in adaylar])        # gündelik sözcük aday olmaz

    def test_srt_gidis_donus(self):
        satirlar = srt.srt_ayristir(ORNEK)
        self.assertEqual(len(satirlar), 7)
        self.assertEqual(satirlar[3].metin, "- Hello, Yor-san.\n- Hi.")
        self.assertEqual(satirlar[2].bas, 9000)
        tekrar = srt.srt_ayristir(srt.srt_yaz(satirlar))
        self.assertEqual([s.metin for s in tekrar], [s.metin for s in satirlar])

    def test_kodlama_taninir(self):
        """UTF-8 olmayan Japonca dosya (Shift-JIS) ve Türkçe dosya (cp1254) doğru okunur."""
        jp = "1\n00:00:01,000 --> 00:00:02,000\n（ケンジ）ハナコ、学校に行くぞ。今日はテストの日だろう？\n" * 4
        tr = "1\n00:00:01,000 --> 00:00:02,000\nÇok güzel bir gün; ışık söndü mü? Ağaçlar çiçek açtı.\n" * 4
        # Eski kodlamalı İngilizce dosya, birkaç aksanlı harfle (lotr.srt: cp1252). Bozuk UTF-8 sayılıp harfleri
        # "?" olmamalı ("Nîn" -> "N?n" altyazıya çıkıyordu).
        en = "1\n00:00:01,000 --> 00:00:02,000\nNîn o Chithaeglir… don’t go, Barad-dûr.\n" + "2\n00:00:03,000 --> 00:00:04,000\nRun.\n" * 400
        for metin, kodlama in ((jp, "cp932"), (tr, "cp1254"), (jp, "utf-8"), (en, "cp1252")):
            yol = os.path.join(self.tmp.name, "k.srt")
            with open(yol, "wb") as f:
                f.write(metin.encode(kodlama))
            self.assertEqual(srt.metin_oku(yol), metin, kodlama)
        # Neredeyse tümüyle UTF-8 dosyada tek bozuk bayt: yalnızca o karakter "?" olur.
        with open(yol, "wb") as f:
            f.write(tr.encode("utf-8").replace("gün;".encode("utf-8"), b"g\x92n;", 1))
        self.assertEqual(srt.metin_oku(yol), tr.replace("gün;", "g?n;", 1))

    def test_yarim_utf16_dosya_cokertmez(self):
        # İndirme yarıda kesilmiş (tek bayt fazla) ya da eşi olmayan vekil taşıyan UTF-16 dosya: okunabilen kısım okunur.
        metin = "1\n00:00:01,000 --> 00:00:02,000\nGüle güle.\n"
        for kodlama in ("utf-16-le", "utf-16-be"):
            bom = b"\xff\xfe" if kodlama == "utf-16-le" else b"\xfe\xff"
            self.assertEqual(srt.metin_coz(bom + metin.encode(kodlama)), metin)
            self.assertEqual(srt.metin_coz(bom + metin.encode(kodlama) + b"A"), metin + "?")
        self.assertEqual(srt.metin_coz(b"\xff\xfea\x00\x00\xd8b\x00"), "a?b")

    def test_sayi_iceren_metin_korunur(self):
        satirlar = srt.srt_ayristir("1\n00:00:01,000 --> 00:00:02,000\nI have\n42\n\n2\n00:00:03,000 --> 00:00:04,000\nOk\n")
        self.assertEqual(satirlar[0].metin, "I have\n42")

    def test_bos_satirsiz_srt_de_numara_metne_karismaz(self):
        # Altyazılar arasına boş satır konmamış dosya: sıradaki numara (bu altyazınınki + 1) metin değildir.
        icerik = ("\ufeff1\n00:00:01,000 --> 00:00:02,000\nHello\n2\n00:00:03,000 --> 00:00:04,000\nI have\n3\n"
                  "3\n00:00:05,000 --> 00:00:06,000\nBye\n")
        self.assertEqual([s.metin for s in srt.srt_ayristir(icerik)], ["Hello", "I have\n3", "Bye"])
        # numarasız dosyada ya da sıradaki numara olmayan sayıda metin korunur
        self.assertEqual([s.metin for s in srt.srt_ayristir(
            "00:00:01,000 --> 00:00:02,000\nI have\n42\n00:00:03,000 --> 00:00:04,000\nOk\n")], ["I have\n42", "Ok"])
        self.assertEqual([s.metin for s in srt.srt_ayristir(
            "7\n00:00:01,000 --> 00:00:02,000\nI have\n42\n00:00:03,000 --> 00:00:04,000\nOk\n")], ["I have\n42", "Ok"])

    def test_kaynak_hazirla(self):
        metin, ustte = temizlik.kaynak_hazirla("{\\an8}{\\pos(1,2)}<b>Hi</b> <I>there</I>&amp;\\Nyou")
        self.assertTrue(ustte)
        self.assertEqual(metin, "Hi <i>there</i>&\nyou")

    def test_son_temizlik(self):
        m = ayar_birlestir()["modlar"]
        self.assertEqual(temizlik.son_temizlik("[müzik] JOHN: Koş !", m), "Koş!")
        self.assertEqual(temizlik.son_temizlik("[müzik]", m), "")
        self.assertEqual(temizlik.son_temizlik("<i>Selam", m), "<i>Selam</i>")
        self.assertEqual(temizlik.son_temizlik("⟦Mellon⟧ dedi {\\c&H1&}", m), "Mellon dedi")
        m2 = dict(m, italik="sil", konusmaci_tiresi="sil", sarki_isareti="sil")
        self.assertEqual(temizlik.son_temizlik("- <i>Selam</i>\n- ♪ Naber ♪", m2), "Selam\nNaber")
        self.assertEqual(temizlik.kalinti_tara("<i>a</i> {b}"), "{}")

    def test_farsca_yarim_bosluk_korunur(self):
        # U+200C Farsça yazımın parçasıdır ("می‌روم"): silinince sözcükler yanlış bitişir. Latin metindeki silinir.
        m = ayar_birlestir()["modlar"]
        self.assertEqual(temizlik.son_temizlik("من می\u200cروم.", m), "من می\u200cروم.")
        self.assertEqual(temizlik.kaynak_hazirla("کتاب\u200cها\u200b را\u200f")[0], "کتاب\u200cها را")
        self.assertEqual(temizlik.kaynak_hazirla("Fro\u200cdo\u200d, a\u00adb\ufeff \u202bc\u202c\u200c")[0], "Frodo, ab c")

    def test_latin_ve_sarma(self):
        self.assertEqual(temizlik.latin_disi("Çğışöü é ō"), "")
        self.assertTrue(temizlik.latin_disi("abc 日本 привет"))
        sarili = temizlik.sar("Bu satır kırk iki karakterden epeyce uzun olduğu için ikiye bölünmeli", 42)
        self.assertEqual(len(sarili.split("\n")), 2)
        self.assertEqual(temizlik.sar("- Bir\n- İki", 5), "- Bir\n- İki")

    def test_parcalara_bol_cumle_ortasindan_kesmez(self):
        satirlar = [srt.Satir(i + 1, i * 2000, i * 2000 + 1500,
                              "devam eden" if i % 3 else "Biten cümle.") for i in range(30)]
        parca = dict(ayar_birlestir()["parca"], altyazi_sayisi=5)
        parcalar = parcalara_bol(satirlar, parca)
        self.assertEqual(parcalar[0][0], 0)
        self.assertEqual(parcalar[-1][1], 30)
        for (_, bit), (bas, _) in zip(parcalar, parcalar[1:]):
            self.assertEqual(bit, bas)
            self.assertEqual(satirlar[bit - 1].metin, "Biten cümle.")

    def test_tam_dosya(self):
        ayar = ayar_birlestir({"modlar": {"konusmaci_tiresi": "korunsun", "ustte_gosterme": "korunsun"}})
        cikti = self._dosya_cevir(ayar, SahteModel())
        metinler = [s.metin for s in cikti]
        self.assertEqual(metinler, [
            "TR Where is he going?", "{\\an8}<i>TR I should have told her.</i>",     # duraklama yeri yok: tek altyazı
            "- TR Hello, Yor-san.\n- TR Hi.", "♪ La la la ♪", "TR Run!"])
        self.assertEqual([(s.bas, s.bit) for s in cikti][:2], [(1000, cikti[0].bit), (9000, cikti[1].bit)])
        self.assertGreater(cikti[0].bit, 3100)                                    # ikinci altyazının süresi eklendi

    def test_eksik_satir_yeniden_denenir_sonra_bolunur(self):
        ayar = ayar_birlestir({"kontrol": {"deneme_sayisi": 3}, "model": {"es_zamanli_istek": 1}})

        def kural(cagri, satirlar):
            return satirlar[:-1] if len(satirlar) > 1 else satirlar  # çoklu chunk'ta hep bir satır eksik

        model = SahteModel(kural)
        cikti = self._dosya_cevir(ayar, model)
        self.assertEqual(len(cikti), 5)                                              # bölünmüş cümle tek altyazı oldu
        self.assertEqual(sum("TR" in s.metin or "♪" in s.metin for s in cikti), 5)
        self.assertGreaterEqual(model.cagri, 2)
        # yanıt erken bitti (eksik satır son gelenden sonra): önce parçanın tamamı bir kez daha soruldu,
        # yine erken bitince son gelen satır da eksiklerle birlikte yeniden soruldu
        sorulan = [[x["id"] for x in g["lines"] if not x.get("skip")] for g in model.gidenler]
        self.assertEqual(sorulan[1], sorulan[0])
        self.assertEqual(len(sorulan[2]), 2)

    def test_etiketli_iki_konusmaci_cizgi_alir(self):
        """（アーニャ）ヒッ / （犬）ボフ: kaynakta çizgi yok ama satırları ayrı kişiler söylüyor. Model çizgiyi çoğu kez
        unutuyordu (olcum55: 13 altyazının 2-5'i) ve kimin ne dediğini karıştırıyordu (SPY x FAMILY 343): satırlar
        modele konuşma tiresiyle gider, tire denetimi çeviride iki tire arar. Tek konuşmacıya konmaz."""
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}})
        icerik = ("1\n00:00:01,000 --> 00:00:03,000\n（アーニャ）Hih!\n（犬）Bofu.\n\n"
                  "2\n00:00:04,000 --> 00:00:06,000\n（ロイド）Calm down,\nboth of you.\n\n")

        def kural(cagri, satirlar):           # ilk yanıtta model ikinci satırın tiresini unutuyor: yeniden sorulur
            return [dict(s, text=s["text"].replace("\n- ", "\n")) if cagri == 1 else s for s in satirlar]

        model = SahteModel(kural)
        cikti = self._dosya_cevir(ayar, model, icerik)
        self.assertEqual(model.gidenler[0]["lines"][0]["text"], "- Hih!\n- Bofu.")
        self.assertEqual(model.gidenler[0]["lines"][1]["text"], "Calm down,\nboth of you.")
        self.assertEqual([s.metin for s in cikti], ["- TR Hih!\n- TR Bofu.", "TR Calm down,\nboth of you."])
        self.assertEqual(model.cagri, 2)                                 # tiresi eksik yanıt yeniden soruldu
        from defta.cevirmen import konusma_tireleri
        self.assertEqual(konusma_tireleri("スパイ失格だ\nちち", ["ロイド", "アーニャ"]), "- スパイ失格だ\n- ちち")
        self.assertEqual(konusma_tireleri("<i>A</i>\nB", ["x", "y"]), "<i>- A</i>\n- B")
        self.assertEqual(konusma_tireleri("A\nB", ["x", "x"]), "A\nB")           # aynı kişi
        self.assertEqual(konusma_tireleri("A\nB", ["x", ""]), "A\nB")            # etiketsiz ikinci satır: aynı kişi
        self.assertEqual(konusma_tireleri("A\nB", ["", "x"]), "- A\n- B")        # ikinci satırda konuşan değişti

    def test_erken_biten_yanit_kaymis_olabilir_parca_yeniden_sorulur(self):
        """olcum55: model iki satırın içeriğini bir satıra yazıp gerisini birer kaydırdı, son satıra içerik kalmadı.
        Satırlar kontrollerden geçiyordu ama 19'u yanlış altyazıdaydı. Erken biten yanıt atılır, parça baştan sorulur."""
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}})
        cumleler = ["The river is high today.", "We cross at dawn.", "Bring the ropes.", "Nobody stays behind.",
                    "Is that clear?"]
        icerik = "".join(f"{n}\n00:00:{3 * n - 2:02},000 --> 00:00:{3 * n:02},000\n{c}\n\n"
                         for n, c in enumerate(cumleler, 1))

        def kural(cagri, satirlar):
            if cagri > 1:           # sonraki yanıtlar başka sözcüklerle: yeniden sorulan satır öncekinin kopyası olmaz
                return [dict(s, text=s["text"].replace("TR ", "TR2 ", 1)) for s in satirlar]
            kayan = [{"id": satirlar[0]["id"], "text": satirlar[0]["text"] + " " + satirlar[1]["text"]}]
            return kayan + [{"id": a["id"], "text": b["text"]} for a, b in zip(satirlar[1:], satirlar[2:])]

        model = SahteModel(kural)
        cikti = self._dosya_cevir(ayar, model, icerik)
        self.assertEqual([s.metin for s in cikti], ["TR2 " + c for c in cumleler])
        self.assertEqual(model.gidenler[1]["lines"], model.gidenler[0]["lines"])
        with open(os.path.join(self.gunluk.klasor, "uyarilar.txt"), encoding="utf-8") as f:
            self.assertIn("içerik kaymış olabilir", f.read())

    def test_ret_orijinal_birakir(self):
        ayar = ayar_birlestir({"kontrol": {"deneme_sayisi": 1}})

        class Reddeden:
            def iste(self, sistem, kullanici, dusunme=None):
                raise Ret("hayır")

        cikti = self._dosya_cevir(ayar, Reddeden())
        self.assertEqual(cikti[0].metin, "Where is")
        self.assertGreater(self.gunluk.hata_sayisi, 0)

    def test_hep_reddedilen_tek_satir_dosyayi_durdurmaz(self):
        """İçerik süzgecine takılan tek satır, parça ikiye bölünerek üst üste 10'dan çok kez reddedilir: model ve
        adres çalıştığı için dosya durmaz; o satır orijinal kalır, ötekiler çevrilir."""
        from defta.cevirmen import ARDISIK_BOZUK
        from defta.model import Ret
        ic = SahteModel()

        class TekSatirRet:
            ret = 0

            def iste(self, sistem, kullanici, dusunme=None):
                if any("Line number 1." == s.get("text") for s in json.loads(kullanici).get("lines", [])):
                    TekSatirRet.ret += 1
                    raise Ret("içerik filtresi: hayır")
                return ic.iste(sistem, kullanici, dusunme)
        icerik = "".join(f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},800\nLine number {n}.\n\n" for n in range(1, 41))
        cikti = self._dosya_cevir(ayar_birlestir({"model": {"es_zamanli_istek": 1}}), TekSatirRet(), icerik)
        self.assertGreater(TekSatirRet.ret, ARDISIK_BOZUK)
        self.assertEqual(cikti[0].metin, "Line number 1.")
        self.assertEqual(len(cikti), 40)
        self.assertTrue(all(s.metin != f"Line number {n}." for n, s in enumerate(cikti[1:], 2)))

    def test_hep_bozuk_yanit_calismayi_durdurur(self):
        """Model hiç JSON vermiyorsa (yanlış adres, json_modu kapalı) parçalar ikiye bölünerek yüzlerce istek gitmez:
        üst üste 10 kullanılamayan yanıttan sonra çalışma nedeniyle durur, dosya çevrilmiş sayılıp yazılmaz."""
        from defta.cevirmen import ARDISIK_BOZUK
        from defta.model import ModelHatasi

        class Duzyazi:
            cagri = 0

            def iste(self, sistem, kullanici, dusunme=None):
                Duzyazi.cagri += 1
                return "Sure! Here is the translation of your subtitles."
        icerik = "".join(f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},800\nLine number {n}.\n\n" for n in range(1, 41))
        with self.assertRaises(ModelHatasi):
            self._dosya_cevir(ayar_birlestir({"model": {"es_zamanli_istek": 1}}), Duzyazi(), icerik)
        self.assertLessEqual(Duzyazi.cagri, ARDISIK_BOZUK + 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "cikti", "a.tr.srt")))

    def test_ek_dusurulurse_yeniden_denenir(self):
        ayar = ayar_birlestir()

        def kural(cagri, satirlar):
            if cagri == 1:
                for s in satirlar:
                    s["text"] = s["text"].replace("Yor-san", "Yor")
            return satirlar

        model = SahteModel(kural)
        cikti = self._dosya_cevir(ayar, model)
        self.assertIn("Yor-san", cikti[2].metin)
        self.assertEqual(model.cagri, 2)

    def test_kaldigi_yerden_devam(self):
        ayar = ayar_birlestir({"parca": {"altyazi_sayisi": 2}, "model": {"es_zamanli_istek": 1}})
        satirlar = srt.srt_ayristir(ORNEK)
        for s in satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        onbellek = os.path.join(self.tmp.name, "o.json")

        def kural(cagri, satirlar_):
            if cagri == 2:
                raise KeyboardInterrupt
            return satirlar_

        with self.assertRaises(KeyboardInterrupt):
            Cevirmen(ayar, SahteModel(kural), "S", self.gunluk).cevir(satirlar, "a.srt", onbellek)
        with open(onbellek, encoding="utf-8") as f:
            kayitli = len(json.load(f))
        self.assertGreater(kayitli, 0)
        model = SahteModel()
        sonuc = Cevirmen(ayar, model, "S", self.gunluk).cevir(satirlar, "a.srt", onbellek)
        self.assertEqual(len(sonuc), 7)  # şarkı satırı gönderilmeden, olduğu gibi eklenir
        gonderilen = sum(1 for _ in range(model.cagri))
        self.assertLess(gonderilen, 4)

    def test_devam_ayar_degisince_bastan_baslar_ve_yarim_dosya_kalmaz(self):
        def kes(cagri, satirlar_):
            if cagri == 2:
                raise KeyboardInterrupt
            return satirlar_
        temel = {"parca": {"altyazi_sayisi": 2}, "model": {"es_zamanli_istek": 1}}
        ayar = ayar_birlestir(temel)
        hedef = os.path.join(self.tmp.name, "cikti", "a.tr.srt")
        with self.assertRaises(KeyboardInterrupt):
            self._dosya_cevir(ayar, SahteModel(kes))
        self.assertFalse(os.path.exists(hedef))                       # yarım .srt yazılmaz
        kayitlar = os.listdir(os.path.join(self.tmp.name, ".onbellek"))
        self.assertEqual(len(kayitlar), 1)
        with open(os.path.join(self.tmp.name, ".onbellek", kayitlar[0]), encoding="utf-8") as f:
            kayit = json.load(f)
        self.assertEqual(sorted(kayit), ["sonuc"])
        # aynı ayarla: yalnızca eksik satırlar gider; eşzamanlılık gibi çeviriyi etkilemeyen ayar kaydı bozmaz
        ayni = ayar_birlestir({**temel, "model": {"es_zamanli_istek": 4}})
        devam = SahteModel()
        self._dosya_cevir(ayni, devam)
        gonderilen = [x["id"] for g in devam.gidenler for x in g["lines"] if not x.get("skip")]
        self.assertIn("0", kayit["sonuc"])
        self.assertTrue(set(gonderilen).isdisjoint(int(k) + 1 for k in kayit["sonuc"]))
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, ".onbellek")), [])      # bitince kayıt silinir
        # çeviriyi etkileyen bir ayar değişirse eski kayıt kullanılmaz: bütün satırlar yeniden gider
        os.remove(hedef)
        with self.assertRaises(KeyboardInterrupt):
            self._dosya_cevir(ayar, SahteModel(kes))
        degisen = ayar_birlestir({**temel, "modlar": {"koseli_not": "cevir"}})
        bastan = SahteModel()
        self._dosya_cevir(degisen, bastan)
        gonderilen = {x["id"] for g in bastan.gidenler for x in g["lines"] if not x.get("skip")}
        self.assertIn(1, gonderilen)

    def test_hitap_parcalari_ayri_kapanir(self):
        satirlar = srt.srt_ayristir(ORNEK)
        for s in satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        for h, bekle_alan in (({"acik": True}, True), ({"acik": True, "tablo": False}, False), ({"acik": False}, False)):
            ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "hitap": h})
            c = Cevirmen(ayar, SahteModel(), "S", self.gunluk)
            c.cevir(satirlar, "a.srt")
            istem = istem_olustur(ayar)
            self.assertEqual('"says_to"' in istem, bekle_alan, h)
            self.assertNotIn('"by"', istem, h)                                # model kimin kime konuştuğunu yazmaz
            self.assertEqual("Formal vs informal address" in istem, h["acik"], h)

    def test_sarki_blogu_tanima(self):
        """Yalnız ilk dizesinin başında ve son dizesinin sonunda ♪ olan şarkının iç dizeleri de şarkıdır."""
        from defta.kaynak import sarki_bloklari

        def blok(*metinler, ara=3000):
            return sarki_bloklari([srt.Satir(n + 1, n * ara, n * ara + 2500, m) for n, m in enumerate(metinler)])
        self.assertEqual(blok("Hi.", "♪～ <i>One</i>", "<i>Two</i>", "<i>Three</i>", "<i>Four</i> ～♪", "Bye."), {2, 3})
        self.assertEqual(blok("♪ Open line", "Middle line", "Close line ♪"), {1})
        self.assertEqual(blok("♪ A open", "a mid", "A close ♪", "Talk.", "♪ B open", "b mid", "B close ♪"), {1, 5})
        # Blok sayılmayanlar: yalnız işaretten oluşan açılış (müzik başladı; arada konuşma var), kapanışsız açılış,
        # italik şarkının arasına giren düz satır, iki konuşmacılı altyazı, 3 dakikayı ya da 40 altyazıyı aşan arada
        self.assertEqual(blok("♪〜", "Talk here.", "〜♪"), set())
        self.assertEqual(blok("♪ Open", "Mid", "Mid 2"), set())
        self.assertEqual(blok("♪～ <i>One</i>", "Not a lyric.", "<i>Last</i> ～♪"), set())
        self.assertEqual(blok("♪ Open", "- Hey!\n- What?", "Close ♪"), set())
        self.assertEqual(blok("♪ Open", "Mid", "Close ♪", ara=100000), set())
        self.assertEqual(blok(*(["♪ Open"] + ["Mid"] * 45 + ["Close ♪"])), set())

    def test_sarki_blogu_sarki_ayarina_uyar(self):
        """Şarkının tamamı aynı ayara uyar; ♪'yi script koyduğu iç dizelerde çıktıda işaret yoktur, model ♪'yi
        düşürürse kaynaktaki yerine konur."""
        kaynak = "".join(f"{n}\n00:00:{2 * n:02d},000 --> 00:00:{2 * n + 1:02d},500\n{m}\n\n" for n, m in enumerate(
            ["Before the song.", "♪～ <i>First lyric</i>", "<i>Second lyric</i>", "<i>Third lyric</i>",
             "<i>Last lyric</i> ～♪", "After the song."], 1))
        duz = lambda c: {s.no: s.metin for s in c}

        model = SahteModel()                                           # orijinal: şarkı modele hiç gitmez
        cikti = duz(self._dosya_cevir(ayar_birlestir({"model": {"es_zamanli_istek": 1}}), model, kaynak))
        self.assertEqual([x["id"] for g in model.gidenler for x in g["lines"] if not x.get("skip")], [1, 6])
        self.assertEqual([cikti[n] for n in (2, 3, 4, 5)],
                         ["♪~ <i>First lyric</i>", "<i>Second lyric</i>", "<i>Third lyric</i>", "<i>Last lyric</i> ~♪"])

        # çeviri: iç dizeler modele şarkı olarak (♪ ile) ve yarım cümle sayılmadan gider; model bütün ♪'leri düşürür
        model = SahteModel(lambda _, s: [dict(x, text=x["text"].replace("♪", "")) for x in s])
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "modlar": {"sarki": "ceviri"}})
        cikti = duz(self._dosya_cevir(ayar, model, kaynak))
        giden = {x["id"]: x for g in model.gidenler for x in g["lines"] if not x.get("skip")}
        self.assertTrue(all("♪" in giden[n]["text"] and not giden[n].get("continues") for n in (2, 3, 4, 5)))
        self.assertTrue(cikti[2].startswith("♪") and cikti[5].endswith("♪"))            # düşen işaret yerinde
        self.assertEqual((cikti[3], cikti[4]), ("TR <i>Second lyric</i>", "TR <i>Third lyric</i>"))

        # orijinal + çeviri: özgün sözü script kaynaktan yazar, model yalnız çevirir (çevirisine söz karışsa da atılır)
        model = SahteModel(lambda _, s: [dict(x, text="♪ çeviri" if x["id"] in (2, 3, 4, 5) else x["text"]) for x in s])
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "modlar": {"sarki": "orijinal_ceviri"}})
        cikti = duz(self._dosya_cevir(ayar, model, kaynak))
        self.assertEqual([cikti[n] for n in (2, 3, 5)],
                         ["♪~ <i>First lyric</i>\n(çeviri)", "<i>Second lyric</i>\n(çeviri)", "<i>Last lyric</i> ~♪\n(çeviri)"])
        self.assertIn("return only the translation", istem_olustur(ayar))

    def test_ozgun_soz_ceviri_kana_ve_kanji(self):
        """Özgün söz + çeviri, Japonca: tamamı kana sözün okunuşunu script yazar; kanjili sözün okunuşunu model
        "reading" alanında verir, vermezse satır yeniden sorulur."""
        kaynak = ("1\n00:00:01,000 --> 00:00:03,000\n♪ ワンワン ワンワーン ♪\n\n"
                  "2\n00:00:04,000 --> 00:00:06,000\n♪ 風の中を 走っていく ♪\n\n")

        def kural(cagri, satirlar):
            yanit = [{"id": 1, "text": "Hav hav"}, {"id": 2, "text": "Rüzgârda koşuyorum"}]
            if cagri > 1:                                    # ilk yanıtta okunuş yok: yeniden sorulur
                yanit[1]["reading"] = "Kaze no naka o hashitte iku"
            return [y for y in yanit if y["id"] in {x["id"] for x in satirlar}]
        model = SahteModel(kural)
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "modlar": {"sarki": "orijinal_ceviri"}})
        cikti = {s.no: s.metin for s in self._dosya_cevir(ayar, model, kaynak)}
        self.assertEqual(cikti, {1: "♪ Wanwan wanwaan ♪\n(Hav hav)", 2: "♪ Kaze no naka o hashitte iku ♪\n(Rüzgârda koşuyorum)"})
        giden = [x for g in model.gidenler for x in g["lines"] if not x.get("skip")]
        self.assertEqual([(x["id"], x.get("reading")) for x in giden], [(1, None), (2, True), (2, True)])
        self.assertIn("reading", model.gidenler[1]["fix"][0]["problem"])
        self.assertIn('"reading": true', istem_olustur(ayar))
        self.assertNotIn('"reading": true', istem_olustur(ayar_birlestir({"modlar": {"sarki": "ceviri"}})))

    def test_istemlerde_ayni_cumle_iki_kez_gecmez(self):
        """Düzenleme betiği bir cümleyi birden çok kez eklerse yakalanır (v15'te ilişki ×3, sınıflandırma ×4)."""
        import re
        from defta import analiz, terim
        for ayar in (ayar_birlestir(), ayar_birlestir({"modlar": {"anime_ekleri": "ceviri"}}),
                     ayar_birlestir({"ceviri": {"hedef_dil": "en"}})):
            sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
            istemler = {"ceviri": istem_olustur(ayar, {"Mount Doom": "Kıyamet Dağı"}, True),
                        "iliski": analiz.iliski_istemi(ayar, sozluk), "sahne": analiz.sahne_istemi(ayar),
                        "siniflandirma": terim.siniflandirma_istemi(ayar), "kesif": terim.kesif_istemi(ayar),
                        "karsilik": terim.karsilik_istemi(ayar), "yazim": terim.yazim_istemi(ayar),
                        "ses": terim.SES_ISTEMI}
            for ad, metin in istemler.items():
                cumleler = [" ".join(c.split()) for c in re.split(r"(?<=[.!?])\s+|\n", metin)]
                tekrar = {c for c in cumleler if len(c) >= 30 and cumleler.count(c) > 1}
                self.assertFalse(tekrar, f"{ad}: {tekrar}")

    def test_istem_modlara_gore_degisir(self):
        a = istem_olustur(ayar_birlestir(), {"Mount Doom": "Kıyamet Dağı", "Mellon": "Mellon"})
        self.assertIn("NEVER translated", a)
        self.assertIn("Mount Doom -> Kıyamet Dağı", a)
        self.assertIn("Mellon (keep exactly as is)", a)
        self.assertIn("⟦Alohomora⟧'yı söyle", a)
        b = istem_olustur(ayar_birlestir({"modlar": {"kurgu_dili": "ceviri", "sarki": "ceviri"},
                                          "ceviri": {"cikti_alfabesi": "serbest"}}))
        self.assertNotIn("NEVER translated", b)
        self.assertNotIn("# Alphabet", b)


class FormulTestleri(unittest.TestCase):
    """Süre uzatma."""

    SRT = ("1\n00:00:01,000 --> 00:00:03,000\nIt is in my pocket.\n\n"
           "2\n00:00:03,100 --> 00:00:03,900\nThis is a very long sentence for such a short time.\n\n"
           "3\n00:00:06,000 --> 00:00:08,000\nFine.\n")

    def _calis(self, ustune):
        class M:
            def __init__(self):
                self.sayac = {}

            def say(self, ad, n=1):
                self.sayac[ad] = self.sayac.get(ad, 0) + n

            def iste(self, sistem, kullanici, dusunme=None):
                ceviri = {1: "Cebinde.", 2: "Bu kadar kısa süre için çok uzun bir cümle bu.", 3: "Peki."}
                return json.dumps({"lines": [{"id": o["id"], "text": ceviri[o["id"]]}
                                             for o in json.loads(kullanici)["lines"] if not o.get("skip")]})
        ayar = ayar_birlestir({"sozluk": {"otomatik": False}, "cikti": ustune})
        with tempfile.TemporaryDirectory() as d:
            kaynak, hedef = os.path.join(d, "a.srt"), os.path.join(d, "a.tr.srt")
            with open(kaynak, "w", encoding="utf-8") as f:
                f.write(self.SRT)
            m = M()
            dosya_cevir(kaynak, hedef, "a.srt", ayar, m, istem_olustur(ayar), Gunluk(os.path.join(d, "logs")),
                        os.path.join(d, "onb"))
            with open(hedef, encoding="utf-8") as f:
                return m, f.read()

    def test_kurgu_anlami_istemde_ayarla_ayni_seyi_soyler(self):
        sil = istem_olustur(ayar_birlestir({"modlar": {"kurgu_anlami": "sil"}}))
        cevir = istem_olustur(ayar_birlestir({}))
        self.assertIn("leave that meaning out", sil)
        self.assertNotIn("leave that meaning out", cevir)
        self.assertIn("translate the meaning", cevir)
        ceviri = istem_olustur(ayar_birlestir({"modlar": {"kurgu_dili": "ceviri", "kurgu_anlami": "cevir"}}))
        self.assertNotIn("still in parentheses", ceviri)

    def test_sure_uzat(self):
        _, once = self._calis({"sure_uzat_azami": 0})
        self.assertIn("00:00:03,100 --> 00:00:03,900", once)
        m, sonra = self._calis({"sure_uzat_azami": 1.5})
        # 46 harf / 25 = 1,84 sn gerekir; sonraki altyazı 6,000'de: 3,100 + 1,841 = 4,941 (azami +1,5 sn = 5,400)
        self.assertIn("00:00:03,100 --> 00:00:04,9", sonra)
        self.assertIn("00:00:06,000 --> 00:00:08,000", sonra)      # sonraki altyazı değişmez
        self.assertEqual(m.sayac["süresi uzatılan altyazı"], 1)


class JaponcaKontrolTestleri(unittest.TestCase):
    """Sözlükte olmayan ad + ek, tek başına 先生, çevrilmiş şarkı satırı."""

    def _cevir(self, kaynak_metin, yanitlar, modlar=None, ceviri=None):
        class M:
            def __init__(self):
                self.n = 0

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                metin = yanitlar[min(self.n, len(yanitlar) - 1)]
                self.n += 1
                return json.dumps({"lines": [{"id": o["id"], "text": metin}
                                             for o in veri["lines"] if not o.get("skip")]})
        ayar = ayar_birlestir({"sozluk": {"otomatik": False}, "modlar": modlar or {}, "ceviri": ceviri or {}})
        with tempfile.TemporaryDirectory() as d:
            kaynak, hedef = os.path.join(d, "a.srt"), os.path.join(d, "a.tr.srt")
            with open(kaynak, "w", encoding="utf-8") as f:
                f.write("1\n00:00:01,000 --> 00:00:04,000\n" + kaynak_metin + "\n")
            m = M()
            dosya_cevir(kaynak, hedef, "a.srt", ayar, m, istem_olustur(ayar), Gunluk(os.path.join(d, "logs")),
                        os.path.join(d, "onb"))
            with open(hedef, encoding="utf-8") as f:
                return m.n, f.read()

    def test_sozlukte_olmayan_adin_eki_aranir(self):
        n, cikti = self._cevir("すみません　タクミ君に つかまって",
                               ["Özür dilerim. Takumi'ye yakalandım.", "Özür dilerim. Takumi-kun'a yakalandım."])
        self.assertIn("Takumi-kun", cikti)

    def test_siradan_sozcuge_ek_aranmaz(self):
        n, cikti = self._cevir("シッターさんが来た", ["Bakıcı geldi."])
        self.assertEqual(n, 1)
        self.assertIn("Bakıcı geldi.", cikti)

    def test_tek_basina_sensei_kalir(self):
        n, cikti = self._cevir("私は 先生と港へ行く", ["Ben hocamla limana gidiyorum.", "Ben Sensei'yle limana gidiyorum."])
        self.assertEqual(n, 2)
        self.assertIn("Sensei", cikti)

    def test_latin_harfli_ek_baska_yazidaki_ceviride_aranmaz(self):
        """Çıktı alfabesi serbest, hedef Latin harfli değil (Rusça, Çince): ek o yazıyla yazılır; Latin harfli "-san" /
        "Sensei" aranıp satır yeniden sorulmaz. Serbest alfabede Latin harfli çeviride ek yine aranır."""
        ru = {"hedef_dil": "ru", "cikti_alfabesi": "serbest"}
        n, cikti = self._cevir("ロイド先輩 ありがとう", ["Спасибо, Лойд-сэмпай."], ceviri=ru)
        self.assertEqual(n, 1)
        self.assertIn("Спасибо, Лойд-сэмпай.", cikti)
        n, cikti = self._cevir("先生、おはよう", ["老师，早上好。"], ceviri={"hedef_dil": "zh", "cikti_alfabesi": "serbest"})
        self.assertEqual(n, 1)
        n, cikti = self._cevir("私は 先生と港へ行く", ["Ben hocamla limana gidiyorum.", "Ben Sensei'yle limana gidiyorum."],
                               ceviri={"cikti_alfabesi": "serbest"})
        self.assertEqual(n, 2)

    def test_sarki_cevrilmisse_yeniden_istenir(self):
        n, cikti = self._cevir("♪ 遠い海まで とんでいけ", ["♪ Uzak denizlere uç", "♪ Tooi umi made tonde ike"])
        self.assertEqual(n, 2)
        self.assertIn("tonde ike", cikti)

    def test_sarkida_dusen_nota_isareti_geri_konur(self):
        n, cikti = self._cevir("♪ 遠い海まで とんでいけ", ["Tooi umi made tonde ike"])
        self.assertEqual(n, 1)
        self.assertIn("♪ Tooi umi made tonde ike", cikti)


class KapatilanAyarTestleri(unittest.TestCase):
    """Kapatılan ayar istemde, sözlük adımında ve isteklerde hiçbir iz bırakmaz."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gunluk = Gunluk(os.path.join(self.tmp.name, "logs"))

    def test_hitap_dogal_her_yerden_kalkar(self):
        from defta import analiz
        ayar = ayar_birlestir({"hitap": {"acik": False}})
        istem = istem_olustur(ayar)
        for iz in ("sen/siz", "says_to", '"address"', '"form"', "informal"):
            self.assertNotIn(iz, istem, iz)
        self.assertNotIn("address", analiz.iliski_istemi(ayar, None))
        self.assertIn("sen/siz", istem_olustur(ayar_birlestir({"hitap": {"acik": True}})))          # açıkken yerinde

    def test_koseli_ve_parantez_ayri_ayri(self):
        a = not_ve_konusmaci_ayir
        self.assertEqual(a("(sighs)\n[door] I'm tired.", True, False, True), ("(sighs)\nI'm tired.", ["door"], []))
        self.assertEqual(a("(sighs)\n[door] I'm tired.", False, True, True), ("[door] I'm tired.", ["sighs"], []))
        self.assertEqual(a("(sighs)\n[door] I'm tired.", False, False, True)[1], [])
        m = ayar_birlestir({"modlar": {"koseli_not": "cevir"}})["modlar"]
        self.assertEqual(temizlik.son_temizlik("[müzik] Koş", m), "[müzik] Koş")
        self.assertEqual(temizlik.son_temizlik("[müzik] (iç ses) Koş", ayar_birlestir({})["modlar"]), "(iç ses) Koş")
        ikisi = istem_olustur(ayar_birlestir({"modlar": {"koseli_not": "cevir", "parantez_not": "cevir"}}))
        self.assertNotIn('"note"', ikisi)
        yalniz = istem_olustur(ayar_birlestir({"modlar": {"parantez_not": "cevir"}}))
        self.assertIn("keep the round parentheses", yalniz)
        self.assertIn('"note"', yalniz)
        self.assertIn("ALWAYS write them inside square brackets", istem_olustur(ayar_birlestir({})))

    def test_duyuru_basligi_konusan_adi_sayilmaz(self):
        """NOT:, UYARI:, WARNING: gibi başlıklar ekran yazısının parçasıdır: konuşan adı diye ayrılmaz, çıktıdan da
        silinmez. Gerçek konuşan adı eskisi gibi ayrılır."""
        from defta.kaynak import not_ve_konusmaci_ayir
        for metin in ("NOT: Kapı kilitli.", "UYARI: Yüksek gerilim.", "WARNING: Keep out.", "- NOTE: Back at five."):
            self.assertEqual(not_ve_konusmaci_ayir(metin), (metin, [], []), metin)
            self.assertEqual(temizlik.son_temizlik(metin, ayar_birlestir({})["modlar"]), metin)
        self.assertEqual(not_ve_konusmaci_ayir("JOHN: Come here."), ("Come here.", [], ["JOHN"]))
        self.assertEqual(temizlik.son_temizlik("JOHN: Gel buraya.", ayar_birlestir({})["modlar"]), "Gel buraya.")

    def test_reklam_altyazisi_modele_gitmez(self):
        r = temizlik.reklam_mi
        for metin in ("Awww.That's so cute.", "Ewww.Gross!"):      # sözcüğün sonu "www." değildir
            self.assertFalse(r(metin), metin)
        for metin in ("Subtitles by SomeGroup", "www.example.com", "Downloaded from\nexample.org", "Çeviri: Ahmet",
                      "<i>Synced and corrected by X</i>", "Support us and become VIP member"):
            self.assertTrue(r(metin), metin)
        for metin in ("He was translated by the gods.", "I got it from Tom.", "Stand by.", "Çeviri yapıyorum.",
                      "Meet me at the dot. Come on.", "Sync the watches.", "Who.Me?", "Yes.Me too."):
            self.assertFalse(r(metin), metin)
        # konuşmada da geçebilen kalıp, dosyanın ortasında künye sayılmaz
        for metin in ("Translated from Elvish, it means friend.", "It's called TheFacebook.com.", "Encoded by a cipher."):
            self.assertTrue(r(metin, kenarda=True), metin)
            self.assertFalse(r(metin, kenarda=False), metin)
        self.assertTrue(r("- sync and corrections by X -\n- www.addic7ed.com -", kenarda=False))
        import tempfile
        from defta.dosya import dosya_oku
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, "a.srt")
            with open(yol, "w", encoding="utf-8") as f:
                f.write("1\n00:00:01,000 --> 00:00:02,000\nSubtitles by X\n\n2\n00:00:03,000 --> 00:00:04,000\nHello.\n")
            self.assertEqual([s.metin for s in dosya_oku(yol, ayar_birlestir({}))[1]], ["Hello."])
            self.assertEqual(len(dosya_oku(yol, ayar_birlestir({"modlar": {"reklam": "kalsin"}}))[1]), 2)

    def test_sahne_ozeti_kapaliyken_iz_birakmaz(self):
        from defta import analiz
        kapali = ayar_birlestir({"sozluk": {"sahne_ozeti": False}})
        self.assertNotIn('"scene"', istem_olustur(kapali))
        self.assertNotIn("scenes", analiz.sahne_istemi(kapali))
        self.assertIn("# fictional", analiz.sahne_istemi(kapali))
        self.assertIn('"scene"', istem_olustur(ayar_birlestir({})))
        self.assertIn("# scenes", analiz.sahne_istemi(ayar_birlestir({})))

    def test_kapali_ozellikler_istemden_kalkar(self):
        yalin = ayar_birlestir({
            "sozluk": {"otomatik": False, "terimler": False, "karakterler": False, "sahne_ozeti": False,
                       "kurgu_tespiti": False},
            "ceviri": {"bolunmus_cumle": "kapali"},
            "kontrol": {"supheli_satir_kontrolu": False, "tekrar_eden_sahne": False}})
        istem = istem_olustur(yalin)
        for iz in ("# Glossary", '"scene"', '"turns"', '"parts"', '"continues"'):
            self.assertNotIn(iz, istem, iz)
        for iz in ("⟦ ⟧", '"context_before"', '"lines"', '"fix"', "Return exactly the ids"):
            self.assertIn(iz, istem, iz)
        # Bölünmüş cümle kapalı: birleştirme yok, "continues" işareti yok.
        satirlar = [srt.Satir(1, 0, 1000, "When we get to the"), srt.Satir(2, 1000, 2000, "castle, we rest.")]
        model = SahteModel()
        Cevirmen(yalin, model, "S", self.gunluk).cevir(satirlar, "x.srt")
        giden = model.gidenler[0]["lines"]
        self.assertEqual(len(giden), 2)
        self.assertFalse(any("parts" in x or "continues" in x for x in giden))

    def test_sozluk_gorunumu_kapatilani_bosaltir(self):
        s = Sozluk(os.path.join(self.tmp.name, "g.json"))
        s.elle_ekle({"Balrog": "Alevcin"}, ["mithril"])
        s.karakterler = {"Frodo": "hobbit"}
        s.sahneler, s.kurgu, s.hitap = {"a": [[1, 2, "x"]]}, {"a": {"1": ["Mellon"]}}, {"A -> B": "resmi"}
        g = s.gorunum(ayar_birlestir({"sozluk": {"terimler": False, "karakterler": False, "sahne_ozeti": False,
                                                 "kurgu_tespiti": False}, "hitap": {"acik": False}}))
        self.assertEqual((g.terimler, g.korunacak, g.karakterler, g.sahneler, g.kurgu, g.hitap),
                         ({}, [], {}, {}, {}, {}))
        self.assertEqual(s.terimler, {"Balrog": "Alevcin"})                     # asıl sözlük değişmez
        acik = s.gorunum(ayar_birlestir({"hitap": {"acik": True}}))
        self.assertEqual((acik.terimler, acik.hitap), ({"Balrog": "Alevcin"}, {"A -> B": "resmi"}))
        self.assertNotIn("Alevcin", istem_olustur(ayar_birlestir({}), g))

    @unittest.skipIf(tomllib is None, "config.toml okumak Python 3.11 ister")

    def test_varsayilanlar_config_ile_ayni(self):
        """config.toml varsayılanların kendisidir: aynı anahtarlar, aynı değerler, aynı sıra (aynı bilgi iki yerde
        farklı yazılmasın; silinen satır dosyada yazılı olan değeri alsın)."""
        from defta.ayar import VARSAYILAN
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml"), "rb") as f:
            dosya = tomllib.load(f)
        self.assertEqual(list(dosya), list(VARSAYILAN))
        for bolum, degerler in VARSAYILAN.items():
            self.assertEqual(list(dosya[bolum].items()), list(degerler.items()), bolum)

    def test_anahtar_ve_ciktilar_depoya_girmez(self):
        # API anahtarı yalnız .env'de durur: git'e eklenip paylaşılmasın. Çıktı klasörleri de depoya girmez.
        yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gitignore")
        if not os.path.exists(yol):
            self.skipTest("depo dışında çalışıyor")
        with open(yol, encoding="utf-8") as f:
            satirlar = {s.strip() for s in f}
        for ad in (".env", "logs/", "ceviri/", ".onbellek/"):
            self.assertIn(ad, satirlar)

    def test_eski_kodlamali_anahtar_ve_ayar_dosyasi(self):
        # Not Defteri'nde ANSI (cp1254) kaydedilmiş dosya: Türkçe açıklama satırı UnicodeDecodeError ile açılışı
        # düşürmesin. Anahtar satırı yine okunur; ayar dosyasında neden anlaşılır biçimde bildirilir.
        from defta import ayar as ayar_modulu
        yol = os.path.join(self.tmp.name, "anahtar.txt")
        with open(yol, "wb") as f:
            f.write("# Anahtarını yaz, kimseyle paylaşma: şifre\nDEFTA_DENEME_ANAHTARI = 'sk-test'\n".encode("cp1254"))
        with mock.patch.dict(os.environ):
            ayar_modulu.env_yukle(yol)
            self.assertEqual(os.environ["DEFTA_DENEME_ANAHTARI"], "sk-test")
        if tomllib is not None:
            yol = os.path.join(self.tmp.name, "a.toml")
            with open(yol, "wb") as f:
                f.write("# çıktı ayarları\n[cikti]\nsatir_karakter = 40\n".encode("cp1254"))
            with self.assertRaises(ayar_modulu.AyarHatasi):
                ayar_modulu.ayar_yukle(yol)

    def test_hitap_kapaliyken_istemde_yok(self):
        self.assertTrue(ayar_birlestir({})["hitap"]["acik"])                      # varsayılan: tablo açık
        self.assertIn("sen/siz", istem_olustur(ayar_birlestir({})))
        self.assertNotIn("sen/siz", istem_olustur(ayar_birlestir({"hitap": {"acik": False}})))

    def test_hitap_dogal_tabloyu_kullanmaz(self):
        ayar = ayar_birlestir({"hitap": {"acik": False}, "sozluk": {"otomatik": False}})
        with tempfile.TemporaryDirectory() as d:
            sozluk = Sozluk(os.path.join(d, "s.json"))
            sozluk.hitap = {"Mara -> Tolen": "resmi"}
            sozluk.karakterler = {"Mara": "", "Tolen": ""}
            c = Cevirmen(ayar, None, "", None, sozluk)
            self.assertEqual(c._tablo(), {})
            self.assertFalse(c.hitap_tablo)
            self.assertEqual(Cevirmen(ayar_birlestir({"hitap": {"acik": True}}), None, "", None, sozluk)._tablo(), sozluk.hitap)

    def test_ekler_ceviri_her_yerden_kalkar(self):
        ayar = ayar_birlestir({"modlar": {"anime_ekleri": "ceviri"}})
        for iz in ("-san", "-kun", "Senpai", "Sensei", "romanized original"):
            self.assertNotIn(iz, istem_olustur(ayar), iz)
        self.assertFalse(Cevirmen(ayar, None, "", None).ek_kontrol)

    def test_sarki_ceviri_romanize_kuralini_kaldirir(self):
        self.assertNotIn('"romanize"', istem_olustur(ayar_birlestir({"modlar": {"sarki": "ceviri"}})))
        self.assertIn('"romanize"', istem_olustur(ayar_birlestir({})))

    def _model(self, sureler):
        """_iste yerine, sırayla verilen süreler kadar bekleyip yanıt dönen sahte gönderici."""
        import threading
        import time
        from defta.model import Model
        m = Model(ayar_birlestir({}), "anahtar")
        sira, kilit, m.cagri = iter(sureler), threading.Lock(), 0

        def sahte(sistem, kullanici, dusunme=None):
            with kilit:
                sure = next(sira)
                m.cagri += 1
            time.sleep(sure)
            return f"yanit {sure}"
        m._iste = sahte
        return m

    def test_geciken_istegin_kopyasi_gonderilir(self):
        m = self._model([0.01] * 4 + [30, 0.01])
        m._gecikti_mi_asil, olcek = m._gecikti_mi, 1000    # testte saniyeler milisaniye gibi sayılır
        for _ in range(4):
            m.iste("s", "k")
        m._tur_sureleri[(hash("s"), None)][1][:] = [5, 5, 5, 5]      # olağan süre 5 sn: sınır 10 sn
        m._gecikti_mi = lambda tur, gecen: m._gecikti_mi_asil(tur, gecen * olcek)
        self.assertEqual(m.iste("s", "k"), "yanit 0.01")           # takılan (30 sn) değil, kopya döndü
        self.assertEqual((m.cagri, m.yeniden_gonderilen), (6, 1))

    def test_yaniti_kopyadan_alinan_istek_yeniden_gonderilmez(self):
        """Geciken isteğin kopyası yanıtladıktan sonra ilk istek zaman aşımına uğrarsa yeniden gönderilmez: yanıtını
        kimse okumayacaktır, boşuna ödenirdi. (İkisi de yanıtsızken her biri yeniden dener: ModelBeklemeTestleri.)"""
        import socket
        from defta import model as model_modulu
        birak, ucuncu, cagri = threading.Event(), threading.Event(), []
        tamam = json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {}}).encode()

        class Yanit:
            headers = {}

            def read(self):
                return tamam

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def ac(istek, zaman_asimi):
            cagri.append(1)
            if len(cagri) == 1:                 # ilk istek takılır, kopya yanıtladıktan sonra zaman aşımına uğrar
                birak.wait(10)
                raise socket.timeout("timed out")
            if len(cagri) == 3:
                ucuncu.set()
            return Yanit()
        m = model_modulu.Model(ayar_birlestir({}), "anahtar")
        m._gecikti_mi = lambda tur, gecen: True
        with mock.patch.object(model_modulu, "_ac", ac), \
                mock.patch.object(model_modulu, "_bekle", lambda olay, s: None):
            self.assertEqual(m.iste("s", "k"), "{}")
            self.assertEqual((len(cagri), m.yeniden_gonderilen), (2, 1))
            birak.set()
            self.assertFalse(ucuncu.wait(0.5))
        self.assertEqual(len(cagri), 2)

    def test_ornegi_az_turde_butun_isteklerin_suresine_bakilir(self):
        """Kaldığı yerden devam eden dosyanın son istekleri: kendi türünden yeterli örnek yok, bütün isteklerin süresine
        bakılır; takılan isteğin kopyası gider (gerçek test X, Arrival 190 sn bekledi)."""
        m = self._model([30, 0.01])
        m.sureler[:] = [5.0] * 10                                    # olağan süre 5 sn: sınır 45 sn
        asil, olcek = m._gecikti_mi, 1000
        m._gecikti_mi = lambda tur, gecen: asil(tur, gecen * olcek)
        self.assertEqual(m.iste("yeni", "k"), "yanit 0.01")
        self.assertEqual(m.yeniden_gonderilen, 1)
        self.assertFalse(asil((hash("yeni"), None), 30))             # 45 sn'den önce kopya yok

    def test_az_ornekle_kopya_gonderilmez(self):
        m = self._model([0.01] * 3)
        for _ in range(3):
            m.iste("s", "k")
        self.assertFalse(m._gecikti_mi((hash("s"), None), 999))
        self.assertEqual(m.yeniden_gonderilen, 0)

    def test_durdurma_ve_olumcul_hata(self):
        """Durdurulunca yeni istek gitmez, bekleyen bırakılır; düzelmeyecek hata öteki istekleri de durdurur."""
        import threading
        import time
        from defta.model import Iptal, ModelHatasi, Ret
        m = self._model([5])
        threading.Timer(0.3, m.iptal.set).start()
        basla = time.time()
        with self.assertRaises(Iptal):
            m.iste("s", "k")                                  # 5 sn süren isteğin yanıtı beklenmez
        self.assertLess(time.time() - basla, 3)
        with self.assertRaises(Iptal):
            m.iste("s", "k")                                  # durdurulduktan sonra istek gönderilmez
        m2 = self._model([])
        m2._iste = lambda *a, **k: (_ for _ in ()).throw(Ret("boş yanıt"))
        with self.assertRaises(Ret):
            m2.iste("s", "k")
        self.assertFalse(m2.iptal.is_set())                   # ret yeniden denenir: çalışma durmaz
        m2._iste = lambda *a, **k: (_ for _ in ()).throw(ModelHatasi("kota bitti"))
        with self.assertRaises(ModelHatasi):
            m2.iste("s", "k")
        self.assertTrue(m2.iptal.is_set())
        self.assertIsInstance(m2.hata, ModelHatasi)                   # özette nedeni yazılacak hata saklanır

    def test_bakiye_bitince_yoldaki_yanit_beklenir(self):
        """Düzelmeyecek hata (biten bakiye) çalışmayı durdururken yolda olan isteğin yanıtı beklenir: ödenmiş yanıt
        kaybolmaz, bakiye yüklenince kaldığı yerden sürer. Yeni istek gönderilmez."""
        import threading
        from defta.model import Iptal, ModelHatasi
        m = self._model([1.2, 0.01])
        threading.Timer(0.3, m.durdur, [ModelHatasi("kota bitti")]).start()
        self.assertEqual(m.iste("s", "k"), "yanit 1.2")        # yolda olan istek tamamlanır
        with self.assertRaises(Iptal):
            m.iste("s", "k")
        self.assertEqual(m.cagri, 1)
        self.assertIsInstance(m.hata, ModelHatasi)

    def test_yavas_durdurmada_yoldaki_yanit_beklenir(self):
        """Kullanıcı durdurunca gönderilmiş isteğin yanıtı beklenir (ödenmiş yanıt kaybolmasın); yenisi gönderilmez."""
        import threading
        from defta.model import Iptal
        m = self._model([1.2, 0.01])
        threading.Timer(0.3, m.yavas_durdur).start()
        self.assertEqual(m.iste("s", "k"), "yanit 1.2")        # yolda olan istek tamamlanır
        with self.assertRaises(Iptal):
            m.iste("s", "k")                                  # durdurulduktan sonra istek gönderilmez
        self.assertEqual(m.cagri, 1)
        self.assertEqual(m.yeniden_gonderilen, 0)

    def test_yavas_durdurmada_kabul_edilen_satirlar_kayda_gecer(self):
        """Yoldaki yanıt durdurmadan sonra gelir ve bir satırı yeniden sormak gerekir: yeni istek gitmez, yanıtın
        kabul edilen satırları kayda geçer (yeniden çalıştırınca bir daha ödenmez), iş tamamlanmış sayılmaz."""
        import glob
        import json
        import threading
        import time
        from defta.dosya import dosya_cevir
        from defta.gunluk import Gunluk
        from defta.model import Iptal, Model
        d = tempfile.mkdtemp()
        kaynak = os.path.join(d, "a.srt")
        with open(kaynak, "w", encoding="utf-8") as f:
            f.write("\n".join(f"{i + 1}\n00:00:{2 * i + 1:02d},000 --> 00:00:{2 * i + 2:02d},000\nThis is sentence {i}.\n"
                              for i in range(6)))
        ayar = ayar_birlestir({})
        m = Model(ayar, "anahtar")
        cagri = []

        def sahte(sistem, kullanici, dusunme=None):
            cagri.append(1)
            satirlar = [{"id": s["id"], "text": f"Bu {s['id']} numaralı cümle."} for s in json.loads(kullanici)["lines"]]
            m.iptal.wait(5)
            time.sleep(0.2)
            satirlar[2]["text"] = ""                     # boş satır: yeniden sorulmalı
            return json.dumps({"lines": satirlar}, ensure_ascii=False)
        m._iste = sahte
        threading.Timer(0.2, m.yavas_durdur).start()
        with self.assertRaises(Iptal):
            dosya_cevir(kaynak, os.path.join(d, "c.srt"), "a.srt", ayar, m, "SISTEM", Gunluk(os.path.join(d, "k")),
                        os.path.join(d, "onbellek"))
        kayit = json.load(open(glob.glob(os.path.join(d, "onbellek", "*.json"))[0], encoding="utf-8"))
        self.assertEqual(len(kayit["sonuc"]), 5)
        self.assertEqual(len(cagri), 1)
        self.assertFalse(os.path.exists(os.path.join(d, "c.srt")))
        shutil.rmtree(d)

    def test_olumcul_hatada_kabul_edilen_satirlar_kayda_gecer(self):
        """Parçanın yanıtı alındı (ödendi), ardından gereken istek düzelmeyecek hatayla döndü (kredi bitti): kabul
        edilen satırlar kayda geçer, yeniden çalıştırınca yalnız kalan satır istenir. Hata bir satırı yeniden sorarken
        de, şüpheli satırları sorarken de (parçanın bütün satırları kabul edilmişken) gelebilir."""
        import glob
        from defta.model import Model, ModelHatasi
        for bos, kayitli in ((True, 5), (False, 6)):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            kaynak = os.path.join(d, "a.srt")
            with open(kaynak, "w", encoding="utf-8") as f:
                f.write("\n".join(f"{i + 1}\n00:00:{2 * i + 1:02d},000 --> 00:00:{2 * i + 2:02d},000\n"
                                  f"This is sentence {i}, Damn it.\n" for i in range(6)))
            ayar = ayar_birlestir({})
            cagri = []

            def sahte(sistem, kullanici, dusunme=None):
                cagri.append(json.loads(kullanici))
                if len(cagri) % 2 == 0:
                    raise ModelHatasi("Hesabın kredisi bitmiş")
                # klişe: bütün satırlar kabul edilirse şüpheli satır isteği gider
                satirlar = [{"id": s["id"], "text": f"Bu {s['id']} numaralı cümle, kahretsin."}
                            for s in cagri[-1]["lines"] if not s.get("skip")]
                if bos and len(cagri) == 1:
                    satirlar[2]["text"] = ""                 # boş satır: yeniden sorulmalı
                return json.dumps({"lines": satirlar}, ensure_ascii=False)
            for tur in (1, 2):
                m = Model(ayar, "anahtar")
                m._iste = sahte
                with self.assertRaises(ModelHatasi):
                    dosya_cevir(kaynak, os.path.join(d, "c.srt"), "a.srt", ayar, m, "SISTEM",
                                Gunluk(os.path.join(d, "k")), os.path.join(d, "onbellek"))
                with open(glob.glob(os.path.join(d, "onbellek", "*.json"))[0], encoding="utf-8") as f:
                    self.assertEqual(len(json.load(f)["sonuc"]), kayitli if tur == 1 else 6, (bos, tur))
                if not bos:
                    break
            if bos:                                          # ikinci çalıştırma yalnız kalan satırı istedi
                self.assertEqual([s["id"] for s in cagri[2]["lines"] if not s.get("skip")], [3])

    def test_bozuk_yanit_yeniden_gonderilir(self):
        """200 ile gelen ama JSON olmayan (vekil sunucunun HTML sayfası) ya da yarım kalan yanıt geçicidir."""
        import http.client
        from unittest import mock
        from defta import model as model_modulu

        class Yanit:
            def __init__(self, govde):
                self.govde, self.headers = govde, {}

            def read(self):
                if isinstance(self.govde, Exception):
                    raise self.govde
                return self.govde

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        tamam = json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {}}).encode()
        # Biçimi bozuk yanıtlar da geçicidir (choices: [null], nesne yerine liste); null kullanım sayısı 0 sayılır,
        # parçalar hâlinde gelen içerik birleştirilir.
        parcali = json.dumps({"choices": [{"message": {"content": [{"type": "text", "text": "{\"a\""}, {"text": ": 1}"}]}}],
                              "usage": {"prompt_tokens": None, "prompt_tokens_details": None}}).encode()
        sira = iter([Yanit(b"<html>Bad gateway</html>"), Yanit(http.client.IncompleteRead(b"{")),
                     Yanit(b'{"choices": [null]}'), Yanit(b"[]"), Yanit(tamam), Yanit(parcali)])
        m = model_modulu.Model(ayar_birlestir({}), "anahtar")
        with mock.patch.object(model_modulu, "_ac", lambda *a, **k: next(sira)), \
                mock.patch.object(model_modulu, "_bekle", lambda olay, s: None):
            self.assertEqual(m.iste("s", "k"), "{}")
            self.assertEqual(m.iste("s", "k"), '{"a": 1}')
        self.assertEqual((m.bekleme_sayisi, m.girdi), (4, 0))
        # Durdurulduysa bekleyen yeniden deneme gönderilmez
        m.iptal.set()
        with self.assertRaises(model_modulu.Iptal):
            m._iste("s", "k")

    def test_yonlendirme_izlenmez(self):
        """Yönlendirme izlenseydi urllib Authorization başlığını başka adrese de taşırdı: 3xx düzelmeyecek hatadır."""
        import http.server
        from defta import model as model_modulu
        gelen = []

        class Yonlendiren(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                gelen.append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/baska" % self.server.server_port)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *a):
                pass
        sunucu = http.server.HTTPServer(("127.0.0.1", 0), Yonlendiren)
        threading.Thread(target=sunucu.serve_forever, daemon=True).start()
        self.addCleanup(sunucu.shutdown)
        m = model_modulu.Model(ayar_birlestir({"model": {"api_adresi": "http://127.0.0.1:%d/v1" % sunucu.server_port}}),
                               "gizli-anahtar")
        with self.assertRaises(model_modulu.ModelHatasi) as h:
            m.iste("s", "k")
        self.assertIn("302", str(h.exception))
        self.assertEqual(gelen, ["Bearer gizli-anahtar"])                # yönlendirilen adrese istek gitmedi
        self.assertTrue(m.iptal.is_set())

    def test_kalan_kota_siniri_dusurur(self):
        from defta.model import Kapi
        k = Kapi(64)
        k.kalan_kota(500)
        self.assertEqual(k.sinir, 64)
        k.kalan_kota(10)
        self.assertEqual(k.sinir, 10)

    def test_hiz_siniri_ayardaki_siniri_asmaz(self):
        """es_zamanli_istek = 1 iken 429 ya da biten kota sınırı 2'ye çıkarmaz: ayar aynı andaki istek sayısının üst
        sınırıdır. Büyük sınır yarıya iner, 2'nin altına düşmez."""
        from defta.model import Kapi
        for olay in (lambda k: k.hiz_siniri(), lambda k: k.kalan_kota(0)):
            k = Kapi(1)
            olay(k)
            self.assertEqual(k.sinir, 1)
        k = Kapi(64)
        k.hiz_siniri()
        self.assertEqual(k.sinir, 32)
        k.kalan_kota(0)
        self.assertEqual(k.sinir, 2)


class SozlukTestleri(unittest.TestCase):
    ELF = """1
00:00:01,000 --> 00:00:02,000
<i>I amar prestar aen.</i>

2
00:00:02,500 --> 00:00:04,000
The world is changed.

3
00:00:05,000 --> 00:00:07,000
He said Mellon and the door opened.

4
00:00:08,000 --> 00:00:09,000
Kenji-senpai!

5
00:00:10,000 --> 00:00:12,000
Use Crimson Lotus Strike, Kenji-senpai.
"""
    ANALIZ = {"terms": [{"source": "Crimson Lotus Strike", "keep": True},
                        {"source": "Mellon", "target": "Dost"}],
              "fictional": [{"id": 1, "text": "I amar prestar aen."}, {"id": 3, "text": "Mellon"},
                            {"id": 2, "text": "uydurma parça"}],
              "address": [{"from": "Frodo", "to": "Gandalf", "form": "formal"}],
              "characters": [{"name": "Frodo", "note": "hobbit, polite"}]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gunluk = Gunluk(os.path.join(self.tmp.name, "logs"))
        # Bu sınıftaki hitap testleri tablo kipini sınar (varsayılan "dogal": hitap modele bırakılır).
        self.ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "hitap": {"acik": True}})
        self.satirlar = srt.srt_ayristir(self.ELF)
        for s in self.satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        self.sozluk = Sozluk(os.path.join(self.tmp.name, "sozluk", "dizi.json"))

    def _analiz(self, model):
        """Terim dışındaki analiz (hitap, karakter, kurgu dili) + elle iki terim girdisi."""
        ayar = dict(self.ayar, sozluk=dict(self.ayar["sozluk"], terimler=False))
        dizi_analiz([("a.srt", self.satirlar, "")], ayar, model, self.sozluk, self.gunluk)
        self.sozluk.girdi_ekle("Mellon", "term", "Dost")
        self.sozluk.girdi_ekle("Crimson Lotus Strike", "fictional", "Crimson Lotus Strike")
        self.sozluk.kaydet()

    def test_analiz_sozluge_islenir_ve_uydurma_isaret_atilir(self):
        self._analiz(SahteModel(analiz=self.ANALIZ))
        self.assertEqual(self.sozluk.terimler, {"Mellon": "Dost"})
        self.assertEqual(self.sozluk.korunacak, ["Crimson Lotus Strike"])
        self.assertEqual(self.sozluk.hitap, {"Frodo -> Gandalf": "resmi"})
        self.assertEqual(self.sozluk.kurgu["a.srt"], {"1": ["I amar prestar aen."], "3": ["Mellon"]})
        tekrar = Sozluk(self.sozluk.yol)
        self.assertEqual(tekrar.analiz_edilen, ["a.srt"])
        istem = istem_olustur(self.ayar, tekrar)
        self.assertNotIn("Frodo -> Gandalf", istem)      # hitap artık isteğin "address" alanında gider
        self.assertNotIn("hobbit, polite", istem)         # karakter notu isteme girmez
        self.assertIn("Mellon -> Dost", istem)

    def test_halk_akrabalik_tanimlik(self):
        """Halk adında güvenilir yerleşik biçimin harfi kazanır; akrabalık unvanı ve tanımlıklı "olduğu gibi"
        kalıp sözlüğe girmez."""
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("Hobbit", {"t": "people", "e": "hobbit"}, True), ("people", "hobbit"))
        self.assertEqual(hedef_belirle("Hobbit", {"t": "people", "e": "hobbit"}, False), ("people", "hobbit"))   # halk adı küçük harf (18.11.2026)
        self.assertEqual(hedef_belirle("Dúnedain", {"t": "people", "e": "Dúnedain"}, True), ("people", "dúnedain"))   # halk adı küçük harf (18.11.2026)
        self.assertEqual(hedef_belirle("Vardy", {"t": "people", "tr": "vardy"}, False), ("people", "vardy"))
        # Mitoloji adı altyazıdaki gibi: harf çevirisi (Kirke, Isparta) yazılmaz, gündelik başka sözcük (Truva) yazılır
        mit = {"t": "name", "real": True, "myth": True}
        self.assertEqual(hedef_belirle("Circe", {**mit, "e": "Kirke"}, True), ("name", "Circe"))
        self.assertEqual(hedef_belirle("Sparta", {**mit, "e": "Isparta"}, True), ("name", "Sparta"))
        self.assertEqual(hedef_belirle("Menelaus", {**mit, "e": "Menelaos"}, True), ("name", "Menelaus"))
        self.assertEqual(hedef_belirle("Troy", {**mit, "e": "Truva"}, True), ("name", "Truva"))
        self.assertEqual(hedef_belirle("Hercules", {**mit, "e": "Herkül"}, True), ("name", "Herkül"))
        self.assertEqual(hedef_belirle("Munich", {"t": "name", "real": True, "e": "Münih"}, True), ("name", "Münih"))  # mitoloji değil
        # kanji terimin "l"si İngilizce çevirisidir: çıktıya girmez; katakana uydurma terimin yazımı kalır
        self.assertEqual(hedef_belirle("爆弾犬", {"t": "term", "l": "bomb dog"}, True), ("term", ""))
        self.assertEqual(hedef_belirle("ミスリル", {"t": "term", "l": "mithril"}, True), ("term", "mithril"))
        # sıradan sözcüklerden yapılmış kavram terimi çevrilir; uydurma terim olduğu gibi kalır
        self.assertEqual(hedef_belirle("超能力者", {"t": "term", "l": "esper", "k": "telepat"}, True), ("term", "telepat"))
        # sıradan anlamı olan, karşılığı gelmemiş terim İngilizce kalmaz; tek sözcüklük katakana adın tam adı alınmaz
        self.assertEqual(hedef_belirle("ring", {"t": "term", "w": "ring"}, True), ("term", ""))
        self.assertEqual(hedef_belirle("アーニャ", {"t": "name", "l": "Anya Forger"}, True), ("name", "Anya"))
        self.assertEqual(hedef_belirle("ロイド", {"t": "name", "l": "Forger Loid"}, True), ("name", "Loid"))
        self.assertEqual(hedef_belirle("フランキー", {"t": "name", "l": "Franky Franklin"}, True), ("name", "Franky"))
        self.assertEqual(hedef_belirle("ロイド・フォージャー", {"t": "name", "l": "Loid Forger"}, True), ("name", "Loid Forger"))
        self.assertEqual(hedef_belirle("kingsfoil", {"t": "term", "tr": "kral otu", "k": "kral otu"}, True),
                         ("term", "kral otu"))
        self.assertEqual(hedef_belirle("mithril", {"t": "term"}, True), ("term", "mithril"))
        s = self.sozluk
        s.girdi_ekle("Uncle", "title", "Amca", kalip="{ad} Amca")
        s.girdi_ekle("Warden", "title", "Muhafız", kalip="Muhafız {ad}")
        s.girdi_ekle("the One", "term", "the One")
        s.girdi_ekle("The Beatles", "name", "The Beatles")
        self.assertEqual((s.terimler, s.degismez), ({"Warden": "Muhafız"}, ["The Beatles"]))

    def test_isaretle(self):
        self._analiz(SahteModel(analiz=self.ANALIZ))
        m = self.ayar["modlar"]
        metin, parcalar, tamami = isaretle(self.satirlar[2].metin, self.sozluk, "a.srt", 3, m)
        self.assertEqual(metin, "He said ⟦Mellon⟧ and the door opened.")
        self.assertFalse(tamami)
        metin, parcalar, tamami = isaretle(self.satirlar[4].metin, self.sozluk, "a.srt", 5, m)
        self.assertEqual(metin, "Use ⟦Crimson Lotus Strike⟧, ⟦Kenji-senpai⟧.")
        self.assertTrue(isaretle(self.satirlar[0].metin, self.sozluk, "a.srt", 1, m)[2])
        self.assertTrue(isaretle(self.satirlar[3].metin, None, "a.srt", 4, m)[2])
        kapali = dict(m, kurgu_dili="ceviri", anime_ekleri="ceviri")
        self.assertEqual(isaretle(self.satirlar[4].metin, self.sozluk, "a.srt", 5, kapali)[1], [])

    def test_tamami_korunan_satir_gonderilmez_ve_isaret_dogrulanir(self):
        self._analiz(SahteModel(analiz=self.ANALIZ))

        def kural(cagri, satirlar):
            if cagri == 1:   # ilk denemede korunan sözcüğü çevirmeye kalkar
                for s in satirlar:
                    s["text"] = s["text"].replace("⟦Mellon⟧", "Dost")
            return satirlar

        model = SahteModel(kural)
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(self.satirlar, "a.srt")
        self.assertEqual(sonuc[0], "<i>I amar prestar aen.</i>")
        self.assertEqual(sonuc[3], "Kenji-senpai!")
        self.assertIn("⟦Mellon⟧", sonuc[2])
        self.assertEqual(model.cagri, 2)
        gonderilen = [s for s in model.gidenler[0]["lines"] if not s.get("skip")]
        self.assertEqual([s["id"] for s in gonderilen], [2, 3, 5])
        m = self.ayar["modlar"]
        self.assertNotIn("⟦", temizlik.son_temizlik(sonuc[2], m))

    def test_eski_duz_sozluk_bicimi_okunur(self):
        yol = os.path.join(self.tmp.name, "eski.json")
        with open(yol, "w", encoding="utf-8") as f:
            json.dump({"Mount Doom": "Kıyamet Dağı", "Mellon": "Mellon"}, f, ensure_ascii=False)
        s = Sozluk(yol)
        self.assertEqual((s.terimler, s.korunacak), ({"Mount Doom": "Kıyamet Dağı"}, ["Mellon"]))

    def test_bozuk_sozluk_dosyasi_ceviriyi_durdurmaz_ve_saklanir(self):
        """Boş, yarım yazılmış ya da JSON nesnesi olmayan sözlük dosyası çalışmayı çökertmez: içeriği ".bozuk" adıyla
        saklanır, sözlük boş başlar, yeniden kurulup yazılabilir ve durum rapora geçer."""
        for n, icerik in enumerate(("", '{"baslik": "X", "girdiler": {"Troy": {"tur": "name", "he', "[1, 2]")):
            yol = os.path.join(self.tmp.name, f"bozuk{n}.json")
            with open(yol, "w", encoding="utf-8") as f:
                f.write(icerik)
            s = Sozluk(yol)
            self.assertEqual((s.girdiler, s.analiz_edilen, s.eski_duzen), ({}, [], False))
            self.assertFalse(os.path.exists(yol))
            with open(yol + ".bozuk", encoding="utf-8") as f:
                self.assertEqual(f.read(), icerik)                # eski içerik kaybolmadı
            dizi_analiz([("a.srt", self.satirlar, "")], self.ayar, SahteModel(analiz=self.ANALIZ), s, self.gunluk)
            self.assertEqual(Sozluk(yol).analiz_edilen, ["a.srt"])   # sözlük yeniden kuruldu ve okunuyor
        with open(os.path.join(self.gunluk.klasor, "hatalar.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("sözlük dosyası okunamadı"), 3)

    def test_sensei_korunur_ve_sozluge_girmez(self):
        m = self.ayar["modlar"]
        self.assertEqual(isaretle("Not bad, huh, Sensei?", None, "a", 1, m)[0], "Not bad, huh, ⟦Sensei⟧?")
        self.sozluk.birlestir("a.srt", {"terms": [{"source": "Sensei", "target": "Üstat"},
                                                  {"source": "Kenji-senpai", "keep": True}]},
                              self.satirlar, True, self.gunluk)
        self.assertEqual((self.sozluk.terimler, self.sozluk.korunacak), ({}, []))

    def test_japonca_ek_ve_tekrar_birlestirme(self):
        from defta.dosya import tekrarlari_birlestir
        c = Cevirmen(self.ayar, SahteModel(), "S", self.gunluk)
        c.satirlar = [srt.Satir(1, 0, 1000, "フォージャーさん？"), srt.Satir(2, 0, 1000, "シッターさん 頼んだ")]
        c.korunan = {}
        c.jp_adlar = {"フォージャー"}
        self.assertIsNone(c._satir_kontrol(1, {1: "Bakıcı ayarladım."}))   # kişi adı değil: kontrol yok
        self.assertEqual(c._satir_kontrol(0, {0: "Forger'lar mı?"}), "ek")
        self.assertIsNone(c._satir_kontrol(0, {0: "Forger-san?"}))
        s = [srt.Satir(1, 0, 500, "A"), srt.Satir(2, 500, 900, "A"), srt.Satir(3, 500, 900, "B"),
             srt.Satir(4, 5000, 6000, "A"), srt.Satir(5, 6083, 7000, "A")]        # 83 ms boşluk: yinelenen söz
        b = tekrarlari_birlestir(s)
        self.assertEqual([(x.no, x.bas, x.bit, x.metin) for x in b],
                         [(1, 0, 900, "A"), (2, 500, 900, "B"), (3, 5000, 6000, "A"), (4, 6083, 7000, "A")])


    def _satirlar(self, metinler):
        return [srt.Satir(i + 1, i * 2000, i * 2000 + 1500, m) for i, m in enumerate(metinler)]

    def test_adaylar_script_bulur(self):
        from defta.terim import adaylari_bul
        bul = lambda metinler, bilinen=(): {a["yazim"]: a for a in adaylari_bul(
            [("x.srt", self._satirlar(metinler), "")], set(bilinen))}
        a = bul(["We sail for Troy.", "If Troy falls, Odysseus wins.", "Look at Troy!", "Odysseus is here.",
                 "Tell Odysseus. If you can.", "If only.", "We call it Operation Lantern.",
                 "Good night, Mr. Frodo.", "Mr. Frodo? He's going cold.", "I met Lady Serane.",
                 "The Vardy are here.", "Two Vardies came. Vardy again. More Vardies."])
        self.assertEqual(sorted(a), ["Frodo", "Mr", "Odysseus", "Operation Lantern", "Serane", "Troy", "Vardy"])
        self.assertEqual(a["Vardy"]["bicimler"], {"Vardies"})                 # çoğul tekilin altında
        self.assertEqual(a["Troy"]["sayi"], 3)
        self.assertNotIn("Troy", bul(["We sail for Troy.", "Troy falls. Troy burns."], ["troy"]))   # kararı var
        j = bul(["情報屋 ナメんなよ", "局長 全員そろいました", "（ＷＩＳＥ局長）ん？", "（ロイド）東国(オスタニア)へ",
                 "東国は遠い", "黄昏(たそがれ)だ", "黄昏(たそがれ)か", "宴(うたげ)だ", "ドア ドア", "ヨルさん"])
        # bir kez geçen okunuşlu sözcük de adaydır (宴(うたげ)); sıradan olduğuna sınıflandırma oyu karar verir
        self.assertEqual(sorted(j), sorted(["情報屋", "局長", "ロイド", "東国(オスタニア)", "黄昏(たそがれ)", "宴(うたげ)",
                                            "ドア"]))
        self.assertEqual(j["東国(オスタニア)"]["bicimler"], {"東国", "オスタニア"})
        # uzatma çizgisi dizisi (えーー) aday değildir; uzatmalı katakana sözcük adaydır
        self.assertEqual(sorted(bul(["えーー", "うわーー", "きゃーーー", "コーヒーだ", "コーヒーか"])), ["コーヒー"])

    def test_aday_listesi_kume_sirasina_bagli_degil(self):
        """Aynı dosya her çalıştırmada aynı aday listesini verir: çoğul birleştirme kümenin (çalıştırmadan çalıştırmaya
        değişen) sırasına bağlı kalırsa "Lo / Los / Loss" ya da "Ros / Rose / Roses" her seferinde başka birleşir."""
        import subprocess
        import sys
        kod = ("import test_defta as T\nfrom defta.terim import adaylari_bul\n"
               "s = T.SozlukTestleri._satirlar(None, ['I saw Lo there.', 'Then Lo left.', 'We met Los there.', "
               "'Then Los left.', 'I like Loss a lot.', 'Then Loss left.', 'I saw Rose there.', 'Tell Rose now.', "
               "'I saw Ros there.', 'Tell Ros now.', 'The two Roses came.', 'Both Roses left.'])\n"
               "print([(a['yazim'], a['sayi'], sorted(a['bicimler'])) for a in adaylari_bul([('x', s, '')], set())])")
        ciktilar = {subprocess.run([sys.executable, "-c", kod], cwd=os.path.dirname(os.path.abspath(__file__)),
                                   env=dict(os.environ, PYTHONHASHSEED=str(tohum)), stdout=subprocess.PIPE,
                                   universal_newlines=True, check=True).stdout for tohum in range(6)}
        self.assertEqual(len(ciktilar), 1, ciktilar)

    def test_sifata_unvan_denirse_dizi_bolunmez(self):
        satirlar = self._satirlar(["The Dark Baron rises again.", "Fear the Dark Baron, child.", "In the Dark Ages we hid.",
                                   "The Dark Ages are over.", "Tell Varn the Dark Tides came.", "Varn saw the Dark Tides."])
        model = SahteModel(terim={"Dark": {"t": "title", "tr": "karanlık", "p": "Karanlık NAME"}, "Varn": {"t": "name"},
                                  "Dark Baron": {"t": "epithet", "tr": "Karanlık Baron"},
                                  "Dark Ages": {"t": "descriptive", "tr": "Karanlık Çağlar"},
                                  "Dark Tides": {"t": "term", "tr": "Kara Gelgit"}})
        dizi_analiz([("l.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertNotIn("Dark", self.sozluk.unvanlar)
        self.assertEqual(self.sozluk.girdiler["Dark Baron"]["hedef"], "Karanlık Baron")
        self.assertEqual(self.sozluk.girdiler["Dark Ages"]["hedef"], "Karanlık Çağlar")
        self.assertFalse({"Ages", "Tides", "Baron"} & set(self.sozluk.girdiler))

    def test_unvan_kaydi_tablodan_yoksa_modelden(self):
        satirlar = self._satirlar(["I spoke to Lord Elrond today.", "Good night, Mr. Frodo.", "Yes, Captain.",
                                   "Ask Master Dwarf.", "Tell Warden Hale we sail.", "And Warden Greyfold knows.",
                                   "The Lord of Mordor sees all.", "リヴァイ隊長、報告です。", "ハンジ団長も来ます。"])
        model = SahteModel(terim={"Elrond": {"t": "name"}, "Frodo": {"t": "name"}, "Dwarf": {"t": "people", "tr": "cüce"},
                                  "Warden": {"t": "title", "tr": "Muhafız", "p": "Muhafız NAME"},
                                  "Warden Hale": {"t": "name"}, "Warden Greyfold": {"t": "name"},
                                  "Lord": {"t": "title", "tr": "Efendi"}, "Captain": {"t": "title", "tr": "Yüzbaşı"},
                                  "リヴァイ": {"t": "name", "l": "Levi"}, "ハンジ": {"t": "name", "l": "Hange"},
                                  "隊長": {"t": "title", "tr": "Yüzbaşı"},
                                  "団長": {"t": "title", "tr": "Komutan", "p": "Komutan NAME"}})
        dizi_analiz([("l.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertEqual(self.sozluk.unvanlar, ["Lord", "Mr", "Warden", "団長", "隊長"])     # adın yanında geçenler
        kayit = self.sozluk.gorunum(self.ayar).unvan_kaydi
        self.assertEqual(kayit, {"Lord": "Lord {ad}", "Mr": "Bay {ad}", "隊長": "Kaptan {ad}",      # tablodan
                                 "Warden": "Muhafız {ad}", "団長": "Komutan {ad}"})               # modelden
        t = self.sozluk.terimler
        self.assertNotIn("Lord", t)                       # bilinen unvan tek başına terim olmaz
        self.assertNotIn("Captain", t)
        self.assertEqual(t["Warden"], "Muhafız")          # uydurma unvan terimdir
        self.assertNotIn("Warden Hale", self.sozluk.degismez)     # tek ad sanılan çift ayrıldı
        self.assertIn("Hale", self.sozluk.degismez)
        istem = istem_olustur(self.ayar, self.sozluk.gorunum(self.ayar))
        for satir in ("- Mr NAME -> Bay NAME", "- Lord NAME -> Lord NAME (the title stays as written)",
                      "- NAME隊長 -> Kaptan NAME", "- Warden NAME -> Muhafız NAME"):
            self.assertIn(satir, istem)
        # anahtarlar: her biri kendi parçasını her yerden kaldırır
        g = lambda **u: self.sozluk.gorunum(ayar_birlestir({"unvan": u})).unvan_kaydi
        self.assertEqual(g(acik=False), {})
        self.assertEqual(g(model_karari=False), {"Lord": "Lord {ad}", "Mr": "Bay {ad}", "隊長": "Kaptan {ad}"})
        self.assertEqual(g(tablo=False), {"Lord": "Efendi {ad}", "隊長": "Yüzbaşı {ad}",       # tablo kapalı: hepsi modelden
                                          "Warden": "Muhafız {ad}", "団長": "Komutan {ad}"})
        self.assertNotIn("Titles with a name", istem_olustur(ayar_birlestir({"unvan": {"acik": False}}),
                                                             self.sozluk.gorunum(ayar_birlestir({"unvan": {"acik": False}}))))
        from defta.terim import siniflandirma_istemi
        self.assertIn('"p" - for every "title"', siniflandirma_istemi(ayar_birlestir()))
        self.assertNotIn('"p" - for every', siniflandirma_istemi(ayar_birlestir({"unvan": {"model_karari": False}})))

    def test_unvan_dizisi_ayrilir_ve_hitap_eki_unvan_sayilmaz(self):
        satirlar = self._satirlar(["We saw Maester Luwin there.", "Then Maester Luwin left.", "ミナト先生　おはよう", "先生に 話します",
                                   "先生は どこですか"])
        model = SahteModel(terim={"Maester Luwin": {"t": "title", "tr": "Üstat", "p": "Üstat NAME"},
                                  "ミナト": {"t": "name", "l": "Minato"}},
                           kesif=[{"s": "先生", "t": "title", "tr": "Öğretmen", "p": "Öğretmen NAME"}])
        for u in ({}, {"model_karari": False}):
            sozluk = Sozluk(os.path.join(self.tmp.name, f"s{len(u)}.json"))
            ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "unvan": u})
            dizi_analiz([("m.srt", satirlar, "")], ayar, model, sozluk, self.gunluk)
            self.assertNotIn("Maester Luwin", sozluk.terimler)         # "Maester Luwin -> Üstat" olsaydı ad düşerdi
            self.assertEqual(sozluk.terimler.get("Maester"), "Üstat")
            self.assertIn("Luwin", sozluk.degismez)
            self.assertNotIn("先生", sozluk.terimler)                   # hitap eki: sözlüğe de unvan kaydına da girmez
            self.assertNotIn("先生", sozluk.unvanlar)
            self.assertEqual(sozluk.gorunum(ayar).unvan_kaydi, {} if u else {"Maester": "Üstat {ad}"})

    def test_unvan_kalibi_karsilik_istegiyle_ayni_sozcugu_tasir(self):
        """Kalıp ("p") sınıflandırmadan, unvanın karşılığı karşılık isteğinden gelir. Sözcükleri ayrışırsa kalıp yalnız
        adın yerini verir: yoksa "Warden Hale" satırı unvan denetimiyle ("Muhafız Hale") terim denetiminden
        ("Gardiyan") aynı anda geçemez ve her seferinde yeniden sorulur."""
        from defta.terim import unvan_kalibi
        satirlar = self._satirlar(["Tell Warden Hale we sail.", "And Warden Greyfold knows.", "The Warden is here.",
                                   "Ask the Warden."])
        model = SahteModel(terim={"Warden": {"t": "title", "tr": "Muhafız", "p": "Muhafız NAME", "k": "Gardiyan"},
                                  "Warden Hale": {"t": "name"}, "Warden Greyfold": {"t": "name"}})
        dizi_analiz([("w.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        gorunum = self.sozluk.gorunum(self.ayar)
        self.assertEqual((gorunum.terimler["Warden"], gorunum.unvan_kaydi["Warden"]), ("Gardiyan", "Gardiyan {ad}"))
        c = Cevirmen(self.ayar, None, "S", self.gunluk, gorunum)
        c._hazirla(satirlar, "w.srt")
        self.assertIsNone(c._satir_kontrol(0, {0: "Gardiyan Hale'e yelken açtığımızı söyle."}))
        self.assertEqual(unvan_kalibi({"p": "NAME Bey"}, "bey"), "{ad} Bey")                 # aynı sözcük: "p" kalır
        self.assertEqual(unvan_kalibi({"p": "NAME Hazretleri"}, "imparator"), "{ad} İmparator")
        self.assertEqual(unvan_kalibi({"p": "Sayın NAME Bey"}, "efendi"), "Sayın {ad} Bey")  # iki yanlı kalıba dokunulmaz

    def test_hitap_kaniti_script_ten_ve_oylama(self):
        from defta import hitap
        from defta.ayar import unvan_sozcukleri
        satirlar = self._satirlar(["ILSA: Captain, are you awake?", "MARA: I am now. What is it?",
                                   "ILSA: You should rest, Captain.", "MARA: Mind your own watch.",
                                   "ILSA: Yes, Captain.", "MARA: Master Tolen, sit down.",
                                   "（レン）\nソウマ隊長　報告します", "（ソウマ）\nご苦労　レン君　何があった",
                                   "（レン）\n船が 現れました", "（ソウマ）\nやはりな　お前も来い"])
        kanit, neden = hitap.kanit_topla(satirlar, unvan_sozcukleri(self.ayar))
        self.assertEqual(dict(kanit[("ILSA", "MARA")]), {"resmi": 3})              # unvanla seslenme, dinleyen komşudan
        self.assertEqual(dict(kanit[("レン", "ソウマ")]), {"resmi": 2})              # です/ます
        self.assertEqual(dict(kanit[("ソウマ", "レン")]), {"samimi": 2})             # くん, お前
        self.assertNotIn(("MARA", "ILSA"), kanit)                                  # İngilizcede samimiyeti script bilemez
        # "でしょう？" ve "〜ましょう" arkadaş arasında da söylenir: kibarlık kanıtı değil; "のよ" düz kadın konuşmasıdır
        robin = self._satirlar(["（ロビン）\n世界情勢は 大荒れでしょう？", "（チョッパー）\nそうだな",
                                "（ロビン）\n早速 いきましょう", "（チョッパー）\nおう",
                                "（ロビン）\nルフィのお兄さんなのよ", "（チョッパー）\nへえ"])
        self.assertEqual(dict(hitap.kanit_topla(robin, [])[0].get(("ロビン", "チョッパー"), {})), {"samimi": 1})
        self.assertIn('ILSA -> MARA: formal in 3 lines: addresses them as "Captain"', hitap.kanit_metni(kanit, neden))
        karakterler = [{"name": "Mara"}, {"name": "Ilsa"}, {"name": "Ren", "source": "レン"}, {"name": "Soma", "source": "ソウマ"}]
        kesin = {(h["from"], h["to"]): h["form"] for h in hitap.kesin_ciftler([kanit], karakterler)}
        self.assertEqual(kesin, {("Ilsa", "Mara"): "formal", ("Ren", "Soma"): "formal", ("Soma", "Ren"): "informal"})
        oy = hitap.oylari_birlestir([{"characters": [{"name": "A"}], "address": [{"from": "A", "to": "B", "form": "formal"}]},
                                     {"address": [{"from": "A", "to": "B", "form": "informal"}, {"from": "C", "to": "D", "form": "formal"}]},
                                     {"address": [{"from": "A", "to": "B", "form": "formal"}]}])
        self.assertEqual([(h["from"], h["form"]) for h in oy["address"]], [("A", "formal")])     # C -> D tek oyda: alınmaz
        # uçtan uca: model tersini söylese de script'in kesin gördüğü çift tabloya script'ten girer; istek 3 kez gider
        analiz = {"characters": karakterler, "address": [{"from": "Ilsa", "to": "Mara", "form": "informal", "why": "x"},
                                                         {"from": "Mara", "to": "Ilsa", "form": "informal", "why": "x"}]}

        class Sayan(SahteModel):
            iliski = 0

            def iste(self, sistem, kullanici, dusunme=None):
                if "who the speaking characters are" in sistem[:400]:
                    Sayan.iliski += 1
                    self.son_iliski = json.loads(kullanici)
                return super().iste(sistem, kullanici, dusunme)
        model = Sayan(analiz=analiz)
        dizi_analiz([("s.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertEqual(Sayan.iliski, 3)
        self.assertIn("evidence", model.son_iliski)
        self.assertEqual((self.sozluk.hitap["Ilsa -> Mara"], self.sozluk.hitap["Mara -> Ilsa"], self.sozluk.hitap["Soma -> Ren"]),
                         ("resmi", "samimi", "samimi"))
        kapali = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "hitap": {"acik": True, "kanit": False, "oy": 1}})
        s2, Sayan.iliski = Sozluk(os.path.join(self.tmp.name, "k.json")), 0
        m2 = Sayan(analiz=analiz)
        dizi_analiz([("s.srt", satirlar, "")], kapali, m2, s2, self.gunluk)
        self.assertEqual((Sayan.iliski, s2.hitap["Ilsa -> Mara"]), (1, "samimi"))      # kanıt kapalı: modelin dediği, tek istek
        self.assertNotIn("evidence", m2.son_iliski)
        # Üç oydan ikisi yanıtsız kaldıysa (ret) tek geçerli yanıtın çiftleri atılmaz: yanıtsız istek oy değildir
        class Reddeden(SahteModel):
            iliski = 0

            def iste(self, sistem, kullanici, dusunme=None):
                if "who the speaking characters are" in sistem[:400]:
                    Reddeden.iliski += 1
                    if Reddeden.iliski <= 2 * kapali["kontrol"]["deneme_sayisi"]:
                        raise Ret("içerik filtresi")
                return super().iste(sistem, kullanici, dusunme)
        uc_oy = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "hitap": {"acik": True, "kanit": False}})
        s3 = Sozluk(os.path.join(self.tmp.name, "r.json"))
        dizi_analiz([("s.srt", satirlar, "")], uc_oy, Reddeden(analiz=analiz), s3, self.gunluk)
        self.assertEqual(s3.hitap, {"Ilsa -> Mara": "samimi", "Mara -> Ilsa": "samimi"})
        from defta.analiz import iliski_istemi
        self.assertNotIn('"evidence"', iliski_istemi(kapali, None))

    def test_of_ile_gelen_yer_adi_unvani_tamlamaya_cevirir(self):
        self.sozluk.unvanlar = ["Princess"]
        satirlar = self._satirlar(["The Princess Sibylla of Jerusalem and her husband!"])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                self.gidenler.append(json.loads(kullanici))
                return json.dumps({"lines": [{"id": 1, "text": "Kudüs Prensesi Sibylla ve kocası!"}]}, ensure_ascii=False)
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "kontrol": {"supheli_satir_kontrolu": False}})
        m = M()
        sonuc = Cevirmen(ayar, m, "S", self.gunluk, self.sozluk.gorunum(ayar)).cevir(satirlar, "u.srt")
        self.assertEqual((sonuc[0], len(m.gidenler)), ("Kudüs Prensesi Sibylla ve kocası!", 1))

    def test_unvan_tablosu_dile_gore(self):
        ozel = ayar_birlestir({"unvanlar": {"tr": {"Lord": "Efendi", "Mr": "{ad} Bey"}, "en": {"隊長": "Captain"}}})
        from defta.ayar import unvan_tablosu, AyarHatasi
        t = unvan_tablosu(ozel)
        self.assertEqual((t["Lord"], t["Mr"], t["Captain"]), ("Efendi {ad}", "{ad} Bey", "Kaptan {ad}"))
        ingilizce = ayar_birlestir({"ceviri": {"hedef_dil": "en"},
                                    "unvanlar": {"en": {"隊長": "Captain"}}})
        self.assertEqual(unvan_tablosu(ingilizce), {"隊長": "Captain {ad}"})        # yalnızca hedef dilin tablosu
        almanca = ayar_birlestir({"ceviri": {"hedef_dil": "de"}})
        self.assertEqual(unvan_tablosu(almanca), {})                               # tablosu yok: model karar verir
        self.assertEqual(unvan_tablosu(ayar_birlestir({"unvan": {"tablo": False}})), {})
        with self.assertRaises(AyarHatasi):
            ayar_birlestir({"unvanlar": {"Lord": "Efendi"}})                       # eski düz biçim açıkça reddedilir

    def test_unvan_denetimi_ve_duzeltmesi(self):
        self.sozluk.unvanlar = ["Master", "Mr", "隊長"]
        self.sozluk.elle_ekle({"リヴァイ": "Levi"})
        self.sozluk.girdi_yaz("Master", tur="title", hedef="Usta", terim=False)     # bilinen unvan: terim değil
        satirlar = self._satirlar(["Master Tolen, you sent for me?", "I saw Mr. Frodo there.", "Go, Mr. Frodo!",
                                   "リヴァイ隊長、報告です。", "Sam told Master Tolen."])
        ilk = {1: "Usta Tolen, beni mi çağırdınız?", 2: "Frodo Bey'i orada gördüm.", 3: "Gidin, Frodo!",
               4: "Yüzbaşı Levi, rapor veriyorum.", 5: "Sam Tolen'e söyledi."}

        def model_kur():
            class M:
                gidenler = []

                def iste(self, sistem, kullanici, dusunme=None):
                    veri = json.loads(kullanici)
                    self.gidenler.append(veri)
                    duzeltilmis = {2: "Bay Frodo'yu orada gördüm.", 4: "Kaptan Levi, rapor veriyorum.",
                                   5: "Sam, Efendi Tolen'e söyledi.", 1: "Efendi Tolen, beni mi çağırdınız?",
                                   3: "Gidin, Bay Frodo!"}
                    return json.dumps({"lines": [{"id": x["id"], "text": duzeltilmis[x["id"]] if "fix" in veri
                                                  else ilk[x["id"]]} for x in veri["lines"] if not x.get("skip")]},
                                      ensure_ascii=False)
            return M()
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "kontrol": {"supheli_satir_kontrolu": False},
                               "modlar": {"anime_ekleri": "ceviri"}})
        model = model_kur()
        sonuc = Cevirmen(ayar, model, "S", self.gunluk, self.sozluk.gorunum(ayar)).cevir(satirlar, "u.srt")
        self.assertEqual(sonuc[0], "Efendi Tolen, beni mi çağırdınız?")     # bilinen unvan sözcüğü: script değiştirdi
        self.assertEqual(sonuc[2], "Gidin, Bay Frodo!")                     # düşen unvan: script ekledi
        # belirsiz olanlar yeniden soruldu: "Frodo Bey" (sıra), "Yüzbaşı" (bilinmeyen sözcük), araya girmiş ad
        self.assertEqual(sorted(f["id"] for f in model.gidenler[1]["fix"]), [2, 4, 5])
        self.assertIn('write it as "Kaptan Levi"', " ".join(f["problem"] for f in model.gidenler[1]["fix"]))
        self.assertEqual((sonuc[1], sonuc[3], sonuc[4]),
                         ("Bay Frodo'yu orada gördüm.", "Kaptan Levi, rapor veriyorum.", "Sam, Efendi Tolen'e söyledi."))
        # duzelt kapalı: script dokunmaz, hepsi sorulur; denetle kapalı: hiçbir şey sorulmaz
        for u, beklenen in (({"denetim": "sor"}, [1, 2, 3, 4, 5]), ({"denetim": "kapali"}, None)):
            a2 = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "kontrol": {"supheli_satir_kontrolu": False},
                                 "modlar": {"anime_ekleri": "ceviri"}, "unvan": u})
            m2 = model_kur()
            Cevirmen(a2, m2, "S", self.gunluk, self.sozluk.gorunum(a2)).cevir(satirlar, "u.srt")
            if beklenen is None:
                self.assertEqual(len(m2.gidenler), 1)
            else:
                self.assertEqual(sorted(f["id"] for f in m2.gidenler[1]["fix"]), beklenen)

    def test_karsiligi_kural_belirler(self):
        from defta.terim import hedef_belirle as h
        self.assertEqual(h("Saltkeep", {"t": "name", "e": "Tuzhisar"}, False), ("name", "Saltkeep"))      # uydurma ad çevrilmez
        # kurgusal adda sınıflandırmanın "e"si alınmaz (19.11.2026)
        self.assertEqual(h("Rivendell", {"t": "name", "e": "Ayrıkvadi"}, True), ("name", "Rivendell"))
        self.assertEqual(h("Trojan War", {"t": "descriptive", "k": "Truva Savaşı"}, True),
                         ("descriptive", "Truva Savaşı"))    # anlamlı sözü karşılık isteği çevirir
        self.assertEqual(h("Troy", {"t": "name", "e": "Truva", "real": True}, False), ("name", "Truva"))  # gerçek dünya
        self.assertEqual(h("Operation Lantern", {"t": "codename", "tr": "Fener Operasyonu"}, True),
                         ("codename", "Operation Lantern"))     # kod adı çevrilmez (karşılık isteği cevabı yok)
        self.assertEqual(h("黄昏(たそがれ)", {"t": "codename", "l": "Twilight", "tr": "Alacakaranlık"}, True),
                         ("codename", "Tasogare"))   # İngilizce yayındaki çeviri değil, duyulan ad (18.11.2026)
        self.assertEqual(h("アーニャ", {"t": "name", "l": "Anya"}, False), ("name", "Anya"))
        self.assertEqual(h("東国(オスタニア)", {"t": "descriptive", "l": "Ostania", "tr": "Doğu Ülkesi"}, True),
                         ("descriptive", "Ostania"))                                                      # okunuşu verilmiş: ad odur
        self.assertEqual(h("婆(ババア)", {"t": "epithet", "l": "Baba", "k": "kocakarı"}, True),
                         ("epithet", "kocakarı"))                                                         # lakapta okunuş karşılık olmaz
        self.assertEqual(h("アーニャ", {"t": "name"}, False), ("name", ""))            # Latin yazımı yoksa girmez
        # karşılık alan türlerin Türkçesi yalnızca karşılık isteğinden ("k"): sınıflandırmanın "tr"/"e"si hedef olmaz
        self.assertEqual(h("Wardens of Greyfold", {"t": "descriptive", "tr": "Greyfold Muhafızları",
                                                   "k": "Greyfold Muhafızları"}, False),
                         ("descriptive", "Greyfold Muhafızları"))
        self.assertEqual(h("Wardens of Greyfold", {"t": "descriptive", "tr": "Greyfold Muhafızları"}, False),
                         ("descriptive", ""))                       # çevrilecek ama karşılık gelmedi: sözlüğe girmez
        self.assertEqual(h("超能力者", {"t": "people", "e": "esper", "l": "chōnōryokusha", "k": "telepat"}, True),
                         ("people", "telepat"))                     # "yerleşik biçim" karşılığı geçemez
        self.assertEqual(h("hobbit", {"t": "people", "tr": "Hobbit"}, False), ("people", "hobbit"))       # ırk küçük harfle
        self.assertEqual(h("局長", {"t": "title", "k": "müdür"}, False), ("title", "müdür"))
        self.assertEqual(h("Vardy", {"t": "people", "tr": "vardy"}, True), ("people", "vardy"))             # çevrilmediyse yazımı kalır, küçük harfle
        self.assertEqual(h("Doctor", {"t": "epithet", "tr": "Doktor", "k": "Doktor"}, True), ("epithet", "Doktor"))
        self.assertEqual(h("ドア", {"t": "common", "tr": "kapı"}, True), ("common", ""))
        self.assertEqual(h("Lord", {"t": "title", "tr": "Efendi", "k": "Efendi"}, True), ("title", "Efendi"))
        from defta.terim import unvan_kalibi
        self.assertEqual(unvan_kalibi({"p": "NAME Bey"}, "Bey"), "{ad} Bey")           # modelin verdiği sıra
        self.assertEqual(unvan_kalibi({}, "Muhafız"), "Muhafız {ad}")                  # yoksa unvan adın önünde
        self.assertEqual(h("Queen", {"t": "olmayan"}, True), ("common", ""))

    SES_SATIRLARI = ["（湊）海人(かいと)、聞こえるか？", "（大きな灰色の犬）ボフ", "（湊）あの犬は何だ？", "（大きな灰色の犬）ボフ ボフ",
                     "（葵）わくわく！", "（店員）どうも", "（湊）カイトはどこだ", "（店員）どうも", "（大きな灰色の犬）ボフッ",
                     "（葵）カイト、わくわくするね", "（葵）はい", "（葵）はい"]

    def _ses_kur(self, **modlar):
        ayar = ayar_birlestir({**self.ayar, "modlar": {**self.ayar["modlar"], **modlar}})
        model = SahteModel(terim={"ボフ": {"t": "name", "l": "Bond"}, "カイト": {"t": "name", "l": "Kaito"}})
        model.sesler = {"ボフ", "わくわく"}
        istekler = []
        asil = model.iste
        model.iste = lambda sistem, kullanici, dusunme=None: (istekler.append(sistem[:38]), asil(sistem, kullanici, dusunme))[1]
        dizi_analiz([("k.srt", self._satirlar(self.SES_SATIRLARI), "")], ayar, model, self.sozluk, self.gunluk)
        return ayar, model, sum(s.startswith("You are given words from the subtitles") for s in istekler)

    def test_ses_sozcugu_ad_olmaz_ve_latin_yazilir(self):
        ayar, model, ses_istegi = self._ses_kur()
        self.assertTrue(all("ボフ" not in istek for istek in model.terim_istekleri))    # havlama ad adayı olmadı
        self.assertTrue(any("カイト" in istek for istek in model.terim_istekleri))      # gerçek ad aday kaldı
        self.assertEqual(ses_istegi, 3)                                                # üç oy
        g = self.sozluk.girdiler
        self.assertEqual((g["ボフ"]["tur"], g["ボフ"]["hedef"]), ("sound", "Bofu"))
        self.assertEqual((g["わくわく"]["tur"], g["わくわく"]["hedef"]), ("sound", "Waku waku"))
        self.assertIs(g["どうも"]["ses"], False)                                        # sıradan sözcük: bir daha sorulmaz
        self.assertNotIn("はい", g)                                                     # ünlem aday bile olmaz
        self.assertNotIn("ボフ", self.sozluk.terimler)
        self.assertEqual(self.sozluk.sesler(ayar), {"ボフ": "Bofu", "わくわく": "Waku waku"})
        c = Cevirmen(ayar, None, "", None, sozluk=self.sozluk)
        c._hazirla(self._satirlar(self.SES_SATIRLARI), "k.srt")
        self.assertEqual([c.sabit.get(i) for i in (1, 3, 4, 8)], ["Bofu", "Bofu bofu", "Waku waku!", "Bofu"])
        self.assertNotIn(9, c.sabit)                                                   # cümle içindeki çevrilir
        self.assertNotIn(5, c.sabit)                                                   # "どうも" çevrilir
        self.assertIn("ボフ = Bofu", istem_olustur(ayar, self.sozluk))

    CIGLIK_SATIRLARI = ["（湊）カイトはどこだ", "（組織員）ぐあっ", "（葵）はい", "（組織員）うわあ！", "（湊）カイト、逃げろ",
                        "- 危ない\n- ぐあっ", "（葵）えっ", "（湊）具合はどうだ"]

    def test_ciglik_sorulmadan_duyuldugu_gibi_yazilir(self):
        """Kullanıcı kararı (20.11.2026): çığlık ve inleme duyulduğu gibi kalır. Ses mi sözcük mü diye modele sorulmaz:
        ぐあ için oylar koşudan koşuya değişiyordu (Kodi 1.0.10, SPY x FAMILY S01E02). Anlamlı ünlemler çevrilir."""
        from defta.kaynak import ciglik_mi
        for soz in ("ぐあ", "ぐわあ", "うわあ", "ヒイ", "ヒエエエ", "ぎゃあ", "キャア", "うおお", "ぐふ", "ぐぬぬ"):
            self.assertTrue(ciglik_mi(soz), soz)
        for soz in ("はい", "ええ", "うん", "えっ", "あっ", "はあ", "ああ", "ぐあい", "うわさ", "ママ", "ワクワク", "がはは"):
            self.assertFalse(ciglik_mi(soz), soz)
        model = SahteModel(terim={"カイト": {"t": "name", "l": "Kaito"}})
        model.sesler = set()                               # model sorulsa "ses değil" diyecek
        istekler = []
        asil = model.iste
        model.iste = lambda sistem, kullanici, dusunme=None: (istekler.append(sistem[:38]), asil(sistem, kullanici, dusunme))[1]
        dizi_analiz([("k.srt", self._satirlar(self.CIGLIK_SATIRLARI), "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertFalse(any(s.startswith("You are given words from the subtitles") for s in istekler))   # oylama yok
        self.assertEqual(self.sozluk.sesler(self.ayar), {"ぐあ": "Gua", "うわあ": "Uwaa"})
        c = Cevirmen(self.ayar, None, "", None, sozluk=self.sozluk)
        c._hazirla(self._satirlar(self.CIGLIK_SATIRLARI), "k.srt")
        self.assertEqual((c.sabit.get(1), c.sabit.get(3)), ("Gua", "Uwaa!"))
        self.assertNotIn(2, c.sabit)                       # "はい" çevrilir
        self.assertNotIn(6, c.sabit)                       # "えっ" çevrilir
        istem = istem_olustur(self.ayar, self.sozluk)
        self.assertIn("ぐあ = Gua", istem)
        self.assertIn("scream, groan or grunt", istem)
        self.assertNotIn('"Uwaa"', istem)

    def test_ses_sozcugu_cevir_modunda_modele_kalir(self):
        ayar, model, ses_istegi = self._ses_kur(ses_sozcukleri="cevir")
        self.assertEqual(ses_istegi, 0)                                                # kapalı: istek de yok
        self.assertTrue(all("ボフ" not in istek for istek in model.terim_istekleri))    # yine de ad adayı değil
        self.assertNotIn("ボフ", self.sozluk.terimler)
        self.assertEqual(self.sozluk.sesler(ayar), {})
        c = Cevirmen(ayar, None, "", None, sozluk=self.sozluk)
        c._hazirla(self._satirlar(self.SES_SATIRLARI), "k.srt")
        self.assertIn(1, c.giden)                                                      # havlama modele gider: "Hav"
        self.assertNotIn("Sound words", istem_olustur(ayar, self.sozluk))

    def test_eski_sozlukte_ad_sanilan_ses_duzelir(self):
        self.sozluk.girdi_ekle("ボフ", "name", "Bond")
        self.assertEqual(self.sozluk.terimler.get("ボフ"), "Bond")
        self._ses_kur()
        self.assertNotIn("ボフ", self.sozluk.terimler)
        self.assertEqual(self.sozluk.girdiler["ボフ"]["hedef"], "Bofu")

    def test_sozluk_tek_kayittan_turetilir(self):
        s = self.sozluk
        s.girdi_ekle("Twilight", "codename", "Twilight")                 # kod adı: aynen kalır ama terimdir
        s.girdi_ekle("東国(オスタニア)", "descriptive", "Ostania", ["東国", "オスタニア", "Ostanias"])
        s.girdi_ekle("Saltkeep", "name", "Saltkeep")
        s.girdi_ekle("mellon", "fictional", "mellon")
        s.girdi_ekle("Captain", "title", "Kaptan", terim=False)          # bilinen unvan: karar saklanır, terim olmaz
        s.girdi_ekle("Look", "common", "")
        self.assertEqual(s.terimler, {"Twilight": "Twilight", "東国(オスタニア)": "Ostania", "東国": "Ostania",
                                      "オスタニア": "Ostania"})
        self.assertEqual((s.degismez, s.korunacak), (["Saltkeep"], ["mellon"]))
        with self.assertRaises(AttributeError):                          # listeler yazılamaz: tek kaynak girdiler
            s.terimler = {}
        s.terimden_cikar("東国(オスタニア)")                              # öteki yazımları da birlikte kalkar
        self.assertEqual(s.terimler, {"Twilight": "Twilight"})
        s.girdi_yaz("Twilight", hedef="Alacakaranlık")
        self.assertEqual(s.terimler, {"Twilight": "Alacakaranlık"})
        s.kaydet()
        with open(s.yol, encoding="utf-8") as f:
            metin = f.read()
        veri = json.loads(metin)
        self.assertEqual(list(veri), ["baslik", "girdiler", "unvanlar", "hitap", "karakterler", "adlar", "latin", "dosyalar"])
        self.assertIn('\n    "Saltkeep": {"tur": "name", "hedef": "Saltkeep"', metin)     # girdi başına tek satır
        tekrar = Sozluk(s.yol)
        self.assertEqual((tekrar.terimler, tekrar.degismez, tekrar.korunacak),
                         ({"Twilight": "Alacakaranlık"}, ["Saltkeep"], ["mellon"]))
        self.assertIs(tekrar.girdiler["Captain"]["terim"], False)

    def test_eski_duzendeki_sozluk_donusturulur(self):
        yol = os.path.join(self.tmp.name, "eski.json")
        girdi = lambda tur, hedef: {"tur": tur, "hedef": hedef, "yazimlar": [], "karar": "model", "sayi": 2, "ornek": ""}
        with open(yol, "w", encoding="utf-8") as f:
            json.dump({"baslik": "X", "terimler": {"Mount Doom": "Hüküm Dağı", "Ring": "Yüzük"},
                       "korunacak": ["mellon"], "degismez": ["Frodo"],
                       "girdiler": {"Mount Doom": girdi("descriptive", "Kıyamet Dağı"), "Frodo": girdi("name", "Frodo"),
                                    "Captain": girdi("title", "Kaptan"), "mellon": girdi("fictional", "mellon")},
                       "dil": {"a.srt": "en", "b.srt": "en"}, "sahneler": {"a.srt": [[1, 2, "x"]]},
                       "sozler": {}, "kurgu": {"a.srt": {"1": ["mellon"]}, "b.srt": {}},
                       "analiz_edilen": ["a.srt"]}, f, ensure_ascii=False)
        s = Sozluk(yol)
        self.assertTrue(s.eski_duzen)
        self.assertEqual(s.terimler, {"Mount Doom": "Hüküm Dağı", "Ring": "Yüzük"})     # elle değişen karşılık kazanır
        self.assertEqual(s.girdiler["Mount Doom"]["hedef"], "Hüküm Dağı")               # artık tek yerde
        self.assertEqual(s.girdiler["Ring"]["karar"], "elle")                           # girdisi olmayan terim girdi oldu
        self.assertIs(s.girdiler["Captain"]["terim"], False)                            # listede yoktu: terim değil
        self.assertEqual((s.degismez, s.korunacak), (["Frodo"], ["mellon"]))
        s.kaydet()
        with open(yol, encoding="utf-8") as f:
            veri = json.load(f)
        for eski in ("terimler", "korunacak", "degismez", "dil", "sahneler", "sozler", "kurgu", "analiz_edilen"):
            self.assertNotIn(eski, veri)
        self.assertEqual(veri["dosyalar"], {"a.srt": {"dil": "en", "sahneler": [[1, 2, "x"]], "kurgu": {"1": ["mellon"]}},
                                            "b.srt": {"dil": "en", "analiz": False}})     # yarım okunan dosya
        tekrar = Sozluk(yol)
        self.assertFalse(tekrar.eski_duzen)
        self.assertEqual((tekrar.analiz_edilen, tekrar.dil, tekrar.kurgu),
                         (["a.srt"], {"a.srt": "en", "b.srt": "en"}, {"a.srt": {"1": ["mellon"]}}))
        self.assertEqual(tekrar.terimler, s.terimler)

    def test_anlam_notu_sozluge_ve_isteme_girer(self):
        satirlar = self._satirlar(["Twilight is here.", "Tell Twilight to wait.", "Tell Odysseus now.", "I saw Odysseus.",
                                   "The Wardens of Greyfold march.", "Wardens of Greyfold never sleep.",
                                   "We sail on Hope.", "I trust Hope."])
        terim = {"Twilight": {"t": "codename", "m": "Loid's spy code name.", "w": '"dusk"'},
                 "Odysseus": {"t": "name", "m": "king of Ithaca"},                  # yalnızca ad: not tutulmaz
                 "Hope": {"t": "name", "m": "the captain's ship", "w": "hope"},     # ad, ama sıradan sözcük de
                 "Wardens of Greyfold": {"t": "descriptive", "tr": "Greyfold Muhafızları", "m": "the order [guards] of Greyfold"}}
        model = SahteModel(terim=terim)
        dizi_analiz([("o.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        g = self.sozluk.girdiler
        self.assertEqual((g["Twilight"]["anlam"], g["Twilight"]["siradan"]), ("Loid's spy code name", "dusk"))
        self.assertEqual(g["Wardens of Greyfold"]["anlam"], "the order (guards) of Greyfold")
        self.assertNotIn("siradan", g["Wardens of Greyfold"])
        self.assertNotIn("anlam", g["Odysseus"])
        self.assertEqual(self.sozluk.siradanlar, {"Twilight": "dusk", "Hope": "hope"})
        istem = istem_olustur(self.ayar, self.sozluk.gorunum(self.ayar))
        self.assertIn("- Twilight (keep exactly as is) [Loid's spy code name] | plain word: dusk\n", istem)
        self.assertIn("- Wardens of Greyfold -> Greyfold Muhafızları [the order (guards) of Greyfold]\n", istem)
        self.assertIn("- Hope (keep exactly as is) [the captain's ship] | plain word: hope\n", istem)
        self.assertIn("- Names and terms written exactly as in the source: Odysseus\n", istem)
        self.assertIn("A note in [ ] says what the entry refers to", istem)
        self.assertIn('is ALSO an everyday word', istem)
        # Ayar kapalı: not modelden istenmez, sözlükteki eski notlar da çeviriye ulaşmaz.
        kapali = ayar_birlestir({"sozluk": {"anlam_notu": False}})
        from defta.terim import siniflandirma_istemi
        self.assertIn('"m" - ', siniflandirma_istemi(ayar_birlestir()))
        self.assertIn('"w" - ', siniflandirma_istemi(ayar_birlestir()))
        self.assertNotIn('"m" - ', siniflandirma_istemi(kapali))
        self.assertNotIn('"w" - ', siniflandirma_istemi(kapali))
        gorunum = self.sozluk.gorunum(kapali)
        self.assertEqual((gorunum.notlar, gorunum.siradanlar), ({}, {}))
        istem = istem_olustur(kapali, gorunum)
        self.assertNotIn("spy code name", istem)
        self.assertNotIn("A note in [ ]", istem)
        self.assertNotIn("plain word", istem)
        self.assertIn("- Twilight (keep exactly as is)\n", istem)
        self.assertIn("- Names and terms written exactly as in the source: Hope, Odysseus\n", istem)
        self.assertIn("anlam", self.sozluk.girdiler["Twilight"])                 # asıl sözlük değişmez

    def test_terim_sozlugu_kurulur(self):
        satirlar = self._satirlar(["We sail for Troy.", "If Troy falls, Odysseus wins.", "Look at Troy!",
                                   "Odysseus is here.", "Tell Odysseus about Saltkeep. If you can.",
                                   "Saltkeep fell. We call it Operation Lantern.", "A halfling came.",
                                   "The halfling left."])
        terim = {"Troy": {"t": "name", "e": "Truva", "real": True}, "Odysseus": {"t": "name"},
                 "Saltkeep": {"t": "name", "e": "Tuzhisar"}, "Operation Lantern": {"t": "codename", "tr": "Fener"},
                 "halfling": {"t": "people", "tr": "Buçukluk"}}
        # keşif yalnızca yazımı bulur: eski biçimdeki öğenin kendi kararı ("halfling" olduğu gibi) kullanılmaz
        kesif = [{"s": "halfling", "t": "people"}, "dragon", "Troy"]          # dragon dosyada yok, Troy'un kararı var
        model = SahteModel(terim=terim, kesif=kesif)
        dizi_analiz([("o.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertEqual(model.terim_istekleri, [["Odysseus", "Troy", "Saltkeep", "Operation Lantern"]] * 3
                         + [["halfling"]] * 3)                                # üç oy; keşfin bulduğu da oylanır
        self.assertEqual(self.sozluk.terimler, {"Troy": "Truva", "Operation Lantern": "Operation Lantern",
                                                "halfling": "buçukluk"})
        self.assertEqual(self.sozluk.degismez, ["Odysseus", "Saltkeep"])      # eser tanınmadı: "Tuzhisar" alınmaz
        self.assertEqual(self.sozluk.girdiler["Troy"]["tur"], "name")
        self.assertEqual(self.sozluk.dil, {"o.srt": "en"})
        istem = istem_olustur(self.ayar, self.sozluk)
        self.assertIn("- Operation Lantern (keep exactly as is)", istem)
        self.assertIn("- Names and terms written exactly as in the source: Odysseus, Saltkeep", istem)
        # sonraki bölümde aynı adlar yeniden sorulmaz; kayıt dosyadan da okunur
        model = SahteModel(terim=terim)
        dizi_analiz([("o2.srt", satirlar, "")], self.ayar, model, Sozluk(self.sozluk.yol), self.gunluk)
        self.assertEqual(model.terim_istekleri, [])

    def test_terim_tutarlilik_kontrolu(self):
        self.sozluk.elle_ekle({"Troy": "Truva", "sirens": "Sirenler"})
        satirlar = self._satirlar(["Eight years since Troy fell.", "Beware the sirens."])

        def kural(cagri, satirlar_):
            for s in satirlar_:
                s["text"] = ("Troy'un düşüşünden beri sekiz yıl." if cagri == 1 and "Troy" in s["text"]
                             else s["text"].replace("TR Eight years since Troy fell.", "Truva'nın düşüşünden beri sekiz yıl.")
                             .replace("TR Beware the sirens.", "Sirenlere dikkat et."))    # küçük harfli terim de denetlenir
            return satirlar_

        model = SahteModel(kural)
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "o.srt")
        self.assertIn("Truva", sonuc[0])
        self.assertEqual(model.cagri, 2)                       # yalnızca hatalı satır yeniden istendi
        self.assertEqual(len([x for x in model.gidenler[1]["lines"] if not x.get("skip")]), 1)

    def test_terim_turkce_disinda_i_harfiyle_kacmis_sayilmaz(self):
        """Hedef dil Türkçe değilken I -> ı çevrilmez: cümle başındaki "Imps" ve büyük harfli "TITAN" sözlükteki
        "imp" ve "Titan"dır, satır terim yüzünden yeniden sorulmaz. Türkçede "TITAN" terimi karşılamaz ("TİTAN")."""
        self.sozluk.elle_ekle({"小鬼": "imp", "巨人": "Titan"})
        satirlar = self._satirlar(["小鬼が来た", "巨人だ"])
        for dil, istek in (("en", 1), ("tr", 2)):
            model = SahteModel(lambda _, s: [dict(x, text={1: "Imps are coming.", 2: "TITAN!"}[x["id"]]) for x in s])
            ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "ceviri": {"hedef_dil": dil}})
            sonuc = Cevirmen(ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "o.srt")
            self.assertEqual((model.cagri, sonuc[1]), (istek, "TITAN!"), dil)

    def test_oldugu_gibi_kalan_terim_de_denetlenir(self):
        # "Master Tolen" tabloya göre aynen kalır; model "Usta Tolen" yazarsa satır yeniden istenir.
        self.sozluk.elle_ekle({"Master Tolen": "Master Tolen"})
        satirlar = self._satirlar(["Master Tolen, you sent for me?", "Sit down."])

        def kural(cagri, satirlar_):
            for s in satirlar_:
                if "Master Tolen" in s["text"]:
                    s["text"] = "Usta Tolen, beni mi çağırdınız?" if cagri == 1 else "Master Tolen, beni mi çağırdınız?"
            return satirlar_

        model = SahteModel(kural)
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "o.srt")
        self.assertIn("Master Tolen", sonuc[0])
        self.assertEqual(model.cagri, 2)

    def test_bozuk_etiket_ve_sarki_kurali(self):
        m = self.ayar["modlar"]
        self.assertEqual(temizlik.son_temizlik("<i><iBir eşim var mı?</i>", m), "<i>Bir eşim var mı?</i>")
        self.assertEqual(temizlik.son_temizlik("<i><i>Selam</i></i>", m), "<i>Selam</i>")
        self.assertIn("ONLY the lines that contain ♪", istem_olustur(self.ayar))
        self.assertEqual(temizlik.tr_kucuk("KIYAMET DAĞI İÇİN"), "kıyamet dağı için")

    def test_yapim_adi_ve_ayri_sozluk(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ceviri", os.path.join(os.path.dirname(__file__), "ceviri.py"))
        c = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(c)
        adlar = {f: c.sozluk_anahtari(c.yapim_adi(f)) for f in (
            "Spy.x.Family.S01E13.JAPANESE.WEBRip.NF.en.srt", "SPY×FAMILY - 【MISSION：1】 x.srt",
            "Doctor Who - 10x02 - Smile.AMZN.WEBRip-NTb.srt", "The.Odyssey.2026.MULTi.VFQ.srt",
            os.path.join("Yüzüklerin Efendisi", "lotr.srt"))}
        self.assertEqual(list(adlar.values()), ["spyxfamily", "spyxfamily", "doctorwho", "theodyssey",
                                                "yüzüklerinefendisi"])

    def test_varsayilanlar_tireyi_korur_an8_siler(self):
        m = self.ayar["modlar"]
        self.assertEqual((m["konusmaci_tiresi"], m["ustte_gosterme"]), ("korunsun", "sil"))
        self.assertEqual(temizlik.son_temizlik("- Selam.\n- Merhaba.", m), "- Selam.\n- Merhaba.")
        self.assertEqual(temizlik.son_temizlik("- Selam.\n- Merhaba.", dict(m, konusmaci_tiresi="sil")), "Selam.\nMerhaba.")

    def test_sahne_ozeti_gider(self):
        self.sozluk.sahneler = {"o.srt": [[1, 2, "Trojans debate the horse on the beach."], [3, 3, "Later."]]}
        model = SahteModel()
        Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(
            self._satirlar(["What does it mean?", "Common gods."]), "o.srt")
        self.assertEqual(model.gidenler[0]["scene"], [{"ids": "1-2", "summary": "Trojans debate the horse on the beach."}])

    def test_kisa_sahne_katilir_ve_adlar_sozluge_uyar(self):
        """2 satırlık sahne özetleri parçalı ve yanlıştı (LOTR 517-518): komşu sahneye katılır. Özet ve hitap "Lloyd"
        derken sözlük "Loid" diyordu (SPY x FAMILY): adlar sözlüğün yazımıyla yazılır."""
        from defta.analiz import adlari_sozluge_uydur, kisa_sahneleri_birlestir
        sahneler = [{"from": 1, "to": 2, "summary": "A"}, {"from": 3, "to": 40, "summary": "B"},
                    {"from": 41, "to": 42, "summary": "C"}, {"from": 43, "to": 80, "summary": "D"}, {"from": "x"}]
        self.assertEqual(kisa_sahneleri_birlestir(sahneler), [{"from": 1, "to": 42, "summary": "B"},
                                                              {"from": 43, "to": 80, "summary": "D"}])
        self.sozluk.girdi_yaz("ロイド", tur="name", hedef="Loid")
        karakterler, hitaplar, sahne = adlari_sozluge_uydur(
            self.sozluk, [{"name": "Lloyd", "source": "ロイド", "note": "Lloyd: a spy"}, {"name": "Anya"}],
            [{"from": "Lloyd", "to": "Anya", "form": "informal"}],
            {"a": [{"from": 1, "to": 9, "summary": "Lloyd adopts Anya; Lloydson is not him."}]})
        self.assertEqual(karakterler[0], {"name": "Loid", "source": "ロイド", "note": "Loid: a spy"})
        self.assertEqual(hitaplar[0]["from"], "Loid")
        self.assertEqual(sahne["a"][0]["summary"], "Loid adopts Anya; Lloydson is not him.")

    def test_kisa_karakter_adi_tam_ada_cevrilmez(self):
        """Kodi 1.0.10, SPY x FAMILY S01E02 (sıfırdan sözlük): kaynak tam ad ("ヨル・ブライア" -> "Yor Briar") olunca
        hitap tablosu tam adla yazılıyor, etiketteki "ヨル" tabloya bağlanamıyor, "says_to" 19 satırdan 1'e düşüyordu."""
        from defta.analiz import adlari_sozluge_uydur
        self.sozluk.girdi_yaz("ヨル・ブライア", tur="name", hedef="Yor Briar")
        self.sozluk.girdi_yaz("ロイド・フォージャー", tur="name", hedef="Loid Forger")
        self.sozluk.girdi_yaz("カミラ", tur="name", hedef="Camilla")
        karakterler, hitaplar, _ = adlari_sozluge_uydur(
            self.sozluk, [{"name": "Yor", "source": "ヨル・ブライア", "note": "Yor: clerk"},
                          {"name": "Lloyd", "source": "ロイド・フォージャー", "note": "Lloyd: a spy"},
                          {"name": "Camilla", "source": "カミラ"}],
            [{"from": "Yor", "to": "Lloyd", "form": "formal"}, {"from": "Camilla", "to": "Yor", "form": "formal"}], {})
        self.assertEqual([k["name"] for k in karakterler], ["Yor", "Loid", "Camilla"])
        self.assertEqual(hitaplar, [{"from": "Yor", "to": "Loid", "form": "formal"},
                                    {"from": "Camilla", "to": "Yor", "form": "formal"}])

    def test_iliski_oylarinda_ad_yazimi_birlesir(self):
        """Gerçek test 38: iki yanıt "Lloyd", biri "Loid" dedi; karakter listesi "Loid", tablo "Lloyd" oldu, konuşmacı
        etiketi tabloya bağlanamadı (says_to 18 yerine 7 satır)."""
        from defta.hitap import oylari_birlestir
        def yanit(ad):
            return {"characters": [{"name": ad, "source": "ロイド・フォージャー"}, {"name": "Yor", "source": "ヨル・ブライア"}],
                    "address": [{"from": ad, "to": "Yor", "form": "informal"}, {"from": "Yor", "to": ad, "form": "formal"}]}
        sonuc = oylari_birlestir([yanit("Loid"), yanit("Lloyd"), yanit("Lloyd")])
        self.assertEqual([k["name"] for k in sonuc["characters"]], ["Loid", "Yor"])
        self.assertEqual([(h["from"], h["to"], h["form"]) for h in sonuc["address"]],
                         [("Loid", "Yor", "informal"), ("Yor", "Loid", "formal")])

    def test_etiketteki_tek_ad_tam_adli_karaktere_baglanir(self):
        """Tam adla kurulmuş sözlük (1.0.10 ile kurulanlar) da çalışsın: "ヨル" -> "Yor Briar"; iki karakterin ortak
        soyadı kimseye bağlanmaz."""
        self.sozluk.karakterler = {"Yor Briar": "", "Yuri Briar": "", "Loid Forger": "", "Anya Forger": "", "Camilla": ""}
        self.assertEqual(self.sozluk.latin_ad("ヨル"), "Yor Briar")
        self.assertEqual(self.sozluk.latin_ad("ロイド"), "Loid Forger")
        self.assertEqual(self.sozluk.latin_ad("アーニャ"), "Anya Forger")
        self.assertIsNone(self.sozluk.latin_ad("ブライア"))
        self.assertIsNone(self.sozluk.latin_ad("フォージャー"))

    def test_dengesiz_dagitim_yeniden_bolunur(self):
        satirlar = [srt.Satir(1, 0, 3000, "Then why do you entertain suitors"),
                    srt.Satir(2, 3000, 6000, "in his house night after night?"),
                    srt.Satir(3, 9000, 11000, "I do what I can.")]
        sonuc = {0: "O zaman", 1: "talipleri neden her gece bu evde ağırlıyorsun, söyle bana?", 2: "Elimden geleni yapıyorum."}
        self.assertEqual(dengele(satirlar, sonuc, {0, 1, 2}), 1)
        self.assertGreater(len(sonuc[0]), 20)
        self.assertEqual(" ".join([sonuc[0], sonuc[1]]),
                         "O zaman talipleri neden her gece bu evde ağırlıyorsun, söyle bana?")
        self.assertEqual(sonuc[2], "Elimden geleni yapıyorum.")
        dengeli = {0: "O zaman talipleri neden", 1: "her gece evinde ağırlıyorsun?", 2: "x"}
        onceki = dict(dengeli)
        self.assertEqual(dengele(satirlar, dengeli, {0, 1, 2}), 0)
        self.assertEqual(dengeli, onceki)
        italik = {0: "<i>Ama</i>", 1: "<i>onun bunu yaptığını öğrenirse beni asla affetmeyecek.</i>", 2: "x"}
        dengele(satirlar, italik, {0, 1, 2})
        self.assertTrue(italik[0].startswith("<i>") and italik[0].endswith("</i>") and len(italik[0]) > 15)

    def test_not_ve_konusmaci_ayrilir(self):
        a = not_ve_konusmaci_ayir
        self.assertEqual(a("（外交官）\n大使館までは"), ("大使館までは", [], ["外交官"]))
        self.assertEqual(a("（カレン）えっ…\n（ロバート）それじゃ"), ("えっ…\nそれじゃ", [], ["カレン", "ロバート"]))
        self.assertEqual(a("（拍手）"), ("", ["拍手"], []))
        self.assertEqual(a("(sighs)\nI'm tired."), ("I'm tired.", ["sighs"], []))
        self.assertEqual(a("Tangado haid! (Hold positions!)")[0], "Tangado haid! (Hold positions!)")
        self.assertEqual(a("(Hold positions!)")[0], "(Hold positions!)")
        # atılan not / etiket satırında açılan italik yarım kalmaz (yarım etiket çeviriyi italik denetiminden döndürür)
        self.assertEqual(a("<i>(SIGHS)\nI can't do this.</i>"), ("<i>I can't do this.</i>", ["SIGHS"], []))
        self.assertEqual(a("<i>I can't do this.\n[door slams]</i>")[0], "<i>I can't do this.</i>")
        self.assertEqual(a("<i>（楓）\nはい。</i>"), ("<i>はい。</i>", [], ["楓"]))
        self.assertEqual(a("<i>(SIGHS)</i>\n<i>Fine.</i>")[0], "<i>Fine.</i>")
        satirlar = self._satirlar(["GUARD: Halt!", "[door slams]", "- Hear that?\n- [whispering] Don't move."])
        model = SahteModel()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk).cevir(satirlar, "x.srt")
        giden = model.gidenler[0]["lines"]
        self.assertEqual(giden[0], {"id": 1, "dur": 1.5, "text": "Halt!", "speaker": "GUARD"})
        self.assertEqual(giden[1], {"id": 2, "text": "[door slams]", "skip": True})
        self.assertEqual(giden[2]["note"], "whispering")
        self.assertEqual(sonuc[1], "")

    def test_dengele_ayri_cumleleri_karistirmaz(self):
        # Japonca kaynakta nokta yok: ayrı konuşmalar bölünmüş cümle sanılmamalı
        satirlar = [srt.Satir(1, 0, 900, "あっ"), srt.Satir(2, 950, 3500, "ユーリ だめ さっきのは冗談で"),
                    srt.Satir(3, 3600, 6000, "Then why do you entertain suitors"),
                    srt.Satir(4, 6000, 8500, "in his house night after night?")]
        sonuc = {0: "Ah!", 1: "Yuri, hayır! Az önceki şakaydı...", 2: "O zaman neden talipleri,",
                 3: "her gece bu evde ağırlayıp duruyorsun peki?"}
        onceki = dict(sonuc)
        self.assertEqual(dengele(satirlar, sonuc, {0, 1, 2, 3}), 0)
        self.assertEqual(sonuc, onceki)
        # İngilizce kaynakta çeviri ilk parçada cümleyi bitirmişse yine dokunulmaz
        s2 = {0: "x", 1: "y", 2: "Vay.", 3: "Ölçülerinizi alacağım küçük hanım, buyurun lütfen."}
        self.assertEqual(dengele(satirlar, s2, {0, 1, 2, 3}), 0)

    def test_yeniden_denemede_sorun_bildirilir_ve_cumle_ipucu_gider(self):
        satirlar = self._satirlar(["If the council hears that we opened it", "without the priestess, we hang.",
                                   "Hello there, Yor-san!"])
        satirlar[1].bas = satirlar[0].bit          # bitişik: bölünmüş cümle

        def kural(cagri, satirlar_):
            if cagri == 1:
                for s in satirlar_:
                    s["text"] = s["text"].replace("⟦Yor-san⟧", "Yor")
            return satirlar_

        model = SahteModel(kural)
        Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        ilk = model.gidenler[0]["lines"]
        self.assertEqual([x["id"] for x in ilk], [1, 3])            # 2. altyazı 1.'nin içinde gider
        self.assertEqual(ilk[0]["parts"], 2)
        self.assertIn("without the priestess", ilk[0]["text"])
        self.assertNotIn("parts", ilk[1])
        ikinci = model.gidenler[1]
        self.assertEqual([x["id"] for x in ikinci["fix"]], [3])
        self.assertIn("protected", ikinci["fix"][0]["problem"])
        self.assertEqual(model.dusunme, [None, None])

    def test_dusunme_bosken_yeniden_denemede_de_gonderilmez(self):
        """[model] dusunme_seviyesi = "": alan hiç gönderilmez, yeniden denemede de (alanı tanımayan sunucu isteği
        reddeder). "none" iken yeniden deneme "low" ile sorulur."""
        for seviye, beklenen in (("", [None, None]), ("none", [None, "low"])):
            model = SahteModel(lambda cagri, s: [x for x in s if not (cagri == 1 and x["id"] == 2)])
            ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1, "dusunme_seviyesi": seviye}})
            Cevirmen(ayar, model, "S", self.gunluk).cevir(self._satirlar(["One.", "Two.", "Three."]), "x.srt")
            self.assertEqual(model.dusunme, beklenen, seviye)

    def test_eksik_satir_tek_basina_yeniden_istenir(self):
        satirlar = self._satirlar(["One.", "Two.", "Three.", "Four."])

        def kural(cagri, satirlar_):
            return [s for s in satirlar_ if not (cagri == 1 and s["id"] == 3)]

        model = SahteModel(kural)
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk).cevir(satirlar, "x.srt")
        self.assertEqual(sonuc[2], "TR Three.")
        self.assertEqual([x["id"] for x in model.gidenler[1]["lines"] if not x.get("skip")], [3])

    def test_supheli_satirlar_nedeniyle_sorulur(self):
        self.sozluk.hitap = {"Sam -> Frodo": "resmi"}
        self.sozluk.unvanlar = ["Mr"]
        self.sozluk.sahneler = {"x.srt": [[1, 5, "Sam finds Frodo by the river."]]}
        satirlar = self._satirlar(["I thought I'd lost you, Mr. Frodo.", "You are safe now.", "Damn it!",
                                   "We saved some for you, Mr. Frodo.", "Come with us."])
        ilk = {1: "Seni kaybettim sandım, Bay Frodo.", 2: "Artık güvendesiniz.", 3: "Kahretsin!",
               4: "Size de ayırdık, Frodo.", 5: "Bizimle gelin."}
        ikinci = {1: "Sizi kaybettim sandım, Bay Frodo.", 3: "Hay aksi!", 4: "Size de ayırdık, Bay Frodo."}

        class M:
            def __init__(self):
                self.gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                kaynak = ikinci if "fix" in veri else ilk
                return json.dumps({"lines": [{"id": s["id"], "text": kaynak[s["id"]]}
                                             for s in veri["lines"] if not s.get("skip")]}, ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk.gorunum(self.ayar)).cevir(satirlar, "x.srt")
        self.assertEqual(len(model.gidenler), 2)                       # tek ek istek
        self.assertEqual(model.gidenler[0]["address"], ["Sam -> Frodo: formal"])
        sorulan = {f["id"]: f["problem"] for f in model.gidenler[1]["fix"]}
        self.assertEqual(sorted(sorulan), [3])                         # klişe; düşen unvanı script ekledi
        self.assertIn("cliché", sorulan[3])
        self.assertEqual(sonuc[0], "Seni kaybettim sandım, Bay Frodo.")
        self.assertEqual(sonuc[2], "Hay aksi!")
        self.assertEqual(sonuc[3], "Size de ayırdık, Bay Frodo.")
        self.assertEqual(sonuc[1], "Artık güvendesiniz.")              # sorulmayan satıra dokunulmaz
        kapali = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "kontrol": {"supheli_satir_kontrolu": False}})
        m2 = M()
        Cevirmen(kapali, m2, "S", self.gunluk, self.sozluk.gorunum(kapali)).cevir(satirlar, "x.srt")
        self.assertEqual(len(m2.gidenler), 1)

    def test_unvanla_hitap(self):
        satirlar = self._satirlar(["Good night, Mr. Frodo.", "Mr. Frodo? He's going cold.", "Lord Elrond is here.",
                                   "Kenji-senpai, Sensei is waiting."])
        ilk = {1: "İyi geceler sana, Bay Frodo.", 2: "Bay Frodo? Buz kesiyor.", 3: "Lord Elrond burada.",
               4: "Kenji-senpai, Sensei bekliyor."}

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                return json.dumps({"lines": [{"id": x["id"], "text": "Size iyi geceler, Bay Frodo." if "fix" in veri
                                              else ilk[x["id"]]} for x in veri["lines"] if not x.get("skip")]},
                                  ensure_ascii=False)

        model = M()
        self.sozluk.unvanlar = ["Mr", "Lord"]
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk.gorunum(self.ayar)).cevir(satirlar, "x.srt")
        self.assertEqual(len(model.gidenler), 1)                        # unvanla seslenme yeniden sordurmaz
        self.assertEqual(sonuc[0], "İyi geceler sana, Bay Frodo.")
        self.assertEqual(sonuc[3], "Kenji-senpai, Sensei bekliyor.")   # Sensei ve -senpai olduğu gibi kalır

    def test_italik_gecisleri(self):
        from defta.analiz import italik_gecisleri
        parca = [{"id": n, "text": t} for n, t in enumerate(
            ["<i>We lay silent.</i>", "<i>For two tides.</i>", "<i>Nobody moved.</i>", "A gift.", "For Athena.",
             "Common gods.", "<i>one stray italic</i>", "Burn it!", "Take it inside."], 1)]
        self.assertEqual(italik_gecisleri(parca), [4])          # tek satırlık italik geçiş sayılmaz

    def test_klise_japonca_kaynakta_da_sorulur(self):
        satirlar = self._satirlar(["くそ、気が緩んでるぞ"])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                return json.dumps({"lines": [{"id": 1, "text": "Hay aksi, gevşiyorsun." if "fix" in veri
                                              else "Kahretsin, gevşiyorsun."}]}, ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertIn("cliché", model.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Hay aksi, gevşiyorsun.")

    def test_yer_tutucu_ve_kopuk_harf_sorulur(self):
        """LOTR 462: "Seni ...'da bekliyor olacağım" (yer sonraki altyazıda), 1117: "dillere s destan": model sözcüğün
        yerine yer tutucu bırakmış ya da sözcüğü bozmuş. Kalıpla yakalanır, satır nedeniyle yeniden sorulur."""
        satirlar = self._satirlar(["I'll be waiting for you...", "the fabled hospitality of the dwarves."])
        satirlar[1].bas = satirlar[0].bit + 3000                  # ayrı cümleler

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                if "fix" in veri:
                    return json.dumps({"lines": [{"id": 1, "text": "Seni handa bekliyor olacağım..."},
                                                 {"id": 2, "text": "cücelerin dillere destan misafirperverliği."}]},
                                      ensure_ascii=False)
                return json.dumps({"lines": [{"id": 1, "text": "Seni ...'da bekliyor olacağım"},
                                             {"id": 2, "text": "cücelerin dillere s destan misafirperverliği."}]},
                                  ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        sorunlar = {f["id"]: f["problem"] for f in model.gidenler[1]["fix"]}
        self.assertIn("in place of a word", sorunlar[1])
        self.assertIn('"s" is a stray letter', sorunlar[2])
        self.assertEqual(sonuc, {0: "Seni handa bekliyor olacağım...", 1: "cücelerin dillere destan misafirperverliği."})
        from defta.denetim import TEK_HARF
        for temiz in ("B planı", "5 m uzakta", "b... bilmiyorum", "o ve e", "Ş-şey"):
            self.assertIsNone(TEK_HARF["tr"].search(temiz), temiz)

    def test_not_olarak_silinen_pay_tire_sayisina_girmez(self):
        satirlar = self._satirlar(["- [laughs]\n- Wow."])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                self.gidenler.append(json.loads(kullanici))
                return json.dumps({"lines": [{"id": 1, "text": "- Vay be."}]}, ensure_ascii=False)
        m = M()
        self.assertEqual(Cevirmen(self.ayar, m, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")[0], "- Vay be.")
        self.assertEqual(len(m.gidenler), 1)                           # "tire tutmuyor" diye yeniden sorulmaz

    def test_bir_satiri_gecmeyen_partide_otekiler_supheden_gecer(self):
        satirlar = self._satirlar(["Hello there.", "Damn it!"])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                if "fix" in veri and any("cliché" in f["problem"] for f in veri["fix"]):
                    return json.dumps({"lines": [{"id": 2, "text": "Hay aksi!"}]}, ensure_ascii=False)
                return json.dumps({"lines": [{"id": x["id"], "text": "" if x["id"] == 1 else "Kahretsin!"}
                                             for x in veri["lines"] if not x.get("skip")]}, ensure_ascii=False)
        m = M()
        sonuc = Cevirmen(self.ayar, m, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(sonuc[1], "Hay aksi!")

    def test_bipli_pekistirici_nitelik_olursa_sorulur_kufursuz_ceviri_kabul(self):
        """Bip'li küfür yayında duyulmaz: küfürsüz çeviri kabul edilir (küfür aranmaz); pekiştiriciyi nitelik gibi
        çeviren ("bok gibi çiğ") yanıt yeniden sorulur."""
        satirlar = self._satirlar(["It's [bleep] raw."])

        def model_(ilk, ikinci):
            class M:
                gidenler = []

                def iste(self, sistem, kullanici, dusunme=None):
                    veri = json.loads(kullanici)
                    self.gidenler.append(veri)
                    return json.dumps({"lines": [{"id": 1, "text": ikinci if "fix" in veri else ilk}]},
                                      ensure_ascii=False)
            return M()

        m = model_("Bildiğin çiğ.", "x")
        self.assertEqual(Cevirmen(self.ayar, m, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")[0], "Bildiğin çiğ.")
        self.assertEqual(len(m.gidenler), 1)
        m = model_("Bok gibi çiğ.", "Resmen çiğ bu.")
        sonuc = Cevirmen(self.ayar, m, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertIn("intensifier", m.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Resmen çiğ bu.")

    def test_her_altyaziya_buyuk_harfle_baslayan_dosyada_cumle_birlesir(self):
        from defta import cumle
        satirlar = self._satirlar(["It's no secret", "That over the years,", "Things got hard."] * 30)
        for n, s in enumerate(satirlar):
            s.bas, s.bit = n * 2000, n * 2000 + 1900
        self.assertTrue(cumle.buyuk_harf_duzeni(satirlar))
        self.assertIn([0, 1, 2], cumle.cumle_gruplari(satirlar, set(range(len(satirlar)))))
        self.assertFalse(cumle.buyuk_harf_duzeni(satirlar[:30]))           # az örnek: şiir dizeleri olabilir

    def test_japonca_ses_notu_konusmaci_sayilmaz(self):
        """（せきばらい） / （黄昏）失敬: konuşan 黄昏'dur, ilk satır nottur (gerçek test 10'da "失敬" silindi).
        İçinde 「」 olan müzik notu da nottur."""
        from defta.kaynak import not_ve_konusmaci_ayir
        self.assertEqual(not_ve_konusmaci_ayir("（せきばらい）\n（黄昏）失敬"), ("失敬", ["せきばらい"], ["黄昏"]))
        self.assertEqual(not_ve_konusmaci_ayir("（「SPY(スパイ) WARS(ウォーズ)」のテーマ）")[0], "")
        self.assertEqual(not_ve_konusmaci_ayir("（外交官）\n大使館までは どれくらいかね")[2], ["外交官"])
        self.assertEqual(not_ve_konusmaci_ayir("（ロイド）\n（ここは慎重に行かねば。…）"),
                         ("（ここは慎重に行かねば。…）", [], ["ロイド"]))

    def test_ortusen_ya_da_baska_etiketli_altyazi_cumleye_katilmaz(self):
        """SPY x FAMILY 447: Anya'nın "—" ile kesilen sözü ile aynı anda geçen Loid'in iç sesi (süreler örtüşüyor) tek
        cümle sayılıyordu; Loid'in sözü kayboldu. Örtüşen ya da başka etiketle başlayan altyazı cümleyi sürdürmez."""
        from defta import cumle
        satirlar = [srt.Satir(1, 0, 2000, "（アーニャ）\nアーニャ いい子にするから—"), srt.Satir(2, 700, 2800, "どうかしてしまったな")]
        self.assertFalse(cumle.cumle_suruyor_mu(satirlar, 0))
        satirlar = [srt.Satir(1, 0, 2000, "（ロイド）何としても—"), srt.Satir(2, 2050, 3000, "その計画を突き止めねば")]
        self.assertTrue(cumle.cumle_suruyor_mu(satirlar, 0))                # aynı kişi, ardışık: sürer
        satirlar = [srt.Satir(1, 0, 2000, "（ロイド）何としても—"), srt.Satir(2, 2050, 3000, "（ヨル）はい")]
        self.assertFalse(cumle.cumle_suruyor_mu(satirlar, 0))               # başka etiket
        satirlar = [srt.Satir(1, 0, 2000, "We went to the"), srt.Satir(2, 2050, 3000, "Frodo: market today.")]
        self.assertFalse(cumle.cumle_suruyor_mu(satirlar, 0))

    def test_gercek_din_ve_halk_adi_ciktida_buyuk_harfle(self):
        self.assertEqual(temizlik.tr_buyuk_harf("müslümanlar ve hristiyan altını, arapsaçı"),
                         "Müslümanlar ve Hristiyan altını, arapsaçı")
        self.assertEqual(temizlik.tr_buyuk_harf("tanrı'nın huzurunda, eski tanrılar"), "Tanrı'nın huzurunda, eski tanrılar")
        self.assertEqual(temizlik.tr_buyuk_harf("islami bir araba"), "İslami bir araba")

    def test_ciktida_kalan_kana_okunusla_yazilir(self):
        self.assertEqual(temizlik.kana_latinle("アウフリーデ\nメーア・エ・ヴォルケン"), "Aufuriide\nMeea e voruken")
        self.assertEqual(temizlik.kana_latinle("Hadi!"), "Hadi!")

    def test_japonca_kaynakta_agir_kufur_sorulur(self):
        satirlar = self._satirlar(["やられたな"])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                return json.dumps({"lines": [{"id": 1, "text": "Beni fena faka bastırdı." if "fix" in veri
                                              else "Ağzıma sıçtı."}]}, ensure_ascii=False)
        m = M()
        sonuc = Cevirmen(self.ayar, m, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertIn("too strong for a Japanese source", m.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Beni fena faka bastırdı.")

    def test_japonca_yayin_bicimi_duzelir(self):
        """Yarım genişlikli etiket ve kana tanınır; 《》 iç ses ve ≪ ekran dışı ses italik olur."""
        from defta.kaynak import not_ve_konusmaci_ayir
        hazir = lambda m: temizlik.kaynak_hazirla(m)[0]
        self.assertEqual(not_ve_konusmaci_ayir(hazir("(ワポル)おお…｡"))[0::2], ("おお…。", ["ワポル"]))
        self.assertEqual(not_ve_konusmaci_ayir(hazir("[ｽﾋﾟｰｶ](ﾓﾙｶﾞﾝｽﾞ)おう"))[2], ["モルガンズ"])
        self.assertEqual(not_ve_konusmaci_ayir(hazir("≪(水夫)次は これか？"))[0::2], ("<i>次は これか？</i>", ["水夫"]))
        self.assertEqual(hazir("《世界政府の闇…\nあんなものを見ちまった以上…》"),
                         "<i>世界政府の闇…</i>\n<i>あんなものを見ちまった以上…</i>")
        self.assertEqual(hazir("あんなものを見ちまった以上…》"), "<i>あんなものを見ちまった以上…</i>")
        self.assertEqual(hazir("(Wapol) hi"), "(Wapol) hi")                  # Latin harfli parantez dokunulmaz
        self.assertEqual(hazir("(おなかの鳴る音)"), "(おなかの鳴る音)")          # ses notu etiket değildir

    def test_cok_kisa_ceviri_suphesi_dogu_asya_yazisinda_sorulmaz(self):
        """Kaynağa göre çok kısa çeviri eksik içerik şüphesiyle sorulur; ama Çince, Japonca, Korece çeviri eksiksizken
        de karakter sayısıyla kaynağın üçte birinden kısadır: orada sorulmaz."""
        satirlar = self._satirlar(["I should have told her about it a very long time ago, you know."])
        for dil, ceviri, istek in (("zh", "我早就应该告诉她这件事了。", 1), ("ja", "ずっと前に彼女に話すべきだった。", 1),
                                   ("ko", "진작 그녀에게 말했어야 했어.", 1), ("tr", "Söylemeliydim.", 2)):
            model = SahteModel(lambda _, s: [dict(x, text=ceviri) for x in s])
            ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1},
                                   "ceviri": {"hedef_dil": dil, "cikti_alfabesi": "latin" if dil == "tr" else "serbest"}})
            Cevirmen(ayar, model, "S", self.gunluk).cevir(satirlar, "x.srt")
            self.assertEqual(model.cagri, istek, dil)
            if istek == 2:
                self.assertIn("much shorter", model.gidenler[1]["fix"][0]["problem"])

    def test_bolunmus_cumle_tek_satir_gider_script_boler(self):
        satirlar = self._satirlar(["If I don't return", "by the time Telemachus comes of age,", "marry again.",
                                   "<i>We lay silent</i>", "<i>in the dark.</i>"])
        for a, b in ((0, 1), (1, 2), (3, 4)):
            satirlar[b].bas = satirlar[a].bit

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                yanit = {1: "Telemakhos reşit olana kadar | dönmezsem, | yeniden evlen.", 4: "Karanlıkta sessizce yattık."}
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        satir = model.gidenler[0]["lines"]
        self.assertEqual([(x["id"], x.get("parts")) for x in satir], [(1, 3), (4, 2)])
        self.assertEqual(satir[0]["text"], "If I don't return by the time Telemachus comes of age, marry again.")   # kesintisiz
        self.assertNotIn("<i>", satir[1]["text"])
        parcalar = [sonuc[i] for i in (0, 1, 2) if sonuc[i] != BIRLESTI]
        self.assertEqual(" ".join(parcalar), "Telemakhos reşit olana kadar dönmezsem, yeniden evlen.")
        # Tek sözcük kendi altyazısında bırakılmaz: öncesiyle birleşir, iki altyazının süresini kaplar.
        self.assertEqual([sonuc[i] for i in (0, 1, 2)],
                         ["Telemakhos reşit olana kadar dönmezsem,", BIRLESTI, "yeniden evlen."])
        self.assertEqual((sonuc[3], sonuc[4]), ("<i>Karanlıkta sessizce yattık.</i>", BIRLESTI))   # işaret yok: tek altyazı
        self.assertEqual(len(model.gidenler), 1)                       # ek tur yok

    def test_cevrilemeyen_bolunmus_cumlenin_altyazilari_aynen_kalir(self):
        satirlar = self._satirlar(["The keeper of the light", "has not been seen."])
        satirlar[1].bas = satirlar[0].bit

        class Reddeden:
            def iste(self, sistem, kullanici, dusunme=None):
                raise Ret("hayır")
        ayar = ayar_birlestir({"kontrol": {"deneme_sayisi": 1}})
        sonuc = Cevirmen(ayar, Reddeden(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual((sonuc[0], sonuc[1]), ("The keeper of the light", "has not been seen."))

    def test_uc_nokta_cumleyi_bitirmez_devam_kucuk_harf(self):
        satirlar = self._satirlar(["The ring perceived...", "...that its time had come."])
        satirlar[1].bas = satirlar[0].bit

        class M:
            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                return json.dumps({"lines": [{"id": x["id"], "text": "Yüzük sezdi... | Vaktinin geldiğini."}
                                             for x in veri["lines"]]}, ensure_ascii=False)
        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual((sonuc[0], sonuc[1]), ("Yüzük sezdi...", "vaktinin geldiğini."))

    def test_tirnakla_biten_altyaziyi_kucuk_harf_izlerse_cumle_surer(self):
        """Kapanan tırnak cümle sonu sayılıyordu: "…said" / "\"I need…sir\"" / "or \"…\"" / "neither…" ayrı gitti, model
        içeriği bir altyazı kaydırdı (gerçek test 26, American Gods 275-277). Büyük harfle başlayan altyazı ayrı kalır."""
        from defta.cumle import cumle_gruplari
        satirlar = self._satirlar(["Because you would have said", '"I need to take your drink, sir"',
                                   'or "I\'m gonna have to take that drink, sir,"', "neither of which happened.",
                                   'He said "Stop."', "Then he left.", "So don't worry."])
        for k in range(1, len(satirlar)):
            satirlar[k].bas = satirlar[k - 1].bit
        self.assertEqual(cumle_gruplari(satirlar, set(range(7))), [[0, 1, 2, 3]])

    def test_harf_buyuklugu_farkli_yazim_tek_girdi_olur(self):
        """Adları küçük harfle yazan kaynakta keşif aynı adı iki harfle veriyordu ("Rivendell" / "rivendell"): iki ayrı
        girdi oluyor, karşılıkları çelişiyordu (gerçek test 28 ve 33, LOTR). Kaynakta çok geçen yazım kalır."""
        from defta.terim import harf_ciftlerini_birlestir
        metin = "to rivendell, mr. frodo. rivendell! Saruman came. saruman and Saruman. The Enemy."
        self.assertEqual(harf_ciftlerini_birlestir(["Rivendell", "rivendell", "saruman", "Saruman", "Enemy", "wraith"], metin),
                         ["rivendell", "Saruman", "Enemy", "wraith"])
        sozluk = Sozluk(os.path.join(self.tmp.name, "h.json"))
        sozluk.girdi_ekle("Rivendell", "name", "Rivendell")
        sozluk.girdi_ekle("rivendell", "name", "Rivendell")              # olduğu gibi kalan adın öteki harfi: girmez
        sozluk.girdi_ekle("the dark lord", "epithet", "Karanlıklar Efendisi")
        sozluk.girdi_ekle("Dark Lord", "epithet", "Karanlık Lord")
        self.assertEqual(sorted(sozluk.girdiler), ["Rivendell", "the dark lord"])
        self.assertEqual(sozluk.tutarsizliklar(), [])

    def test_sozluk_celiskileri_bildirilir(self):
        """Sözlüğün kendi içindeki çelişki koşu günlüğüne yazılır: aynı sözün iki girdisi, kısa terimin karşılığını
        taşımayan uzun terim. Harf işareti farkı ("Udun" / "Udûn") çelişki sayılmaz."""
        sozluk = Sozluk(os.path.join(self.tmp.name, "c.json"))
        sozluk.girdi_yaz("Ringwraiths", tur="people", hedef="yüzük tayfı")
        sozluk.girdi_yaz("ringwraiths", tur="people", hedef="ringwraiths")
        sozluk.girdi_yaz("White Tower", tur="descriptive", hedef="Ak Kule")
        sozluk.girdi_yaz("white tower of ecthelion", tur="descriptive", hedef="Ecthelion'un Beyaz Kulesi")
        sozluk.girdi_yaz("mount doom", tur="descriptive", hedef="Kıyamet Dağı")
        sozluk.girdi_yaz("the fires of mount doom", tur="descriptive", hedef="Kıyamet Dağı'nın ateşleri")
        sozluk.girdi_yaz("udun", tur="name", hedef="Udun")
        sozluk.girdi_yaz("flame of udun", tur="descriptive", hedef="Udûn'un Alevi")
        sorunlar = sozluk.tutarsizliklar()
        self.assertEqual(len(sorunlar), 2)
        self.assertIn("Ringwraiths", sorunlar[0])
        self.assertIn("White Tower", sorunlar[1])

    def test_gercek_dunyadaki_isletme_ve_yer_adi_kaynaktaki_gibi_kalir(self):
        """Gerçek dünyada geçen eserde uydurma yer, işletme, marka adı çevrilmez (American Gods: Eagle Point bir koşuda
        kalıyor, ötekinde "Kartal Noktası" oluyordu; Buffalo Burger dört ayrı karşılık). Kararı oyların çoğu verir;
        uydurma dünyadaki anlamlı ad (Bag End) ve gerçek yer (Chicago) bu kuraldan etkilenmez."""
        from defta.terim import isletme_adi, _cogunluk, hedef_belirle
        self.assertTrue(isletme_adi({"t": "name", "biz": True}, "Eagle Point"))
        self.assertTrue(isletme_adi({"t": "descriptive", "biz": True}, "Buffalo Burger"))
        self.assertFalse(isletme_adi({"t": "name"}, "Bag End"))
        self.assertFalse(isletme_adi({"t": "name", "biz": True, "real": True}, "Chicago"))
        self.assertFalse(isletme_adi({"t": "term", "biz": True}, "the gift"))
        oylar = [{"items": [{"i": 1, "t": "descriptive", "tr": "Kartal Noktası", "biz": True}, {"i": 2, "t": "name", "biz": True}]},
                 {"items": [{"i": 1, "t": "name", "biz": True}, {"i": 2, "t": "name"}]},
                 {"items": [{"i": 1, "t": "descriptive", "tr": "Kartal Noktası"}, {"i": 2, "t": "name"}]}]
        karar = {c["i"]: c for c in _cogunluk(oylar, {1: "Eagle Point", 2: "Bag End"})}
        self.assertTrue(karar[1].get("biz"))
        self.assertNotIn("biz", karar[2])
        self.assertEqual(hedef_belirle("Eagle Point", {**karar[1], "kaynak": True}, True),
                         ("descriptive", "Eagle Point"))

    def test_tekrar_eden_latin_satir_cifti_bulunur(self):
        """Latin harfli kaynakta sözleri uzak satırda yeniden geçen satır çifti eşitlemeye aday olur (gerçek test 26,
        American Gods 313/321); 3 satırdan yakın ya da birebir aynı satırlar aday olmaz. Sıradan sözleri paylaşan çift de
        aday olabilir ("color" çifti): onu bırakmak modelin işi."""
        from defta.terim import tekrar_eden_latin_satirlar
        m = {i: "Okay." for i in range(40)}
        m.update({3: "What should I call you if I were so inclined?", 11: "And if I was inclined, what might I call you?",
                  5: "Look at the color of that inside.", 30: "Look at the damn color of the bacon.",
                  20: "Private Kimmel, what is my view of gambling in the barracks?",
                  22: "Private Kimmel, what is my view of gambling...",
                  25: "We will never surrender to them today.", 35: "We will never surrender to them today."})
        bulunan = tekrar_eden_latin_satirlar(m, ["Kimmel"])
        self.assertEqual(sorted(bulunan.values()), [[3, 11], [5, 30]])
        self.assertEqual(set(next(k for k, v in bulunan.items() if v == [3, 11]).split()), {"what", "i", "call", "you", "if", "inclined"})

    def test_kesme_isareti_ekrana_cikmaz_ve_devam_kucuk_harf(self):
        satirlar = self._satirlar(["No one knows, but the elders\nwant Ithaca to be prepared.",
                                   "If the council hears", "that we opened it, we hang.", "He saw Zeus", "in the storm."])
        satirlar[2].bas = satirlar[1].bit
        satirlar[4].bas = satirlar[3].bit
        yanit = {1: "Kimse bilmiyor ama ihtiyarlar | İthaka hazır olsun istiyor.",
                 2: "Konsey duyarsa | Açtığımızı, asılırız.", 4: "Fırtınada | Zeus'u gördü."}

        class M:
            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(sonuc[0], "Kimse bilmiyor ama ihtiyarlar\nİthaka hazır olsun istiyor.")
        self.assertEqual([sonuc[1], sonuc[2]], ["Konsey duyarsa", "açtığımızı, asılırız."])
        self.assertEqual(sonuc[4], "Zeus'u gördü.")                    # özel ad büyük kalır
        self.assertNotIn("|", temizlik.son_temizlik("a | b", self.ayar["modlar"]))

    def test_japonca_surdurme_cizgisi_cumleyi_birlestirir(self):
        satirlar = self._satirlar(["（ヘンダーソン）地球上で\n最も人間を殺している生物が—", "（ヘンダーソン）分かるかね？\n（ロイド）え？",
                                   "蚊だ", "今回の任務は—", "城の解放だ"])
        for a, b in ((0, 1), (3, 4)):
            satirlar[b].bas = satirlar[a].bit

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                yanit = {1: "Dünyada en çok insan öldüren canlı...", 2: "- Nedir, bilir misiniz?\n- Ha?", 3: "Sivrisinek.",
                         4: "Bu görevde | kaleyi özgürleştireceksin."}
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        veri = model.gidenler[0]
        self.assertEqual(veri["translate_to"], "Turkish (Türkçe)")
        satir = {x["id"]: x for x in veri["lines"]}
        self.assertTrue(satir[1].get("continues"))                    # farklı konuşmacı: birleşmez, ama sürdüğü bildirilir
        self.assertNotIn("parts", satir[1])
        self.assertEqual(satir[4]["parts"], 2)                         # aynı konuşmacı: tek cümle olarak gider
        self.assertEqual(satir[4]["text"], "今回の任務は 城の解放だ")
        self.assertEqual([sonuc[3], sonuc[4]], ["Bu görevde", "kaleyi özgürleştireceksin."])

    def test_terim_ve_ek_kontrolu_giden_metne_bakar(self):
        self.sozluk.elle_ekle({"ＷＩＳＥ": "WISE", "黄昏": "Twilight"})
        satirlar = self._satirlar(["（ＷＩＳＥ機関員）本日ですね", "あれが本物の黄昏さんか"])
        yanitlar = iter([{1: "Bugün, değil mi?", 2: "Demek gerçek Twilight bu."}, {2: "Demek gerçek Twilight-san bu."}])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                y = next(yanitlar)
                return json.dumps({"lines": [{"id": x["id"], "text": y[x["id"]]} for x in veri["lines"]
                                             if not x.get("skip")]}, ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(sonuc[0], "Bugün, değil mi?")               # etiketteki WISE terim sayılmaz
        self.assertEqual(len(model.gidenler), 1)        # kanji ad + さん: düşen ek yerinde eklenir, yeniden sorulmaz
        self.assertEqual(sonuc[1], "Demek gerçek Twilight-san bu.")

    def test_eski_ic_ses_alani_yok_sayilir(self):
        satirlar = self._satirlar(["今回の任務は", "城の解放だ", "落ち着くのです ヨル", "それは ありえません", "行くぞ"])
        for a, b in ((0, 1), (2, 3)):
            satirlar[b].bas = satirlar[a].bit
        # eski sürümlerin "turns" alanı (iç ses işareti, ölçülüp kaldırıldı) yok sayılır
        self.sozluk.birlestir("x.srt", {"turns": [{"from": 3, "to": 4, "who": "Yor -> self"}]}, satirlar, True, self.gunluk)

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                yanit = {1: "Bu görevde", 2: "kaleyi özgürleştireceksin.", 3: "Sakin ol, Yor.", 4: "Bu mümkün değil.", 5: "Gidelim."}
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        veri = model.gidenler[0]
        self.assertNotIn("turns", veri)
        self.assertEqual([sonuc[0], sonuc[1]], ["Bu görevde", "kaleyi özgürleştireceksin."])

    def test_uzatilan_ek_ve_ekiyle_kayitli_ad(self):
        satirlar = self._satirlar(["アーニャさーん　どこですか？", "スワン先生を救ったのだ", "シッターさんが来た", "東国の話だ"])
        self.sozluk.elle_ekle({"アーニャ": "Anya", "東国(オスタニア)": "Ostania"})
        self.sozluk.elle_ekle(degismez=["スワン先生", "ワンちゃん"])
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "x.srt")
        self.assertEqual(c._satir_kontrol(0, {0: "Anya Hanım! Neredesiniz?"}), "ek")       # さーん de -san'dır
        self.assertIsNone(c._satir_kontrol(0, {0: "Anya-san! Neredesiniz?"}))
        self.assertEqual(c._satir_kontrol(1, {1: "Bay Swan'ı kurtardın."}), "ek")          # ad, ekiyle kayıtlıydı
        self.assertIsNone(c._satir_kontrol(1, {1: "Swan-sensei'yi kurtardın."}))
        self.assertIsNone(c._satir_kontrol(2, {2: "Bakıcı geldi."}))                       # sıradan ad + さん
        self.assertNotIn("ワン", c.jp_adlar)                                                # "köpecik" kişi adı değil
        self.assertEqual(c._satir_kontrol(3, {3: "Doğu ülkesinin meselesi."}), "terim")    # okunuşsuz yazım da terim
        self.assertIsNone(c._satir_kontrol(3, {3: "Ostania'nın meselesi."}))

    def test_ekli_kayit_sozlukten_atilir(self):
        yol = os.path.join(self.tmp.name, "s.json")
        with open(yol, "w", encoding="utf-8") as f:
            json.dump({"terimler": {"ヨルさん": "Bayan Yor", "ヨル": "Yor"}, "korunacak": []}, f, ensure_ascii=False)
        self.assertEqual(Sozluk(yol).terimler, {"ヨル": "Yor"})

    def test_kurgu_dili_anlami_kaynakta_uc_mod(self):
        metinler = ["Tangado haid! (Hold positions!)", '<i>"Tinúviel elvanui\n(Tinúviel the elven-fair)</i>',
                    "Im Arwen - Telin le thaed.\n(I am Arwen - I've come to help you.)", "Who is she?",
                    "<i>I amar prestar aen.</i>", "The world is changed."]
        isaret = {"1": ["Tangado haid!"], "2": ["Tinúviel elvanui"], "3": ["Im Arwen - Telin le thaed."],
                  "5": ["I amar prestar aen."]}
        yanit = {1: "Mevzilerinizi koruyun!", 2: "<i>Elf güzeli Tinúviel</i>",
                 3: "Ben Arwen. Sana yardıma geldim.", 4: "Kim o?", 6: "Dünya değişti."}
        beklenen = {
            "orijinal": ["Tangado haid!", '<i>"Tinúviel elvanui</i>', "Im Arwen - Telin le thaed.", "Kim o?"],
            "orijinal_ceviri": ["Tangado haid!\n(Mevzilerinizi koruyun!)", '<i>"Tinúviel elvanui\n(Elf güzeli Tinúviel)</i>',
                                "Im Arwen - Telin le thaed.\n(Ben Arwen. Sana yardıma geldim.)", "Kim o?"],
            "ceviri": ["Mevzilerinizi koruyun!", "<i>Elf güzeli Tinúviel</i>", "Ben Arwen. Sana yardıma geldim.", "Kim o?"],
        }
        for mod, sonuclar in beklenen.items():
            ayar = ayar_birlestir({"modlar": {"kurgu_dili": mod, "kurgu_anlami": "sil"}})
            satirlar = self._satirlar(metinler)
            self.sozluk.kurgu["x.srt"] = isaret

            class M:
                gidenler = []

                def iste(self, sistem, kullanici, dusunme=None):
                    veri = json.loads(kullanici)
                    self.gidenler.append(veri)
                    return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]
                                                 if not x.get("skip")]},
                                      ensure_ascii=False)

            model = M()
            sonuc = Cevirmen(ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
            self.assertEqual([sonuc[i] for i in range(4)], sonuclar, mod)
            # Anlamı bir sonraki altyazıda verilen Elfçe satır her modda aynen kalır (çeviri iki kez çıkmasın)
            self.assertEqual([sonuc[4], sonuc[5]], ["<i>I amar prestar aen.</i>", "Dünya değişti."], mod)
            gonderilen = {x["id"]: x for x in model.gidenler[0]["lines"] if not x.get("skip")}
            if mod == "orijinal":
                self.assertEqual(list(gonderilen), [4, 6])                       # Elfçe satırlar modele hiç gitmez
            else:
                self.assertEqual(gonderilen[1]["text"], "Hold positions!")       # modele yalnızca anlam gider
                self.assertNotIn("note", gonderilen[2])                          # parantez "ses notu" sanılmaz

    def test_dizeler_ve_iki_konusmaci_birlestirilmez(self):
        from defta.cumle import cumle_gruplari
        dizeler = ["The lamp is lit upon the hill", "And every door is open wide", "The night is long and very still",
                   "And nobody is left outside.", "This is what the old man used to say:", "- Go on.\n- I will."]
        satirlar = self._satirlar(dizeler)
        for a, b in zip(satirlar, satirlar[1:]):
            b.bas = a.bit + 100
        self.assertEqual(cumle_gruplari(satirlar, set(range(len(satirlar)))), [])
        uc = self._satirlar(["Three were given to the kings...", "...who ruled the northern hills.", "He waited...",
                             "Nobody came."])
        for a, b in zip(uc, uc[1:]):
            b.bas = a.bit + 100
        self.assertEqual(cumle_gruplari(uc, {0, 1, 2, 3}), [[0, 1]])      # üç nokta + üç nokta: cümle sürüyor
        duz = self._satirlar(["When we get to the city", "we will find the old", "bridge and cross it."])
        for a, b in zip(duz, duz[1:]):
            b.bas = a.bit + 100
        self.assertEqual(cumle_gruplari(duz, {0, 1, 2}), [[0, 1, 2]])      # sıradan bölünmüş cümle yine birleşir

    def test_sarki_orijinal_kana_script_yazar(self):
        satirlar = self._satirlar(["（アーニャ）\n♪ ワンワン ワンワーン", "♪ 世界の ワンワン", "犬だ"])

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                yanit = {2: "♪ Sekai no wanwan", 3: "Bir köpek."}
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]
                                             if not x.get("skip")]}, ensure_ascii=False)

        model = M()
        sonuc = Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(sonuc[0], "♪ Wanwan wanwaan")                       # etiket atıldı, model görmedi
        gonderilen = {x["id"]: x for x in model.gidenler[0]["lines"] if not x.get("skip")}
        self.assertEqual(sorted(gonderilen), [2, 3])
        self.assertTrue(gonderilen[2].get("romanize"))                       # kanji var: yalnızca okunuşu istenir
        self.assertNotIn("romanize", gonderilen[3])


    def test_kun_eki(self):
        satirlar = self._satirlar(["おはよう ボンドマン君", "ヨルさんも本当にありがとう"])
        self.sozluk.elle_ekle({"ボンドマン": "Bondman", "ヨル": "Yor"})
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "x.srt")
        onar = {0: "Günaydın, Bondman."}
        self.assertIsNone(c._satir_kontrol(0, onar))                                       # 君 de -kun'dur:
        self.assertEqual(onar[0], "Günaydın, Bondman-kun.")                                # ek yerinde eklenir
        self.assertEqual(c._satir_kontrol(0, {0: "Günaydın, Bay Bondman."}), "ek")          # unvan var: model düzeltir
        self.assertIsNone(c._satir_kontrol(0, {0: "Günaydın, Bondman-kun."}))
        yeni = self._satirlar(["ベルク先生を救ったのだ", "シッターさんが来た"])                    # sözlükte olmayan ad
        c2 = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c2._hazirla(yeni, "y.srt")
        self.assertEqual(c2._satir_kontrol(0, {0: "Bay Berg'i kurtardın."}), "ek")          # 先生 kişiye takılır
        self.assertIsNone(c2._satir_kontrol(0, {0: "Berg-sensei'yi kurtardın."}))
        self.assertIsNone(c2._satir_kontrol(1, {1: "Bakıcı geldi."}))                        # さん: yalnız bilinen adlar

    def test_hitap_kanitla_duzelir_ve_satira_yazilir(self):
        metinler = (["（アーニャ）\nはは これ なに"] * 9 + ["（ヨル）\nそれは お花ですよ"] * 9
                    + ["（ヨル）アーニャさん どうぞ\n（アーニャ）わーい", "（ロイド）\nヨルさん 行きましょう"])
        satirlar = self._satirlar(metinler)
        analiz = {"address": [{"from": "Anya", "to": "Yor", "form": "informal"},
                              {"from": "Yor", "to": "Anya", "form": "formal"},
                              {"from": "Loid", "to": "Yor", "form": "formal"}],
                  "characters": [{"name": "Anya", "source": "アーニャ", "note": "child"},
                                 {"name": "Yor", "source": "ヨル", "note": "mother"},
                                 {"name": "Loid", "source": "ロイド", "note": "father"}]}
        self.sozluk.birlestir("x.srt", analiz, satirlar, True, self.gunluk)
        self.assertEqual(self.sozluk.hitap["Anya -> Yor"], "samimi")
        self.assertEqual(self.sozluk.hitap["Yor -> Anya"], "resmi")
        self.assertEqual(self.sozluk.latin_ad("ヨル・フォージャー"), "Yor")
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "x.srt")
        veri = json.loads(c._yuk([18, 19]))
        satir = {x["id"]: x for x in veri["lines"]}
        self.assertEqual(satir[19]["says_to"], "Yor -> Anya: formal; Anya -> Yor: informal")
        self.assertEqual(satir[20]["says_to"], "Loid -> Yor: formal")

    def test_karsiliga_cumledeki_sayi_girmez(self):
        """Sözlük her geçtiği yerde aynı biçimi dayatır: karşılığa terimde olmayan, cümleden gelen sayı girmez."""
        from defta.terim import baglam_sayisi_sil as sil
        self.assertEqual(sil("104. Harbiyeli Birliği Sınıfı", "Cadet Corps Class", ["The 104th Cadet Corps Class"]),
                         "Harbiyeli Birliği Sınıfı")
        self.assertEqual(sil("9. Bölge", "District 9", ["Welcome to District 9."]), "9. Bölge")       # terimin sayısı
        self.assertEqual(sil("7. Takım", "Seventh Squad", ["The Seventh Squad"]), "7. Takım")      # cümlede rakam yok
        satirlar = self._satirlar(["The 104th Cadet Corps Class entrance ceremony will now begin!", "Silence!",
                                   "The Cadet Corps Class will train for three years.", "Yes, sir!"])
        model = SahteModel(terim={"Cadet Corps Class": {"t": "descriptive", "tr": "104. Harbiyeli Birliği Sınıfı"}})
        dizi_analiz([("x.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertEqual(self.sozluk.girdiler["Cadet Corps Class"]["hedef"], "Harbiyeli Birliği Sınıfı")
        # Sayılı satır girdinin iki örnek satırı arasında değil, ama karşılık isteğine gitti: sayı yine girmez
        satirlar = self._satirlar(["The Cadet Corps Class will train for three years.", "Cadet Corps Class, dismissed!",
                                   "Welcome, Cadet Corps Class.", "The 104th Cadet Corps Class entrance ceremony begins!"])
        sozluk = Sozluk(os.path.join(self.tmp.name, "sayi.json"))
        dizi_analiz([("y.srt", satirlar, "")], self.ayar, model, sozluk, self.gunluk)
        self.assertIn("104th", " ".join(model.karsilik_istekleri[-1][0]["lines"]))
        self.assertEqual(sozluk.girdiler["Cadet Corps Class"]["hedef"], "Harbiyeli Birliği Sınıfı")

    def test_karsiligi_belirlenemeyen_terim_kayda_gecmez(self):
        """Beş karşılık cevabı da farklıysa terim sözlüğe girmez ve karar kaydedilmez: sonraki bölümde yeniden sorulur."""
        satirlar = self._satirlar(["Cadets ran.", "Cadets, stand!", "The halfling sat.", "A halfling came."])
        model = SahteModel(terim={"Cadets": {"t": "people", "tr": "harbiyeli",
                                             "k": ["harbiyeli", "asker adayı", "öğrenci asker", "kursiyer", "çömez"]},
                                  "halfling": {"t": "people", "tr": "buçukluk"}}, eser="Bir Eser",
                           kesif=[{"s": "Cadets"}, {"s": "halfling"}])
        dizi_analiz([("x.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertNotIn("Cadets", self.sozluk.girdiler)
        self.assertNotIn("cadets", self.sozluk.karar_verilenler())
        self.assertEqual(self.sozluk.girdiler["halfling"]["hedef"], "buçukluk")
        with open(os.path.join(self.gunluk.klasor, "uyarilar.txt"), encoding="utf-8") as f:
            self.assertIn("'Cadets' için karşılık belirlenemedi", f.read())
        # Tek oyda ([sozluk] oy = 1) cevabı gelmeyen terim de kayda geçmez: geçerse bir daha hiç sorulmaz
        tek_oy = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "sozluk": {"oy": 1}})
        sozluk = Sozluk(os.path.join(self.tmp.name, "tek.json"))
        model = SahteModel(terim={"halfling": {"t": "people", "tr": "buçukluk", "k": []}}, kesif=["halfling"])
        dizi_analiz([("x.srt", satirlar, "")], tek_oy, model, sozluk, self.gunluk)
        self.assertNotIn("halfling", sozluk.karar_verilenler())

    def test_bicimi_bozuk_yanit_sozluk_adimini_cokertmez(self):
        """Geçerli JSON ama yanlış biçimli yanıt ("items" sayı, "i" ya da "t" liste, "address" sayı) bozuk yanıt gibi
        ele alınır: yeniden sorulur ya da o öğe atlanır; TypeError ile bütün çalışma durmaz."""
        satirlar = self._satirlar(["We sail for Troy.", "If Troy falls, Odysseus wins.", "Look at Troy!",
                                   "Odysseus is here.", "Tell Odysseus now."])
        bozuklar = {"You classify": ['{"items": 5}', '{"items": [{"i": [1], "t": "name"}]}',
                                     '{"items": [{"i": 1, "t": ["name"]}]}'],
                    "You render terms": ['{"items": 7}'], "You read the subtitles of one film": ['{"items": 7}'],
                    "You prepare subtitles for translation into": ['{"address": 5}', '{"characters": "yok"}']}
        for bas, yanitlar in bozuklar.items():
            for n, yanit in enumerate(yanitlar):
                class Bozuk(SahteModel):
                    def iste(self, sistem, kullanici, dusunme=None):
                        return yanit if sistem.startswith(bas) else super().iste(sistem, kullanici, dusunme)
                sozluk = Sozluk(os.path.join(self.tmp.name, f"b{len(bas)}{n}.json"))
                model = Bozuk(terim={"Troy": {"t": "descriptive", "tr": "Truva"}, "Odysseus": {"t": "name"}})
                dizi_analiz([("x.srt", satirlar, "")], self.ayar, model, sozluk, self.gunluk)     # çökmez
                self.assertEqual(sozluk.analiz_edilen, ["x.srt"])

    def test_latin_disi_hedef_dilde_karsilik_oya_girer(self):
        """Hedef dilin yazısı Latin değilse (cikti_alfabesi = "serbest") karşılık o yazıdadır: oylamada elenmez, terim
        sözlüğe girer. Çıktı Latin harfliyken başka yazıdaki cevap yine oya girmez."""
        satirlar = self._satirlar(["A halfling came.", "The halfling left."])
        terim = {"halfling": {"t": "people", "tr": "полурослик"}}
        rusca = ayar_birlestir({"model": {"es_zamanli_istek": 1},
                                "ceviri": {"hedef_dil": "ru", "cikti_alfabesi": "serbest"}})
        model = SahteModel(terim=terim, kesif=["halfling"])
        dizi_analiz([("x.srt", satirlar, "")], rusca, model, self.sozluk, self.gunluk)
        self.assertEqual(self.sozluk.terimler, {"halfling": "полурослик"})
        self.assertEqual(len(model.karsilik_istekleri), 3)                 # üç oy birleşti: ek oy gerekmedi
        latin = Sozluk(os.path.join(self.tmp.name, "latin.json"))
        dizi_analiz([("x.srt", satirlar, "")], self.ayar, SahteModel(terim=terim, kesif=["halfling"]), latin, self.gunluk)
        self.assertNotIn("halfling", latin.girdiler)

    def test_ingilizce_hedefte_ingilizce_yazim_karsiliktir(self):
        """Latin dışı kaynakta İngilizce yazımla ("l") aynı cevap Türkçe hedefte karşılık sayılmaz; hedef dil
        İngilizceyse karşılığın kendisidir: elenirse kanjili terim İngilizce hedefte sözlüğe hiç giremez."""
        satirlar = self._satirlar(["調査兵団が来た", "調査兵団は強い"])
        terim = {"調査兵団": {"t": "descriptive", "l": "Survey Corps", "k": "Survey Corps"}}
        ingilizce = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "ceviri": {"hedef_dil": "en"}})
        model = SahteModel(terim=terim, kesif=["調査兵団"])
        dizi_analiz([("x.srt", satirlar, "")], ingilizce, model, self.sozluk, self.gunluk)
        self.assertEqual(self.sozluk.terimler, {"調査兵団": "Survey Corps"})
        self.assertEqual(len(model.karsilik_istekleri), 3)
        turkce = Sozluk(os.path.join(self.tmp.name, "tr.json"))
        dizi_analiz([("x.srt", satirlar, "")], self.ayar, SahteModel(terim=terim, kesif=["調査兵団"]), turkce, self.gunluk)
        self.assertNotIn("調査兵団", turkce.girdiler)                       # Türkçede İngilizcesi karşılık değildir

    def test_takma_ad_karsilik_olmaz(self):
        """Modelin "yerleşik biçim" dediği yazım aynı altyazıda başka bir ad olarak geçiyorsa takma addır: kaynak
        yazım kalır. Altyazıda geçmeyen gerçek karşılık (Athens -> Atina) kalır."""
        satirlar = self._satirlar(["The dwarves dug deep in Morvain.", "Morvain was their greatest city.",
                                   "They called it Keldum in their tongue.", "Keldum is lost.",
                                   "We sailed from Athens.", "Athens is far."])
        model = SahteModel(terim={"Keldum": {"t": "name", "e": "Morvain"}, "Morvain": {"t": "name"},
                                  "Athens": {"t": "name", "e": "Atina", "real": True}}, eser="Bir Eser")
        dizi_analiz([("x.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertEqual({k: self.sozluk.girdiler[k]["hedef"] for k in ("Keldum", "Morvain", "Athens")},
                         {"Keldum": "Keldum", "Morvain": "Morvain", "Athens": "Atina"})

    def test_ekli_sozcuk_tek_karsilik_alir(self):
        satirlar = self._satirlar(["犬さん！", "でっかい犬さーん", "あの犬さん どこ？", "ワンちゃん", "ワンちゃん？",
                                   "ワンちゃんだ", "ボンドさん", "ボンドさん！", "ボンドさん？", "ボンドだ", "ボンドか"])
        model = SahteModel(terim={"犬さん": {"t": "epithet", "tr": "Köpekçik"}, "ワンちゃん": {"t": "name", "l": "Wan-chan"},
                                  "ボンド": {"t": "name", "l": "Bond"}, "ボンドさん": {"t": "epithet", "tr": "Bondcuk"}})
        dizi_analiz([("s.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertTrue(all({"犬さん", "ワンちゃん"} <= set(x) for x in model.terim_istekleri))
        # sevimli ad olarak sözlüğe girer (ekisiz, küçük harf, uzatılmış yazım da); "ad" denen ekli sözcük girmez
        self.assertEqual({k: v for k, v in self.sozluk.terimler.items() if "犬" in k or "ワン" in k or "ボンド" in k},
                         {"犬さん": "köpekçik", "犬さーん": "köpekçik", "ボンド": "Bond"})   # adın eklisi girmez
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertEqual(c._satir_kontrol(2, {2: "O köpek nerede?"}), "terim")
        self.assertIsNone(c._satir_kontrol(2, {2: "O köpekçiğim nerede?"}))

    def test_tamlama_terimi_cogulda_da_gecer(self):
        """"deniz akıncısı" çoğulda "deniz akıncıları" olur: tamlama eki düşer, terim yine geçmiş sayılır."""
        satirlar = self._satirlar(["The people from the sea.", "Who are the people from the sea?"])
        self.sozluk.girdi_yaz("people from the sea", tur="descriptive", hedef="deniz akıncısı", karar="model", sayi=2)
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertIsNone(c._satir_kontrol(0, {0: "Deniz akıncısı."}))
        self.assertIsNone(c._satir_kontrol(1, {1: "Bu deniz akıncıları kim?"}))
        self.assertEqual(c._satir_kontrol(1, {1: "Bu korsanlar kim?"}), "terim")
        satirlar = self._satirlar(["Espers like me!", "An esper must beware."])
        self.sozluk.girdi_yaz("esper", tur="term", hedef="psişik güç sahibi", karar="model", sayi=2)
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertIsNone(c._satir_kontrol(0, {0: "Benim gibi psişik güç sahipleri!"}))   # sahibi / sahipleri
        satirlar = self._satirlar(["Your power is mine.", "Their power?"])
        self.sozluk.girdi_yaz("power", tur="term", hedef="güç", karar="model", sayi=2)
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertIsNone(c._satir_kontrol(1, {1: "Peki ya onların gücü?"}))               # güç / gücü
        # -y ile biten halk adı: kaynakta çoğulu "-ies", çeviride yazımı kalır ("vardy'ler", "Vardiler" değil)
        satirlar = self._satirlar(["The Vardies did it.", "A Vardy is here."])
        self.sozluk.girdi_yaz("Vardy", tur="people", hedef="vardy", karar="model", sayi=2)
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertIsNone(c._satir_kontrol(0, {0: "Vardy'ler yaptı."}))
        self.assertEqual(c._satir_kontrol(0, {0: "Vardiler yaptı."}), "terim")

    def test_turkce_kucuk_harf_kurali_baska_hedef_dile_tasmaz(self):
        """Ekli sözcüğün karşılığı yalnız Türkçede küçük harfe çevrilir (Almancada ad büyük harfle yazılır; "I" başka
        dilde "ı" olmaz); oylamada "I" / "i" farkı ayrı cevap sayılmaz."""
        from defta.terim import _kazanan
        satirlar = self._satirlar(["犬さん！", "でっかい犬さーん", "あの犬さん どこ？", "イヌさん", "イヌさん？", "イヌさんだ"])
        almanca = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "ceviri": {"hedef_dil": "de"}})
        model = SahteModel(terim={"犬さん": {"t": "epithet", "tr": "Hündchen"}, "イヌさん": {"t": "epithet", "tr": "Igelchen"}})
        dizi_analiz([("s.srt", satirlar, "")], almanca, model, self.sozluk, self.gunluk)
        self.assertEqual((self.sozluk.terimler["犬さん"], self.sozluk.terimler["イヌさん"]), ("Hündchen", "Igelchen"))
        self.assertEqual(_kazanan(["Inspector", "inspector", "handler"], 2), "Inspector")
        self.assertEqual(_kazanan(["Irk", "ırk", "soy"], 2), "Irk")                # Türkçe çift de tek oy

    def test_japonca_adlar_birbirini_tutar(self):
        from defta.sozluk import japonca_adlari_esitle
        t = {"ロイド": "Loid", "ロイドマン": "Lloydman", "ロイド・フォージャー": "Roido Foja", "フォージャー": "Forger",
             "シルヴィア": "Sylvia", "シルヴィア・シャーウッド": "Shiruvia Shauddo", "ニューストン": "Newsston Şatosu",
             "ニューストン城": "Newston Şatosu", "アーニャ姫": "Prenses Anya", "アーニャ": "Anya"}
        tur = {k: "descriptive" if k.endswith(("城", "ン")) and "ニュース" in k else "name" for k in t}
        japonca_adlari_esitle(t, tur)
        self.assertEqual((t["ロイドマン"], t["ロイド・フォージャー"], t["ニューストン"], t["ニューストン城"], t["アーニャ姫"]),
                         ("Loidman", "Loid Forger", "Newston", "Newston Şatosu", "Prenses Anya"))
        self.assertNotIn("シルヴィア・シャーウッド", t)          # bilinen parçayla çelişen tam ad çıkar

    def test_ortusen_yazimlar_tutarli(self):
        from defta.sozluk import japonca_adlari_esitle
        t = {"ロイド": "Loid", "ロイドマン": "Lloyd Adam", "モジャモジャ": "Kont Mojamoja", "モジャモジャ伯爵": "Kabarık Kont",
             "ニューストン": "Newston Kalesi", "ニューストン城": "Newston Şatosu", "アンドロイド": "android"}
        tur = {"ロイド": "name", "ロイドマン": "epithet", "モジャモジャ": "epithet", "モジャモジャ伯爵": "epithet",
               "ニューストン": "descriptive", "ニューストン城": "descriptive", "アンドロイド": "term",
               "アーニャ": "name", "アーニャ姫": "epithet", "ボンドマン": "epithet", "ジャンポール": "name"}
        t.update({"アーニャ": "Anya", "アーニャ姫": "Prenses Anya", "ボンドマン": "Bond-man", "ジャンポール": "Jean-Paul"})
        japonca_adlari_esitle(t, tur)
        self.assertEqual((t["ロイドマン"], t["モジャモジャ"], t["ニューストン"], t["アンドロイド"], t["アーニャ"],
                          t["ボンドマン"], t["ジャンポール"]),
                         ("Loidman", "Kabarık", "Newston", "android", "Anya", "Bondman", "Jean-Paul"))
        # Okunuşlu yazımın parçaları aynı karşılığı taşır: kısa yazım uzunun son sözcüğünü kaybetmez
        t = {"鷹(ファルコ)": "Operation Falco", "鷹": "Operation Falco", "ファルコ": "Operation Falco"}
        japonca_adlari_esitle(t, {"鷹(ファルコ)": "codename"})
        self.assertEqual(t["鷹"], "Operation Falco")

    def test_oylarda_resmi_yazim_kazanir(self):
        from defta.terim import _cogunluk
        oy = lambda *c: [{"items": [x]} for x in c]
        # Tek bir sapan oy kazanmaz (Mikasa, Mikasa, Mikassa -> Mikassa olurdu): resmî yazım okunuş kadar oy alırsa
        # kazanır; almazsa okunuş kalır ve resmî yazımı yazım isteği (eserin adı ve satırlarıyla, iki oyla) belirler.
        sec = _cogunluk(oy({"i": 0, "t": "name", "l": "Sutera"}, {"i": 0, "t": "name", "l": "Stella"},
                           {"i": 0, "t": "name", "l": "Sutera"}), {0: "ステラ"})[0]
        self.assertEqual(sec["l"], "Sutera")
        sec = _cogunluk(oy({"i": 0, "t": "name", "l": "Stella"}, {"i": 0, "t": "name", "l": "Stella"},
                           {"i": 0, "t": "name", "l": "Sutera"}), {0: "ステラ"})[0]
        self.assertEqual(sec["l"], "Stella")
        sec = _cogunluk(oy({"i": 0, "t": "name", "l": "Mikasa"}, {"i": 0, "t": "name", "l": "Mikassa"},
                           {"i": 0, "t": "name", "l": "Mikasa"}), {0: "ミカサ"})[0]
        self.assertEqual(sec["l"], "Mikasa")
        sec = _cogunluk(oy({"i": 0, "t": "name", "l": "Farukon", "w": "falcon"}, {"i": 0, "t": "name"},
                           {"i": 0, "t": "name", "l": "Farukon"}), {0: "ファルコン"})[0]
        self.assertEqual(sec["l"], "Falcon")                       # İngilizce sözcükten gelen ad
        sec = _cogunluk(oy({"i": 0, "t": "name", "l": "Inu", "w": "dog"}), {0: "イヌ"})[0]
        self.assertEqual(sec["l"], "Inu")                          # okunuşu tutmayan anlam yazım olmaz

    def test_kaynagin_kopyasi_oya_girmez(self):
        from defta.terim import _cogunluk
        oy = lambda *c: [{"items": list(x)} for x in c]
        grup = oy([{"i": 1, "t": "term", "tr": "超能力者"}], [{"i": 1, "t": "term", "tr": "telepat"}])
        self.assertEqual(_cogunluk(grup[:1], {1: "超能力者"})[0].get("tr"), None)

    def test_yarida_kesilen_sozluk_adiminin_odenmis_yanitlari_saklanir(self):
        """Sözlük adımı yarıda durdurulursa ödenmiş yanıtlar kayıttan döner: yeniden başlayınca yalnız eksik istekler
        modele gider. Oylamada aynı istek birkaç kez sorulur: her biri ayrı saklanır. Ret kayda geçmez."""
        from defta.model import Iptal, Kayitli
        yol = os.path.join(self.tmp.name, "analiz_kayit.json")

        class Sayan:
            def __init__(self, dur=None):
                self.cagri, self.dur = [], dur

            def iste(self, sistem, kullanici, dusunme=None):
                if self.dur is not None and len(self.cagri) >= self.dur:
                    raise Iptal()
                self.cagri.append(kullanici)
                return "yanıt %d" % len(self.cagri)

            def maliyet(self):
                return 7
        ilk = Sayan(dur=3)
        k = Kayitli(ilk, yol)
        self.assertEqual([k.iste("s", "a"), k.iste("s", "a"), k.iste("s", "b")], ["yanıt 1", "yanıt 2", "yanıt 3"])
        with self.assertRaises(Iptal):
            k.iste("s", "c")
        self.assertEqual(k.maliyet(), 7)                       # öteki her şey asıl modele iletilir
        ikinci = Sayan()
        k = Kayitli(ikinci, yol)
        self.assertEqual([k.iste("s", "a"), k.iste("s", "a"), k.iste("s", "b")], ["yanıt 1", "yanıt 2", "yanıt 3"])
        self.assertEqual(ikinci.cagri, [])                     # üçü de kayıttan
        self.assertEqual(k.iste("s", "a"), "yanıt 1")          # üçüncü oy kayıtta yok: modele gider
        self.assertEqual(k.iste("s", "c"), "yanıt 2")
        self.assertEqual(ikinci.cagri, ["a", "c"])
        self.assertEqual(Kayitli(Sayan(), yol).iste("s", "b", "low"), "yanıt 1")   # düşünme düzeyi farklı: başka istek

    def test_turkce_ornek_yalniz_turkce_hedefte_gider(self):
        """Modele giden talimatlarda hedef dile özgü örnek yalnız o hedefte yazılır: Almanca ya da İngilizce hedefte
        istemlere Türkçe sözcük girmez; İngilizce hedefte "never in English" denmez. Türkçe hedefte örnekler durur."""
        from defta import terim
        istemler = lambda a: [istem_olustur(a, {"Hope": "Hope"}), terim.siniflandirma_istemi(a), terim.kesif_istemi(a),
                              terim.karsilik_istemi(a), terim.yazim_istemi(a)]
        for kod in ("de", "en", "ja"):
            for metin in istemler(ayar_birlestir({"ceviri": {"hedef_dil": kod}})):
                self.assertNotRegex(metin, r"[ğışİĞŞ]|\[\[tr|\]\]|Turkish|sen/siz", kod)
        self.assertNotIn("never in English", terim.karsilik_istemi(ayar_birlestir({"ceviri": {"hedef_dil": "en"}})))
        tr = istemler(ayar_birlestir({}))
        self.assertIn('"Na zdorovje!" -> "Şerefe!"', tr[0])
        self.assertIn("Rome -> Roma", tr[1])
        self.assertIn("never in English", tr[3])
        self.assertNotRegex("".join(tr), r"\[\[tr|\]\]")

    def test_latin_disi_kaynakta_ad_duyuldugu_gibi_yazilir(self):
        """Japonca sesle izleyen duyduğu adı okur: kurgusal adın İngilizce yayındaki çevirisi ("yerleşik biçim") alınmaz,
        soyadı tam ada dönmez. Gerçek dünyadan adın yerleşik biçimi ve Latin harfli kaynağın kuralları değişmez."""
        from defta import terim
        h = terim.hedef_belirle
        self.assertEqual(h("エクボ", {"t": "name", "l": "Ekubo", "e": "Dimple"}, True), ("name", "Ekubo"))
        self.assertEqual(h("最上", {"t": "name", "l": "Mogami", "e": "Keiji Mogami"}, True), ("name", "Mogami"))
        self.assertEqual(h("ローマ", {"t": "name", "l": "Roma", "e": "Roma", "real": True}, True), ("name", "Roma"))
        self.assertEqual(h("Rome", {"t": "name", "e": "Roma", "real": True}, True), ("name", "Roma"))
        self.assertEqual(h("Mogami", {"t": "name", "e": "Keiji Mogami"}, True), ("name", "Mogami"))    # tam ad eklenmez
        for istem in (terim.siniflandirma_istemi(ayar_birlestir({})), terim.yazim_istemi(ayar_birlestir({}))):
            self.assertIn("never \"Star\"", istem)
            self.assertNotIn("Royal Knights", istem)
        self.assertIn("the way it SOUNDS", istem_olustur(ayar_birlestir({})))
        self.assertNotIn("the way it SOUNDS", istem_olustur(ayar_birlestir({"ceviri": {"hedef_dil": "ru", "cikti_alfabesi": "serbest"}})))

    def test_karsiligi_yazimiyla_ayni_terim_kayda_gecer(self):
        """Bütün cevaplar Latin yazımın kendisiyse (ミスリル -> mithril) karar belirsiz sayılmaz: terim yazımıyla kayda
        geçer, her bölümde yeniden sorulmaz. Kanji yazımda "l" İngilizce çeviridir: o belirsiz kalır. Yazım isteği
        "l"yi sonradan değiştirdiyse karar da belirsizdir."""
        from defta.terim import _karsiliklari_tamamla, hedef_belirle, karsiliklari_uygula
        sor = lambda cevap: (lambda y: {"items": [{"i": x["i"], "tr": cevap} for x in y["items"]]})
        c = {"i": 1, "t": "term", "l": "mithril"}
        n, oneriler, _ = _karsiliklari_tamamla([[c]], [[{"yazim": "ミスリル", "ornek": []}]], sor("Mithril"), 3, 2)
        karsiliklari_uygula(oneriler)
        self.assertFalse(c.get("belirsiz"))
        self.assertEqual(hedef_belirle("ミスリル", c, True), ("term", "mithril"))
        c = {"i": 1, "t": "descriptive", "l": "Survey Corps"}                                 # kanji: İngilizce çeviri
        karsiliklari_uygula(_karsiliklari_tamamla([[c]], [[{"yazim": "調査兵団", "ornek": []}]], sor("Survey Corps"), 3, 2)[1])
        self.assertTrue(c.get("belirsiz"))
        c = {"i": 1, "t": "term", "l": "misuriru"}                                            # yazım sonradan değişti
        n, oneriler, _ = _karsiliklari_tamamla([[c]], [[{"yazim": "ミスリル", "ornek": []}]], sor("misuriru"), 3, 2)
        c["l"] = "mithril"
        karsiliklari_uygula(oneriler)
        self.assertTrue(c.get("belirsiz"))
        c = {"i": 1, "t": "term", "l": "mithril", "tr": "mithril"}                            # tek cevap yetmez
        karsiliklari_uygula(_karsiliklari_tamamla([[c]], [[{"yazim": "ミスリル", "ornek": []}]], sor("mithril"), 1, 1)[1])
        self.assertTrue(c.get("belirsiz"))

    def test_karsilik_alan_terimin_turkcesini_karsilik_istegi_verir(self):
        """Halk adı, unvan, lakap, tanım ve kavramın Türkçesi yalnızca karşılık isteğinden gelir; sınıflandırmanın
        "yerleşik biçim"i ya da karşılığı hedef olmaz (Spy'da "esper" oradan geçmişti)."""
        from defta.terim import _karsilik_sorulacak, _karsiliklari_tamamla, hedef_belirle, karsiliklari_uygula

        def tamamla(*a, **k):                       # öneriler yazım isteğinden sonra uygulanır
            n, oneriler, _ = _karsiliklari_tamamla(*a, **k)
            karsiliklari_uygula(oneriler)
            return n
        kararlar = [[{"i": 1, "t": "people", "l": "chōnōryokusha", "e": "esper", "m": "a psychic"},
                     {"i": 2, "t": "term", "tr": "ödül avcısı"}, {"i": 3, "t": "name", "l": "Anya"}]]
        partiler = [[{"yazim": "超能力者", "ornek": ["娘 超能力者"]}, {"yazim": "賞金稼ぎ", "ornek": []},
                     {"yazim": "アーニャ", "ornek": []}]]
        self.assertEqual(hedef_belirle("超能力者", kararlar[0][0], True)[1], "")   # yerleşik biçim tek başına yetmez
        yuk_ = []

        def sor(yuk):
            yuk_.append(yuk)
            return {"items": [{"i": x["i"], "tr": "telepat"} for x in yuk["items"] if x["s"] in ("超能力者", "犬さん")]}
        self.assertEqual(tamamla(kararlar, partiler, sor, 3, 2, "SPY×FAMILY", lambda y: ["satır 1", "satır 2"]), 2)
        self.assertEqual([x["s"] for x in yuk_[0]["items"]], ["超能力者", "賞金稼ぎ"])      # ad sorulmaz
        self.assertEqual((yuk_[0]["title_hint"], yuk_[0]["items"][0]["lines"]), ("SPY×FAMILY", ["satır 1", "satır 2"]))
        self.assertEqual(hedef_belirle("超能力者", kararlar[0][0], True)[1], "telepat")
        self.assertEqual(hedef_belirle("賞金稼ぎ", kararlar[0][1], True)[1], "")      # yanıt gelmedi: sözlüğe girmez
        self.assertTrue(kararlar[0][1].get("belirsiz"))                                  # ek oyda da: kayda geçmez
        # İngilizce yanıt kabul edilmez; yazım isteği "l"yi sonradan İngilizce yazdıysa ("Devil Fruit") o da alınmaz
        c = {"i": 1, "t": "term", "l": "esper"}
        tamamla([[c]], [[{"yazim": "超能力者", "ornek": []}]], lambda y: {"items": [{"i": 1, "tr": "esper"}]}, 3, 2)
        self.assertNotIn("k", c)
        c = {"i": 1, "t": "term", "tr": "şeytan meyvesi"}
        n, oneriler, _ = _karsiliklari_tamamla([[c]], [[{"yazim": "悪魔の実", "ornek": []}]],
                                            lambda y: {"items": [{"i": 1, "tr": "Devil Fruit"}]}, 3, 2)
        c["l"] = "Devil Fruit"
        karsiliklari_uygula(oneriler)
        self.assertNotIn("k", c)
        # yazım farkları tek oy sayılır: "kral otu" / "kralotu" çoğunluk olur, en sık yazılan biçim alınır
        c = {"i": 1, "t": "term", "tr": "kral otu"}
        oylar = iter([{"items": [{"i": 1, "tr": "kral yaprağı"}]}, {"items": [{"i": 1, "tr": "kral otu"}]},
                      {"items": [{"i": 1, "tr": "kralotu"}]}])
        tamamla([[c]], [[{"yazim": "kingsfoil", "ornek": []}]], lambda y: next(oylar), 3, 1)
        self.assertEqual(c["k"], "kral otu")
        # cevaplar birleşmezse yalnız o terim için aynı soru iki kez daha sorulur; beş oyda en çok oyu alan kazanır,
        # beraberlikte önce verilen; beşi de farklıysa karar "belirsiz": kayda geçmez (ilk cevap alınmaz)
        def oylayan(*cevaplar):
            sira, giden_ = iter(cevaplar), []

            def sor_(yuk):
                giden_.append([x["s"] for x in yuk["items"]])
                return {"items": [{"i": x["i"], "tr": cevap} for x, cevap in zip(yuk["items"], next(sira))]}
            return sor_, giden_
        a1, a2 = {"i": 1, "t": "term", "tr": "x"}, {"i": 2, "t": "term", "tr": "y"}
        parti = [[{"yazim": "超能力者", "ornek": []}, {"yazim": "賞金稼ぎ", "ornek": []}]]
        sor_, giden_ = oylayan(["telepat", "ödül avcısı"], ["medyum", "ödül avcısı"], ["psişik", "kelle avcısı"],
                               ["telepat"], ["kâhin"])
        n, oneriler, ek = _karsiliklari_tamamla([[a1, a2]], parti, sor_, 3, 1)
        karsiliklari_uygula(oneriler)
        self.assertEqual((a1.get("k"), a2.get("k"), ek, giden_[3:]), ("telepat", "ödül avcısı", 1, [["超能力者"], ["超能力者"]]))
        a1 = {"i": 1, "t": "term", "tr": "x"}
        sor_, _ = oylayan(["medyum"], ["telepat"], ["psişik"], ["telepat"], ["medyum"])        # 2–2: önce verilen
        karsiliklari_uygula(_karsiliklari_tamamla([[a1]], [parti[0][:1]], sor_, 3, 1)[1])
        self.assertEqual(a1.get("k"), "medyum")
        a1 = {"i": 1, "t": "term", "tr": "x"}
        sor_, _ = oylayan(["medyum"], ["telepat"], ["psişik"], ["kâhin"], ["esper adam"])     # beşi de farklı
        karsiliklari_uygula(_karsiliklari_tamamla([[a1]], [parti[0][:1]], sor_, 3, 1)[1])
        self.assertEqual((a1.get("k"), a1.get("belirsiz")), (None, True))
        # Latin harfli kaynakta yazımın kendisi geçerli yanıttır: çevrilmeyeceğini söyler
        c = {"i": 1, "t": "people", "w": "hobbit"}
        tamamla([[c]], [[{"yazim": "Hobbit", "ornek": []}]], lambda y: {"items": [{"i": 1, "tr": "hobbit"}]}, 3, 2)
        self.assertEqual(hedef_belirle("Hobbit", c, True), ("people", "hobbit"))
        # kim sorulur: karşılık alan bütün türler; Latin harflide yalnızca çevrilecek söz; okunuşlu yazım sorulmaz
        self.assertTrue(_karsilik_sorulacak({"t": "people", "tr": "medyum"}, "超能力者"))
        self.assertTrue(_karsilik_sorulacak({"t": "descriptive", "l": "Secret Society", "e": "Gizli Cemiyet"}, "秘密結社"))
        self.assertTrue(_karsilik_sorulacak({"t": "term", "tr": "kral otu"}, "kingsfoil"))
        self.assertTrue(_karsilik_sorulacak({"t": "epithet", "tr": "Yolgezer"}, "Strider"))
        self.assertFalse(_karsilik_sorulacak({"t": "people", "tr": "Dúnedain"}, "Dúnedain"))   # kaynağın yazımı kalır
        # küçük harfli Latin terim sorulur (sıradan söz olabilir: wormhole); uydurmaysa karşılık yazımın kendisidir
        self.assertTrue(_karsilik_sorulacak({"t": "term"}, "mithril"))
        self.assertTrue(_karsilik_sorulacak({"t": "term"}, "black hole"))
        self.assertTrue(_karsilik_sorulacak({"t": "term"}, "Murphy's Law"))
        self.assertFalse(_karsilik_sorulacak({"t": "term"}, "Mithril"))
        self.assertFalse(_karsilik_sorulacak({"t": "term", "l": "Stella", "tr": "yıldız"}, "星(ステラ)"))
        self.assertFalse(_karsilik_sorulacak({"t": "name", "l": "Anya"}, "アーニャ"))
        # ekli sözcüğe ton ipucu gider
        yuk_.clear()
        tamamla([[{"i": 1, "t": "epithet"}]], [[{"yazim": "犬さん", "ornek": [], "ekli": "犬"}]], sor, 3, 2,
                dil="Turkish (Türkçe)")
        self.assertIn("little bear", yuk_[0]["items"][0]["hint"])
        self.assertNotIn("TARGET", yuk_[0]["items"][0]["hint"])

    def test_okunus_kalan_yazim_ayrica_sorulur(self):
        from defta.terim import _yazimlari_tamamla
        kararlar = [[{"i": 1, "t": "term", "l": "Sutera"}, {"i": 2, "t": "name", "l": "Haru"},
                     {"i": 3, "t": "codename", "l": "Tasogare", "e": "Alacakaranlık"}, {"i": 4, "t": "name", "l": "Loid"}]]
        partiler = [[{"yazim": "星(ステラ)", "ornek": []}, {"yazim": "ハル", "ornek": []},
                     {"yazim": "黄昏(たそがれ)", "ornek": []}, {"yazim": "ロイド", "ornek": []}]]
        yanit = iter([{"items": [{"i": 1, "l": "Stella"}, {"i": 2, "l": "Hal"}, {"i": 3, "l": "Twilight"}]},
                      {"items": [{"i": 1, "l": "Stella"}, {"i": 2, "l": "Haru"}, {"i": 3, "l": "Twilight"}]},
                      {"items": [{"i": 1, "l": "Sutera"}, {"i": 2, "l": "Haru"}, {"i": 3, "l": "Tasogare"}]}])
        giden = []

        def sor(yuk):
            giden.append([x["s"] for x in yuk["items"]])
            return next(yanit)
        self.assertEqual(_yazimlari_tamamla(kararlar, partiler, sor, 3, 1, "SPY"), 3)
        self.assertEqual([c["l"] for c in kararlar[0]], ["Stella", "Haru", "Twilight", "Loid"])
        self.assertEqual(giden[0], ["星(ステラ)", "ハル", "黄昏(たそがれ)"])          # resmî yazımlı ad sorulmaz
        # kurgusal adın "e"si yazımın sorulmasını engellemez (ブリタニア -> "Britannia"); gerçek adınki engeller
        kararlar = [[{"i": 1, "t": "name", "l": "Buritania", "e": "Britanya"},
                     {"i": 2, "t": "name", "l": "Amerika", "e": "Amerika", "real": True}]]
        partiler = [[{"yazim": "ブリタニア", "ornek": []}, {"yazim": "アメリカ", "ornek": []}]]
        giden.clear()
        yanit = iter([{"items": [{"i": 1, "l": "Britannia"}]}] * 3)
        self.assertEqual(_yazimlari_tamamla(kararlar, partiler, sor, 3, 1, "Code Geass"), 1)
        self.assertEqual(kararlar[0][0]["l"], "Britannia")
        self.assertEqual(giden[0], ["ブリタニア"])

    def test_halk_adinin_koku_sifat_ve_dil_bicimini_kabul_eder(self):
        satirlar = self._satirlar(["The Chinese army is moving.", "He speaks Chinese."])
        self.sozluk.girdi_ekle("Chinese", "people", "Çinli")
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertIsNone(c._satir_kontrol(0, {0: "Çin ordusu ilerliyor."}))
        self.assertIsNone(c._satir_kontrol(1, {1: "Çince konuşuyor."}))
        self.assertEqual(c._satir_kontrol(0, {0: "Ordu ilerliyor."}), "terim")

    def test_kisa_terim_uzun_yazimin_icinde_aranmaz(self):
        satirlar = self._satirlar(["悪の親玉 モジャモジャ伯爵", "モジャジャジャジャ", "モジャ！"])
        self.sozluk.girdi_ekle("モジャ", "epithet", "Kıllı")
        self.sozluk.girdi_ekle("モジャモジャ伯爵", "epithet", "Kont Mojamoja")
        self.sozluk.girdi_ekle("モジャジャジャジャ", "common", "")
        c = Cevirmen(self.ayar, None, "S", self.gunluk, self.sozluk)
        c._hazirla(satirlar, "s.srt")
        self.assertIsNone(c._satir_kontrol(0, {0: "Kötülerin başı Kont Mojamoja."}))   # "Kıllı" istenmez
        self.assertEqual(c._satir_kontrol(0, {0: "Kötülerin başı Kabarık Kont."}), "terim")
        self.assertIsNone(c._satir_kontrol(1, {1: "Mojajajaja!"}))                    # gülüş: sıradan sözcük
        self.assertEqual(c._satir_kontrol(2, {2: "Mojaa!"}), "terim")

    def test_ekli_ad_sozluge_girmez(self):
        from defta.sozluk import ekli_ad_mi
        for k in ("ロイドさん", "ヘンダーソン先生", "ヨルちゃん", "Kenji-senpai", "Sensei"):
            self.assertTrue(ekli_ad_mi(k), k)
        for k in ("ロイド", "ヨルティシア", "Mr. Frodo", "東国"):
            self.assertFalse(ekli_ad_mi(k), k)
        self.sozluk.elle_ekle({"ロイドさん": "Bay Loid", "ロイドマン": "Loidman"})       # eski koşudan kalmış kayıt
        self.assertEqual(self.sozluk.terimler, {"ロイドマン": "Loidman"})

    def test_tekrar_eden_sahnede_model_secer(self):
        satirlar = self._satirlar(["For Athena.", "Common gods.", "Respect their offering.", "Save it!",
                                   "Later that night.", "For Athena.", "Common gods.", "Respect their offering.",
                                   "Burn it!", "No.", "Why?", "No.", "Why?"])
        yanit = iter(["Atena'ya.", "Tanrılar hepimizin.", "Adaklarına saygı gösterin.", "Kurtarın!", "O gece.",
                      "Atena için.", "Tanrılarımız ortak.", "Sunduğumuz armağana saygı gösterin.", "Yakın!",
                      "Hayır.", "Neden?", "Olmaz.", "Niye?"])

        class M:
            secim_istegi = None

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if "items" in veri:
                    M.secim_istegi = veri
                    return json.dumps({"choices": [{"n": x["n"], "pick": 2} for x in veri["items"]]})
                return json.dumps({"lines": [{"id": x["id"], "text": next(yanit)} for x in veri["lines"]]},
                                  ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        ogeler = M.secim_istegi["items"]
        # art arda yinelenen üç replik de tekrar sayılır (en az bir komşusuyla birlikte yeniden geçiyorlar)
        self.assertEqual([x["source"] for x in ogeler], ["for athena.", "common gods.", "respect their offering."])
        self.assertEqual(ogeler[1]["candidates"], {"1": "Tanrılar hepimizin.", "2": "Tanrılarımız ortak."})
        self.assertEqual(sonuc[1], "Tanrılarımız ortak.")                         # ilk değil, seçilen kazanır
        self.assertEqual(sonuc[6], "Tanrılarımız ortak.")
        self.assertEqual([sonuc[0], sonuc[5]], ["Atena için.", "Atena için."])
        self.assertEqual([sonuc[2], sonuc[7]], ["Sunduğumuz armağana saygı gösterin."] * 2)
        self.assertEqual([sonuc[3], sonuc[8]], ["Kurtarın!", "Yakın!"])           # yinelenmeyen satıra dokunulmaz
        self.assertEqual([sonuc[9], sonuc[11]], ["Hayır.", "Olmaz."])             # kısa tesadüfi tekrar: dokunulmaz

    def test_yinelenen_uzun_replik_ayni_okunur(self):
        """Uzun replik ilk geçişinde bölünmüş bir cümlenin parçasıysa yinelenen satır o cümlenin sözleriyle yeniden
        yazılır (yankı). Tek başına yeniden geçen tam cümle modele sorulur: aynı anlamdaysa bir aday seçer, bağlam
        başkaysa "0" der ve her geçiş kendi çevirisiyle kalır. Yanıt gelmezse de kalır."""
        satirlar = self._satirlar([
            "I wish none of this had happened.", "Go home.", "You couldn't keep the gate shut",
            "because you lacked the strength.", "Run!", "Who are you?", "Later.",
            "I wish none of this had happened.", "Why...?!", "Because you lacked the strength.",
            "Who are you?", "You will pay for this, my friend.", "Stop.", "You will pay for this, my friend."])
        satirlar[3].bas = satirlar[2].bit
        yanit = {1: "Keşke bunların hiçbiri olmasaydı.", 2: "Eve git.", 3: "Gücün yetmediği için | kapıyı kapalı tutamadın.",
                 5: "Kaç!", 6: "Sen kimsin?", 7: "Sonra.", 8: "Bunların hiçbiri olmasaydı keşke.", 9: "Neden...?!",
                 10: "Çünkü yeterince güçlü değildin.", 11: "Kimsiniz?", 12: "Bunu ödeyeceksin dostum.",
                 13: "Dur.", 14: "Bunu ödeyeceksiniz dostum."}

        class M:
            secim_istegi = sistem = None

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if "items" in veri:
                    M.secim_istegi, M.sistem = veri, sistem
                    return json.dumps({"choices": [{"n": x["n"], "pick": 2 if x["source"].startswith("I wish") else 0}
                                                   for x in veri["items"] if "candidates" in x],
                                       "texts": [{"n": x["n"], "text": "Gücün yetmediği için"}
                                                 for x in veri["items"] if "sentence" in x]}, ensure_ascii=False)
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        ogeler = M.secim_istegi["items"]
        self.assertEqual([x["source"] for x in ogeler], [
            "I wish none of this had happened.", "Who are you?", "You will pay for this, my friend.",
            "Because you lacked the strength."])
        tekrar = ogeler[0]
        self.assertEqual(tekrar["occurrences"], [{"before": "", "after": "Go home.", "now": 1},
                                                 {"before": "Later.", "after": "Why...?!", "now": 2}])
        self.assertEqual(ogeler[3]["sentence"], "You couldn't keep the gate shut because you lacked the strength.")
        self.assertEqual(ogeler[3]["sentence_translation"], "Gücün yetmediği için kapıyı kapalı tutamadın.")
        self.assertIn('"texts"', M.sistem)
        self.assertIn('"pick": 0', M.sistem)
        # aynı anlam: seçilen aday iki geçişte de
        self.assertEqual([sonuc[0], sonuc[7]], ["Bunların hiçbiri olmasaydı keşke."] * 2)
        self.assertEqual(sonuc[9], "Gücün yetmediği için.")              # yankı duyulur; düşen nokta yerinde
        # "0": başka kişiye söylenmiş, her geçiş kendi çevirisiyle
        self.assertEqual([sonuc[5], sonuc[10]], ["Sen kimsin?", "Kimsiniz?"])
        self.assertEqual([sonuc[11], sonuc[13]], ["Bunu ödeyeceksin dostum.", "Bunu ödeyeceksiniz dostum."])

    def test_tekrar_eden_ifade_ceviriden_sonra_esitlenir(self):
        """Japonca kaynakta uzak satırlarda geçen ifade ayrı parçalarda ayrı çevrildiyse model yalnız o sözleri
        eşitler; çok değişen satır kullanılmaz (gerçek test 21-22). Tırnaklı katakana mesaj satırı işaretlenir."""
        metinler = ["そうか"] * 60
        metinler[3], metinler[45] = "実はプロの鬼ごっこ集団", "おじさんたちと鬼ごっこしてた"
        metinler[10] = "えっと…\n“タソガレ サンジョウ”"
        satirlar = self._satirlar(metinler)
        yanit = {i + 1: "Peki." for i in range(60)}
        yanit.update({4: "Aslında profesyonel bir yakalamaca ekibiyiz.", 46: "Amcalarla körebe oynuyordum.",
                      11: "Şey... Tasogare geldi."})
        giden = []

        class M:
            ifade = None

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if sistem.startswith("Each item is a Japanese expression"):
                    M.ifade = veri
                    return json.dumps({"lines": [{"id": 46, "text": "Amcalarla yakalamaca oynuyordum."},
                                                 {"id": 4, "text": "Tamamen başka bir cümle yazdım buraya."}]},
                                      ensure_ascii=False)
                if "lines" not in veri:
                    return json.dumps({"items": []})
                giden.append(veri)
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]
                                             if not x.get("skip")]}, ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(M.ifade["items"][0]["expression"], "鬼ごっこ")
        self.assertEqual(sonuc[45], "Amcalarla yakalamaca oynuyordum.")
        self.assertEqual(sonuc[3], "Aslında profesyonel bir yakalamaca ekibiyiz.")   # çok değişen kullanılmadı
        mesaj = next(x for v in giden for x in v["lines"] if x["id"] == 11)
        self.assertTrue(mesaj["katakana_message"])
        self.assertEqual(mesaj["hiragana"], "たそがれ さんじょう")
        c = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk)
        self.assertTrue(c._katakana_okunusla("タソガレ サンジョウ", "Şey... “Tasogare sanjo.”"))
        self.assertTrue(c._katakana_okunusla("オニサン コチラ", "“Oni-san kochira.”"))
        self.assertFalse(c._katakana_okunusla("オニサン コチラ", "“Ebe, ben buradayım!”"))

    def test_latin_tekrar_eden_satir_ceviriden_sonra_esitlenir(self):
        """İngilizce kaynakta sözleri uzak satırda yeniden geçen satırın çevirisi modele sorulur; değişiklik ancak az
        değişiklikse ve satır denetiminden geçerse kullanılır (gerçek test 26, American Gods 313/321)."""
        metinler = ["Okay."] * 30
        metinler[3], metinler[12] = "What should I call you if I were so inclined?", "And if I was inclined, what might I call you?"
        satirlar = self._satirlar(metinler)
        for k in range(1, 30):
            satirlar[k].bas = satirlar[k - 1].bit + 2000
        yanit = {i + 1: "Tamam." for i in range(30)}
        yanit.update({4: "İstersem sana ne diye hitap edeyim?", 13: "Peki ben sana ne diye hitap edeyim?"})

        class M:
            ifade = None

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if sistem.startswith("Each item holds two lines"):
                    M.ifade = veri
                    return json.dumps({"lines": [{"id": 13, "text": "Peki istersem sana ne diye hitap edeyim?"}]},
                                      ensure_ascii=False)
                if "lines" not in veri:
                    return json.dumps({"items": []})
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]
                                             if not x.get("skip")]}, ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual([x["id"] for x in M.ifade["items"][0]["lines"]], [4, 13])
        self.assertEqual(sonuc[12], "Peki istersem sana ne diye hitap edeyim?")
        self.assertEqual(sonuc[3], "İstersem sana ne diye hitap edeyim?")

    def test_yinelenen_replik_etiketsiz_eslesir_yarim_cumle_sorulmaz(self):
        """Konuşmacı etiketli geçiş ("（カミラ）", "Gandalf:") etiketsiz geçişle eşleşir; yarım cümle (sonu üç nokta ya da
        virgülle kesik) tek başına yinelense de sorulmaz: fragmandaki yarım cümle tam cümlenin yerine geçiyordu."""
        satirlar = self._satirlar([
            "（カミラ）ぜひ パートナーと ご一緒に", "はい", "Gandalf: Fly, you fools, run now!", "We went to the market,",
            "Stop.", "ぜひ パートナーと ご一緒に", "Fly, you fools, run now!", "Later.", "We went to the market,"])
        yanit = {1: "Partnerinizle birlikte tabii.", 2: "Evet.", 3: "Kaçın aptallar, hemen!", 4: "Pazara gittik,",
                 5: "Dur.", 6: "Partnerinle birlikte gel.", 7: "Kaçın, aptallar, koşun!", 8: "Sonra.",
                 9: "Çarşıya gittik,"}

        class M:
            secim_istegi = None

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if "items" in veri:
                    M.secim_istegi = veri
                    return json.dumps({"choices": [{"n": x["n"], "pick": 1} for x in veri["items"]]})
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        # yarım cümle seçime girmez (ilk geçişi bölünmüş cümlenin parçası olduğu için yankı olarak sorulabilir)
        self.assertEqual([x["source"] for x in M.secim_istegi["items"] if "occurrences" in x],
                         ["ぜひ パートナーと ご一緒に", "Fly, you fools, run now!"])
        self.assertEqual([sonuc[0], sonuc[5]], ["Partnerinizle birlikte tabii."] * 2)
        self.assertEqual([sonuc[2], sonuc[6]], ["Kaçın aptallar, hemen!"] * 2)          # etiketli geçiş eşleşti
        self.assertEqual(sonuc[3], "Pazara gittik,")
        from defta.denetim import _YARIM_BAS, _YARIM_SON, tekrar_metni
        self.assertEqual(tekrar_metni("（カミラ）\n“ぜひ パートナーと”"), "ぜひ パートナーと")
        self.assertEqual(tekrar_metni("- Gandalf: Fly, you fools!"), "Fly, you fools!")
        for yarim in ("We went to the market,", "and then we ran.", "...and then we ran.", "If you go—", "それで…"):
            t = tekrar_metni(yarim)
            self.assertTrue(_YARIM_SON.search(t) or _YARIM_BAS.search(t), yarim)
        for tam in ("はは 存在しない", "Fly, you fools!", "I wish none of this had happened."):
            self.assertFalse(_YARIM_SON.search(tam) or _YARIM_BAS.search(tam), tam)

    def test_yanki_icin_yazilan_gecersiz_ceviri_kullanilmaz(self):
        satirlar = self._satirlar(["You couldn't keep the gate shut", "because you lacked the strength.", "Run!",
                                   "Because you lacked the strength."])
        satirlar[1].bas = satirlar[0].bit
        yanit = {1: "Gücün yetmediği için | kapıyı kapalı tutamadın.", 3: "Kaç!", 4: "Çünkü yeterince güçlü değildin."}

        class M:
            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if "items" in veri:
                    return json.dumps({"texts": [{"n": 1, "text": "力が足りなかったから"}]}, ensure_ascii=False)
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        sonuc = Cevirmen(self.ayar, M(), "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(sonuc[3], "Çünkü yeterince güçlü değildin.")

    def test_zamir_yankisi_sorulur(self):
        satirlar = self._satirlar(["We think you should stay.", '- "We"?\n- All of us.'])
        ilk = {1: "Bence burada kalmalısın.", 2: '- "Bence" mi?\n- Hepimiz.'}
        ikinci = {1: "Bizce burada kalmalısın.", 2: '- "Biz" mi?\n- Hepimiz.'}

        class M:
            gidenler = []

            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                self.gidenler.append(veri)
                kaynak = ikinci if "fix" in veri else ilk
                return json.dumps({"lines": [{"id": x["id"], "text": kaynak[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)

        model = M()
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "modlar": {"konusmaci_tiresi": "korunsun"}})
        sonuc = Cevirmen(ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "x.srt")
        self.assertEqual(sorted(f["id"] for f in model.gidenler[1]["fix"]), [1, 2])
        self.assertIn("echoes", model.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Bizce burada kalmalısın.")

    def test_noktalama_latin(self):
        self.assertEqual(temizlik.noktalama_latin("（ＷＩＳＥ）♪〜"), "(WISE)♪~")
        self.assertTrue(temizlik.latin_disi("abc。"))
        self.assertEqual(temizlik.son_temizlik("♪〜", self.ayar["modlar"], True), "♪~")


KAYMA = """1
00:00:01,000 --> 00:00:03,000
Did you ever see it, Maren?

2
00:00:03,200 --> 00:00:05,000
The old lighthouse of Velloth.

3
00:00:05,200 --> 00:00:07,500
Its flags fly high above the harbour.

4
00:00:07,700 --> 00:00:10,000
Nobody has lived there for years.

5
00:00:12,000 --> 00:00:13,000
Oh, Tolen.
"""


class KaymaTestleri(unittest.TestCase):
    """Çeviri yanlış altyazıya yazılmışsa script yakalar; okunamayan kesme yeri ve kötü satır bölme düzelir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gunluk = Gunluk(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _cevir(self, kural, sozluk=None, ayar=None):
        ayar = ayar or ayar_birlestir({"model": {"es_zamanli_istek": 1}})
        satirlar = srt.srt_ayristir(KAYMA)
        for s in satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        model = SahteModel(kural)
        sonuc = Cevirmen(ayar, model, "S", self.gunluk, sozluk=sozluk).cevir(satirlar, "a.srt")
        return sonuc, model

    def test_komsuda_ayni_ceviri_kaymadir_blok_yeniden_sorulur(self):
        def kural(cagri, satirlar):
            if cagri == 1:                       # 2 ve 3, bir sonrakinin çevirisini taşıyor; 3 ile 4 aynı
                metin = {s["id"]: s["text"] for s in satirlar}
                for s in satirlar:
                    if s["id"] in (2, 3):
                        s["text"] = metin[s["id"] + 1] if s["id"] == 3 else metin[3]
            return satirlar
        sonuc, model = self._cevir(kural)
        self.assertEqual(model.cagri, 2)
        self.assertEqual(sorted(f["id"] for f in model.gidenler[1]["fix"]), [1, 2, 3, 4, 5])   # sonraki satır da
        self.assertIn("shifted", model.gidenler[1]["fix"][0]["problem"])
        self.assertIn("Velloth", sonuc[1])
        self.assertIn("flags", sonuc[2])

    def test_kapaliyken_kayma_sorulmaz(self):
        def kural(cagri, satirlar):
            if cagri == 1:
                satirlar[2]["text"] = satirlar[3]["text"]
            return satirlar
        _, model = self._cevir(kural, ayar=ayar_birlestir({"model": {"es_zamanli_istek": 1},
                                                           "kontrol": {"kayma_kontrolu": False}}))
        self.assertEqual(model.cagri, 1)

    def test_degisen_ad_ve_komsuya_kayan_ad_sorulur(self):
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle(degismez=["Maren", "Velloth", "Tolen"])

        def kural(cagri, satirlar):
            if cagri == 1:
                for s in satirlar:
                    if s["id"] == 5:
                        s["text"] = "Ah, Maren."                        # kaynakta Tolen
                    elif s["id"] == 1:
                        s["text"] = "TR Did you ever see the lighthouse of Velloth, Maren?"
                    elif s["id"] == 2:
                        s["text"] = "TR It shines like a spear."         # ad bir önceki altyazıya gitmiş
            return satirlar
        sonuc, model = self._cevir(kural, sozluk)
        sorunlar = {f["id"]: f["problem"] for f in model.gidenler[1]["fix"]}
        self.assertIn('the source names "Tolen" but the translation names "Maren"', sorunlar[5])
        self.assertIn("shifted", sorunlar[1])
        self.assertIn("shifted", sorunlar[2])
        self.assertEqual(sonuc[4], "TR Oh, Tolen.")

    def test_kayma_oncesindeki_uc_satir_da_sorulur(self):
        """Kayma işaretli satırdan önce başlamış olabilir: önceki üç satır da birlikte sorulur. Yalnız işaretli iki
        satır sorulunca öncekiler kaymış kaldı (gerçek test 26, American Gods 275-277). Sonraki satırlar da sorulur
        (gerçek test 28, American Gods 366): aşağıdaki testte 2-3'teki kayma 4-5'i de kapsar."""
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle(degismez=["Maren", "Velloth", "Tolen"])

        def kural(cagri, satirlar):
            if cagri == 1:
                for s in satirlar:
                    if s["id"] == 4:
                        s["text"] = "TR Nobody has lived there for years, Tolen."   # ad sonraki altyazıdan gelmiş
                    elif s["id"] == 5:
                        s["text"] = "TR Oh."
            return satirlar
        sonuc, model = self._cevir(kural, sozluk)
        self.assertEqual(sorted(f["id"] for f in model.gidenler[1]["fix"]), [1, 2, 3, 4, 5])
        self.assertEqual(sonuc[4], "TR Oh, Tolen.")

    def test_kayma_sonrasindaki_uc_satir_da_sorulur(self):
        """Kaymanın sonu işaretli aralığın dışına taşabilir: sonraki üç satır da birlikte sorulur (gerçek test 28,
        American Gods 366)."""
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle(degismez=["Maren", "Velloth", "Tolen"])

        def kural(cagri, satirlar):
            if cagri == 1:
                for s in satirlar:
                    if s["id"] == 1:
                        s["text"] = "TR Did you ever see the lighthouse of Velloth, Maren?"
                    elif s["id"] == 2:
                        s["text"] = "TR Its flags fly high above the harbour."
                    elif s["id"] == 3:
                        s["text"] = "TR Nobody has lived there for years."
            return satirlar
        sonuc, model = self._cevir(kural, sozluk)
        self.assertEqual(sorted(f["id"] for f in model.gidenler[1]["fix"]), [1, 2, 3, 4, 5])
        self.assertEqual(sonuc[2], "TR Its flags fly high above the harbour.")

    def _bolunmus(self, metinler, ilk, duzeltme):
        satirlar = srt.srt_ayristir("".join(
            f"{n + 1}\n00:00:{2 * n:02d},000 --> 00:00:{2 * n + 2:02d},000\n{m}\n\n" for n, m in enumerate(metinler)))
        for s_ in satirlar:
            s_.metin, s_.ustte = temizlik.kaynak_hazirla(s_.metin)
        istekler = []

        class M:
            def iste(self, sistem, kullanici, dusunme=None):
                veri = json.loads(kullanici)
                if "lines" not in veri:
                    return json.dumps({"items": []})
                istekler.append(veri)
                yanit = duzeltme if "fix" in veri else ilk
                return json.dumps({"lines": [{"id": x["id"], "text": yanit[x["id"]]} for x in veri["lines"]]},
                                  ensure_ascii=False)
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}})
        return Cevirmen(ayar, M(), "S", self.gunluk).cevir(satirlar, "a.srt"), istekler

    def test_bolunmus_cumlenin_son_parcasi_sonraki_satirin_cevirisiyse_sorulur(self):
        """Model bölünmüş cümlenin son parçasını sonraki altyazının içeriğiyle dolduruyordu: cümle ekranda iki kez
        çıkıyor, bazı koşularda kayma buradan başlıyordu (gerçek test 26-32, American Gods 359-361)."""
        metinler = ["They give you a free little kit", "in first class.", "You might not have known that.", "Really?"]
        ilk = {1: "Birinci sınıfta bedava ufak bir set veriyorlar. | Bunu bilmiyor olabilirsin.",
               3: "Bunu bilmiyor olabilirsin.", 4: "Gerçekten mi?"}
        duzeltme = {1: "Birinci sınıfta bedava | ufak bir set veriyorlar.", 3: "Bunu bilmiyor olabilirsin.",
                    4: "Gerçekten mi?"}
        sonuc, istekler = self._bolunmus(metinler, ilk, duzeltme)
        fix = {f["id"]: f["problem"] for f in istekler[1]["fix"]}
        self.assertIn("repeats the translation of id 3", fix[1])
        self.assertIn(3, fix)
        self.assertEqual((sonuc[0] + " " + sonuc[1]).replace("\n", " "), "Birinci sınıfta bedava ufak bir set veriyorlar.")
        self.assertEqual(sonuc[2], "Bunu bilmiyor olabilirsin.")

    def test_kaynakta_da_yinelenen_soz_kayma_sayilmaz(self):
        """Kaynak aynı sözü sonraki altyazıda gerçekten yineliyorsa çevirideki tekrar doğrudur: yeniden sorulmaz."""
        metinler = ["I told you to stop because", "I can't do this anymore.", "I can't do this anymore.", "Really?"]
        ilk = {1: "Sana dur dedim, çünkü | bunu artık yapamıyorum.", 3: "Bunu artık yapamıyorum.", 4: "Gerçekten mi?"}
        sonuc, istekler = self._bolunmus(metinler, ilk, {})
        self.assertEqual(len(istekler), 1)
        self.assertEqual(sonuc[2], "Bunu artık yapamıyorum.")
        metinler = ["You are too forward and familiar,", "and I don't like it.", "I don't like you.", "Really?"]
        ilk = {1: "Fazla samimi ve laubalisin, | bundan hoşlanmıyorum.", 3: "Senden hoşlanmıyorum.", 4: "Gerçekten mi?"}
        self.assertEqual(len(self._bolunmus(metinler, ilk, {})[1]), 1)          # kaynaklar da birbirine benziyor

    def test_ad_denetimi_sozcugun_icinde_ad_aramaz(self):
        """LOTR 363 (gerçek test 10-11): "hobbit" "hobbiton"un içinde bulunuyor, küçük harfli "bagginses" görülmüyordu;
        doğru çeviri "Baggins yok" -> "hiç hobbit yok" diye bozuldu. Ad tam sözcük aranır; kaynakta küçük harfle
        geçen ad çeviride fazla sayılmaz. Gerçek değişiklik (Tolen -> Maren) yine sorulur."""
        kaynak = ("1\n00:00:01,000 --> 00:00:03,000\nThere's no bagginses around here.\nThey're all up in hobbiton.\n\n"
                  "2\n00:00:04,000 --> 00:00:06,000\nThe hobbits are hiding.\n\n"
                  "3\n00:00:07,000 --> 00:00:09,000\nOh, Tolen.\n")
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle(degismez=["hobbit", "Baggins", "Tolen", "Maren"])
        ceviri = {1: "Buralarda Baggins yok.\nHepsi Hobbiton'da.", 2: "Hobbitler saklanıyor.", 3: "Ah, Maren."}

        def kural(cagri, satirlar):
            if cagri == 1:
                for x in satirlar:
                    x["text"] = ceviri[x["id"]]
            return satirlar
        satirlar = srt.srt_ayristir(kaynak)
        for x in satirlar:
            x.metin, x.ustte = temizlik.kaynak_hazirla(x.metin)
        model = SahteModel(kural)
        Cevirmen(ayar_birlestir({"model": {"es_zamanli_istek": 1}}), model, "S", self.gunluk, sozluk=sozluk).cevir(
            satirlar, "a.srt")
        sorulan = {f["id"]: f["problem"] for g in model.gidenler[1:] for f in g.get("fix", [])}
        self.assertNotIn(1, sorulan)
        self.assertIn('the source names "Tolen" but the translation names "Maren"', sorulan.get(3, ""))

    def test_adlari_kucuk_yazan_kaynakta_eksik_ad_da_sorulur(self):
        """LOTR 363 (gerçek test 14): OCR'lı kaynak adları küçük yazıyor; "bagginses" -> "Sackville-Bagginses"
        yakalanmıyordu. Adları çoğunlukla küçük yazan dosyada eksik ad küçük harfle de aranır."""
        satir = lambda n, m: f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},900\n{m}\n"
        metinler = ["There's no bagginses around here.", "We met gandalf and frodo in bree.",
                    "frodo and sam left bree.", "gandalf, frodo! the sackville-bagginses are here.",
                    "sam waited for frodo.", "gandalf saw frodo and sam."]
        kaynak = "\n".join(satir(n, m) for n, m in enumerate(metinler, 1))
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle(degismez=["Baggins", "Sackville-Bagginses", "Gandalf", "Frodo", "Sam", "Bree"])
        ceviri = {1: "Burada Sackville-Bagginses yok.", 2: "Bree'de Gandalf ve Frodo ile tanıştık.",
                  3: "Frodo ve Sam Bree'den ayrıldı.", 4: "Gandalf, Frodo! Sackville-Bagginsler burada.",
                  5: "Sam, Frodo'yu bekledi.", 6: "Gandalf, Frodo ve Sam'i gördü."}

        def kural(cagri, satirlar):
            if cagri == 1:
                for x in satirlar:
                    x["text"] = ceviri[x["id"]]
            return satirlar
        satirlar = srt.srt_ayristir(kaynak)
        for x in satirlar:
            x.metin, x.ustte = temizlik.kaynak_hazirla(x.metin)
        model = SahteModel(kural)
        Cevirmen(ayar_birlestir({"model": {"es_zamanli_istek": 1}}), model, "S", self.gunluk, sozluk=sozluk).cevir(
            satirlar, "a.srt")
        sorulan = {f["id"]: f["problem"] for g in model.gidenler[1:] for f in g.get("fix", [])}
        self.assertIn('the source names "Baggins"', sorulan.get(1, ""))
        self.assertNotIn(4, sorulan)

    def test_satir_ozel_adin_ortasindan_bolunmez(self):
        self.assertEqual(temizlik.sar("Eski haritada Kara Dağ'ın ardındaki yol görünüyordu.", 42),
                         "Eski haritada Kara Dağ'ın\nardındaki yol görünüyordu.")
        self.assertEqual(temizlik.sar("Yalnızca gündüz yolculuk et. Yoldan da uzak dur.", 42),
                         "Yalnızca gündüz yolculuk et.\nYoldan da uzak dur.")

    def test_akrabalik_sozcugu_unvan_kaydina_girmez(self):
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.unvanlar = ["Uncle", "Mr"]
        sozluk.girdi_yaz("Uncle", tur="title", hedef="Amca", kalip="Amca {ad}", terim=False)
        self.assertEqual(sozluk.gorunum(ayar_birlestir({})).unvan_kaydi, {"Mr": "Bay {ad}"})

    def test_ortusen_terimler_ayni_karsiligi_kullanir(self):
        from defta.terim import ortusenleri_esitle
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle(degismez=["Varn"])
        for yazim, hedef in {"Grey Warden": "Boz Muhafız", "Grey Warden Varn": "Gri Bekçi Varn",
                             "Grey Warden Hall": "Boz Salon"}.items():
            sozluk.girdi_ekle(yazim, "epithet", hedef)          # elle verilen karar geçişlerden korunur
        ortusenleri_esitle(sozluk)
        self.assertEqual(sozluk.terimler["Grey Warden Varn"], "Boz Muhafız Varn")
        self.assertEqual(sozluk.terimler["Grey Warden Hall"], "Boz Salon")        # "Hall" ad değil: dokunulmaz

    def test_kacan_terimde_anlam_notu_da_soylenir(self):
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("Velloth", "term", "Kuleburun", anlam="the watchtower order")

        def kural(cagri, satirlar):
            return satirlar
        _, model = self._cevir(kural, sozluk)
        sorunlar = [f["problem"] for g in model.gidenler for f in g.get("fix", [])]
        self.assertTrue(any(x.endswith("The entry means: the watchtower order") for x in sorunlar), sorunlar)

    def test_siradan_anlami_da_olan_terim_satir_satir_denetlenmez(self):
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("Velloth", "term", "Kuleburun", anlam="the watchtower order", siradan="tower")

        def kural(cagri, satirlar):
            return satirlar
        _, model = self._cevir(kural, sozluk)
        sorunlar = [f["problem"] for g in model.gidenler for f in g.get("fix", [])]
        self.assertFalse(any("glossary" in x for x in sorunlar), sorunlar)      # hangi anlamda geçtiğini script bilemez

    def test_kacan_terim_adiyla_sorulur(self):
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle({"Velloth": "Kuleburun"})

        def kural(cagri, satirlar):
            return satirlar
        _, model = self._cevir(kural, sozluk)
        sorunlar = [f["problem"] for g in model.gidenler for f in g.get("fix", [])]
        self.assertTrue(any('the glossary writes "Velloth" as "Kuleburun"' in x for x in sorunlar), sorunlar)

    def test_bolunmus_cumlede_ayni_parca_iki_kez_yazilmaz(self):
        icerik = "1\n00:00:01,000 --> 00:00:03,000\nThe keeper of the northern light\n\n2\n00:00:03,100 --> 00:00:05,000\nhas not been seen since winter.\n"
        satirlar = srt.srt_ayristir(icerik)
        for s in satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)

        def kural(cagri, satirlar_):
            for s in satirlar_:
                if s["id"] == 1:
                    s["text"] = "Kuzey fenerinin bekçisi kıştan beri görülmedi. | Kuzey fenerinin bekçisi kıştan beri görülmedi."
            return satirlar_
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}})
        sonuc = Cevirmen(ayar, SahteModel(kural), "S", self.gunluk).cevir(satirlar, "a.srt")
        self.assertEqual(sonuc[0], "Kuzey fenerinin bekçisi kıştan beri görülmedi.")     # yinelenen parça atıldı
        self.assertEqual(sonuc[1], BIRLESTI)                                             # süresi ilk altyazıya eklendi

    def test_okunamayan_ceviri_kisalttirilmaz(self):
        # Kısaltma kuralı kaldırıldı (kısalırken anlam gidiyordu): okumaya süresi yetmeyen çeviri yeniden sorulmaz,
        # yazım aşaması yalnız ekranda kalma süresini uzatır. Eski config.toml'daki ayar hata vermeden geçilir.
        icerik = "1\n00:00:01,000 --> 00:00:02,000\nWe leave at dawn.\n\n2\n00:00:02,100 --> 00:00:06,000\nGood.\n"
        satirlar = srt.srt_ayristir(icerik)
        for s in satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        uzun = "Şafak söker sökmez hiç vakit kaybetmeden hep birlikte yola çıkıyoruz."

        def kural(cagri, satirlar_):
            for s in satirlar_:
                if s["id"] == 1:
                    s["text"] = uzun if cagri == 1 else "Şafakta yola çıkıyoruz."
            return satirlar_
        model = SahteModel(kural)
        eski_ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "kontrol": {"hizli_satiri_kisalt": True}})
        self.assertNotIn("hizli_satiri_kisalt", eski_ayar["kontrol"])
        self.assertEqual(Cevirmen(eski_ayar, model, "S", self.gunluk).cevir(satirlar, "a.srt")[0], uzun)
        self.assertEqual(model.cagri, 1)

    def test_kucuk_harfli_terim_buyuk_ve_cogul_gecse_de_denetlenir(self):
        icerik = "1\n00:00:01,000 --> 00:00:04,000\nWardens came from the north.\n\n2\n00:00:05,000 --> 00:00:08,000\nNobody saw them.\n"
        satirlar = srt.srt_ayristir(icerik)
        for s in satirlar:
            s.metin, s.ustte = temizlik.kaynak_hazirla(s.metin)
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.elle_ekle({"warden": "Bekçi"})

        def kural(cagri, satirlar_):
            for s in satirlar_:
                if s["id"] == 1:
                    s["text"] = "Korucular kuzeyden geldi." if cagri == 1 else "Bekçiler kuzeyden geldi."
            return satirlar_
        model = SahteModel(kural)
        sonuc = Cevirmen(ayar_birlestir({"model": {"es_zamanli_istek": 1}}), model, "S", self.gunluk, sozluk=sozluk).cevir(satirlar, "a.srt")
        self.assertIn('the glossary writes "warden" as "Bekçi"', model.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Bekçiler kuzeyden geldi.")

    def test_kesif_yanitlari_birlesir(self):
        from defta.terim import _kesifleri_birlestir
        y = _kesifleri_birlestir([{"language": "en", "work": "", "items": ["wraith"]},
                                  {"language": "en", "work": "Saltkeep", "items": ["wraith", {"s": "warden"}, 3]},
                                  None])
        self.assertEqual((y["language"], y["work"], y["items"]), ("en", "Saltkeep", ["wraith", "warden"]))


class BicimTestleri(unittest.TestCase):
    """srt dışındaki girdiler: ass/ssa, vtt, sub. Örnekler bu testler için yazıldı."""

    ASS = r"""[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic, Alignment, MarginV
Style: Default,Arial,48,&H00FFFFFF,0,0,2,20
Style: Dusunce,Arial,48,&H00FFFFFF,0,-1,2,20
Style: Ust,Arial,48,&H00FFFFFF,0,0,8,20
Style: OP-Romaji,Arial,40,&H00FFFFFF,0,0,8,20
Style: OP-English,Arial,40,&H00FFFFFF,0,0,2,20
Style: OP-Kanji,Arial,40,&H00FFFFFF,0,0,8,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.50,0:00:01.00,Default,,0,0,0,,not: bu satir okunmaz
Dialogue: 0,0:00:01.00,0:00:03.00,Default,Kaede,0,0,0,,The tide turned, and nobody saw it.\NNot even the harbour master.
Dialogue: 0,0:00:03.50,0:00:05.00,Default,,0,0,0,,I said {\i1}never{\i0} open that door.
Dialogue: 0,0:00:05.50,0:00:07.00,Dusunce,,0,0,0,,She knows.
Dialogue: 0,0:00:07.50,0:00:09.00,Default,,0,0,0,,{\an8}Voices from the upper deck.
Dialogue: 0,0:00:09.50,0:00:11.00,Ust,,0,0,0,,Radio chatter.
Dialogue: 0,0:00:11.00,0:00:14.00,Default,,0,0,0,,{\pos(320,60)\fs30}SALTKEEP HARBOUR OFFICE
Dialogue: 1,0:00:11.00,0:00:14.00,Default,,0,0,0,,{\pos(322,62)\fs30}SALTKEEP HARBOUR OFFICE
Dialogue: 0,0:00:11.50,0:00:13.00,Default,,0,0,0,,Is this the place?
Dialogue: 0,0:00:14.50,0:00:16.00,Default,,0,0,0,,{\p1}m 0 0 l 100 0 100 100 0 100{\p0}
Dialogue: 0,0:00:20.00,0:00:24.00,OP-Romaji,,0,0,0,,Kaze no machi de
Dialogue: 0,0:00:20.00,0:00:24.00,OP-English,,0,0,0,,In the town of wind
Dialogue: 0,0:00:20.00,0:00:24.00,OP-Kanji,,0,0,0,,風の街で
Comment: 0,0:00:25.00,0:00:28.00,Default,,0,0,0,karaoke,{\k20}yo{\k30}ru {\k25}no {\k40}umi
Dialogue: 0,0:00:25.00,0:00:25.20,Default,,0,0,0,fx,{\pos(100,40)}yo
Dialogue: 0,0:00:25.20,0:00:25.50,Default,,0,0,0,fx,{\pos(130,40)}ru
"""

    def _ass(self, **ustune):
        from defta import bicim
        return bicim.ayristir(self.ASS, ".ass", ayar_birlestir(ustune))

    def test_ass_metin_zaman_ve_kodlar(self):
        s = self._ass()
        self.assertEqual(s[0].metin, "The tide turned, and nobody saw it.\nNot even the harbour master.")   # metindeki virgül
        self.assertEqual((s[0].bas, s[0].bit), (1000, 3000))
        self.assertEqual(s[1].metin, "I said <i>never</i> open that door.")
        self.assertEqual(s[2].metin, "<i>She knows.</i>")                                  # stilin kendisi italik
        self.assertEqual(s[3].metin, "{\\an8}Voices from the upper deck.")                 # satırdaki kod
        self.assertEqual(s[4].metin, "{\\an8}Radio chatter.")                              # stilin hizası
        self.assertEqual([x.no for x in s], list(range(1, len(s) + 1)))
        self.assertFalse(any("not:" in x.metin or "m 0 0" in x.metin for x in s))          # yorum ve çizim atılır

    def test_ass_ekran_yazisi_katmanlari_tek_satir_ve_isaretli(self):
        s = self._ass()
        yazi = [x for x in s if x.metin == "SALTKEEP HARBOUR OFFICE"]
        self.assertEqual(len(yazi), 1)
        self.assertTrue(yazi[0].ekran)
        self.assertFalse(any(x.ekran for x in s if x is not yazi[0]))
        # zaman sırası: yazı 11,0'da başlar, araya giren replik 11,5'te
        self.assertLess(s.index(yazi[0]), next(n for n, x in enumerate(s) if x.metin == "Is this the place?"))

    def test_ass_sarki_tek_dize_ve_moda_gore_secim(self):
        metinler = [x.metin for x in self._ass()]
        self.assertIn("{\\an8}♪ Kaze no machi de ♪", metinler)            # şarkı özgün kalacak: okunuşu
        self.assertNotIn("♪ In the town of wind ♪", metinler)
        self.assertFalse(any("風" in x for x in metinler))
        self.assertIn("♪ yoru no umi ♪", metinler)                         # karaoke şablonunun asıl dizesi
        self.assertFalse(any(x in ("yo", "ru") for x in metinler))         # hece hece "fx" satırları atılır
        cevrilecek = [x.metin for x in self._ass(modlar={"sarki": "ceviri"})]
        self.assertIn("♪ In the town of wind ♪", cevrilecek)
        self.assertFalse(any("Kaze no machi" in x for x in cevrilecek))

    def test_ass_sarki_stili_kapaliyken_sarki_siradan_satirdir(self):
        metinler = [x.metin for x in self._ass(girdi={"ass_sarki_stili": False})]
        self.assertIn("In the town of wind", metinler)
        self.assertFalse(any("♪" in x for x in metinler))

    CIFT = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic, Alignment, MarginV
Style: Main,Arial,48,&H00FFFFFF,0,0,2,20
Style: Default-ja,Arial,40,&H00FFFFFF,0,0,8,20
Style: sign_12_Harbour_Office,Arial,40,&H00FFFFFF,0,0,5,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "".join(
        f"Dialogue: 0,0:00:{n * 4:02d}.00,0:00:{n * 4 + 3:02d}.00,Default-ja,,0,0,0,,（楓）港の灯りが{n}つ消えた\n"
        f"Dialogue: 0,0:00:{n * 4:02d}.10,0:00:{n * 4 + 3:02d}.20,Main,,0,0,0,,Harbour light number {n}\\Nwent out.\n"
        for n in range(1, 13)) + (
        "Dialogue: 0,0:00:05.00,0:00:09.00,sign_12_Harbour_Office,,0,0,0,,Harbour Office\n"
        "Dialogue: 0,0:00:55.00,0:00:56.00,Main,,0,0,0,,Huh?\n"
        "Dialogue: 0,0:00:58.00,0:00:59.00,Default-ja,,0,0,0,,うん\n")

    def _cift(self, mod):
        from defta import bicim
        return bicim.ayristir(self.CIFT, ".ass", ayar_birlestir({"girdi": {"cift_dil": mod}}))

    def test_cift_dilli_ass_ozgun_dil_cevrilir_ikinci_dil_basvuru_olur(self):
        s = self._cift("referans")
        ja = [x for x in s if "港" in x.metin]
        self.assertEqual(len(ja), 12)
        self.assertTrue(all(x.ref == f"Harbour light number {n} went out." for n, x in enumerate(ja, 1)))
        self.assertFalse(any("Harbour light" in x.metin for x in s))           # ikinci dilin eşli satırları yazılmaz
        self.assertFalse(any(x.metin.startswith("{\\an8}") for x in ja))       # üstte durması yalnızca yerleşimdi
        self.assertEqual([x.metin for x in s if x.ekran], ["Harbour Office"])  # stil adından ekran yazısı, yerinde kalır
        self.assertIn("Huh?", [x.metin for x in s])                            # yalnızca bir dilde olan replik kalır
        self.assertEqual(next(x for x in s if x.metin == "うん").ref, "")

    def test_cift_dil_oteki_kipler(self):
        self.assertFalse(any(x.ref for x in self._cift("asil")))
        self.assertEqual(sum("港" in x.metin for x in self._cift("asil")), 12)
        ikinci = self._cift("ikinci")
        self.assertEqual(sum("港" in x.metin for x in ikinci), 0)
        self.assertEqual(sum("Harbour light" in x.metin for x in ikinci), 12)
        kapali = self._cift("kapali")
        self.assertEqual((sum("港" in x.metin for x in kapali), sum("Harbour light" in x.metin for x in kapali)), (12, 12))

    def test_basvuru_istege_gider_cikti_tek_altyazidir(self):
        from defta.dosya import dosya_cevir
        ayar = ayar_birlestir({"sozluk": {"otomatik": False}})
        with tempfile.TemporaryDirectory() as d:
            kaynak, hedef = os.path.join(d, "a.ja-en.ass"), os.path.join(d, "a.tr.srt")
            with open(kaynak, "w", encoding="utf-8") as f:
                f.write(self.CIFT)
            model = SahteModel(lambda cagri, satirlar: [{"id": o["id"], "text": f"Liman ışığı söndü {o['id']}."}
                                                        for o in satirlar])
            dosya_cevir(kaynak, hedef, "a.ja-en.ass", ayar, model, istem_olustur(ayar, basvuru=True), Gunluk(os.path.join(d, "logs")),
                        os.path.join(d, "onb"))
            giden = [o for g in model.gidenler for o in g["lines"] if not o.get("skip")]
            self.assertEqual(sum("ref" in o for o in giden), 12)
            self.assertTrue(all(o["ref"].startswith("Harbour light number") for o in giden if "ref" in o))
            with open(hedef, encoding="utf-8") as f:
                cikti = f.read()
            self.assertEqual(cikti.count("-->"), 15)                        # 12 replik + tabela + iki tek dilli replik
            self.assertNotIn("Harbour light", cikti)
        self.assertIn('"ref" on a line', istem_olustur(ayar, basvuru=True))
        self.assertNotIn('"ref" on a line', istem_olustur(ayar))                 # başvurusu olmayan dosyada kuralı da yok

    def test_dosya_adindan_yapim_adi_ve_hedef(self):
        import ceviri
        for ad, yapim in (("[Grup] Kaze no Machi S2 - 01 [720p][ETIKET].ja-en.ass", "Kaze no Machi"),
                          ("[Grup] Kaze no Machi S2 - 02 [720p][ETIKET].ja.srt", "Kaze no Machi"),
                          ("[Grup] Saltkeep Chronicles - 03 (1080p) [ABCD1234].srt", "Saltkeep Chronicles"),
                          ("Saltkeep.Chronicles.S01E01.en.srt", "Saltkeep Chronicles"),
                          ("What Is It.srt", "What Is It"), ("[Grup] Tek Film.srt", "Tek Film")):
            self.assertEqual(ceviri.yapim_adi(ad), yapim)
        self.assertEqual(ceviri.hedef_adi("[Grup] Kaze - 01 [720p].ja-en.ass", "tr"), "[Grup] Kaze - 01 [720p].tr.srt")
        self.assertEqual(ceviri.hedef_adi("What Is It.srt", "tr"), "What Is It.tr.srt")

    @unittest.skipIf(tomllib is None, "config.toml okumak Python 3.11 ister")

    def test_baslatici_durduran_hatanin_nedenini_yazar(self):
        """Düzelmeyecek model hatası (biten kredi) yeni istek gönderilmesini durdurur; konsol ve hatalar.txt asıl
        nedeni yazar, özet yine çıkar. O an yolda olan isteklerin yanıtı beklenir: ödenmiş yanıtla biten dosya yazılır
        (bakiye yüklenip yeniden çalıştırınca yalnız kalan dosya çevrilir)."""
        import contextlib
        import glob
        import io
        import sys
        import ceviri
        from defta import model as model_modulu

        class Kotasiz(model_modulu.Model):
            def _iste(self, sistem, kullanici, dusunme=None):       # ağa hiç çıkmaz
                satirlar = json.loads(kullanici)["lines"]
                if satirlar and isinstance(satirlar[0], str):        # dil isteği
                    return json.dumps({"language": "en"})
                if "Zorlu" in kullanici:                             # yalnızca c.srt'nin çeviri isteği hata verir
                    time.sleep(0.4)
                    raise model_modulu.ModelHatasi("Hesabın kredisi bitmiş")
                time.sleep(1.5)                                      # a ve b o sırada yolda: yanıtları beklenir
                return json.dumps({"lines": [{"id": s["id"], "text": "TR " + s["text"]}
                                             for s in satirlar if not s.get("skip")]})
        gecici = tempfile.TemporaryDirectory()
        self.addCleanup(gecici.cleanup)
        d = gecici.name
        os.makedirs(os.path.join(d, "kaynak", "Dizi"))
        for ad in ("a.srt", "b.srt", "c.srt"):          # a ve b önce Iptal ile biter: asıl neden c'nin hatası
            with open(os.path.join(d, "kaynak", "Dizi", ad), "w", encoding="utf-8") as f:
                f.write(ORNEK.replace("Hello", "Zorlu") if ad == "c.srt" else ORNEK)
        toml = os.path.join(d, "a.toml")
        with open(toml, "w", encoding="utf-8") as f:
            f.write("[klasorler]\n" + "".join(f"{k} = '{os.path.join(d, k)}'\n" for k in ("kaynak", "ceviri", "sozluk", "logs"))
                    + "[sozluk]\notomatik = false\n")
        konsol = io.StringIO()
        with mock.patch.object(ceviri, "Model", Kotasiz), mock.patch.object(sys, "argv", ["ceviri.py", toml]), \
                mock.patch.object(ceviri, "KOK", d), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), contextlib.redirect_stdout(konsol):
            ceviri.main()
        self.assertIn("Model hatası, çalışma durdu: Hesabın kredisi bitmiş", konsol.getvalue())
        log = glob.glob(os.path.join(d, "logs", "*"))[0]
        with open(os.path.join(log, "hatalar.txt"), encoding="utf-8") as f:
            self.assertIn("kredisi bitmiş", f.read())
        self.assertTrue(os.path.exists(os.path.join(log, "ozet.txt")))
        # a ve b'nin yolda olan yanıtları kayda geçti: yeniden çalıştırınca bir daha ödenmez
        kayitlar = []
        for yol in glob.glob(os.path.join(d, ".onbellek", "**", "*.json"), recursive=True):
            with open(yol, encoding="utf-8") as f:
                kayitlar.append(json.load(f).get("sonuc") or {})
        self.assertEqual(len([k for k in kayitlar if any(str(v).startswith("TR ") for v in k.values())]), 2, kayitlar)

    def _ana_akis(self, dosyalar, toml_ek, sahte):
        """ceviri.main sahte modelle: (konsol, okunan dosyalar, klasör). Ağa çıkılmaz."""
        import contextlib
        import io
        import sys
        import ceviri
        from defta import dosya as dosya_modulu, model as model_modulu

        class Yerel(model_modulu.Model):
            def _iste(self, sistem, kullanici, dusunme=None):
                return sahte.iste(sistem, kullanici, dusunme)
        gecici = tempfile.TemporaryDirectory()
        self.addCleanup(gecici.cleanup)
        d = gecici.name
        os.makedirs(os.path.join(d, "kaynak", "Dizi"))
        for ad, icerik in dosyalar.items():
            with open(os.path.join(d, "kaynak", "Dizi", ad), "w", encoding="utf-8") as f:
                f.write(icerik)
        toml = os.path.join(d, "a.toml")
        with open(toml, "w", encoding="utf-8") as f:
            f.write("[klasorler]\n" + "".join(f"{k} = '{os.path.join(d, k)}'\n" for k in ("kaynak", "ceviri", "sozluk", "logs"))
                    + "[model]\nes_zamanli_istek = 1\n" + toml_ek)
        okunan, gercek = [], dosya_modulu.dosya_oku

        def say(yol, ayar):
            okunan.append(os.path.basename(yol))
            return gercek(yol, ayar)
        konsol = io.StringIO()
        with mock.patch.object(ceviri, "Model", Yerel), mock.patch.object(ceviri, "dosya_oku", say), \
                mock.patch.object(ceviri, "KOK", d), \
                mock.patch.object(dosya_modulu, "dosya_oku", say), mock.patch.object(sys, "argv", ["ceviri.py", toml]), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), contextlib.redirect_stdout(konsol):
            ceviri.main()
        return konsol.getvalue(), okunan, d

    @unittest.skipIf(tomllib is None, "config.toml okumak Python 3.11 ister")

    def test_her_dosya_bir_kez_okunur(self):
        for sozluk_acik in (True, False):
            sahte = SahteModel(terim={"Yor": {"t": "name"}})
            _, okunan, d = self._ana_akis({"a.srt": ORNEK, "b.srt": ORNEK.replace("Hello", "Hey")},
                                          "" if sozluk_acik else "[sozluk]\notomatik = false\n", sahte)
            self.assertEqual(sorted(okunan), ["a.srt", "b.srt"], sozluk_acik)
            self.assertEqual(len(os.listdir(os.path.join(d, "ceviri", "Dizi"))), 2)
            # dil keşiften geliyorsa ayrıca sorulmaz; sözlük kapalıyken küçük dil isteği gider
            self.assertEqual(getattr(sahte, "dil_istekleri", 0), 0 if sozluk_acik else 2)

    @unittest.skipIf(tomllib is None, "config.toml okumak Python 3.11 ister")

    def test_kesif_yokken_dil_kucuk_istekle_bulunur(self):
        """Keşif kapalıyken de kaynağı hedef dilde olan dosya çevrilmez; başka dildeki çevrilir."""
        turkce = "1\n00:00:01,000 --> 00:00:03,000\nBunu yarın konuşuruz.\n\n2\n00:00:04,000 --> 00:00:06,000\nTamam, görüşürüz.\n"
        for dil, cevrilir in (("tr", False), ("en", True)):
            sahte = SahteModel()
            sahte.dil = dil
            konsol, _, d = self._ana_akis({"a.srt": turkce}, "[sozluk]\nkesif = false\n", sahte)
            self.assertEqual(sahte.dil_istekleri, 1, dil)
            self.assertEqual(sahte.cagri > 0, cevrilir, dil)
            self.assertEqual(os.path.exists(os.path.join(d, "ceviri", "Dizi", "a.tr.srt")), cevrilir, dil)
            self.assertEqual("atlandı (zaten" in konsol, not cevrilir, dil)

    @unittest.skipIf(tomllib is None, "config.toml okumak Python 3.11 ister")

    def test_iki_bicimden_srt_cevrilir_dil_eki_olsa_da(self):
        # film.ass + film.en.srt aynı çıktıya (film.tr.srt) yazılır: öncelik sırası (srt, ass...) dil ekine takılmaz.
        konsol, okunan, d = self._ana_akis({"film.ass": self.ASS, "film.en.srt": ORNEK},
                                           "[sozluk]\notomatik = false\n", SahteModel())
        self.assertEqual(okunan, ["film.en.srt"])
        self.assertIn("başka biçimi çevriliyor: " + os.path.join("Dizi", "film.en.srt"), konsol)
        self.assertEqual(os.listdir(os.path.join(d, "ceviri", "Dizi")), ["film.tr.srt"])

    @unittest.skipIf(tomllib is None, "config.toml okumak Python 3.11 ister")

    def test_okunamayan_dosya_cevrildi_diye_sayilmaz(self):
        # Görüntü tabanlı .sub atlanır ve rapora yazılır: konsolda "✓" almaz, özette çevrilen dosya sayılmaz.
        konsol, _, d = self._ana_akis({"a.sub": "\x00\x00\x01\xba\x44\x00", "b.srt": ORNEK},
                                      "[sozluk]\notomatik = false\n", SahteModel())
        self.assertNotIn("✓ " + os.path.join("Dizi", "a.sub"), konsol)
        self.assertIn("atlandı (okunamadı", konsol)
        self.assertIn("Çevrilen dosya: 1\n", konsol)
        self.assertIn("Atlanan dosya: 1\n", konsol)
        self.assertEqual(os.listdir(os.path.join(d, "ceviri", "Dizi")), ["b.tr.srt"])

    def test_japonca_ekten_sonra_unlu_uyumu(self):
        d = temizlik.tr_ek_uyumu
        self.assertEqual(d("Haruka-kun'ın defteri, Haruka-kun'ı gördün mü, Haruka-kun'e sor, Haruka-kun'den"),
                         "Haruka-kun'un defteri, Haruka-kun'u gördün mü, Haruka-kun'a sor, Haruka-kun'dan")
        self.assertEqual(d("Minato-san'in, Aoi-chan'e, Minato-san'ün"), "Minato-san'ın, Aoi-chan'a, Minato-san'ın")
        self.assertEqual(d("Haruka-kun'un, Minato-san'a, Minato-sensei'ye, Haruka-kun'cuğum"),
                         "Haruka-kun'un, Minato-san'a, Minato-sensei'ye, Haruka-kun'cuğum")      # doğru olana dokunmaz
        # ünlüyle biten ekler kaynaştırma harfi alır; Sensei / Senpai istemdeki gibi "i" ile biter
        self.assertEqual(d("Anya-sama'a, Anya-sama'nin, Loid-dono'nin, Sensei'a, Kenji-senpai'nın, Loid-shi'la"),
                         "Anya-sama'ya, Anya-sama'nın, Loid-dono'nun, Sensei'ye, Kenji-senpai'nin, Loid-shi'yle")
        self.assertEqual(d("Anya-chan'ten, Mob-kun'nun, Anya-sama'yla"), "Anya-chan'dan, Mob-kun'un, Anya-sama'yla")

    def test_buyuk_harfli_ek_cokertmez_ve_buyuk_kalir(self):
        # Eşleşme harf büyüklüğüne bakmaz ve "i" Türkçe İ ile de eşleşir: bağıran replikteki "SENSEİ'YE" çeviri bittikten
        # sonra dosya yazılırken KeyError veriyordu. Büyük harfli ek de küçülmez; yalnız yanlış ünlü düzelir.
        d = temizlik.tr_ek_uyumu
        self.assertEqual(d("SENSEİ'YE DOKUNMA! SENPAİ'Yİ GÖRDÜM."), "SENSEİ'YE DOKUNMA! SENPAİ'Yİ GÖRDÜM.")
        self.assertEqual(d("YAMADA-SHİ'YA SOR, senseı'ye de."), "YAMADA-SHİ'YE SOR, senseı'ye de.")
        self.assertEqual(d("MOB-KUN'UN EVİ, ANYA-SAN'A SOR"), "MOB-KUN'UN EVİ, ANYA-SAN'A SOR")
        self.assertEqual(d("MOB-KUN'IN EVİ, ANYA-SAMA'NİN, SENSEİ'NIN"), "MOB-KUN'UN EVİ, ANYA-SAMA'NIN, SENSEİ'NİN")

    def test_yeni_ekler_ve_ekli_sozcuk_adaylari(self):
        from defta.kaynak import JP_AD_EK, _ek_latin
        from defta.terim import _ekli_sozcukler, _japonca_adaylar
        bul = lambda t: (lambda m: m and (m.group(1), _ek_latin(m.group(2))))(JP_AD_EK.search(t))
        self.assertEqual([bul(t) for t in ("ロイド殿", "ロイド殿下", "ヨル氏", "ヨル氏名", "ダミアン嬢", "アーニャたーん")],
                         [("ロイド", "dono"), None, ("ヨル", "shi"), None, ("ダミアン", "jou"), ("アーニャ", "tan")])
        metin = "\n".join(["犬さん！", "でっかい犬さーん", "あの犬さん どこ？", "（ヨル）ヨルさん", "ヨルさん 待って",
                           "ヨルさん！", "お客さん", "ワンちゃん", "しんちゃん 来て", "しんちゃん！"])
        self.assertEqual(_ekli_sozcukler(metin), {"犬さん": (3, {"犬さーん"}, "犬")})  # etiketteki ad, 2 kez geçen değil
        self.assertEqual(_ekli_sozcukler("黄昏(たそがれ)\n黄昏さん\n黄昏さん！\n黄昏さんか"), {})  # okunuşlu ad: kod adı
        self.assertIn("しん", _japonca_adaylar(metin))                                # hiragana ad + ek

    def test_okunuslu_ad_ve_ek_ceviride_aranir(self):
        satirlar = srt.srt_ayristir("1\n00:00:01,000 --> 00:00:03,000\n２年１組 湊川(みなとがわ)君\n\n"
                                    "2\n00:00:04,000 --> 00:00:06,000\nお母(かあ)さんは元気？\n")
        c = Cevirmen(ayar_birlestir({"sozluk": {"otomatik": False}}), None, "", None)
        c._hazirla(satirlar, "a.srt")
        self.assertEqual(c._satir_kontrol(0, {0: "2. sınıf 1. şubeden Minatogawa."}), "ek")
        self.assertIsNone(c._satir_kontrol(0, {0: "2. sınıf 1. şubeden Minatogawa-kun."}))
        self.assertIsNone(c._satir_kontrol(1, {1: "Annen iyi mi?"}))            # sıradan sözcükteki さん ek aranmaz

    def test_japonca_ad_arti_unvan_terimi_adi_dusurmez(self):
        from defta import terim
        from defta.sozluk import Sozluk
        with tempfile.TemporaryDirectory() as d:
            sozluk = Sozluk(os.path.join(d, "s.json"))
            sozluk.girdi_ekle("湊川", "name", "Minatogawa")
            sozluk.girdi_ekle("湊川会長", "title", "başkan")
            sozluk.girdi_ekle("生徒会長", "title", "öğrenci konseyi başkanı")
            sozluk.girdi_ekle("白波部長", "title", "kulüp başkanı")
            metin = "（湊川）おはよう\n（青海(あおみ)白波(しらなみ)）白波部長と呼べ\n湊川会長、生徒会長の件です"
            bulunan = terim._adla_gecen_unvanlar(sozluk, {}, set(), metin)
            self.assertNotIn("湊川会長", sozluk.terimler)                       # ad sözlükte: girdi bölünür
            self.assertNotIn("白波部長", sozluk.terimler)                       # ad etikette okunuşuyla yazılmış
            self.assertIn("生徒会長", sozluk.terimler)                          # "生徒" ad değil: terim kalır
            self.assertEqual(sozluk.girdiler["部長"]["kalip"], "Kulüp başkanı {ad}")
            self.assertTrue({"会長", "部長"} <= bulunan)

    def test_vtt(self):
        from defta import bicim
        vtt = ("WEBVTT\nKind: captions\n\nNOTE bu bir not\n\nSTYLE\n::cue { color: white }\n\n"
               "giris\n00:01.000 --> 00:03.500 line:10% align:center\n<v Kaede>We leave <c.sari>at dawn</c>.</v>\n\n"
               "2\n01:00:04.000 --> 01:00:06.250\n<i>Bring</i> <00:01:04.500>the lantern.\nAnd the rope.\n\n"
               "00:07.000 --> 00:08.000\n<v 楓>はい。\n")
        s = bicim.ayristir(vtt, ".srt", ayar_birlestir({}))               # uzantı yanlış olsa da içerik tanınır
        self.assertEqual([(x.bas, x.bit) for x in s], [(1000, 3500), (3604000, 3606250), (7000, 8000)])
        self.assertEqual(s[0].metin, "{\\an8}KAEDE: We leave <c.sari>at dawn</c>.</v>")
        self.assertEqual(temizlik.kaynak_hazirla(s[0].metin), ("KAEDE: We leave at dawn.", True))
        self.assertEqual(s[1].metin, "<i>Bring</i> the lantern.\nAnd the rope.")
        self.assertEqual(s[2].metin, "（楓）はい。")

    def test_bos_satirsiz_vtt_de_altyazilar_karismaz(self):
        from defta import bicim
        vtt = ("WEBVTT\n\n1\n00:01.000 --> 00:02.000\nFirst.\n2\n00:03.000 --> 00:04.000 line:10%\nSecond,\n3 of them.\n"
               "00:05.000 --> 00:06.000\nThird.\n")
        s = bicim.ayristir(vtt, ".vtt", ayar_birlestir({}))
        self.assertEqual([(x.bas, x.metin) for x in s],
                         [(1000, "First."), (3000, "{\\an8}Second,\n3 of them."), (5000, "Third.")])

    def test_sub_microdvd_mpl2_subviewer(self):
        from defta import bicim
        ayar = ayar_birlestir({})
        s = bicim.ayristir("{1}{1}25\n{25}{75}First line|second line\n{100}{}{y:i}Whispered.\n{200}{250}/Aside.|Plain.\n",
                           ".sub", ayar)
        self.assertEqual([(x.bas, x.bit, x.metin) for x in s],
                         [(1000, 3000, "First line\nsecond line"), (4000, 7000, "<i>Whispered.</i>"),
                          (8000, 10000, "<i>Aside.</i>\nPlain.")])
        s = bicim.ayristir("{24}{48}Tek satir.\n", ".sub", ayar_birlestir({"girdi": {"sub_fps": 24}}))
        self.assertEqual((s[0].bas, s[0].bit), (1000, 2000))               # kare hızı dosyada yok: ayardaki
        s = bicim.ayristir("[10][35]Deniz sakin.|/Simdilik.\n", ".sub", ayar)
        self.assertEqual([(x.bas, x.bit, x.metin) for x in s], [(1000, 3500, "Deniz sakin.\n<i>Simdilik.</i>")])
        s = bicim.ayristir("[INFORMATION]\n[TITLE]x\n[END INFORMATION]\n00:00:01.50,00:00:03.00\nHold the line.[br]Not yet.\n\n"
                           "00:00:04.00,00:00:05.00\nNow.\n", ".sub", ayar)
        self.assertEqual([(x.bas, x.bit, x.metin) for x in s], [(1500, 3000, "Hold the line.\nNot yet."), (4000, 5000, "Now.")])

    def test_microdvd_kare_hizi_bildirimi(self):
        from defta import bicim
        ayar = ayar_birlestir({"girdi": {"sub_fps": 25}})
        # "00" kare hızı değildir: sıfıra bölme yerine ayardaki hız kullanılır
        s = bicim.ayristir("{1}{1}00.000\n{25}{75}Tek satir.\n", ".sub", ayar)
        self.assertEqual([(x.bas, x.bit, x.metin) for x in s], [(1000, 3000, "Tek satir.")])
        # ilk karede başlayıp ekranda kalan iki haneli sayı repliktir, kare hızı bildirimi değil: zamanlar kaymaz
        s = bicim.ayristir("{0}{50}23\n{100}{150}Sonra.\n", ".sub", ayar)
        self.assertEqual([(x.bas, x.bit, x.metin) for x in s], [(0, 2000, "23"), (4000, 6000, "Sonra.")])

    def test_bozuk_sayilar_bicim_okuyucusunu_cokertmez(self):
        from defta import bicim
        ayar = ayar_birlestir({})
        olay = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        # hiza alanında rakam sayılan ama sayıya çevrilemeyen karakter ya da binlerce hane: stil varsayılan hizayla okunur
        for hiza in ("²", "9" * 5000):
            s = bicim.ayristir("[V4+ Styles]\nFormat: Name, Italic, Alignment\nStyle: Default,0," + hiza + "\n" + olay
                               + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Bir\n", ".ass", ayar)
            self.assertEqual([(x.bas, x.metin) for x in s], [(1000, "Bir")])
        # binlerce haneli saat ya da kare numarası: o satır atlanır, gerisi okunur
        dev = "1" * 5000
        s = bicim.ayristir(olay + "Dialogue: 0," + dev + ":00:01.00,0:00:02.00,Default,,0,0,0,,Bir\n"
                           "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,Iki\n", ".ass", ayar)
        self.assertEqual([x.metin for x in s], ["Iki"])
        s = bicim.ayristir("WEBVTT\n\n" + dev + ":00:01.000 --> " + dev + ":00:02.000\nBir\n\n00:03.000 --> 00:04.000\nIki\n",
                           ".vtt", ayar)
        self.assertEqual([x.metin for x in s], ["Iki"])
        for ac, kapa in ("{}", "[]"):
            s = bicim.ayristir(f"{ac}10{kapa}{ac}20{kapa}Bir\n{ac}{dev}{kapa}{ac}{dev}{kapa}Iki\n", ".sub", ayar)
            self.assertEqual([x.metin for x in s], ["Bir"])

    def test_goruntu_tabanli_sub_hata_verir_calisma_durmaz(self):
        from defta import bicim
        from defta.dosya import dosya_cevir
        with self.assertRaises(bicim.BicimHatasi):
            bicim.ayristir("\x00\x00\x01\xba\x44\x00", ".sub", ayar_birlestir({}))
        with tempfile.TemporaryDirectory() as d:
            kaynak = os.path.join(d, "a.sub")
            with open(kaynak, "wb") as f:
                f.write(b"\x00\x00\x01\xba\x44\x00\x04\x00")
            g = Gunluk(os.path.join(d, "logs"))
            sonuc = dosya_cevir(kaynak, os.path.join(d, "a.tr.srt"), "a.sub", ayar_birlestir({}), None, "S", g)
            self.assertEqual(sonuc["satir"], 0)
            self.assertEqual(g.hata_sayisi, 1)

    def test_ass_dosyasi_srt_olarak_cevrilir_ekran_yazisi_ayara_uyar(self):
        from defta.dosya import dosya_cevir, dosya_oku

        def calis(mod):
            ayar = ayar_birlestir({"sozluk": {"otomatik": False}, "modlar": {"ekran_yazisi": mod, "sarki": "ceviri"}})
            with tempfile.TemporaryDirectory() as d:
                kaynak, hedef = os.path.join(d, "a.ass"), os.path.join(d, "a.tr.srt")
                with open(kaynak, "w", encoding="utf-8") as f:
                    f.write(self.ASS)
                model = SahteModel()
                dosya_cevir(kaynak, hedef, "a.ass", ayar, model, istem_olustur(ayar), Gunluk(os.path.join(d, "logs")),
                            os.path.join(d, "onb"))
                with open(hedef, encoding="utf-8") as f:
                    return f.read(), len(dosya_oku(kaynak, ayar)[1])

        cevir, n = calis("cevir")
        self.assertIn("TR SALTKEEP HARBOUR OFFICE", cevir)
        self.assertIn("1\n00:00:01,000 --> 00:00:03,", cevir)                 # başlangıç aynı; bitiş okuma süresiyle uzayabilir
        self.assertIn("00:00:11,000 --> 00:00:14,000\nTR SALTKEEP", cevir)     # ekran yazısı repliğin üstüne biner: ikisi de yazılır
        self.assertNotIn("{", cevir)                                        # hiçbir ASS kodu çıktıya geçmez
        birak, _ = calis("birak")
        self.assertIn("\nSALTKEEP HARBOUR OFFICE\n", birak)                  # çevrilmeden, aynen
        sil, m = calis("sil")
        self.assertNotIn("SALTKEEP HARBOUR OFFICE", sil)
        self.assertEqual(m, n - 1)


class ModelBeklemeTestleri(unittest.TestCase):
    """Sunucu hep hata dönerse istek asılı kalmaz: sınırlı denemeden sonra hata yükselir ve bekleme bildirilir."""

    def _sunucu(self, yanitla):
        import http.server

        class Isleyici(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                kod, govde, basliklar = yanitla()
                veri = json.dumps(govde).encode("utf-8")
                self.send_response(kod)
                for ad, deger in {"Content-Length": str(len(veri)), **basliklar}.items():
                    self.send_header(ad, deger)
                self.end_headers()
                try:
                    self.wfile.write(veri)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass                # istemci zaman aşımıyla ayrıldı: test bunu bekliyor, iz basılmasın

        sunucu = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Isleyici)
        threading.Thread(target=sunucu.serve_forever, daemon=True).start()
        self.addCleanup(sunucu.shutdown)
        from defta.model import Model
        model = Model(ayar_birlestir({"model": {"api_adresi": f"http://127.0.0.1:{sunucu.server_address[1]}/v1",
                                                "es_zamanli_istek": 4}}), "x")
        model.ILK_BEKLEME = 0.05            # testte beklemeler kısa
        return model

    def _iste(self, model, sure=20):
        from defta.model import ModelHatasi
        sonuc = []

        def dene():
            try:
                sonuc.append(("yanit", model.iste("S", "{}")))
            except ModelHatasi as e:
                sonuc.append(("hata", str(e)))
        is_parcacigi = threading.Thread(target=dene, daemon=True)
        is_parcacigi.start()
        is_parcacigi.join(sure)
        self.assertTrue(sonuc, "istek asılı kaldı: hata da yanıt da dönmedi")
        return sonuc[0]

    def test_surekli_hiz_siniri_hata_olarak_doner_asili_kalmaz(self):
        sayac = {"n": 0}
        tamam = {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        def yanitla():
            sayac["n"] += 1
            if sayac["n"] <= 6:
                return 200, tamam, {}
            time.sleep(0.25)
            return 429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "0.05"}
        model = self._sunucu(yanitla)
        model._gecikti_mi = lambda tur, gecen: gecen > 1      # yanıt gecikince kopya gönderilsin
        mesajlar = []
        model.bildir = mesajlar.append
        for _ in range(6):
            model.iste("S", "{}")
        tur, metin = self._iste(model)
        self.assertEqual(tur, "hata")
        self.assertIn("429", metin)
        self.assertEqual(sayac["n"] - 6, 14)                   # istek + tek kopya, her biri 7 deneme; fazlası gitmez
        self.assertTrue(mesajlar and "hız sınırı (HTTP 429)" in mesajlar[0])

    def test_kisa_retry_after_artan_beklemenin_altina_inmez(self):
        """Dakikalık token sınırında sunucu "13 ms sonra dene" diyor; yedi deneme bir saniyede tükenip çalışma
        duruyordu (gerçek test X). Bekleme artan beklemeden kısa olmaz."""
        sayac = {"n": 0, "zaman": []}
        tamam = {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        def yanitla():
            sayac["n"] += 1
            sayac["zaman"].append(time.time())
            return (429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "0"}) if sayac["n"] <= 3 \
                else (200, tamam, {})
        model = self._sunucu(yanitla)
        model.ILK_BEKLEME = 0.2
        tur, metin = self._iste(model, 10)
        self.assertEqual((tur, sayac["n"]), ("yanit", 4))
        self.assertGreaterEqual(sayac["zaman"][3] - sayac["zaman"][0], 0.2 + 0.4 + 0.8 - 0.05)

    def test_gecici_sunucu_hatasi_yeniden_denenir(self):
        """Cloudflare'in 520'si gibi her 5xx geçicidir: çalışma durmaz, istek yeniden gider."""
        sayac = {"n": 0}
        tamam = {"choices": [{"message": {"content": "{}"}}], "usage": {}}

        def yanitla():
            sayac["n"] += 1
            return (520, {"error": "cf"}, {"Retry-After": "0.05"}) if sayac["n"] == 1 else (200, tamam, {})
        tur, metin = self._iste(self._sunucu(yanitla), 10)
        self.assertEqual((tur, metin, sayac["n"]), ("yanit", "{}", 2))

    def test_biten_kota_beklenmeden_bildirilir(self):
        sayac = {"n": 0}

        def yanitla():
            sayac["n"] += 1
            return 429, {"error": {"code": "insufficient_quota"}}, {}
        tur, metin = self._iste(self._sunucu(yanitla), 5)
        self.assertEqual((tur, sayac["n"]), ("hata", 1))
        self.assertIn("kredisi bitmiş", metin)

    def test_yanitsiz_sunucu_en_cok_uc_kez_beklenir(self):
        sayac = {"n": 0}

        def yanitla():
            sayac["n"] += 1
            threading.Event().wait(1.2)        # yeniden deneme beklemesi aşağıda susturuluyor
            return 200, {}, {}
        model = self._sunucu(yanitla)
        model.m["zaman_asimi"] = 0.3
        from defta import model as model_modulu
        with mock.patch.object(model_modulu, "_bekle", lambda olay, s: None):
            tur, metin = self._iste(model)
        self.assertEqual((tur, sayac["n"]), ("hata", 3))
        self.assertIn("yanıt vermedi", metin)



class Karar0134Testleri(unittest.TestCase):
    """Japonca kaynakta sözlük eşleşmeleri ve çıktı temizliği (Kodi 0.1.34 denetimi)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gunluk = Gunluk(os.path.join(self.tmp.name, "logs"))

    def _cevir(self, kaynaklar, sozluk, ceviriler):
        """kaynaklar: altyazı metinleri; ceviriler: çağrı no -> {id: metin}. (sonuç, model) döndürür."""
        icerik = "".join(f"{n}\n00:00:{5 * n:02d},000 --> 00:00:{5 * n + 3:02d},000\n{m}\n\n" for n, m in enumerate(kaynaklar, 1))
        satirlar = srt.srt_ayristir(icerik)
        for x in satirlar:
            x.metin, x.ustte = temizlik.kaynak_hazirla(x.metin)

        def kural(cagri, satirlar_):
            for x in satirlar_:
                x["text"] = ceviriler.get(cagri, ceviriler[1]).get(x["id"], ceviriler[1].get(x["id"], "?"))
            return satirlar_
        model = SahteModel(kural)
        ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}, "kontrol": {"tekrar_eden_sahne": False}})
        return Cevirmen(ayar, model, "S", self.gunluk, sozluk=sozluk).cevir(satirlar, "a.srt"), model

    def test_hiragana_okunus_tek_basina_ad_sayilmaz(self):
        # 凪(なぎ) -> Nagi. Okunuş "なぎ" sıradan fiilin içinde de geçer (つなぎとめる): o satıra ad yazdırılmaz.
        from defta.kaynak import _terim_bicimleri, okunus_yazimi
        self.assertEqual(_terim_bicimleri("凪(なぎ)"), ["凪(なぎ)"])
        self.assertEqual(_terim_bicimleri("東国(オスタニア)"), ["東国(オスタニア)", "東国", "オスタニア"])   # katakana okunuş kalır
        self.assertTrue(okunus_yazimi("凪(なぎ)", "なぎ"))
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("凪(なぎ)", "name", "Nagi", ("なぎ", "凪"))
        self.assertEqual(dict(sozluk.terimler), {"凪(なぎ)": "Nagi", "凪": "Nagi"})
        sonuc, model = self._cevir(["心をつなぎとめるしかない", "凪は来ない"], sozluk,
                                   {1: {1: "Kalbini bağlamaktan başka çare yok.", 2: "Nagi gelmeyecek."}})
        self.assertEqual(model.cagri, 1)                                      # 1. satır yeniden sorulmadı
        self.assertEqual(sonuc[0], "Kalbini bağlamaktan başka çare yok.")

    def test_tek_kanjili_terim_birlesik_sozcukte_aranmaz(self):
        from defta.kaynak import bicim_gecer
        self.assertFalse(bicim_gecer("牙", "ここが敵の牙城だ"))
        self.assertTrue(bicim_gecer("牙", "“牙”の幹部だ"))
        self.assertTrue(bicim_gecer("凪", "凪君が来た") and bicim_gecer("凪", "凪先生"))    # hitap eki bitişiklik değil
        self.assertFalse(bicim_gecer("兄", "兄弟だろ"))
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("牙", "descriptive", "Diş")
        sonuc, model = self._cevir(["ここが敵の牙城だ", "“牙”の幹部だ"], sozluk,
                                   {1: {1: "Burası düşmanın kalesi.", 2: "Diş'in yöneticisiyim."}})
        self.assertEqual(model.cagri, 1)
        self.assertEqual(sonuc[0], "Burası düşmanın kalesi.")

    def test_akrabalik_terimi_hitap_ekli_halinde_de_aranir(self):
        # 兄 -> abi; 兄さん "sıradan sözcük" diye kayıtlı olsa da aynı karşılığı alır ("ağabey" ile karışmasın)
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("兄", "title", "abi")
        sozluk.girdi_ekle("兄さん", "common", "")
        sonuc, model = self._cevir(["兄さんは初めて見た", "行こう"], sozluk,
                                   {1: {1: "Ağabeyimi ilk kez böyle gördüm.", 2: "Gidelim."},
                                    2: {1: "Abimi ilk kez böyle gördüm."}})
        self.assertIn('"abi"', model.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Abimi ilk kez böyle gördüm.")

    def test_etikette_okunusu_yazan_adin_yazimi_okunustur(self):
        from defta.terim import okunuslu_adlari_isle
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("峰谷", "name", "Minatani")                       # model okunuşu yanlış yazmış
        sozluk.girdi_ekle("翔(しょう)", "name", "Sho")                       # uzun ünlü farkı: dokunulmaz
        sozluk.girdi_ekle("隊長", "title", "komutan")
        sozluk.adlar["熊沢(くまざわ)"] = "Kumizawa"
        metin = "（峰谷(みねたに)）\n行くぞ\n（翔(しょう)）\nああ\n（熊沢(くまざわ)）\n待て\n（隊長(たいちょう)）\nよし\n（早瀬(はやせ) 隊員）\nはい\n（鬼瓦天牙(おにがわらてんが)）\nおう\n蔑(さげす)まれて"
        duzelen = okunuslu_adlari_isle(sozluk, metin)
        self.assertEqual(sozluk.terimler["峰谷"], "Minetani")
        self.assertEqual(sozluk.terimler["翔(しょう)"], "Sho")
        self.assertEqual(sozluk.terimler["熊沢"], "Kumazawa")                # sözlükte yoktu: eklendi
        self.assertEqual(sozluk.adlar["熊沢(くまざわ)"], "Kumazawa")
        self.assertEqual(sozluk.terimler["早瀬"], "Hayase")
        self.assertEqual(sozluk.terimler["隊長"], "komutan")                  # unvan: ad değil
        self.assertNotIn("蔑", sozluk.girdiler)                               # etiket dışındaki okunuş yardımı ad değil
        self.assertNotIn("鬼瓦天牙", sozluk.girdiler)                         # soyadı + ad tek parça: script bölemez
        self.assertEqual(duzelen, ["峰谷: Minatani -> Minetani", "熊沢: Kumizawa -> Kumazawa"])

    def test_guc_ve_sahibi_iki_ayri_terim_olarak_kalir(self):
        # 念力 "zihin gücü" / 念力者 "medyum": ortak sözcük yok diye kısa terim silinmezdi olmalı (者 türetme eki)
        sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        sozluk.girdi_ekle("念力", "term", "zihin gücü")
        sozluk.girdi_ekle("念力者", "people", "zihinci")
        self.assertEqual(dict(sozluk.terimler), {"念力": "zihin gücü", "念力者": "zihinci"})
        self.assertIn("build its rendering on the other item's rendering",
                      __import__("defta.terim", fromlist=["x"]).karsilik_istemi(ayar_birlestir()))

    def test_kanjili_adin_yazimi_okunusa_uymuyorsa_ceviri_sayilir(self):
        # 牙: model "ad" deyip İngilizcesini ("Fang") yazdı; okunuş "kiba". Script bunu çeviri sayar: karar tanıma
        # döner, karşılığı hedef dilde sorulur. Okunuşa uyan yazım, okunuşsuz ad ve kod adı olduğu gibi kalır.
        from defta.terim import KARSILIKLI, _cogunluk, siniflandirma_istemi
        oy = lambda **k: {"items": [{"i": 1, **k}]}
        karar = _cogunluk([oy(t="name", l="Fang", r="きば", e="Fang")] * 3, {1: "牙"})[0]
        self.assertEqual((karar["t"], "l" in karar, "e" in karar), ("descriptive", False, False))
        self.assertIn(karar["t"], KARSILIKLI)
        self.assertIn("Fang", karar["m"])
        self.assertEqual(_cogunluk([oy(t="name", l="Kenji", r="けんじ")] * 3, {1: "健二"})[0], {"i": 1, "t": "name", "l": "Kenji"})
        self.assertEqual(_cogunluk([oy(t="name", l="Ryuji", r="りゅうじ")] * 3, {1: "竜司"})[0]["t"], "name")   # uzun ünlü farkı
        self.assertEqual(_cogunluk([oy(t="name", l="Fang")] * 3, {1: "牙"})[0]["t"], "name")              # okunuş yok
        karar = _cogunluk([oy(t="codename", l="Dusk", r="たそがれ")] * 3, {1: "黄昏"})[0]
        self.assertEqual((karar["t"], karar["l"]), ("codename", "Tasogare"))    # kod adı duyulduğu gibi (18.11.2026)
        self.assertIn('"r" - only for a "name" or "codename" written with kanji', siniflandirma_istemi(ayar_birlestir()))

    def test_yabanci_tepki_sesi_yeniden_sorulur(self):
        sonuc, model = self._cevir(["Ugh, no! Stop talking.", "Fine."], None,
                                   {1: {1: "Ugh, olmaz! Konuşma artık.", 2: "Peki."}, 2: {1: "Of, olmaz! Konuşma artık."}})
        self.assertIn("is not how Turkish", model.gidenler[1]["fix"][0]["problem"])
        self.assertEqual(sonuc[0], "Of, olmaz! Konuşma artık.")

    def test_ciglik_okunusu_yabanci_ses_sayilmaz(self):
        sonuc, model = self._cevir(["（組織員）危ない うわあ", "（葵）はい"], None, {1: {1: "Dikkat! Uwaa!", 2: "Evet."}})
        self.assertEqual(len(model.gidenler), 1)           # yeniden sorulmadı
        self.assertEqual(sonuc[0], "Dikkat! Uwaa!")

    def test_yon_isaretleri_temizlenir(self):
        for kaynak, beklenen in (("Sure. >", "Sure."), ("< Is this it?\nYes.", "Is this it?\nYes."),
                                 (">> Hello there", "Hello there"), ("We do! > 'His plan\nwas a cafe,", "We do! 'His plan\nwas a cafe,"),
                                 ("<i>Fine.</i>", "<i>Fine.</i>"), ("a>b", "a>b")):
            self.assertEqual(temizlik.kaynak_hazirla(kaynak)[0], beklenen)
        self.assertEqual(temizlik.son_temizlik("Tabii. >", ayar_birlestir()["modlar"]), "Tabii.")
        self.assertEqual(temizlik.kalinti_tara(temizlik.son_temizlik("< Burası mı?", ayar_birlestir()["modlar"])), "")


class Karar102Testleri(unittest.TestCase):
    """1.0.2: Shield kayıtlarından (17.11.2026) çıkan düzeltmeler."""

    def test_kucuk_harfli_konusmaci_etiketi_dosyada_tekrarlaniyorsa_taninir(self):
        from defta.kaynak import latin_etiketleri, not_ve_konusmaci_ayir
        metinler = ["Mara:\nWe leave at dawn.", "Tolen:\nNo.", "Mara:\nThen I go alone.", "Mara:\nGoodbye.",
                    "Tolen:\nWait.", "Tolen:\nTake the map.", "Remember:\nthe bridge is out.",
                    "- Where is she?\n- Tolen: Gone north."]
        etiketler = latin_etiketleri(metinler)
        self.assertEqual(etiketler, {"Mara", "Tolen"})            # "Remember:" bir kez geçti: söz, etiket değil
        self.assertEqual(not_ve_konusmaci_ayir(metinler[0], adli_etiketler=etiketler),
                         ("We leave at dawn.", [], ["Mara"]))
        self.assertEqual(not_ve_konusmaci_ayir(metinler[7], adli_etiketler=etiketler),
                         ("- Where is she?\n- Gone north.", [], ["", "Tolen"]))
        self.assertEqual(not_ve_konusmaci_ayir(metinler[6], adli_etiketler=etiketler)[0], metinler[6])
        self.assertEqual(not_ve_konusmaci_ayir(metinler[0])[0], metinler[0])     # dosya bilgisi yoksa dokunulmaz

    def test_karsilik_partileri_aileyi_bolmez(self):
        from defta.terim import KARSILIK_PARTI, _karsilik_partileri
        az = [{"i": n, "s": f"term{n}"} for n in range(1, 11)]
        self.assertEqual(_karsilik_partileri(az), [az])                          # sığıyorsa istek eskisiyle aynı
        def sozcuk(n):                                                           # her öğeye ayrı, rakamsız bir sözcük
            return "q" + "".join(chr(97 + (n // 26 ** k) % 26) for k in range(4))
        cok = [{"i": n, "s": sozcuk(n)} for n in range(1, 61)]
        cok[2]["s"], cok[57]["s"] = "Silver Ring", "Ring of Silver"              # uzak ama aynı aile
        partiler = _karsilik_partileri(cok)
        self.assertGreater(len(partiler), 1)
        self.assertTrue(all(len(p) <= KARSILIK_PARTI for p in partiler))
        self.assertEqual(sorted(o["i"] for p in partiler for o in p), list(range(1, 61)))
        ayni = [p for p in partiler if any(o["i"] == 3 for o in p)][0]
        self.assertIn(58, [o["i"] for o in ayni])

    def test_konusan_etiketi_ad_kaymasi_sayilmaz(self):
        import re as _re
        from defta import denetim
        kaynak = "Tolen:\nHurry, Mara."
        # denetimin kullandığı kural: satır başındaki "Ad:" sözün içindeki ad değildir
        self.assertTrue(_re.search(r"(?m)^\s*(?:[-–—]\s*)?" + _re.escape("Tolen") + r"\s*:", kaynak))
        self.assertFalse(_re.search(r"(?m)^\s*(?:[-–—]\s*)?" + _re.escape("Mara") + r"\s*:", kaynak))
        self.assertIn("konuşanın etiketidir", open(denetim.__file__, encoding="utf-8").read())

    def test_master_turkcede_efendi_yazilir(self):
        from defta.ayar import ayar_birlestir, unvan_tablosu
        self.assertEqual(unvan_tablosu(ayar_birlestir({}))["Master"], "Efendi {ad}")


class Karar103Testleri(unittest.TestCase):
    """Shield kaydından çıkan sözlük kusurları: aynı dağın iki adı, sıfatın unvan sayılması."""

    def test_akraba_terim_en_sik_gecenin_karsiligina_uyar(self):
        from defta import terim
        yazimlar = ["Mount Doom", "Mountain of Doom", "White City"]
        oylar = [["Hüküm Dağı", "Hüküm Dağı", "Kıyamet Dağı"], ["Kıyamet Dağı", "Kıyamet Dağı", "Hüküm Dağı"],
                 ["Beyaz Şehir", "Beyaz Şehir", "Ak Şehir"]]
        kazanan = [terim._kazanan(o, 2) for o in oylar]
        self.assertEqual(terim._aile_uyumu(yazimlar, [6, 1, 2], oylar, kazanan), ["Hüküm Dağı", "Hüküm Dağı", "Beyaz Şehir"])

    def test_akraba_icin_cevap_uydurulmaz_ve_kararsiz_karara_baglanmaz(self):
        from defta import terim
        yazimlar = ["Mount Doom", "Quest of Mount Doom", "Doom Gate"]
        oylar = [["Hüküm Dağı"] * 3, ["Kıyamet Dağı Seferi"] * 3, ["Hüküm Kapısı", "Kıyamet Kapısı", "Felaket Kapısı"]]
        kazanan = [terim._kazanan(o, 2) for o in oylar]
        self.assertEqual(terim._aile_uyumu(yazimlar, [6, 1, 1], oylar, kazanan), ["Hüküm Dağı", "Kıyamet Dağı Seferi", None])

    def test_latin_disi_kaynakta_aile_uyumu_karismaz(self):
        from defta import terim
        oylar = [["esper"] * 3, ["psişik güç", "psişik güç", "esper gücü"]]
        self.assertEqual(terim._aile_uyumu(["超能力者", "超能力"], [9, 3], oylar, ["esper", "psişik güç"]), ["esper", "psişik güç"])


class Karar106Testleri(unittest.TestCase):
    """18.11.2026, kullanıcının kararları: kod adı duyulduğu gibi, operasyon/proje adının genel sözcüğü hedef dilde,
    unvan/lakap içindeki ad korunur, kurgusal halk adı hedef dilin yazımıyla küçük harf; bir kez geçen okunuşlu
    sözcük de aday."""

    def test_kod_adinin_genel_sozcugu_karsiliktan(self):
        from defta.terim import _karsilik_sorulacak, hedef_belirle, kod_adi_adi
        self.assertEqual(kod_adi_adi("Operation Strix"), "Strix")
        self.assertEqual(kod_adi_adi("Tasogare"), "")
        self.assertTrue(_karsilik_sorulacak({"t": "codename", "l": "Project Apple"}, "プロジェクト〈アップル〉"))
        self.assertFalse(_karsilik_sorulacak({"t": "codename", "l": "Tasogare"}, "黄昏"))
        y = "オペレーション〈梟(ストリクス)〉"
        self.assertEqual(hedef_belirle(y, {"t": "codename", "l": "Operation Strix", "k": "Strix Operasyonu"}, True),
                         ("codename", "Strix Operasyonu"))
        # adı çeviren cevap alınmaz: yazım olduğu gibi kalır
        self.assertEqual(hedef_belirle(y, {"t": "codename", "l": "Operation Strix", "k": "Baykuş Operasyonu"}, True),
                         ("codename", "Operation Strix"))

    def test_kod_adi_okunusla_yazilir(self):
        from defta.terim import _kod_adi_duyuldugu_gibi
        c = {"l": "Thorn Princess"}
        _kod_adi_duyuldugu_gibi(c, "いばら姫", [{"r": "いばらひめ"}])
        self.assertEqual(c["l"], "Ibarahime")
        c = {"l": "Ibara Hime"}
        _kod_adi_duyuldugu_gibi(c, "いばら姫", [{"r": "いばらひめ"}])
        self.assertEqual(c["l"], "Ibara Hime")

    def test_unvandaki_ad_korunur_baskasinin_adi_girmez(self):
        from defta.terim import _adlara_uyan
        adlar = {"ハニー": "Honey", "武蔵": "Musashi"}
        self.assertEqual(_adlara_uyan(["Bal Prensesi", "Prenses Honey"], "ハニー姫", adlar), ["Prenses Honey"])
        self.assertEqual(_adlara_uyan(["Kulüp başkanı Musashi", "kulüp başkanı"], "部長", adlar), ["kulüp başkanı"])

    def test_halk_adi_hedef_dilde_kucuk_harf(self):
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("elf", {"t": "people", "e": "Elf"}, True), ("people", "elf"))
        self.assertEqual(hedef_belirle("elf", {"t": "people", "e": "Elf"}, True, hedef_kodu="de"), ("people", "Elf"))
        self.assertEqual(hedef_belirle("日本人", {"t": "people", "k": "Japon", "real": True}, True), ("people", "Japon"))

    def test_ad_unvan_karsiligindan_ad_cikar(self):
        from defta.terim import _addan_kalip
        self.assertEqual(_addan_kalip("Başkan Musashi", "Musashi"), "Başkan {ad}")
        self.assertEqual(_addan_kalip("Prenses Anya", "Anya"), "Prenses {ad}")
        self.assertEqual(_addan_kalip("başkan", ""), "Başkan {ad}")

    def test_kaliptan_ad_cikar_ve_etiket_okunusu(self):
        from defta.terim import _kaliptan_adi_cikar, _latin_ad, kod_adi_latin
        self.assertEqual(_kaliptan_adi_cikar("Prenses Anya {ad}", "Anya"), "Prenses {ad}")
        self.assertEqual(_kaliptan_adi_cikar("Anya {ad}", "Anya"), "Anya {ad}")         # unvan kalmazsa dokunulmaz

        class S:
            girdiler, adlar = {}, {"郷田(ごうだ)武蔵(むさし)": "Musashi"}
        self.assertEqual(_latin_ad(S, "武蔵"), "Musashi")
        self.assertEqual(kod_adi_latin("オペレーション〈梟(ストリクス)〉", "Strix"), "Operation Strix")
        self.assertEqual(kod_adi_latin("黄昏", "Tasogare"), "Tasogare")

    def test_ad_unvan_girdisinden_ad_cikar_etiket_okunusuyla(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import _adla_gecen_unvanlar
        s = Sozluk(os.path.join(tempfile.mkdtemp(), "x.json"))
        s.girdi_ekle("武蔵部長", "title", "Musashi kulüp başkanı", (), "model", 3, "", "")
        _adla_gecen_unvanlar(s, {}, set(), "（郷田(ごうだ)武蔵(むさし)）\nすいません 武蔵部長\n武蔵部長！")
        self.assertEqual((s.girdiler["部長"]["hedef"], s.girdiler["部長"]["kalip"]), ("kulüp başkanı", "{ad} kulüp başkanı"))

    def test_karsilik_anlasilirlik_denetimine_gitmez(self):
        """Anlaşılırlık denetimi kaldırıldı (kullanıcının kararı, 19.11.2026): esper, Elf, buçukluk elenmiyordu ama
        yanlış eleniyordu. Karşılık oylarla belirlenir; Japonca kaynakta İngilizce yazımla aynı cevap yine alınmaz."""
        from defta.terim import _karsiliklari_tamamla

        def tamamla(c, yazim, cevap):
            return _karsiliklari_tamamla([[c]], [[{"yazim": yazim, "ornek": ["x"], "sayi": 9}]],
                                         lambda yuk: {"items": [{"i": o["i"], "tr": cevap} for o in yuk["items"]]}, 3, 1,
                                         "L", None, "Turkish", True, False, {})[1][0][1]
        self.assertEqual(tamamla({"i": 1, "t": "people", "m": "x", "ceviri_oyu": True}, "elves", "Elf"), "Elf")
        self.assertEqual(tamamla({"i": 1, "t": "people", "m": "x", "ceviri_oyu": True}, "halfling", "buçukluk"), "buçukluk")
        self.assertEqual(tamamla({"i": 1, "t": "codename", "l": "Operation Strix"}, "オペレーション〈梟〉", "Strix Operasyonu"),
                         "Strix Operasyonu")
        self.assertIsNone(tamamla({"i": 1, "t": "term", "l": "esper"}, "超能力者", "esper"))

    def test_parcalar_arasi_tekrar_eden_ifade_bulunur(self):
        """Japonca kaynakta uzak satırlarda tekrar eden ifade sözlük adayı olur (gerçek test 21: 懇親会, 鬼ごっこ,
        selam kalıbı iki ayrı parçada iki ayrı çeviri almıştı)."""
        from defta.terim import tekrar_eden_ifadeler
        m = {i: "そうか" for i in range(80)}
        m.update({2: "こんにちは あるいは こんばんは 黄昏君", 60: "こんにちは あるいは こんばんは エドガーさん",
                  10: "懇親会へ潜入せよ", 14: "定期的に開かれる懇親会のみ",
                  20: "実はプロの鬼ごっこ集団", 70: "おじさんたちと鬼ごっこしてた",
                  30: "写真を返せ", 31: "写真だ",                  # 3 satırdan yakın: alınmaz
                  40: "ロイドの家", 75: "ロイドの車"})
        bulunan = tekrar_eden_ifadeler(m, ["ロイド"])
        self.assertEqual(bulunan.get("こんにちは あるいは こんばんは"), [2, 60])
        self.assertEqual(bulunan.get("懇親会"), [10, 14])
        self.assertEqual(bulunan.get("鬼ごっこ"), [20, 70])
        self.assertNotIn("写真", bulunan)
        self.assertFalse(any("ロイド" == k for k in bulunan))

    def test_tek_sozcuk_kurgusal_ad_kaynaktaki_gibi_kalir(self):
        """Tek sözcüklük kurgusal özel ad kaynaktaki gibi kalır, anlamlı söz çevrilir (kullanıcının kararı, 19.11.2026)."""
        from defta.terim import kaynaktaki_gibi_kalir, hedef_belirle, _karsilik_sorulacak
        metin = ("Strider! To rivendell. Wonder at Mirkwood. In the southfarthing, westfarthing.\n"
                 "The four farthings of the shire. The dark lord. The Enemy is near. Beware the Enemy.\n"
                 "Troy burns. The Middle-earth.")
        kalir = lambda t, y, **k: kaynaktaki_gibi_kalir({"t": t, **k}, y, metin)
        self.assertTrue(kalir("epithet", "Strider"))
        self.assertTrue(kalir("name", "rivendell"))
        self.assertTrue(kalir("descriptive", "Mirkwood"))
        self.assertTrue(kalir("descriptive", "southfarthing"))         # yer adı "the" alır, yine kalır
        self.assertFalse(kalir("descriptive", "farthings"))            # "four farthings": anlamlı söz
        self.assertFalse(kalir("epithet", "Enemy"))                    # lakapta "the": anlamlı söz
        self.assertFalse(kalir("epithet", "dark lord"))                # birden çok sözcük
        self.assertFalse(kalir("descriptive", "Middle-earth"))         # tireyle birleşik
        self.assertFalse(kalir("name", "Troy", real=True))             # gerçek dünyadan ad bugünkü kuralla
        self.assertFalse(kalir("term", "kingsfoil"))                   # terim, halk adı, unvan çevrilir
        self.assertFalse(kalir("people", "halfling"))
        self.assertFalse(kaynaktaki_gibi_kalir({"t": "name"}, "ヨル", "ヨル"))
        # hedef: kaynaktaki yazım; sınıflandırmanın karşılığı alınmaz, karşılık sorulmaz
        self.assertEqual(hedef_belirle("Strider", {"t": "epithet", "tr": "Yolgezer", "kaynak": True}, True), ("epithet", "Strider"))
        self.assertEqual(hedef_belirle("rivendell", {"t": "name", "e": "Ayrıkvadi", "kaynak": True}, True)[1].lower(), "rivendell")
        self.assertEqual(hedef_belirle("westfarthing", {"t": "descriptive", "k": "Batı Çeyreği", "kaynak": True}, True)[1],
                         "Westfarthing")
        self.assertFalse(_karsilik_sorulacak({"t": "descriptive", "tr": "Kuyutorman", "kaynak": True}, "Mirkwood"))
        # çok sözcüklü anlamlı ad karşılığa gider; kişi adı gitmez (gerçek test 20, S4: "Bag End" kalmıştı)
        from defta.terim import anlamli_cok_sozcuklu_ad as anl
        tek = {"frodo", "baggins", "sackville"}
        self.assertTrue(anl({"t": "name"}, "Bag End", tek))
        self.assertTrue(anl({"t": "name"}, "Middle-earth", tek))
        self.assertFalse(anl({"t": "name"}, "Frodo Baggins", tek))
        self.assertFalse(anl({"t": "name"}, "Sackville-Baggins", tek))
        self.assertFalse(anl({"t": "name", "real": True}, "New York", tek))
        self.assertFalse(anl({"t": "descriptive"}, "dark lord", tek))       # tanım zaten karşılığa gider
        self.assertFalse(anl({"t": "name"}, "ヨル・ブライア", tek))
        self.assertFalse(anl({"t": "name"}, "Shadow Moon", {"shadow", "moon"}))   # gerçek test 25: "Gölge Ay"
        self.assertFalse(_karsilik_sorulacak({"t": "epithet", "unvanli_ad": "Bay Wednesday"}, "Mr. Wednesday"))
        self.assertEqual(hedef_belirle("Mr. Wednesday", {"t": "epithet", "unvanli_ad": "Bay Wednesday", "k": "Bay Çarşamba"},
                                       True), ("epithet", "Bay Wednesday"))
        self.assertTrue(_karsilik_sorulacak({"t": "name", "anlamli_ad": True}, "Bag End"))
        self.assertEqual(hedef_belirle("bag end", {"t": "name", "anlamli_ad": True, "k": "Çıkın Çıkmazı"}, True),
                         ("name", "Çıkın Çıkmazı"))

    def test_odunc_okunuslu_unvan_duyuldugu_gibi(self):
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("管理官(ハンドラー)", {"t": "title", "l": "Handler", "k": "amir"}, True), ("title", "Handler"))
        self.assertEqual(hedef_belirle("婆(ババア)", {"t": "epithet", "l": "Babaa", "k": "cadı karı"}, True), ("epithet", "cadı karı"))
        # sıradan sözcüğün harf harf okunuşu ad sayılmaz ("Baba" Türkçede başka sözcük)
        self.assertEqual(hedef_belirle("婆(ババア)", {"t": "descriptive", "l": "Baba", "w": "old hag", "k": "kocakarı"}, True),
                         ("descriptive", "kocakarı"))

    def test_uydurma_terim_kucuk_harf(self):
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("mithril", {"t": "term", "e": "Mithril"}, True), ("term", "mithril"))
        self.assertEqual(hedef_belirle("TARDIS", {"t": "term"}, True), ("term", "TARDIS"))
        self.assertEqual(hedef_belirle("Athelas", {"t": "term"}, True, hedef_kodu="de"), ("term", "Athelas"))

    def test_tam_ad_parcalardan_kurulur(self):
        from defta.terim import _bilesik_adlari_esitle

        class S:
            girdiler = {}
        k = [[{"i": 1, "t": "name", "l": "Roid Fōjā"}, {"i": 2, "t": "name", "l": "Loid"}, {"i": 3, "t": "name", "l": "Forger"},
              {"i": 4, "t": "name", "l": "Henri Henderson"}]]
        p = [[{"yazim": "ロイド・フォージャー"}, {"yazim": "ロイド"}, {"yazim": "フォージャー"}, {"yazim": "ヘンリー・ヘンダーソン"}]]
        _bilesik_adlari_esitle(k, p, S)
        self.assertEqual([c["l"] for c in k[0]], ["Loid Forger", "Loid", "Forger", "Henri Henderson"])

    def test_okunuslu_kod_adi_okunusla_degismez(self):
        from defta.terim import _kod_adi_duyuldugu_gibi
        c = {"l": "Operation Strix"}
        _kod_adi_duyuldugu_gibi(c, "オペレーション〈梟(ストリクス)〉", [{"r": "すとりくす"}])
        self.assertEqual(c["l"], "Operation Strix")

    def test_cogul_girdi_tekilin_karsiligini_alir(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import cogullari_esitle
        s = Sozluk(os.path.join(tempfile.mkdtemp(), "x.json"))
        for k, h in (("dwarf", "cüce"), ("dwarves", "cüceler"), ("wraith", "hayalet"), ("wraiths", "hayaletler")):
            s.girdi_ekle(k, "people", h, (), "model", 3, "")
        s.girdi_ekle("lens", "term", "mercek", (), "model", 3, "")
        cogullari_esitle(s)
        self.assertEqual({k: g["hedef"] for k, g in s.girdiler.items()},
                         {"dwarf": "cüce", "dwarves": "cüce", "wraith": "hayalet", "wraiths": "hayalet", "lens": "mercek"})

    def test_cogul_halk_adi_tekil_istenir(self):
        from defta.terim import cogul_gibi
        self.assertTrue(all(cogul_gibi(w) for w in ("dwarves", "elves", "orcs")))
        self.assertFalse(any(cogul_gibi(w) for w in ("hobbit", "mithril", "status", "Rangers")))

    def test_konusmaci_parcasindaki_ses_aday(self):
        from defta.kaynak import konusmaci_parcalari
        from defta.terim import ses_adaylari
        self.assertEqual(len(konusmaci_parcalari("（アーニャ）何でもいい\n（ヨル）ガーン")), 2)
        satirlar = SozlukTestleri._satirlar(None, ["（アーニャ）何でもいい\n（ヨル）ガーン"])
        self.assertIn("ガーン", ses_adaylari([("x", satirlar, "")]))

    def test_cumle_icindeki_bip_metinde_kalir(self):
        from defta.kaynak import not_ve_konusmaci_ayir
        self.assertEqual(not_ve_konusmaci_ayir("Two seconds with me. [bleep].")[:2], ("Two seconds with me. [bleep].", []))
        self.assertEqual(not_ve_konusmaci_ayir("[bleep]")[:2], ("", ["bleep"]))
        self.assertEqual(not_ve_konusmaci_ayir("- [bleep].")[:2], ("", ["bleep"]))
        self.assertEqual(not_ve_konusmaci_ayir("...")[0], "...")
        self.assertEqual(not_ve_konusmaci_ayir("[laughs] Holy [bleep]!")[:2], ("Holy [bleep]!", ["laughs"]))
        self.assertEqual(not_ve_konusmaci_ayir("- [bleep]\n- why is the plate not ready?")[0],
                         "- [bleep]\n- why is the plate not ready?")

    def test_unvanin_tek_basina_yazimi_kaliptan_harf_alir(self):
        import tempfile
        from defta.sozluk import Sozluk
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("Mr", "title", "bay", kalip="Bay {ad}")
        sz.girdi_ekle("Uncle", "title", "amca", kalip="{ad} Amca")
        sz.girdi_ekle("Lady", "title", "leydi", kalip="Hanım {ad}")
        self.assertEqual([sz.girdiler[k]["hedef"] for k in ("Mr", "Uncle", "Lady")], ["Bay", "Amca", "leydi"])

    def test_ad_ekli_lakap_ornek_addan_kurulur(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import ad_ekli_lakaplari_esitle
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("ロイド", "name", "Loid")
        sz.girdi_ekle("ボンドマン", "name", "Bondman")
        sz.girdi_ekle("ロイドマン", "epithet", "Loid Adam")
        sz.girdi_ekle("ロイドパパ", "epithet", "Loid Baba")      # eki taşıyan başka ad yok: karar kalır
        self.assertEqual(ad_ekli_lakaplari_esitle(sz), ["ロイドマン"])
        self.assertEqual(sz.girdiler["ロイドマン"]["hedef"], "Loidman")
        self.assertEqual(sz.girdiler["ロイドパパ"]["hedef"], "Loid Baba")

    def test_gercek_yer_adinin_turkce_harfli_yazimi_alinmaz(self):
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("新宿", {"t": "name", "l": "Shinjuku", "e": "Şinjuku", "real": True}, True)[1],
                         "Shinjuku")
        self.assertEqual(hedef_belirle("日本", {"t": "name", "l": "Nihon", "e": "Japonya", "real": True}, True)[1],
                         "Japonya")                                          # başka sözcük olan yerleşik biçim kalır
        self.assertEqual(hedef_belirle("Moscow", {"t": "name", "e": "Moskova", "real": True}, True)[1], "Moskova")

    def test_katakana_terim_onu_iceren_girdinin_okunusunu_alir(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import katakana_parcalarini_esitle
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("ヒノカミ", "term", "Ateş Tanrısı")
        sz.girdi_ekle("ヒノカミ神楽", "term", "Hinokami Kagura")
        sz.girdi_ekle("チャクラ", "term", "çakra")
        self.assertEqual(katakana_parcalarini_esitle(sz), ["ヒノカミ"])
        self.assertEqual((sz.girdiler["ヒノカミ"]["hedef"], sz.girdiler["チャクラ"]["hedef"]), ("Hinokami", "çakra"))

    def test_cince_fansub_kunyesi_reklamdir(self):
        self.assertTrue(temizlik.reklam_mi("本字幕由诸神字幕组(kamigami.org)制作，仅供交流学习"))
        self.assertTrue(temizlik.reklam_mi("日听:风风 翻译:Pulsar 校对:小可"))
        self.assertFalse(temizlik.reklam_mi("字幕を見ながら話す"))

    def test_katakana_uydurma_terim_cevrilmez(self):
        from defta.terim import _karsilik_sorulacak, hedef_belirle
        self.assertFalse(_karsilik_sorulacak({"t": "term", "l": "Geass"}, "ギアス"))
        self.assertEqual(hedef_belirle("ギアス", {"t": "term", "l": "Geass"}, True)[1], "Geass")
        self.assertTrue(_karsilik_sorulacak({"t": "term", "l": "Chakura"}, "チャクラ"))        # harf harf okunuş
        self.assertTrue(_karsilik_sorulacak({"t": "term", "l": "Toss", "w": "toss"}, "トス"))  # sıradan sözcük

    def test_tv_altyazisi_ass_okunuslari_atilir_satirlari_birlesir(self):
        from defta import bicim
        olay = "Dialogue: 0,0:00:{0:02d}.00,0:00:{1:02d}.00,Default,,0000,0000,0000,,{{\\pos({2},{3}){4}}}{5}"
        satirlar = ["[Script Info]", "[V4+ Styles]", "Format: Name, Alignment", "Style: Default,2", "[Events]",
                    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
        for n in range(0, 50, 2):
            satirlar += [olay.format(n, n + 1, 272, 353, "\\fscx50\\fscy50", "さんた"),
                         olay.format(n, n + 1, 172, 407, "\\fscx50", "（三太）おめぇの年だろ。"),
                         olay.format(n, n + 1, 312, 497, "", "（翔馬）２０歳です。")]
        sonuc = bicim.ayristir("\n".join(satirlar), ".ass", ayar_birlestir({}))
        self.assertEqual(len(sonuc), 25)
        self.assertEqual(sonuc[0].metin, "（三太）おめぇの年だろ。\n（翔馬）２０歳です。")
        self.assertFalse(sonuc[0].ekran)

    def test_tek_oyun_cevirisi_karsilik_sordurur(self):
        from defta.terim import _cogunluk, _karsilik_sorulacak, _cevrilir
        # "His Grace": üç oyda da kalıp "Majesteleri NAME", "e" tek oyda -> karşılığı sorulur
        oylar = [{"items": [{"i": 1, "t": "title", "p": "Majesteleri NAME"}]},
                 {"items": [{"i": 1, "t": "title", "p": "Majesteleri NAME"}]},
                 {"items": [{"i": 1, "t": "title", "e": "Majesteleri", "p": "Majesteleri NAME"}]}]
        c = _cogunluk(oylar, {1: "His Grace"})[0]
        self.assertNotIn("e", c)
        self.assertTrue(_karsilik_sorulacak(c, "His Grace"))
        # "Citadel": tek oyun "e"si
        oylar = [{"items": [{"i": 1, "t": "name"}]}, {"items": [{"i": 1, "t": "descriptive", "e": "Hisar"}]},
                 {"items": [{"i": 1, "t": "descriptive"}]}]
        self.assertTrue(_karsilik_sorulacak(_cogunluk(oylar, {1: "Citadel"})[0], "Citadel"))
        # kaynakla aynı kalıp ("Lord NAME", "Ser NAME") çeviri değildir
        oylar = [{"items": [{"i": 1, "t": "title", "p": "Ser NAME"}]}] * 3
        self.assertFalse(_cevrilir(_cogunluk(oylar, {1: "Ser"})[0], "Ser"))
        # özel ad gibi kalan halk adı sorulmaz, yerden kurulan halk adı sorulur
        oylar = [{"items": [{"i": 1, "t": "people"}]}] * 3
        self.assertFalse(_karsilik_sorulacak(_cogunluk(oylar, {1: "Dúnedain"})[0], "Dúnedain"))
        self.assertTrue(_karsilik_sorulacak(_cogunluk(oylar, {1: "Valyrian"})[0], "Valyrian"))

    def test_latin_adin_turevi_adin_yerlesik_bicimiyle_kurulur(self):
        from defta.terim import _karsiliklari_tamamla
        from defta.temizlik import tr_buyuk_harf
        gidenler = []

        def sor(yuk):
            gidenler.append(yuk)
            return {"items": [{"i": 1, "tr": "Truvalı" if len(gidenler) % 3 else "Troyalı"}]}
        kararlar = [[{"i": 1, "t": "name", "e": "Truva", "myth": True}, {"i": 2, "t": "people", "tr": "Truvalı"}]]
        partiler = [[{"yazim": "Troy", "ornek": ["Princes of Troy,"], "sayi": 9},
                     {"yazim": "Trojan", "ornek": ["With the Trojans."], "sayi": 5}]]
        _, oneriler, _ = _karsiliklari_tamamla(kararlar, partiler, sor, 3, 1, baslik="Troy", dil="Turkish")
        self.assertEqual(gidenler[0]["items"][0].get("names"), {"Troy": "Truva"})
        self.assertIn("Truvalı", json.dumps(oneriler, ensure_ascii=False))
        self.assertNotIn("Troyalı", json.dumps(oneriler, ensure_ascii=False))
        # halk adı büyük harf; "personel", "türkü" dokunulmaz
        self.assertEqual(tr_buyuk_harf("persler ve personel türkü söyledi"), "Persler ve personel türkü söyledi")
        self.assertEqual(tr_buyuk_harf("amerika yerlisi ve amerikan ordusu, rustik almanak"),
                         "Amerika yerlisi ve Amerikan ordusu, rustik almanak")

    def test_para_birimi_ve_hitap_sozcugu_aday_olur(self):
        """Sayıdan sonraki katakana sözcük bir kez geçse de aday (uydurma para birimi her bölüm başka yazılıyordu);
        bir karakterin hep tek başına söylediği kana sözcük (Anya'nın ちち'si) epithet olabilecek aday olur."""
        from defta.terim import birim_adaylari, hitap_sozcugu_adaylari, siniflandirma_istemi, kesif_istemi
        self.assertEqual(dict(birim_adaylari("１ダルクの物は\n10ペント硬貨じゃ買えないぞ\n総額300万ダルク相当")),
                         {"ダルク": 2, "ペント": 1})
        self.assertEqual(dict(birim_adaylari("黄昏(たそがれ)　20分程度かと　ペントハウス")), {})
        satirlar = [srt.Satir(i + 1, i * 2000, i * 2000 + 1500, m) for i, m in enumerate(
            ["（アーニャ）ちち", "（アーニャ）ちち", "ちち すごい ウソつき", "（アーニャ）ちち〜",
             "（アーニャ）うい", "（ロイド）うい", "（ロイド）うい", "（アーニャ）うい"])]
        sesler = {"ちち": {"sayi": 3, "konusanlar": {"アーニャ"}, "ad_degil": False, "ornek": "（アーニャ）ちち"},
                  "うい": {"sayi": 4, "konusanlar": {"アーニャ", "ロイド"}, "ad_degil": False, "ornek": "（アーニャ）うい"},
                  "ハア": {"sayi": 2, "konusanlar": {"アーニャ"}, "ad_degil": False, "ornek": "ハア"}}
        adaylar = hitap_sozcugu_adaylari(sesler, set(), set(), [("a", satirlar, "")])
        self.assertEqual([a["yazim"] for a in adaylar], ["ちち"])
        self.assertEqual(adaylar[0]["ornek"], ["（アーニャ）ちち", "ちち すごい ウソつき"])
        self.assertEqual(hitap_sozcugu_adaylari(sesler, set(), {"ちち"}, [("a", satirlar, "")]), [])  # karar verilmiş
        self.assertIn("a child's own word for her father", siniflandirma_istemi(ayar_birlestir()))
        self.assertIn("invented currency or unit", siniflandirma_istemi(ayar_birlestir()))
        self.assertIn("a currency or unit: even once", kesif_istemi(ayar_birlestir()))

    def test_sozlukteki_kucuk_terim_kesme_almaz(self):
        """Türkçede kesme yalnız özel adda olur: sözlükteki küçük harfli terim (Anya'nın babasına dediği "baba",
        "elf") eki kesmesiz alır; çok heceli sözcükte sert ünsüz yumuşar. Özel ad ve hitap ekli ad dokunulmaz."""
        from defta.temizlik import kucuk_terim_kesmesi
        self.assertEqual(kucuk_terim_kesmesi("Ben Anya, baba'nın kızıyım. Baba'yla Anya'nın evine.", ["baba"]),
                         "Ben Anya, babanın kızıyım. Babayla Anya'nın evine.")
        self.assertEqual(kucuk_terim_kesmesi("babacık'ı gördüm, bir elf'in yayı, Yor-san'a, elfçe", ["babacık", "elf"]),
                         "babacığı gördüm, bir elfin yayı, Yor-san'a, elfçe")
        self.assertEqual(kucuk_terim_kesmesi("kaçakçılık örgütü'nün adamı", ["kaçakçılık örgütü"]),
                         "kaçakçılık örgütünün adamı")
        # cümle içinde büyük harf: ad gibi kullanılıyor, kalır; cümle başında büyük harf birleşir
        self.assertEqual(kucuk_terim_kesmesi("Az önceki... Köpekçik'in düşüncesi miydi? Köpekçik'e bak.", ["köpekçik"]),
                         "Az önceki... Köpekçik'in düşüncesi miydi? Köpekçiğe bak.")

    def test_kaliptaki_baska_harfli_ad_cikarilir(self):
        from defta.terim import _kaliptan_adi_cikar
        self.assertEqual(_kaliptan_adi_cikar("Marki Baltho {ad}", "Barto"), "Marki {ad}")
        self.assertEqual(_kaliptan_adi_cikar("Prenses Anya {ad}", "Anya"), "Prenses {ad}")
        self.assertEqual(_kaliptan_adi_cikar("Kaptan {ad}", "Kapton"), "Kaptan {ad}")

    def test_bilim_kurgu_testi_duzeltmeleri(self):
        from defta.terim import _yazimi_yenile, halk_harfi, hedef_belirle
        # karşılıktaki harf harf okunuş, ad yazım isteğinde düzelince yenilenir
        self.assertEqual(_yazimi_yenile("Gandamu pilotu", {"Gandamu": "Gundam"}), "Gundam pilotu")
        self.assertEqual(_yazimi_yenile("Gandamuxx", {"Gandamu": "Gundam"}), "Gandamuxx")
        # kurgusal halk adında kesmeli yer adı korunur
        self.assertEqual(halk_harfi("Tabr Sietch'inden Fedaykin"), "Tabr Sietch'inden fedaykin")
        self.assertEqual(halk_harfi("Fremen"), "fremen")
        # büyük harfli kaynak terim (the Matrix) küçültülmez; küçük harfli olan küçülür
        self.assertEqual(hedef_belirle("Matrix", {"t": "term"}, True)[1], "Matrix")
        self.assertEqual(hedef_belirle("mithril", {"t": "term"}, True)[1], "mithril")

    def test_karsilik_istegi_hedef_dili_bildirir(self):
        from defta.terim import _karsiliklari_tamamla
        gidenler = []

        def sor(yuk):
            gidenler.append(yuk)
            return {"items": [{"i": 1, "tr": "Prenses"}]}
        kararlar = [[{"i": 1, "t": "title", "tr": "Prenses"}]]
        partiler = [[{"yazim": "Принцесса", "ornek": ["Принцесса!"], "sayi": 2}]]
        _karsiliklari_tamamla(kararlar, partiler, sor, 1, 1, baslik="Star Wars", dil="Turkish")
        self.assertTrue(gidenler)
        self.assertEqual(gidenler[0].get("target_language"), "Turkish")

    def test_film_dizi_terim_kurallari(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import _karsilik_sorulacak, _latin_adaylar, baslik_terimi, kisaltma_adlari
        # eserin adındaki uydurma sözcük kalır; sıradan anlamı olan çevrilir
        self.assertEqual(baslik_terimi("witchers", {"t": "people"}, "The Witcher"), "Witcher")
        self.assertEqual(baslik_terimi("gladiator", {"t": "people", "w": "gladiator"}, "Gladiator"), "")
        self.assertEqual(baslik_terimi("witchers", {"t": "people"}, "The Witcher", "mutant canavar avcısı"), "Witcher")
        self.assertEqual(baslik_terimi("dragon", {"t": "people"}, "House of the Dragon", "ejderha"), "")
        self.assertEqual(baslik_terimi("dragon", {"t": "people", "e": "ejderha"}, "House of the Dragon",
                                       "uçan sürüngen"), "")
        self.assertEqual(baslik_terimi("Samurai", {"t": "people"}, "The Last Samurai", "samuray"), "")
        # adın kısaltması addır
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("Catelyn", "name", "Catelyn")
        sz.girdi_ekle("Cat", "epithet", "Kedi")
        sz.girdi_ekle("Imp", "epithet", "Cüce")
        self.assertEqual(kisaltma_adlari(sz), ["Cat"])
        self.assertEqual(sz.girdiler["Cat"]["hedef"], "Cat")
        # iyelik unvan olmaz; tireli Arapça edat adın parçası; "of" ile biten parça kalmaz
        sayim, _, unvanlar, _, onler = _latin_adaylar(["Trust me, Your Grace.", "Your Grace, again.",
                                                      "Hail Lisan al-Gaib!", "The Hand of God came."], 1, set())
        self.assertNotIn("Your", onler)
        self.assertIn("Lisan al-Gaib", sayim)
        self.assertNotIn("Hand of", sayim)
        # "Your Grace" unvan + ad diye bölünmez ("Your -> Majesteleri" terimi olmaz)
        from defta.terim import _adla_gecen_unvanlar
        sz.girdi_ekle("Your Grace", "title", "Majesteleri", kalip="Majesteleri {ad}")
        sz.girdi_ekle("Warden Hale", "title", "Muhafız", kalip="Muhafız {ad}")
        sz.girdi_ekle("Grand Maester", "title", "Büyük Üstat", kalip="Büyük Üstat {ad}")
        sz.girdi_ekle("High Counsellor", "title", "", kalip="Başdanışman {ad}")
        sz.girdi_ekle("Ambassador Xandem", "title", "", kalip="Büyükelçi Xandem {ad}")
        sz.girdi_ekle("Brother Day", "title", "", kalip="Kardeş Gün {ad}")
        _adla_gecen_unvanlar(sz, {}, set(), "Ask the maesters. Warden Hale came.")
        self.assertNotIn("Your", sz.girdiler)
        self.assertIn("Warden", sz.girdiler)
        self.assertNotIn("Grand", sz.girdiler)
        for bas in ("High", "Ambassador", "Brother"):             # niteleyici / kalıbı ikinci sözcüğü taşıyan
            self.assertNotIn(bas, sz.girdiler)
        # dil adı ve gerçek halk adı büyük harf
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("Farsi", {"t": "people", "k": "farsça"}, True)[1], "Farsça")
        self.assertEqual(hedef_belirle("Portuguese", {"t": "people", "k": "Portekizce"}, True)[1], "")  # halk mı dil mi
        self.assertEqual(hedef_belirle("Japanese", {"t": "descriptive", "k": "Japonca"}, True)[1], "")
        self.assertEqual(hedef_belirle("Portuguese", {"t": "people", "k": "Portekizli"}, True)[1], "Portekizli")
        self.assertEqual(hedef_belirle("Scottish", {"t": "people", "k": "iskoç"}, True)[1], "İskoç")
        self.assertEqual(hedef_belirle("Scottish", {"t": "people", "k": "İskoçlar"}, True)[1], "İskoç")
        self.assertEqual(hedef_belirle("Welsh", {"t": "people", "k": "gallerliler"}, True)[1], "Gallerli")
        self.assertEqual(hedef_belirle("Aborigines", {"t": "people", "k": "Aborjinler", "real": True}, True)[1], "Aborjinler")
        # iyelikli çok sözcüklü terim küçültülmez
        self.assertEqual(hedef_belirle("Murphy's Law", {"t": "term"}, True)[1], "Murphy's Law")
        # bağlaçla kurulan ad tanımı sorulur ("Rivyalı Geralt")
        self.assertTrue(_karsilik_sorulacak({"t": "descriptive"}, "Geralt of Rivia"))

    def test_animenin_taninan_terimi_ozgun_sesiyle_kalir(self):
        from defta.terim import _karsilik_sorulacak, hedef_belirle
        bankai = {"t": "term", "l": "Bankai", "o": True}
        self.assertEqual(hedef_belirle("卍解", bankai, True, True, "tr", True), ("term", "Bankai"))
        self.assertEqual(hedef_belirle("月牙天衝", {"t": "term", "l": "Getsuga Tenshou", "o": True}, True, True, "tr", True),
                         ("term", "Getsuga Tensho"))                         # uzun ünlü ikilenmez
        self.assertEqual(hedef_belirle("死神", {"t": "people", "l": "Shinigami", "o": True}, True, True, "tr", True),
                         ("people", "Shinigami"))                            # halk adı gibi küçültülmez
        self.assertFalse(_karsilik_sorulacak(bankai, "卍解"))                  # karşılık istenmez
        # okunuşu hiraganayla verilmiş kanji terim: model "o" demese de okunuşuyla kalır
        self.assertEqual(hedef_belirle("獄門疆(ごくもんきょう)", {"t": "term", "k": "Hapishane Diyarı"}, True, True, "tr",
                                       True), ("term", "Gokumonkyo"))
        self.assertEqual(hedef_belirle("獄門疆(ごくもんきょう)", {"t": "term", "k": "Hapishane Diyarı"}, True, True, "tr",
                                       False), ("term", "Hapishane Diyarı"))   # anime ekleri çevriliyorsa kural yok
        # yalnız katakana yabancı kökenli sözcüktür: harf harf okunuşu yazılmaz, karşılık isteğinin işi
        self.assertEqual(hedef_belirle("チャクラ", {"t": "term", "l": "Chakura", "o": True, "k": "çakra"}, True, True, "tr",
                                       True), ("term", "çakra"))
        # anime ekleri çevriliyorsa (ozgun kapalı) ya da model "o" demediyse karşılık isteğinin işi
        self.assertEqual(hedef_belirle("卍解", bankai, True, True, "tr", False), ("term", ""))
        self.assertEqual(hedef_belirle("国王", {"t": "title", "l": "Kokuo", "k": "Kral"}, True, True, "tr", True),
                         ("title", "Kral"))

    def test_gercek_halk_ve_din_buyuk_harfle(self):
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("Muslim", {"t": "people", "k": "müslüman", "real": True}, True)[1], "Müslüman")
        self.assertEqual(hedef_belirle("Islam", {"t": "term", "k": "islam", "real": True}, True)[1], "İslam")
        self.assertEqual(hedef_belirle("Hobbit", {"t": "people", "k": "Hobbit"}, True)[1], "hobbit")   # kurgusal küçük

    def test_iyelikli_hitap_unvani_terim_olmaz(self):
        from defta.terim import unvan_govdesi
        self.assertEqual(unvan_govdesi("Our Lord"), "Lord")
        self.assertEqual(unvan_govdesi("my Lady"), "Lady")
        self.assertEqual(unvan_govdesi("Lordship"), "Lordship")

    def test_okunusu_ingilizce_siradan_sozcuk_cevrilir(self):
        from defta.terim import _karsilik_sorulacak, hedef_belirle, okunus_adi_mi
        kategori = {"t": "term", "l": "Category", "w": "category", "k": "kategori"}
        self.assertEqual(hedef_belirle("警戒強度(カテゴリー)", kategori, True)[1], "kategori")
        self.assertTrue(_karsilik_sorulacak(kategori, "警戒強度(カテゴリー)"))
        self.assertEqual(hedef_belirle("星(ステラ)", {"t": "term", "l": "Stella", "w": "star"}, True)[1], "Stella")
        self.assertTrue(okunus_adi_mi("滅却師(クインシー)", "Quincy"))          # qu = ku, c = s

    def test_tek_ad_parcasi_tam_adla_ayni_yazilir(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import tam_ad_parcalarini_esitle
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("ロイド・フォージャー", "name", "Loid Forger")
        sz.girdi_ekle("ヨル・フォージャー", "name", "Yor Forger")
        sz.girdi_ekle("フォージャー", "name", "Foja")            # oylar harf harf okunuşa gitmiş
        sz.girdi_ekle("ロイド", "name", "Loid")
        sz.girdi_ekle("ヨル", "name", "Yoru")
        sz.girdiler["ヨル"]["karar"] = "elle"                      # elle karar kalır
        sz.girdi_ekle("エース・サボ・ルフィ", "name", "Ace, Sabo, Luffy")   # ad listesi: tam ad değil
        sz.girdi_ekle("サボ", "name", "Sabo")
        self.assertEqual(tam_ad_parcalarini_esitle(sz), ["フォージャー"])
        self.assertEqual(sz.girdiler["フォージャー"]["hedef"], "Forger")
        self.assertEqual(sz.girdiler["ヨル"]["hedef"], "Yoru")

    def test_harf_harf_tam_ad_parcayi_ezmez_tam_ad_parcalardan_kurulur(self):
        """Gerçek test 10: ヨル "Yor" doğruydu, tam ad oyları sırada ayrışınca "Yoru Buraia" kaldı ve parçayı "Yoru"
        yaptı. Harf harf tam ad parçayı ezmez; parçalar biliniyorsa tam ad onlardan kurulur; oylar kaynak sırasına
        dizilir."""
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import kaynak_sirasi, tam_ad_parcalarini_esitle
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("ヨル・ブライア", "name", "Yoru Buraia")
        sz.girdi_ekle("ヨル", "name", "Yor")
        sz.girdi_ekle("ブライア", "name", "Briar")
        sz.girdi_ekle("ユーリ・ブライア", "name", "Yuri Buraia")          # ユーリ sözlükte yok: dokunulmaz
        self.assertEqual(tam_ad_parcalarini_esitle(sz), ["ヨル・ブライア"])
        self.assertEqual([sz.girdiler[y]["hedef"] for y in ("ヨル", "ブライア", "ヨル・ブライア", "ユーリ・ブライア")],
                         ["Yor", "Briar", "Yor Briar", "Yuri Buraia"])
        self.assertEqual(kaynak_sirasi("ヨル・ブライア", "Briar Yor"), "Yor Briar")
        self.assertEqual(kaynak_sirasi("ロイド・フォージャー", "Loid Forger"), "Loid Forger")
        self.assertEqual(kaynak_sirasi("影山茂夫", "Shigeo Kageyama"), "Shigeo Kageyama")    # kanji: kendi kuralı
        from defta.terim import _bilesik_adlari_esitle, _mekanik_mi
        self.assertTrue(_mekanik_mi("ヨル・ブライア", "Yoru Buraiya"))           # "iya" kayması da harf harf okunuş
        self.assertFalse(_mekanik_mi("ユーリ・ブライア", "Yuri Briar"))
        # parçaya tam ad verilmişse (ユーリ -> "Yuri Briar") tam ad ondan kurulmaz ("Yuri Briar Briar" çıkmıştı)
        k = [[{"i": 1, "t": "name", "l": "Yuri Briar"}, {"i": 2, "t": "name", "l": "Yuri Briar"}]]
        _bilesik_adlari_esitle(k, [[{"yazim": "ユーリ"}, {"yazim": "ユーリ・ブライア"}]], type("S", (), {"girdiler": {}}))
        self.assertEqual(k[0][1]["l"], "Yuri Briar")

    def test_japonca_tam_ad_duyuldugu_sirayla(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import _cogunluk, japon_ad_sirasi

        def sec(yazim, oy):
            return _cogunluk([{"items": [dict(oy, i=1, t="name")]}], {1: yazim})[0].get("l")
        self.assertEqual(sec("影山茂夫(しげお)", {"l": "Shigeo Kageyama", "r": "かげやましげお"}), "Kageyama Shigeo")
        self.assertEqual(sec("森重恭平(もりしげきょうへい)", {"l": "Kyōhei Morishige"}), "Morishige Kyōhei")
        self.assertEqual(sec("神室真司", {"l": "Kamuro Shinji", "r": "かむろしんじ"}), "Kamuro Shinji")
        self.assertEqual(sec("ロイド・フォージャー", {"l": "Loid Forger"}), "Loid Forger")
        self.assertEqual(_cogunluk([{"items": [{"i": 1, "t": "name"}]}], {1: "Boromir of Gondor"})[0]["t"], "descriptive")
        self.assertEqual(_cogunluk([{"items": [{"i": 1, "t": "name", "real": True}]}], {1: "Leonardo da Vinci"})[0]["t"],
                         "name")
        tanim = _cogunluk([{"items": [{"i": 1, "t": "name", "l": "Dash Baba"}]}], {1: "婆(ババア)"})[0]
        self.assertEqual((tanim["t"], tanim.get("l")), ("descriptive", None))     # okunuşu tutmayan uyarlama
        self.assertEqual(sec("東国(オスタニア)", {"l": "Ostania"}), "Ostania")
        from defta.terim import hedef_belirle
        self.assertEqual(hedef_belirle("鬼瓦天牙(おにがわらてんが)", {"t": "name", "l": "Onigawara Tenga",
                                                                "e": "Tenga Onigawara"}, True)[1], "Onigawara Tenga")
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("神室", "name", "Kamuro")
        sz.girdi_ekle("真司", "name", "Shinji")
        sz.girdi_ekle("神室真司", "name", "Shinji Kamuro")
        sz.girdi_ekle("森重恭平(もりしげきょうへい)", "name", "Kyohei Morishige")
        self.assertEqual(sorted(japon_ad_sirasi(sz)), ["森重恭平(もりしげきょうへい)", "神室真司"])
        self.assertEqual(sz.girdiler["森重恭平(もりしげきょうへい)"]["hedef"], "Morishige Kyohei")
        self.assertEqual(sz.girdiler["神室真司"]["hedef"], "Kamuro Shinji")

    def test_turemis_ad_disari_sayilmaz(self):
        from defta.terim import _adlara_uyan
        adlar = {"Troy": "Truva", "Sparta": "Sparta"}
        self.assertEqual(_adlara_uyan(["Truva Savaşı", "Troya Savaşı"], "Trojan War", adlar), ["Truva Savaşı"])
        self.assertEqual(_adlara_uyan(["Sparta Savaşı", "Büyük Savaş"], "Great War", adlar), ["Büyük Savaş"])

    def test_elle_karar_tutarlilik_gecislerinden_korunur(self):
        import tempfile
        from defta.sozluk import Sozluk
        from defta.terim import cogullari_esitle, japon_ad_sirasi
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        sz.girdi_ekle("dwarf", "people", "cüce")
        sz.girdi_ekle("dwarves", "people", "cüceler", karar="elle")
        sz.girdi_ekle("神室", "name", "Kamuro")
        sz.girdi_ekle("真司", "name", "Shinji")
        sz.girdi_ekle("神室真司", "name", "Shinji Kamuro", karar="elle")
        cogullari_esitle(sz)
        self.assertEqual(japon_ad_sirasi(sz), [])
        self.assertEqual((sz.girdiler["dwarves"]["hedef"], sz.girdiler["神室真司"]["hedef"]), ("cüceler", "Shinji Kamuro"))

    def test_kurulmus_sozluk(self):
        import tempfile
        from defta.sozluk import Sozluk
        sz = Sozluk(os.path.join(tempfile.mkdtemp(), "s.json"))
        for n in range(25):
            sz.girdi_ekle(f"Ad{n}", "name", f"Ad{n}")
        self.assertFalse(sz.kurulmus())                 # hiç dosya incelenmedi
        sz.analiz_edilen.append("S01E01.srt")
        self.assertTrue(sz.kurulmus())

    def test_kaydet_diskteki_elle_karari_korur(self):
        import tempfile
        from defta.sozluk import Sozluk
        yol = os.path.join(tempfile.mkdtemp(), "s.json")
        a = Sozluk(yol)
        a.girdi_ekle("Strider", "epithet", "Yolgezer")
        a.kaydet()
        b = Sozluk(yol)                                   # süren çeviri sözlüğü açık tutuyor
        duzelten = Sozluk(yol)
        duzelten.girdi_yaz("Strider", hedef="Adımsız", karar="elle")
        duzelten.kaydet()
        b.girdi_ekle("Bree", "name", "Bree")
        b.kaydet()                                        # elle düzeltmeyi ezmez
        c = Sozluk(yol)
        self.assertEqual((c.girdiler["Strider"]["hedef"], c.girdiler["Strider"]["karar"]), ("Adımsız", "elle"))
        self.assertIn("Bree", c.girdiler)

    def test_japon_tv_isaretleri_atilir(self):
        from defta.temizlik import kaynak_hazirla
        self.assertEqual(kaynak_hazirla("（ロビン）なぜなら…。➡")[0], "（ロビン）なぜなら…。")
        self.assertEqual(kaynak_hazirla("🔊（通信兵）ルルシア王国は\n世界会議後に➡")[0], "（通信兵）ルルシア王国は\n世界会議後に—")
        self.assertEqual(kaynak_hazirla("この船に\n乗ってしまったからにゃあ➡\nいや")[0], "この船に\n乗ってしまったからにゃあ\nいや")

    def test_seri_adi(self):
        from defta.sozluk import seri_adi
        self.assertEqual(seri_adi("The Lord of the Rings: The Two Towers"), "The Lord of the Rings")
        self.assertEqual(seri_adi("Mission: Impossible - Fallout"), "Mission")
        self.assertEqual(seri_adi("Re:Zero"), "Re:Zero")
        self.assertEqual(seri_adi("The Lord of the Rings The Two Towers", ["The Lord of the Rings", "Spy x Family"]),
                         "The Lord of the Rings")
        self.assertEqual(seri_adi("The Ring Two", ["The Ring"]), "The Ring Two")       # artikel dışında tek sözcük
        self.assertEqual(seri_adi("It Follows", ["It"]), "It Follows")
        self.assertEqual(seri_adi("Spy x Family", ["Spy x Family"]), "Spy x Family")

    def test_bipli_pekistirici_nitelik_degil(self):
        from defta.denetim import BIP_ONUNDE, KLISE, NITELIK_KUFUR
        self.assertTrue(BIP_ONUNDE.search("- [bleep] cold."))
        self.assertTrue(BIP_ONUNDE.search("That's the [bleep] oven."))
        self.assertFalse(BIP_ONUNDE.search("What the [bleep]?"))
        self.assertTrue(NITELIK_KUFUR["tr"].search("bok gibi soğuk."))
        k = KLISE["tr"]
        self.assertTrue(k.search("şu lanet fırını temizle"))
        self.assertTrue(k.search("lanet olsun!"))
        self.assertFalse(k.search("ona lanet etti"))
        self.assertFalse(k.search("lanetli bir ev"))
        from defta.denetim import ARAYA_KUFUR
        self.assertTrue(ARAYA_KUFUR["tr"].search("ortalığı amına koyayım savaş alanına çeviriyorsun"))
        self.assertFalse(ARAYA_KUFUR["tr"].search("geç kaldın, amına koyayım."))
        self.assertFalse(ARAYA_KUFUR["tr"].search("amına koyayım, lastik gibi olmuş."))
        self.assertTrue(ARAYA_KUFUR["tr"].search("o, lan, ızgarada durmuş."))
        self.assertFalse(ARAYA_KUFUR["tr"].search("ızgarada durmuş lan, görmüyor musun?"))

    def test_okunusu_tutmayan_yazim_ad_sayilmaz(self):
        from defta.terim import okunus_adi_mi
        self.assertTrue(okunus_adi_mi("東国(オスタニア)", "Ostania"))
        self.assertTrue(okunus_adi_mi("梟(ストリクス)", "Strix"))
        self.assertFalse(okunus_adi_mi("婆(ババア)", "Dash Baba"))

    def test_bir_kez_gecen_okunuslu_sozcuk(self):
        from defta.terim import _japonca_adaylar
        a = _japonca_adaylar("作戦を担う 黄昏(たそがれ)は—\n霊幻(れいげん)です\n霊幻 師匠")
        self.assertIn("黄昏(たそがれ)", a)
        self.assertNotIn("霊幻(れいげん)", a)      # sözcük okunuşsuz da geçiyor: okuma yardımı


class Karar105Testleri(unittest.TestCase):
    """18.11.2026: katakana adın oyları ayrılınca uzun ünlü işaretli harf harf okunuş ("Furankī Furankurin")
    mekanik sayılır; oyların çoğunun yerleşik biçimi aynı sesse ("Franky Franklin") ad odur."""

    def _sec(self, yazim, *oylar):
        from defta.terim import _cogunluk
        return _cogunluk([{"items": [dict(o, i=1, t="name")]} for o in oylar], {1: yazim})[0].get("l")

    def test_uzun_unlu_isaretli_okunus_mekaniktir(self):
        from defta.terim import _mekanik_mi
        self.assertTrue(_mekanik_mi("フランキー・フランクリン", "Furankī Furankurin"))
        self.assertTrue(_mekanik_mi("バーリント", "Bārinto"))
        self.assertFalse(_mekanik_mi("ステラ", "Stella"))

    def test_yerlesik_bicim_mekanik_cogunluga_ustun(self):
        self.assertEqual(self._sec("フランキー・フランクリン", {"l": "Furankī Furankurin", "e": "Franky Franklin"},
                                   {"l": "Franky Franklin", "e": "Franky Franklin"},
                                   {"l": "Furankī Furankurin", "e": "Franky Franklin"}), "Franky Franklin")
        self.assertEqual(self._sec("シルヴィア・シャーウッド", {"l": "Shirūia Shāwuddo", "e": "Sylvia Sherwood"},
                                   {"l": "Shirwood Sylvia", "e": "Sylvia Sherwood"},
                                   {"l": "Shiruvia Shāuddo", "e": "Sylvia Sherwood"}), "Sylvia Sherwood")

    def test_resmi_cogunluk_ve_ceviri_yerlesik_degismez(self):
        self.assertEqual(self._sec("ロイド", {"l": "Loid", "e": "Lloyd"}, {"l": "Loid", "e": "Lloyd"},
                                   {"l": "Roido", "e": "Lloyd"}), "Loid")
        self.assertEqual(self._sec("ホシ", {"l": "Hoshi", "e": "Star"}, {"l": "Hoshi"}, {"l": "Hoshi", "e": "Star"}),
                         "Hoshi")
        from defta.terim import _sesi_tutan_yerlesik
        self.assertEqual(_sesi_tutan_yerlesik("ホシ", ["Star", "Star", "Star"], 3), "")
        self.assertEqual(_sesi_tutan_yerlesik("バーリント", ["Berlint", "Berlint"], 3), "Berlint")
        self.assertEqual(_sesi_tutan_yerlesik("バーリント", ["Berlint"], 3), "")

    def test_ekli_soz_karsilik_ipucu_hedef_dili_ister(self):
        from defta.terim import EKLI_KARSILIK_IPUCU
        self.assertIn("never Japanese", EKLI_KARSILIK_IPUCU)


class Karar104Testleri(unittest.TestCase):
    """Kodi'deki LOTR çevirisinde 16 "terim kaçtı" uyarısının hepsi konuşmacı etiketiydi ("Pippin: ...")."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gunluk = Gunluk(os.path.join(self.tmp.name, "logs"))
        self.sozluk = Sozluk(os.path.join(self.tmp.name, "s.json"))
        self.ayar = ayar_birlestir({"model": {"es_zamanli_istek": 1}})

    def test_sozun_basindaki_etiket_dosyada_tekrarlaniyorsa_taninir(self):
        from defta.kaynak import latin_etiketleri, not_ve_konusmaci_ayir
        metinler = ["Pippin: Biraz da lahana.", "Pippin: Sonra?", "- Legolas!\n- Pippin: Koş!", "Merry: Hayır.",
                    "Look: there.", "Look: here.", "Look: now.", "look at him",
                    "Second Hobbit: Gandalf!", "Second Hobbit: Ne?", "Second Hobbit: Of.", "a second time"]
        etiketler = latin_etiketleri(metinler)
        self.assertEqual(etiketler, {"Pippin"})          # Merry bir kez; Look ve Second küçük harfle de geçiyor
        kalan, _, konusan = not_ve_konusmaci_ayir("Pippin: Biraz da lahana.", adli_etiketler=etiketler)
        self.assertEqual((kalan, konusan), ("Biraz da lahana.", ["Pippin"]))

    def test_etiket_duzeni_kesin_dosyada_kucuk_harfle_de_gecen_ad_etiket_sayilir(self):
        from defta.kaynak import latin_etiketleri
        kesin = ["Gandalf: Bir.", "Gandalf: İki.", "Gandalf: Üç.", "Frodo: Bir.", "Frodo: İki.", "Frodo: Üç."]
        ozensiz = ["Merry: Evet.", "Merry: Hayır.", "where is merry?", "Look: there.", "look at him"]
        self.assertEqual(latin_etiketleri(kesin + ozensiz), {"Gandalf", "Frodo", "Merry"})   # "Look" bir kez
        self.assertEqual(latin_etiketleri(kesin[:3] + ozensiz), {"Gandalf"})               # düzen kesin değil

    def test_etiket_duzeni_kesin_dosyada_tek_ve_ara_etiket_ayrilir(self):
        """LOTR: bir kez geçen etiket ("Maggot:", "Voice of the ring:", OCR'lı "Sau Ron:") ve aynı satırdaki ikinci
        etiket ("Are we lost? Merry: No.") çeviride kalıyordu; ardından söz gelmeyen yalnız etiket de ("Gimli:").
        Sık cümle başı ve "A promise:" gibi sözler etiket sayılmaz."""
        from defta.cevirmen import konusma_tireleri
        from defta.kaynak import latin_etiketleri, not_ve_konusmaci_ayir
        kesin = ["Gandalf: Bir.", "Gandalf: İki.", "Gandalf: Üç.", "Frodo: Bir.", "Frodo: İki.", "Frodo: Üç."]
        tekil = ["Maggot: You get back here!", "Voice of the ring: Baggins.", "Sau Ron:\nBuild me an army.",
                 "Pippin: Are we lost? Merry: No.", "A promise:\n\"Don't you leave him.\"", "Look: there.", "Gimli:"]
        etiketler = latin_etiketleri(kesin + tekil)
        self.assertTrue({"Maggot", "Voice of the ring", "Sau Ron", "Pippin", "Gimli"} <= etiketler)
        self.assertFalse({"A promise", "Look"} & etiketler)

        def ayir(metin):
            kalan, _, konusan = not_ve_konusmaci_ayir(metin, adli_etiketler=etiketler | {"Merry"})
            return konusma_tireleri(kalan, konusan) if konusan else kalan
        self.assertEqual(ayir("Maggot: You get back here!"), "You get back here!")
        self.assertEqual(ayir("Voice of the ring: Baggins."), "Baggins.")
        self.assertEqual(ayir("Pippin: Are we lost? Merry: No."), "- Are we lost?\n- No.")
        self.assertEqual(ayir("He's remembered. Gandalf: No."), "- He's remembered.\n- No.")
        self.assertEqual(ayir("Gimli:"), "")
        self.assertEqual(ayir("A promise:\n\"Don't you leave him.\""), "A promise:\n\"Don't you leave him.\"")

    def test_kesif_sayimi_harf_ayirmaz_ve_cogulu_sayar(self):
        from defta.terim import _kesif_sayisi
        metin = "A wraith came. Two wraiths! The Fellowship is broken. fellowship of nine. Wraithlike."
        self.assertEqual(_kesif_sayisi("wraith", metin), 2)         # "Wraithlike" sayılmaz
        self.assertEqual(_kesif_sayisi("fellowship", metin), 2)
        self.assertEqual(_kesif_sayisi("Fellowship", metin), 2)          # büyük harfli yazımda çoğul eklenmez
        self.assertEqual(_kesif_sayisi("霊力", "霊力と霊力"), 2)

    def test_kucuk_harfle_yazilmis_ozel_ad_buyuk_harfle_girer(self):
        satirlar = [srt.Satir(i + 1, i * 2000, i * 2000 + 1500, m) for i, m in enumerate(
            ["We ride to mordor.", "The road to mordor is long.", "minas tirith will fall.", "minas tirith burns.",
             "Two hobbits came.", "The hobbits ran."])]
        model = SahteModel(terim={"mordor": {"t": "name"}, "minas tirith": {"t": "name"}, "hobbits": {"t": "people", "tr": "hobbit"}},
                           kesif=["mordor", "minas tirith", "hobbits"])
        from defta.terim import terimleri_cikar
        terimleri_cikar([("l.srt", satirlar, "")], self.ayar, model, self.sozluk, self.gunluk)
        self.assertEqual(self.sozluk.girdiler["mordor"]["hedef"], "Mordor")
        self.assertEqual(self.sozluk.girdiler["minas tirith"]["hedef"], "Minas Tirith")
        self.assertEqual(self.sozluk.girdiler["hobbits"]["hedef"], "hobbit")     # cins ad küçük kalır

    def test_etiketteki_ad_terim_kactı_sayilmaz(self):
        self.sozluk.elle_ekle({"Merry": "Merry", "Gandalf": "Gandalf"})
        satirlar = [srt.Satir(1, 1000, 2000, "Ne dedin? Merry: Hiç."),
                    srt.Satir(2, 5000, 6500, "Ikinci hobbit 1: Gandalf geliyor!"),
                    srt.Satir(3, 9000, 10500, "Merry, wait!")]
        cevap = {1: "- Ne dedin?\n- Hiç.", 2: "Gandalf geliyor!", 3: "Bekle!"}
        model = SahteModel(lambda _, s: [dict(x, text=cevap[x["id"]]) for x in s])
        Cevirmen(self.ayar, model, "S", self.gunluk, self.sozluk).cevir(satirlar, "o.srt")
        yeniden = [x["id"] for x in model.gidenler[1]["lines"] if not x.get("skip")] if model.cagri > 1 else []
        self.assertEqual(yeniden, [3])                   # yalnız gerçekten kaçan satır yeniden istendi


if __name__ == "__main__":
    unittest.main()
