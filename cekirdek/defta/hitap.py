"""Sen/siz: tablo için kaynaktan okunan kanıt, modelin yanıtlarının oylanması ve tablonun isteklere konması.

Kimin kime resmi ya da samimi konuştuğunu çoğu satırda yalnızca model çıkarabilir. Ama kaynağın kendisi
söylüyorsa script okur; aynı dosya her zaman aynı kanıtı verir:
  - unvanla seslenme ("Yes, Captain.", "Master Tolen, ..."): konuşan o kişiye resmidir
  - Japoncada kibar biçim (です/ます) resmi, düz biçim ve お前 / あんた / くん / ちゃん samimidir
Kanıt iki işe yarar: modele gider (tabloyu sıfırdan değil bunlara dayanarak kurar) ve aynı çift için en az iki
satırda, hep aynı yönde görülmüşse tabloya doğrudan script'in kararı olarak girer (model değiştiremez).
"""
import re
from collections import Counter

from . import temizlik
from .kaynak import JP_AD_EK

ETIKET_LATIN = re.compile(r"^\s*(?:<i>)?\s*([A-Z][A-Z .'’-]{1,22}):\s+")
ETIKET_JP = re.compile(r"^\s*（([^（）]{1,12})）")
SAYGI_SOZU = (r"sir|ma'am|madam|my lord|my lady|milord|milady|your majesty|your grace|your highness|"
              r"your honou?r|your excellency")
# "でしょう？" (değil mi?) ve "〜ましょう" (haydi) arkadaşlar arasında da söylenir: kibarlık kanıtı sayılmaz.
# "のよ / のね / わよ / かしら" düz konuşan kadın karakterin sonlarıdır ("ですのよ" gibi kibar biçim önce yakalanır).
JP_KIBAR = re.compile(r"(?:です|ます|ません|ました|でした|ください|ございます)[かねよわな]*[。！？!?…〜ー～]*\s*$", re.M)
JP_DUZ = re.compile(r"お前|おまえ|貴様|きさま|てめえ|あんた|(?:だ|だな|だぞ|だろ|だよ|ぞ|しろ|くれ|するな|のか|のよ|のね|わよ|わね|かしら)"
                    r"[。！？!?…]*\s*$", re.M)
JP_SESLENME = re.compile(r"([ァ-ヺー]{2,})(さん|様|さま|先生|先輩|隊長|団長|くん|君|ちゃん)?(?=[　 、。！？!?…]|$)")
ARA = 6000          # ms: bundan uzun sessizlik karşılıklı konuşmayı böler


def _konusan(metin):
    """(konuşmacı etiketi, etiketsiz metin). Etiket yoksa ya da altyazıda iki konuşmacı varsa (None, metin)."""
    for desen in (ETIKET_JP, ETIKET_LATIN):
        m = desen.match(metin)
        if m:
            kalan = metin[m.end():]
            if any(desen.match(satir) for satir in kalan.split("\n")[1:]):
                return None, metin
            ad = m.group(1).strip()
            # Ses etiketi konuşmacı değildir: （ドアが開く音）, （犬のおびえる声）
            return (None if re.search(r"[音声]$", ad) else ad), kalan
    return None, metin


def kanit_topla(satirlar, unvan_sozcukleri):
    """Bir dosyanın satırlarından kanıt: (kimden, kime) -> Counter({"resmi": n, "samimi": n}) ve nedenler.

    "kimden" konuşmacı etiketindeki yazımdır; etiket yoksa "?" (kim olduğunu model bilir). "kime": adıyla ya da
    unvan + adla seslenilen kişi, yoksa karşılıklı konuşmada araya girdiği öteki konuşmacı."""
    unvan = "|".join(sorted(map(re.escape, (u for u in unvan_sozcukleri if not temizlik.latin_disi(u))), key=len, reverse=True))
    unvanli = re.compile(rf"(?:^|[,.!?…]\s+)(?:{unvan})\.? ([A-Z][a-z]+)(?=[,.!?…]|\s*$)", re.M) if unvan else None
    yalin = re.compile(rf"(?:^|[,.!?…]\s+)(?:my )?(?:{unvan + '|' if unvan else ''}{SAYGI_SOZU})(?=[,.!?…]|\s*$)", re.M | re.I)
    konusanlar = [_konusan(temizlik.ETIKET.sub("", s.metin)) for s in satirlar]
    kanit, neden = {}, {}

    def ekle(kimden, kime, bicim, aciklama):
        if kimden and kime and kimden.lower() != kime.lower():
            kanit.setdefault((kimden, kime), Counter())[bicim] += 1
            neden.setdefault((kimden, kime, bicim), Counter())[aciklama] += 1

    for i, (kim, metin) in enumerate(konusanlar):
        # Karşılıklı konuşma: komşu altyazılarda (önceki, sonraki) kendisinden başka tek bir konuşmacı varsa
        # söz ona söylenmiştir. Üçüncü bir kişi araya giriyorsa dinleyen belirsizdir.
        komsu = {konusanlar[j][0] for j in (i - 1, i + 1)
                 if 0 <= j < len(satirlar) and abs(satirlar[j].bas - satirlar[i].bas) < ARA * 2} - {None, kim}
        karsi = komsu.pop() if kim and len(komsu) == 1 else None
        if temizlik.latin_disi(metin):
            m = next((m for m in JP_SESLENME.finditer(metin) if m.group(2) and m.group(1) != kim), None)
            kime = m.group(1) if m else karsi
            if not kim or not kime:
                continue
            if JP_KIBAR.search(metin):
                ekle(kim, kime, "resmi", "polite verb forms (です/ます)")
            elif JP_DUZ.search(metin) or (m and m.group(2) in ("くん", "君", "ちゃん")):
                ekle(kim, kime, "samimi", "plain forms / くん, ちゃん, お前")
        else:
            m = unvanli.search(metin) if unvanli else None
            if m:                                   # "Master Tolen, ...": kime söylendiği satırın içinde
                ekle(kim or "?", karsi or m.group(1), "resmi",
                     f'addresses them by title and name ("{m.group(0).strip(" ,.!?…")}")')
            elif karsi and yalin.search(metin):     # "Yes, Captain.": dinleyen karşılıklı konuşmadan belli
                ekle(kim, karsi, "resmi", f'addresses them as "{yalin.search(metin).group(0).strip(" ,.!?…")}"')
    return kanit, neden


def kanit_metni(kanit, neden):
    """Modele gidecek kanıt satırları (kaynaktaki yazımlarla)."""
    ing = {"resmi": "formal", "samimi": "informal"}
    satirlar = []
    for (kimden, kime), sayim in sorted(kanit.items(), key=lambda x: -sum(x[1].values())):
        for bicim, n in sayim.most_common():
            aciklama = neden[(kimden, kime, bicim)].most_common(1)[0][0]
            kim = "an unlabelled speaker" if kimden == "?" else kimden
            satirlar.append(f"{kim} -> {kime}: {ing[bicim]} in {n} line{'s' if n > 1 else ''}: {aciklama}")
    return satirlar[:40]


def kesin_ciftler(kanitlar, karakterler):
    """Script'in kendi kararı olan çiftler: en az iki satırda, hep aynı biçimde görülmüş ve iki ucu da
    adlandırılabilen çiftler. Adlar modelin verdiği Latin kısa adlara çevrilir (etiketteki yazım -> ad)."""
    adlar = {}
    for k in karakterler:
        if isinstance(k, dict) and str(k.get("name") or "").strip():
            ad = str(k["name"]).strip()
            adlar[ad.lower()] = ad
            if str(k.get("source") or "").strip():
                adlar[str(k["source"]).strip().lower()] = ad

    def ad(yazim):                 # yalnızca modelin karakter saydığı adlar: "Venn" gibi soyadı çift kurmaz
        return adlar.get(yazim.lower()) or adlar.get(yazim.split("・")[0].lower())
    toplam = {}
    for kanit in kanitlar:
        for (kimden, kime), sayim in kanit.items():
            a, b = (None if kimden == "?" else ad(kimden)), ad(kime)
            if a and b and a != b:
                toplam.setdefault((a, b), Counter()).update(sayim)
    return [{"from": a, "to": b, "form": "formal" if "resmi" in sayim else "informal", "why": "script"}
            for (a, b), sayim in toplam.items() if len(sayim) == 1 and sum(sayim.values()) >= 2]


def oylari_birlestir(yanitlar):
    """Aynı ilişki isteğine verilmiş birkaç yanıtı tek yanıta indirir: karakterler ilk dolu yanıttan, hitap
    çiftlerinde çoğunluğun dediği (çifti yanıtların yarısından azı saydıysa çift alınmaz)."""
    yanitlar = [y for y in yanitlar if isinstance(y, dict)]
    if len(yanitlar) <= 1:
        return yanitlar[0] if yanitlar else {}
    karakterler = next((y["characters"] for y in yanitlar if isinstance(y.get("characters"), list) and y["characters"]), [])
    oylar = {}
    for y in yanitlar:
        gorulen = set()
        for h in y.get("address") or []:
            if isinstance(h, dict) and h.get("form") in ("informal", "formal"):
                cift = (str(h.get("from") or "").strip(), str(h.get("to") or "").strip())
                if all(cift) and cift not in gorulen:
                    gorulen.add(cift)
                    oylar.setdefault(cift, []).append(h)
    hitap = []
    for cift, liste in oylar.items():
        if len(liste) * 2 >= len(yanitlar):
            form = Counter(h["form"] for h in liste).most_common(1)[0][0]
            hitap.append(next(h for h in liste if h["form"] == form))
    return {"characters": karakterler, "address": hitap}


# ---- çeviride sen/siz: biçimi tanıyan desenler (dosya sonu raporu) ve Cevirmen'in hitap tablosu ----
# Samimi ikinci kişi: zamir, -sın eki ("geliyorsun"), geçmiş zaman ("yazdın", "gittin") ve koşul ("istersen").
# -dın/-tın ile biten sık adlar dışarıda tutulur.
SEN = re.compile(r"\b(sen|seni|sana|senin|sende|senden|seninle)\b|"
                 r"(yor|acak|ecek|mış|miş|muş|müş|malı|meli)s[ıiuü]n\b|"
                 r"\b(?!(?:kadın|altın|bütün|satın|tütün|hatun|metin|çetin|yakın)\b)\w{2,}[dt][ıiuü]n\b|"
                 r"\w[aeıiuü]rs[ae]n\b")
SIZ = re.compile(r"\b(siz|sizi|size|sizin|sizde|sizden|sizinle)\b|s[ıiuü]n[ıiuü]z\b")


class HitapDenetimi:
    """Cevirmen'in parçası: hitap tablosu ve kimin kime konuştuğunu kaynağın kesin söylediği satırlar."""

    def _tablo(self):
        """Hitap tablosu; kapalıysa ([hitap] acik / tablo) ya da sözlük yoksa boş."""
        return self.sozluk.hitap if self.hitap_tablo and self.sozluk is not None else {}

    def _kime(self, i):
        """Satırın kimden kime söylendiği script'çe kesin biliniyorsa [(kimden, kime, biçim)]; yoksa [].
        İki durum: altyazıda iki ayrı konuşmacı etiketi var (karşılıklı konuşma) ya da tek konuşmacı
        dinleyene adıyla sesleniyor ("ヨルさん…")."""
        if not self._tablo():
            return []
        konusanlar = [self.sozluk.latin_ad(k) for k in dict.fromkeys(self.konusanlar.get(i) or ()) if k]
        if not konusanlar or None in konusanlar:
            return []
        ciftler = []
        if len(konusanlar) == 2:
            ciftler = [(konusanlar[0], konusanlar[1]), (konusanlar[1], konusanlar[0])]
        elif len(konusanlar) == 1:
            for m in JP_AD_EK.finditer(self.giden.get(i, "")):
                kime = self.sozluk.latin_ad(m.group(1))
                if kime and kime != konusanlar[0]:
                    ciftler = [(konusanlar[0], kime)]
                    break
        return [(a, b, self._tablo()[f"{a} -> {b}"]) for a, b in ciftler if f"{a} -> {b}" in self._tablo()]

    def _ilgili_hitap(self, bas, son):
        """Bu aralıkta en az bir ucunun adı geçen çiftlerin hitap biçimi; modelin gözünün önünde dursun diye isteğe
        eklenir."""
        if not self._tablo():
            return []
        parcalar = [ozet for a, b, ozet in self.sahneler if a <= son + 1 and b >= bas + 1]
        for i in range(max(0, bas - 5), min(len(self.satirlar), son + 6)):
            parcalar.append(self.satirlar[i].metin)
            parcalar.extend(self.konusanlar.get(i, ()))
        metin = " ".join(parcalar)

        def geciyor(ad):
            """Ad tam sözcük (çoğuluyla, harf büyüklüğü fark etmez) ya da büyük harfli daha uzun bir adın başı
            ("Samwise") olarak geçiyor mu? "same" Sam değildir."""
            d = re.escape(ad)
            return bool(re.search(r"(?<!\w)" + d + r"(?:e?s)?(?!\w)", metin, re.I)
                        or (ad[:1].isupper() and re.search(r"(?<!\w)" + d, metin)))
        # Çiftin bir ucu geçse yeter: konuşan çoğu satırda adıyla anılmaz (Nardole'nin "Doctor"a resmi hitabı, adı
        # geçmeyen istekte düşüyor, "tabloda yok = samimi" kuralıyla "sen" oluyordu).
        ilgili = []
        for cift, bicim in self._tablo().items():
            adlar = [a.strip() for a in cift.split("->")]
            if len(adlar) == 2 and all(adlar) and any(geciyor(a) for a in adlar):
                ilgili.append(f"{cift}: {'informal' if bicim == 'samimi' else 'formal'}")
        return ilgili
