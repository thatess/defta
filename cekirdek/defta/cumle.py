"""Birkaç altyazıya bölünmüş cümleler: bulunması, çevirinin altyazılara dağıtılması ve dosyanın isteklere bölünmesi."""
import math
import re

from . import temizlik
from .kaynak import TIRELI, _yalin, cumle_sonu_mu, sarki_mi, token_tahmini

SURDURME = re.compile(r"[—―]\s*$")      # "…生物が—": cümle sonraki altyazıda sürüyor
BIRLESTI = "\x00+"       # bu altyazının süresi bir öncekine eklendi (cümle daha az parçaya bölündü)
_BAS_TASMA = re.compile(r"^(\S+?[.!?…]+|\S+ \S+?[.!?…]+)\s+(\S.*)$")
_SON_TASMA = re.compile(r"^(.*\S[.!?…]+)\s+(\S+)$")


def _cumle_sonu_sozcugu(parca):
    """Parçanın son sözcüğü gerçekten cümle bitiriyor mu ("bağlı.")? "Dr." gibi kısaltmalar bitirmez."""
    oz = re.sub(r"[.!?…]+$", "", parca.split()[-1])
    return len(oz) >= 2 and not (oz[:1].isupper() and len(oz) <= 4)


def tasani_duzelt(parcalar, sinirlar=None):
    """Kesme yeri bir cümle sonunun bir-iki sözcük yanına düşmüşse cümle sonuna çekilir:
    "…geleceği | bağlı. Daha ciddi olun"  ->  "…geleceği bağlı. | Daha ciddi olun"
    "…sızdır. Onlar | da kaçınır"          ->  "…sızdır. | Onlar da kaçınır"
    Yalnızca noktalamaya bakar; her dilde çalışır. "sinirlar" verilirse (parça başına en çok karakter)
    okuma hızını aşacak kaydırma yapılmaz."""
    p = list(parcalar)
    for k in range(len(p) - 1):
        a, b = p[k], p[k + 1]
        if not a or not b:
            continue
        m = _BAS_TASMA.match(b)
        if m and a[-1] not in ".!?…" and _cumle_sonu_sozcugu(m.group(1)):
            if not sinirlar or len(a) + 1 + len(m.group(1)) <= sinirlar[k]:
                p[k], p[k + 1] = a + " " + m.group(1), m.group(2)
            continue
        m = _SON_TASMA.match(a)
        if m and _cumle_sonu_sozcugu(m.group(1)) and (
                not sinirlar or len(m.group(2)) + 1 + len(b) <= sinirlar[k + 1]):
            p[k], p[k + 1] = m.group(1), m.group(2) + " " + b
    return p


def _yerlesimler(n, m):
    """n altyazıyı sırayı bozmadan m parçaya paylaştırmanın bütün yolları: her parçanın kapladığı altyazı sayısı."""
    if m == 1:
        return [[n]]
    return [[ilk] + kalan for ilk in range(1, n - m + 2) for kalan in _yerlesimler(n - ilk, m - 1)]


_ARA_NOKTA = re.compile(r"(?<=[,;:])\s+|(?<=\.\.\.)\s+|(?<=…)\s+"
                        r"|(?<=[a-zçğıöşü][a-zçğıöşü][a-zçğıöşü][.!?])\s+")
_UC_NOKTA_IZI = re.compile(r"(?:\.{3}|…)\s+(?:\.{3}|…)\s*")      # "çalıp... ...durdu": kesilmeyen yerde iz kalmasın


def kesme_adaylari(isaretli):
    """Cümlenin kesilebileceği yerler. (atomlar, cezalar) döndürür: cezalar[k], k. atomdan sonra kesmenin cezası.
    Modelin işaretlediği yer cezasızdır; parçanın içindeki noktalama (virgül, üç nokta, cümle sonu) da adaydır
    ama küçük bir cezayla: model orayı duraklama yeri saymamış olabilir."""
    atomlar, cezalar = [], []
    for parca in isaretli:
        alt = [x.strip() for x in (_ARA_NOKTA.split(parca) if "⟦" not in parca else [parca]) if x.strip()]
        for k, x in enumerate(alt):
            atomlar.append(x)
            cezalar.append(0.1 if k < len(alt) - 1 else 0.0)
    return atomlar, cezalar[:-1]


def yerlestir(atomlar, cezalar, sureler, hiz, en_cok_karakter):
    """Kesme adaylarından hangilerinin kullanılacağını seçer.

    Atomlar sırayla m öbeğe (m <= altyazı sayısı), öbekler de sırayla altyazılara paylaştırılır; bir öbek birden
    çok altyazıyı kaplayabilir (süreleri birleşir, başlangıç zamanı değişmez). Bütün paylaşımlar denenir, en düşük
    maliyetli seçilir:
      - her öbeğin metin payı ile süre payı arasındaki fark (denge),
      - okuma hızını aşan öbek, tek sözcüklük öbek, birleştirilen her altyazı ve noktalamadan kesme için ceza.
    İki satıra sığmayan ya da 7 saniyeden uzun ekranda kalacak öbek kabul edilmez.
    (öbekler, her öbeğin kapladığı altyazı sayısı) döndürür; uygun paylaşım yoksa None."""
    n, tum = len(sureler), sum(sureler)
    toplam = sum(len(a) for a in atomlar) or 1
    cok_sozcuk = sum(len(a.split()) for a in atomlar) >= 6
    en_iyi = None
    for m in range(1, min(n, len(atomlar)) + 1):
        for atom_payi in _yerlesimler(len(atomlar), m):
            obekler, sinir, ceza = [], 0, 0.4 * (n - m)
            for adet in atom_payi:
                obekler.append(_UC_NOKTA_IZI.sub(" ", " ".join(atomlar[sinir:sinir + adet])))
                sinir += adet
                if sinir < len(atomlar):
                    ceza += cezalar[sinir - 1]
            if any(len(o) > en_cok_karakter for o in obekler):
                continue
            if cok_sozcuk and m > 1:
                ceza += 0.5 * sum(1 for o in obekler if len(o.split()) == 1)
            for kaplama in _yerlesimler(n, m):
                maliyet, sinir, olur = ceza, 0, True
                for obek, adet in zip(obekler, kaplama):
                    sure = sum(sureler[sinir:sinir + adet])
                    sinir += adet
                    if adet > 1 and sure > 7000:
                        olur = False
                        break
                    maliyet += abs(len(obek) / toplam - sure / tum)
                    asim = len(obek) / (sure / 1000) / hiz
                    if asim > 1:
                        maliyet += 0.6 * asim
                if olur and (en_iyi is None or maliyet < en_iyi[0]):
                    en_iyi = (maliyet, obekler, kaplama)
    return (en_iyi[1], en_iyi[2]) if en_iyi else None


BAGLAC = {"ve", "ama", "fakat", "çünkü", "ki", "veya", "ancak", "yoksa", "sonra", "diye", "hem", "ya"}
UC_NOKTA_SON = re.compile(r"(?:\.\.\.|…)\s*$")
UC_NOKTA_BAS = re.compile(r"^[\s\"“'‘]*(?:\.\.\.|…)")


def uc_nokta_surer_mi(metin, sonraki):
    """"...and nine rings..." / "...were gifted to men": üç noktayla biten altyazıyı üç noktayla ya da küçük
    harfle başlayan altyazı izliyorsa cümle sürüyordur (bazı altyazılar bölünmüş cümleyi böyle gösterir)."""
    duz, son = temizlik.ETIKET.sub("", metin).strip(), temizlik.ETIKET.sub("", sonraki).strip()
    if not UC_NOKTA_SON.search(duz) or not son:
        return False
    return bool(UC_NOKTA_BAS.match(son)) or son.lstrip("\"“'‘ ")[:1].islower()


def cumle_suruyor_mu(satirlar, i):
    """i. altyazıdaki cümle bir sonrakinde sürüyor mu? Yalnızca noktalaması güvenilir (Latin harfli) kaynakta."""
    if i + 1 >= len(satirlar):
        return False
    metin = temizlik.ETIKET.sub("", satirlar[i].metin).strip()
    if not metin or TIRELI.match(satirlar[i].metin) or sarki_mi(metin):
        return False
    sonraki = satirlar[i + 1].metin
    if sarki_mi(sonraki):
        return False            # şarkı dizesine cümle akmaz
    if TIRELI.match(sonraki) or "\n-" in sonraki or "\n–" in sonraki:
        return False            # sonraki altyazıda iki kişi konuşuyor: bu cümleyle birleştirilmez
    yakin = satirlar[i + 1].bas - satirlar[i].bit <= 1000
    if uc_nokta_surer_mi(metin, sonraki):
        return yakin
    if temizlik.latin_disi(metin):
        # Noktalaması güvenilmez yazılarda (Japonca...) tek kesin işaret: altyazının sonundaki sürdürme çizgisi
        return yakin and bool(SURDURME.search(metin))
    return not cumle_sonu_mu(metin) and yakin


def dize_mi(satirlar, grup):
    """Şarkı ya da şiir dizeleri mi? Noktasız biten ama her biri büyük harfle başlayan üç ve daha çok altyazı
    tek cümle değil, alt alta dizelerdir (♪ işareti olmasa da): birleştirilip yeniden bölünmez."""
    if len(grup) < 3:
        return False
    return all(temizlik.ETIKET.sub("", satirlar[j].metin).lstrip(" \"“'‘")[:1].isupper() for j in grup[1:])


def cumle_gruplari(satirlar, uygun):
    """Art arda altyazılara bölünmüş cümlelerin index grupları (en çok 5 altyazı)."""
    gruplar, i = [], 0
    while i < len(satirlar):
        grup = [i]
        while len(grup) < 5 and grup[-1] in uygun and grup[-1] + 1 in uygun and cumle_suruyor_mu(satirlar, grup[-1]):
            grup.append(grup[-1] + 1)
        if len(grup) > 1 and not dize_mi(satirlar, grup):
            gruplar.append(grup)
        i = grup[-1] + 1
    return gruplar


def _bol(kelimeler, paylar):
    """Kelimeleri verilen paylara (toplamı 1) yakın uzunlukta parçalara böler; virgül ve bağlaçta kesmeyi yeğler."""
    toplam = len(" ".join(kelimeler))
    parcalar, bas, biriken = [], 0, 0.0
    for sira, pay in enumerate(paylar[:-1]):
        biriken += pay
        hedef = toplam * biriken
        kalan_parca = len(paylar) - sira - 1
        en_iyi, en_maliyet = bas + 1, None
        for j in range(bas + 1, len(kelimeler) - kalan_parca + 1):
            konum = len(" ".join(kelimeler[:j]))
            maliyet = abs(konum - hedef)
            if kelimeler[j - 1][-1] in ",;:":
                maliyet -= toplam * 0.12
            elif kelimeler[j].lower() in BAGLAC:
                maliyet -= toplam * 0.08
            if j - bas == 1 or (kalan_parca == 1 and len(kelimeler) - j == 1):
                maliyet += toplam * 0.2    # tek sözcüklük altyazı bırakma
            if j - 1 > 0 and kelimeler[j - 1][:1].isupper() and kelimeler[j][:1].isupper():
                maliyet += toplam * 0.15   # iki büyük harfli sözcük: büyük olasılıkla tek bir ad ("Kül Vadisi")
            if en_maliyet is None or maliyet < en_maliyet:
                en_iyi, en_maliyet = j, maliyet
        parcalar.append(" ".join(kelimeler[bas:en_iyi]))
        bas = en_iyi
    parcalar.append(" ".join(kelimeler[bas:]))
    return parcalar


def dengele(satirlar, sonuc, cevrilen, gunluk=None, etiket="", konusanlar=None):
    """Bölünmüş cümlede bir altyazı neredeyse boş, komşusu doluysa cümleyi sürelerle orantılı yeniden böler.
    konusanlar: index -> satırın konuşmacıları; farklı kişilerin satırları birbirine karıştırılmaz."""
    i, n, duzeltilen = 0, len(satirlar), 0
    while i < n - 1:
        grup = [i]
        while (len(grup) < 4 and grup[-1] + 1 < n and grup[-1] in cevrilen and grup[-1] + 1 in cevrilen
               and cumle_suruyor_mu(satirlar, grup[-1])
               and not cumle_sonu_mu(sonuc[grup[-1]])):      # çeviri de cümleyi bitirmemiş olmalı
            grup.append(grup[-1] + 1)
        i = grup[-1] + 1
        if len(grup) < 2 or dize_mi(satirlar, grup):
            continue
        if konusanlar and len({tuple(konusanlar.get(j, ())) for j in grup}) > 1:
            continue
        if any(sonuc[j] == BIRLESTI for j in grup):
            continue
        metinler = [sonuc[j].replace("\n", " ").strip() for j in grup]
        if any(TIRELI.match(m) or sarki_mi(m) for m in metinler):
            continue
        italik = all(m.startswith("<i>") and m.endswith("</i>") and m.count("<i>") == 1 for m in metinler)
        if italik:
            metinler = [m[3:-4].strip() for m in metinler]
        elif any("<i>" in m or "</i>" in m for m in metinler):
            continue
        uzunluklar = [len(m) for m in metinler]
        toplam = sum(uzunluklar)
        sureler = [max(satirlar[j].bit - satirlar[j].bas, 300) for j in grup]
        paylar = [s / sum(sureler) for s in sureler]
        oranlar = [u / max(toplam * p, 1) for u, p in zip(uzunluklar, paylar)]
        en_bos = oranlar.index(min(oranlar))
        sozcuk = len(metinler[en_bos].split())
        bos = (sozcuk <= 1 and min(oranlar) < 0.5) or (sozcuk <= 2 and min(oranlar) < 0.35)
        if toplam < 30 or not bos:
            continue
        kelimeler = " ".join(metinler).split()
        if len(kelimeler) < len(grup) * 2:
            continue
        yeni = _bol(kelimeler, paylar)
        if any(not y for y in yeni):
            continue
        for j, y in zip(grup, yeni):
            sonuc[j] = "<i>" + y + "</i>" if italik else y
        duzeltilen += 1
        if gunluk is not None:
            gunluk.uyari(etiket, f"satır {grup[0] + 1}-{grup[-1] + 1}: dengesiz dağıtım yeniden bölündü: "
                                 f"{' | '.join(metinler)}  ->  {' | '.join(yeni)}")
    return duzeltilen


def parcalara_bol(satirlar, parca):
    """Satırları (başlangıç, bitiş) aralıklarına böler. Cümle ortasından kesmemeye çalışır.
    parca: [parca] ayarları; altyazi_sayisi 0 ise parça büyüklüğü token tahminiyle kendiliğinden seçilir."""
    auto = not parca["altyazi_sayisi"]
    hedef = parca["altyazi_sayisi"]
    if auto:
        # Parçalar aynı anda gider, süreyi en uzun parça belirler: dosya en az 16 eşit parçaya bölünür ve
        # hiçbir parça ortalama 35 altyazıyı geçmez (uzun filmde parça büyümez, sayısı artar).
        toplam_token = sum(token_tahmini(s.metin) for s in satirlar)
        adet = max(16, math.ceil(len(satirlar) / 35))
        hedef = max(250, toplam_token / adet)
    parcalar, bas, toplam = [], 0, 0
    for i, s in enumerate(satirlar[:-1]):
        toplam += token_tahmini(s.metin) if auto else 1
        bitti = cumle_sonu_mu(s.metin)
        bosluk = satirlar[i + 1].bas - s.bit
        sahne = bitti and bosluk >= parca["sahne_boslugu"] * 1000
        if (toplam >= hedef and bitti) or (toplam >= hedef * 0.7 and sahne) or toplam >= hedef * 1.3:
            parcalar.append((bas, i + 1))
            bas, toplam = i + 1, 0
    if satirlar:
        parcalar.append((bas, len(satirlar)))
    return parcalar


class CumleDagitimi:
    """Cevirmen'in parçası: birleşik cümlenin çevirisini altyazılarına böler."""

    def _coz(self, grup):
        """Birleşik cümle bu istekte kullanılamıyorsa (parça sınırı, önbellek) parçaları ayrı ayrı çevrilir."""
        bas = grup[0]
        self.birlesik.pop(bas, None)
        self.korunan_birlesik.pop(bas, None)
        for j in grup[1:]:
            self.uye.pop(j, None)

    def _dagit(self, bas, metin):
        """Birleşik cümlenin çevirisini altyazılara böler. Model duraklama yerlerini işaretler; hangilerinde
        kesileceğini script seçer (yerlestir). Uygun yerleşim yoksa cümle sürelerle orantılı bölünür.
        index -> metin döndürür."""
        b = self.birlesik[bas]
        grup = b["grup"]
        metin = temizlik.ETIKET.sub("", metin).strip()
        if metin == b["metin"]:                # çevrilemedi, kaynak bırakıldı: altyazılar olduğu gibi kalır
            return {j: self.giden[j] for j in grup}
        if "|" not in metin and metin.count("\n") == len(grup) - 1:
            metin = metin.replace("\n", " | ")           # model duraklama yerini satır sonuyla göstermiş
        metin = metin.replace("\n", " ")
        sureler = [max(self.satirlar[j].bit - self.satirlar[j].bas, 300) for j in grup]
        isaretli = [p_.strip() for p_ in metin.split("|") if p_.strip()]
        # Model aynı metni iki parçaya da yazdıysa biri atılır (aynı yazı iki altyazıda çıkmasın).
        isaretli = [p_ for k, p_ in enumerate(isaretli) if k == 0 or _yalin(p_) != _yalin(isaretli[k - 1])]
        hiz = self.ayar["cikti"]["saniye_basina_karakter"]
        secim = yerlestir(*kesme_adaylari(isaretli), sureler, hiz, 2 * self.ayar["cikti"]["satir_karakter"]) \
            if isaretli else None
        kaplama = None
        if secim:
            parcalar = secim[0]
            kaplama = secim[1] if len(parcalar) < len(grup) else None
        else:
            # Hiçbir aday yerleşmedi (duraklama yeri olmayan, iki satıra sığmayan cümle): sözcük sınırından bölünür.
            kelimeler = re.findall(r"⟦[^⟧]*⟧\S*|\S+", _UC_NOKTA_IZI.sub(" ", " ".join(isaretli)))
            if len(kelimeler) < len(grup):      # cümle parça sayısından kısa: sığdığı kadar altyazıya yazılır
                parcalar = kelimeler + [""] * (len(grup) - len(kelimeler))
            else:
                parcalar = tasani_duzelt(_bol(kelimeler, [x / sum(sureler) for x in sureler]),
                                         [sure / 1000 * hiz for sure in sureler])
            self.gunluk.uyari(self.etiket, f"satır {grup[0] + 1}-{grup[-1] + 1}: duraklama yerleri altyazılara "
                                           f"yerleşmedi, script böldü: {' | '.join(isaretli)}  ->  {' | '.join(parcalar)}")
        with self._kilit:
            if secim:
                self.model_boldu += 1
                self.sure_birlesti += bool(kaplama)
            else:
                self.script_boldu += 1
        if kaplama:
            # Daha az parça: her parça kapladığı altyazıların ilkine yazılır, ötekilerin süresi ona eklenir.
            yayilmis = []
            for p_, adet in zip(parcalar, kaplama):
                yayilmis += [p_] + [None] * (adet - 1)
            self.gunluk.uyari(self.etiket, f"satır {grup[0] + 1}-{grup[-1] + 1}: cümle {len(parcalar)} parçaya bölündü, "
                                           f"altyazı süreleri birleştirildi: {' | '.join(parcalar)}")
        else:
            yayilmis = list(parcalar)
        dolu = [k for k, y in enumerate(yayilmis) if y is not None]
        parcalar = [yayilmis[k] for k in dolu]
        # Cümle sürerken büyük harfle başlayan parça (özel ad değilse) küçük harfle yazılır.
        turkce = self.ayar["ceviri"]["hedef_dil"].lower().startswith("tr")
        for k in range(1, len(parcalar) if turkce and not temizlik.latin_disi(b["metin"]) else 0):
            once, bu = parcalar[k - 1].rstrip(), parcalar[k]
            # Üç nokta cümleyi bitirmez: "sezdi... | Vaktinin geldiğini" -> "vaktinin geldiğini"
            if not bu or not once or bu.startswith("⟦") or not bu[0].isupper():
                continue
            if once[-1] in ".!?:\"”" and not once.endswith("..."):
                continue
            ilk = re.split(r"['’\s,.;:!?]", bu, maxsplit=1)[0]
            if len(ilk) > 1 and ilk.isupper():
                continue                                     # kısaltma
            if ilk in self.ozel_adlar or ilk[:4] in {x[:4] for x in self.ozel_adlar if len(x) >= 4 and len(ilk) >= 4}:
                continue
            parcalar[k] = ("ı" if bu[0] == "I" else "i" if bu[0] == "İ" else bu[0].lower()) + bu[1:]
        cikti = {j: BIRLESTI for j in grup}
        for k, y in zip(dolu, parcalar):
            cikti[grup[k]] = "<i>" + y + "</i>" if b["italik"] and y else y
        return cikti
