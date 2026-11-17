"""Çeviriden sonraki denetimler: şüpheli satırın yeniden sorulması (kayma, klişe, eksik içerik), yinelenen satır.

Okumaya süresi yetmeyen çeviri kısalttırılmaz (kısalırken anlam gidiyordu): yazım aşaması o altyazının
ekranda kalma süresini sonraki altyazıya kadarki sessizliğe doğru uzatır, metne dokunmaz."""
import json
import re

from . import temizlik
from .ayar import dil_adi, hedef_kodu
from .cumle import BIRLESTI
from .hitap import SEN, SIZ
from .kaynak import _yalin
from .model import Iptal, Ret, json_coz

SECIM_ISTEMI = """A film subtitle repeats some of its lines (a flashback, a repeated scene, a line quoted or echoed later). Each such line was translated into {dil} in more than one way. For every item with "candidates", choose the candidate that renders "source" most accurately and naturally ("before" and "after", when given, are its neighbouring source lines), so that the line reads the same every time. Choose only among the candidates."""
YANKI_ISTEMI = """For every item with "sentence": "source" repeats words that are also said inside a longer sentence elsewhere in the film ("sentence", translated as "sentence_translation"). Translate "source" with the same wording that sentence_translation uses for those words, so the echo is heard ("current" is its translation now)."""
YANKI = re.compile(r'^["“]?(We|Me|I|You|Us|Him|Her|Them|Mine|Ours)["”]?\s*\?$', re.I)
ARDISIK_UZAKLIK = 3        # art arda yinelenen replik en çok kaç altyazı sonra aranır (araya giren konuşmalar)
ARDISIK_BOSLUK = 150       # ms: ilkinin bitişi ile ikincinin başlangıcı arasındaki en büyük boşluk


def ardisik_mi(once, sonra):
    """İki altyazının süreleri bitişik mi (biri biterken öteki başlıyor)."""
    return -ARDISIK_BOSLUK <= sonra.bas - once.bit <= ARDISIK_BOSLUK


KLISE = {"tr": re.compile(r"kahretsin|kahrolası|lanet olası|seni aptal|seni pislik")}

# Hedef dilde yazılmamış tepki sesi (İngilizce ya da Japonca okunuşuyla kalmış): satırın tamamı ya da başı.
YABANCI_SES = {"tr": re.compile(r"(?<![\wçğıöşü])(u+gh+|a+r+gh+|u+wa+h*|n+g+h+|hmph|oops|ouch|ga+h|e+w+|whoa+|yikes|phew)(?![\wçğıöşü])")}


class Denetimler:
    """Cevirmen'in parçası: script somut bir işaret görürse o satırı nedeniyle birlikte bir kez sorar."""

    def _supheler(self, idler, kabul):
        """index -> modele sorulacak kısa neden. Yalnızca kesin ölçülebilen işaretlere bakar."""
        supheli, kod = {}, hedef_kodu(self.ayar)

        def ekle(i, neden):                  # bir satırın birden çok işareti varsa hepsi birlikte sorulur
            supheli[i] = f"{supheli[i]}; also {neden}" if i in supheli else neden

        def duz(i):
            return temizlik.ETIKET.sub("", kabul[i]).replace("⟦", "").replace("⟧", "")

        for i in idler:
            kaynak, ceviri = self._kaynak(i), duz(i)
            kucuk = temizlik.tr_kucuk(ceviri)
            # dublaj klişesi (kaynak dil ne olursa olsun)
            klise = KLISE.get(kod)
            m = klise.search(kucuk) if klise else None
            if m:
                ekle(i, f'"{m.group(0)}" is a dubbing cliché: use what a person would really say, at the same strength')
            ses = YABANCI_SES.get(kod)
            m = ses.search(kucuk) if ses else None
            if m:
                ekle(i, f'"{m.group(0)}" is not how {dil_adi(self.ayar)} speakers write this sound: write the sound '
                        f"in its {dil_adi(self.ayar)} spelling")
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

    def _kayma_supheleri(self, idler, kabul):
        """Çevirinin yanlış altyazıya yazıldığını gösteren kesin işaretler: index -> neden.

        1) Komşu iki altyazının kaynağı farklı, çevirisi aynı.
        2) Kaynakta bir altyazıda geçen ad, çeviride onun yerine komşu altyazıda geçiyor.
        3) Kaynaktaki ad çeviride yok, yerinde kaynakta olmayan başka bir ad var ("Oh, Sam." -> "Ah, Frodo.").
        İşaretli satırların arasında kalanlar da sorulur: kayma tek satırda durmaz."""
        sira = [i for i in idler if i in kabul]
        adlar = [a for a in self.sozluk.degismez
                 if len(a) >= 3 and " " not in a and not temizlik.latin_disi(a)] if self.sozluk is not None else []
        desenler = [(a, re.compile(r"(?<![\w'’])" + re.escape(a))) for a in adlar]

        def gecen(metin):
            return {a for a, d in desenler if d.search(metin)}

        duz = {i: temizlik.ETIKET.sub("", kabul[i]) for i in sira}
        kaynak = {i: temizlik.ETIKET.sub("", self._kaynak(i)).replace("⟦", "").replace("⟧", "") for i in sira}
        k_ad = {i: gecen(kaynak[i]) for i in sira}
        c_ad = {i: gecen(duz[i]) for i in sira}
        kayan, neden = set(), {}
        for a, b in zip(sira, sira[1:]):
            if b - a > len(self.birlesik.get(a, {}).get("grup", [a])):
                continue                                    # arada başka (gönderilmeyen) altyazı var: komşu değiller
            if len(_yalin(duz[a])) >= 12 and _yalin(duz[a]) == _yalin(duz[b]) and _yalin(kaynak[a]) != _yalin(kaynak[b]):
                # Aynı çeviri iki kez yazıldıysa kayma daha önce başlamıştır: öncesindeki üç satır da sorulur.
                kayan.update(sira[max(0, sira.index(a) - 3):sira.index(b) + 1])
            for x, y in ((a, b), (b, a)):                   # ad x'in kaynağında, ama çevirisi y'ye yazılmış
                if (k_ad[x] - c_ad[x]) & (c_ad[y] - k_ad[y]):
                    kayan.update((a, b))
        for i in sira:
            eksik, fazla = k_ad[i] - c_ad[i], c_ad[i] - k_ad[i]
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
            for i in kayan:
                neden[i] = ("translations around this id are shifted against their source lines (the content of one id "
                            "is written under a neighbouring id): translate every id from its own source text only")
        return neden

    def _sorgula(self, idler, kabul):
        """Şüpheli satırları nedenleriyle bir kez daha sorar; yanıt kontrolleri geçerse kullanılır."""
        if not self.ayar["kontrol"]["supheli_satir_kontrolu"]:
            return
        supheli = self._supheler(idler, kabul)
        if not supheli:
            return
        kalan = sorted(supheli)
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

    def _tekrar_eden_sahne(self, sonuc):
        """Yinelenen sahne her geçişinde aynı okunur. Aynı replik önceki ya da sonraki satırıyla birlikte yeniden
        geçiyorsa (geri dönüş, hatırlama) çeviriler farklıysa hangisinin kalacağını model seçer: yeni metin yazmaz,
        adaylardan birini işaret eder. Tek başına yeniden geçen replik birleştirilmez: aynı sözler başka bir
        bağlamda başka anlama gelir (tanıtımda yarım kesilen cümle ile sahnedeki tamamı); seçim doğru çeviriyi
        yanlışıyla değiştiriyordu. Art arda gelen aynı replik ayrıdır (_ardisik_yinelenen).
        Uzun replik başka bir geçişinde bölünmüş bir cümlenin parçasıysa o parçanın çevirisi ayrıca yoktur: model
        tek başına geçen satırı o cümlenin çevirisindeki sözlerle yeniden yazar. Sen/siz biçimi farklı çeviriler
        (başka birine söylenmiş olabilir) birleştirilmez."""
        def norm(i):
            if not 0 <= i < len(self.satirlar):
                return ""
            return " ".join(temizlik.ETIKET.sub("", self.satirlar[i].metin).lower().split())

        def duz(i):
            return temizlik.ETIKET.sub("", sonuc[i]).strip()

        def hitap_biri(metinler):            # Türkçede sen ve siz biçimli çeviriler bir arada değil
            if hedef_kodu(self.ayar) != "tr":
                return True
            bicimler = {("siz" if SIZ.search(k) else "") + ("sen" if SEN.search(k) else "")
                        for k in map(temizlik.tr_kucuk, metinler)} - {""}
            return len(bicimler) <= 1

        def uzun(i):                         # baştaki tire, tırnak ve üç nokta, sondaki nokta ve virgül sayılmaz
            t = re.sub(r"^[\s\-–—….\"'“”‘’«»„]+|[\s.,;:…\"'“”‘’«»]+$", "", norm(i))
            yeter = len(re.sub(r"\W", "", t)) >= 10 if temizlik.latin_disi(t) else len(re.findall(r"\w+", t)) >= 4
            return t if yeter else ""

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
        for anahtar, idler in ciftler.items():
            if len(idler) > 1:
                i0 = idler[0]
                gruplar.setdefault((norm(i0 - 1), norm(i0), norm(i0 + 1)), [])
                hedef = gruplar[(norm(i0 - 1), norm(i0), norm(i0 + 1))]
                hedef.extend(x for x in idler if x not in hedef)
        sahnede = {i for v in gruplar.values() if len(v) > 1 for i in v}
        secilecek = [({"before": k[0], "source": k[1], "after": k[2]}, v, "tekrar eden sahne")
                     for k, v in gruplar.items() if len(v) > 1 and len({duz(i) for i in v}) > 1]
        tekli, parcali = {}, {}
        for i in sorted(self.giden):
            anahtar = uzun(i) if i in sonuc and i not in sahnede else ""
            if not anahtar:
                continue
            if i in self.uye or i in self.birlesik:
                parcali.setdefault(anahtar, self.uye.get(i, i))
            else:
                tekli.setdefault(anahtar, []).append(i)
        yazilacak = []
        for anahtar, idler in tekli.items():
            kaynak = " ".join(temizlik.ETIKET.sub("", self.satirlar[idler[0]].metin).split())
            if anahtar in parcali:
                cumle = self.birlesik[parcali[anahtar]]
                onceki = " ".join(" ".join(duz(j).split()) for j in cumle["grup"] if sonuc.get(j, BIRLESTI) != BIRLESTI)
                if hitap_biri([onceki] + [duz(i) for i in idler]):
                    yazilacak.append(({"source": kaynak, "sentence": cumle["metin"], "sentence_translation": onceki,
                                       "current": " ".join(duz(idler[0]).split())}, idler))
        if not secilecek and not yazilacak:
            return
        ogeler, adaylar = [], []
        for n, (oge, idler, _) in enumerate(secilecek, 1):
            secenek = list(dict.fromkeys(duz(i) for i in idler))
            adaylar.append(secenek)
            ogeler.append({"n": n, **oge, "candidates": {str(k): c for k, c in enumerate(secenek, 1)}})
        for n, (oge, _) in enumerate(yazilacak, len(secilecek) + 1):
            ogeler.append({"n": n, **oge})
        bicim = (['"choices": [{"n": 1, "pick": 2}]'] if secilecek else []) + (
            [f'"texts": [{{"n": {len(secilecek) + 1}, "text": "..."}}]'] if yazilacak else [])
        sistem = (SECIM_ISTEMI.format(dil=dil_adi(self.ayar)) + ("\n" + YANKI_ISTEMI if yazilacak else "")
                  + "\nReturn ONLY this JSON object: {" + ", ".join(bicim) + "} with one entry per item.")
        secim, yazi = {}, {}
        try:
            ham = self.model.iste(sistem, json.dumps({"items": ogeler}, ensure_ascii=False))
            self.gunluk.ham(self.etiket, {"tur": "secim", "giden": {"items": ogeler}, "gelen": ham})
            cevap = json_coz(ham)
            for x in (cevap.get("choices") or []):
                secim[int(x["n"])] = int(x["pick"])
            for x in (cevap.get("texts") or []):
                if isinstance(x.get("text"), str) and x["text"].strip():
                    yazi[int(x["n"])] = x["text"].strip()
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
            k = secim.get(n, 1)
            secilen = secenek[k - 1] if 1 <= k <= len(secenek) else secenek[0]
            for i in idler:
                if duz(i) != secilen:
                    koy(i, secilen, f"{neden}, seçilen çeviri kullanıldı")
        for n, (_, idler) in enumerate(yazilacak, len(secilecek) + 1):
            if n not in yazi:
                continue
            # Model sondaki noktalamayı düşürdüyse satırın şimdiki çevirisindeki konur (".", "?!", "...").
            son = re.search(r"[.!?…]+$", duz(idler[0]))
            metin = yazi[n] + (son.group() if son and not re.search(r"[.!?…]$", yazi[n]) else "")
            denenen = {i: {i: metin} for i in idler}             # satır denetimi metni yerinde düzeltir
            sorun = next((s for s in (self._satir_kontrol(i, d) for i, d in denenen.items()) if s), None)
            if sorun:
                self.gunluk.uyari(self.etiket, f"satır {idler[0] + 1}: yankı için yazılan çeviri kullanılmadı "
                                               f"({sorun}): {metin!r}")
                continue
            for i, d in denenen.items():
                if duz(i) != temizlik.ETIKET.sub("", d[i]).strip():
                    koy(i, d[i], "yankı, bölünmüş cümledeki sözlerle yeniden yazıldı")
