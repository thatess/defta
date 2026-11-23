"""Adın yanındaki unvanın ve Japonca saygı ekinin çeviride doğru yazıldığının denetimi."""
import re

from . import temizlik
from .kaynak import _ses_yalin


class UnvanDenetimi:
    """Cevirmen'in parçası: unvan kaydına ve saygı eklerine uyum."""

    def _unvanli(self, kaynak):
        """Kaynak metinde adın yanında geçen kayıtlı unvanlar: (eşleşme, unvan, adın kaynaktaki yazımı)."""
        bulunan = []
        for m in self.unvan_latin.finditer(kaynak) if self.unvan_latin else ():
            # Cümle ortasında büyük harfli bir sözcüğün ardından geliyorsa daha uzun bir adın parçasıdır
            # ("the Dark Lord Sauron"): unvan + ad sayılmaz.
            once = re.search(r"(?:^|([.!?…:\"“(]\s*|[-–—]\s+)|(\s))([A-ZÀ-Þ][\w'’-]*) $", kaynak[:m.start()])
            if not (once and once.group(2)):
                bulunan.append((m, m.group(1), m.group(2)))
        if self.unvan_diger:
            bulunan += [(m, m.group(2), m.group(1)) for m in self.unvan_diger.finditer(kaynak)]
        return bulunan

    def _unvan_kontrol(self, i, kaynak, sonuc):
        """Kaynakta adın yanında geçen unvan çeviride kayıttaki biçimiyle mi ("Mr. Frodo" -> "Bay Frodo")?
        Değilse yerinde düzeltir ([unvan] denetim = "duzelt"); olmuyorsa modele sorulacak nedeni döndürür."""
        terimler = self.sozluk.terimler if self.sozluk is not None else {}
        for m, unvan, ad in self._unvanli(kaynak):
            if temizlik.latin_disi(ad):
                hedef_ad = terimler.get(ad) or (self.sozluk.latin_ad(ad) if self.sozluk is not None else None)
            else:
                hedef_ad = terimler.get(ad) or ad
            duz = " ".join(temizlik.ETIKET.sub("", sonuc[i]).split())
            if not hedef_ad or not re.search(r"(?<![\w-])" + re.escape(hedef_ad) + r"(?![\w-])", duz):
                continue                  # ad çeviride yok (zamirle karşılanmış) ya da karşılığı bilinmiyor
            kalip = self.unvan_kaydi[unvan]
            beklenen = kalip.replace("{ad}", hedef_ad)
            if temizlik.tr_kucuk(beklenen) in temizlik.tr_kucuk(duz):
                continue
            # Tamlama: unvanın önünde onu niteleyen bir ad varsa Türkçede unvan iyelik eki alır, bu da kayıttaki
            # biçimdir. "Princess Sibylla of Jerusalem" -> "Kudüs Prensesi Sibylla"; "監査局のブレナン次官" ->
            # "Denetim Dairesi Müsteşarı Brennan" (yeniden sorulunca model eki atıp "Dairesi Müsteşar Brennan"
            # yazıyordu: 13 denemenin 9'u). Önünde sözcük yoksa ("Müsteşarı Brennan geldi") kabul edilmez.
            if (kalip.endswith(" {ad}")
                    and re.search(r"[^\W\d_]['’\w]*\s+" + re.escape(temizlik.tr_kucuk(kalip[:-len(" {ad}")]))
                                  + r"s?[ıiuü]\s+" + re.escape(temizlik.tr_kucuk(hedef_ad)) + r"(?![\w-])",
                                  temizlik.tr_kucuk(duz))):
                continue
            onarilmis = self._unvani_duzelt(sonuc[i], kalip, hedef_ad) if self.unvan_duzelt else None
            if onarilmis is None:
                return f'the source says "{m.group(0)}": write it as "{beklenen}", in exactly this form'
            self.gunluk.uyari(self.etiket, f"satır {i + 1}: unvan kayda göre düzeltildi: {sonuc[i]!r} -> {onarilmis!r}")
            sonuc[i] = onarilmis
            with self._kilit:
                self.unvan_duzeldi += 1
        return None

    def _unvani_duzelt(self, ceviri, kalip, hedef_ad):
        """Unvanı kayıttaki biçimiyle yazar; yalnızca kesin durumda, yoksa None (satır yeniden sorulur).
        Kesin durum: ad çeviride tam bir kez geçiyor ve (1) hemen önünde ya da ardında bilinen bir unvan sözcüğü
        var ("Usta Tolen", "Tolen Usta" -> "Master Tolen"), ya da (2) ad satır başında ya da noktalamadan sonra
        yalın duruyor ("Tolen, ..." -> "Master Tolen, ...")."""
        yerler = list(re.finditer(r"(?<![\w-])" + re.escape(hedef_ad) + r"(?![\w-])", ceviri))
        if len(yerler) != 1 or not kalip.endswith(" {ad}"):
            return None                   # unvan adın arkasına yazılan dillerde yalnızca yeniden sorulur
        unvan, m = kalip[:-len(" {ad}")], yerler[0]
        bas, son = ceviri[:m.start()], ceviri[m.end():]
        once = re.search(r"([^\W\d_]+\.?)(\s+)$", bas)
        sonra = re.match(r"(\s+)([^\W\d_]+)", son)
        bilinen = lambda sozcuk: temizlik.tr_kucuk(sozcuk.rstrip(".")) in self.unvan_bicimleri
        if once and bilinen(once.group(1)):
            return bas[:once.start(1)] + unvan + once.group(2) + hedef_ad + son
        if sonra and bilinen(sonra.group(2)):
            return bas + unvan + " " + hedef_ad + son[sonra.end():]
        if (not bas.strip() or re.search(r"[.,!?…:;—–\-\"“(>]\s*$", bas)) and not (sonra and sonra.group(2)[:1].isupper()):
            return bas + unvan + " " + hedef_ad + son
        return None

    @staticmethod
    def _adi_yazmis(jp_ad, ceviri):
        """Sözlükte olmayan katakana sözcük kişi adı mı? Çeviri onu büyük harfle, okunuşuyla yazmışsa evet
        ("タクミ君" -> "Takumi'ye": ad; "シッターさん" -> "bakıcı": ad değil)."""
        ses = temizlik.kana_latin(jp_ad)
        if not ses or len(ses) < 3:
            return False
        hedef = _ses_yalin(ses)
        return any(_ses_yalin(soz) == hedef for soz in re.findall(r"\b[A-ZÇĞİÖŞÜ][\w]+", ceviri))

    def _eki_ekle(self, ceviri, jp_ad, ek):
        """Model adı yazıp eki düşürdüyse ("Twilight" <- 黄昏さん) ek yerinde eklenir; yeniden sormaya gerek kalmaz.
        Yalnızca kesin durumda: adın Latin yazımı biliniyor, çeviride tam bir kez geçiyor, eksiz yazılmış ve
        önünde modelin koyduğu bir unvan sözcüğü yok. Aksi hâlde None döner ve satır yeniden istenir."""
        if self.sozluk is None:
            return None
        ad = self.sozluk.terimler.get(jp_ad) or self.sozluk.latin_ad(jp_ad)
        if not ad or " " in ad:
            return None
        yerler = list(re.finditer(r"(?<![\w-])" + re.escape(ad) + r"(?![\w-])", ceviri))
        if len(yerler) != 1:
            return None
        m = yerler[0]
        once = ceviri[:m.start()].rstrip()
        onceki = re.search(r"([^\s.!?…,;:\"“]+)$", once)
        if onceki and onceki.group(1)[:1].isupper() and not re.search(r"[.!?…]\s*$", once[:onceki.start()] or "."):
            return None                 # "Öğretmen Swan": unvanı silmek gerekir, bunu model yapar
        sonraki = re.match(r"\s+([^\s.!?…,;:\"”]+)", ceviri[m.end():])
        if sonraki and sonraki.group(1)[:1].isupper():
            return None                 # "Anya Hanım": unvan addan sonra gelmiş, onu da model siler
        with self._kilit:
            self.ek_onarildi += 1
        return ceviri[:m.end()] + "-" + ek + ceviri[m.end():]
