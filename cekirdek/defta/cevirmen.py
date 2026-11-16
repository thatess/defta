"""Çeviri çekirdeği: hazırlık, paralel istek, satır kontrolü, yeniden deneme, kaldığı yerden devam."""
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import temizlik
from .ayar import acik, dil_adi, hedef_kodu, unvan_tablosu
from .cumle import CumleDagitimi, cumle_gruplari, cumle_suruyor_mu, dengele, parcalara_bol
from .denetim import Denetimler
from .hitap import SEN, SIZ, HitapDenetimi
from .kaynak import (EK, JP_AD_EK, JP_EK_DESEN, JP_ETIKET, KATAKANA, OKUNUSLU_AD_EK, TIRELI, UNVAN_EKI, YALNIZ_UNVAN, _ek_latin,
                     _terim_bicimleri, anlami_ayir, bicim_gecer, not_ve_konusmaci_ayir, romaji_mi, sarki_mi,
                     ses_satiri, ses_yaz)
from .model import Iptal, ModelHatasi, Ret, json_coz
from .sozluk import ekli_ad_mi, isaretle
from .unvan import UnvanDenetimi

ARDISIK_BOZUK = 10          # bu kadar üst üste kullanılamayan yanıtta çalışma durur (bozuk ayar para yakmasın)
# Kullanılabilir yanıt bir kez geldiyse model ve adres doğrudur: hep reddedilen tek satır (parça ikiye bölünerek 15-20
# kez üst üste sorulur) bütün dosyayı durdurmasın; o satır orijinal bırakılır. Sınır yine de vardır.
ARDISIK_BOZUK_SONRA = 50
SORUN = {
    "bos": "empty text: every id needs a translation",
    "latin": "not translated or still in a non-Latin script: translate it into the target language, in Latin letters",
    "isaret": "protected ⟦ ⟧ text was changed or dropped: copy it exactly",
    "italik": "unbalanced <i> tags",
    "tire": "the number of rows starting with '- ' differs from the source",
    "ek": "a Japanese honorific was dropped or replaced: keep the one the source has, attached to the name "
          "(さん = -san, ちゃん = -chan, くん = -kun, 様 = -sama, 先輩 = -senpai, 先生 = Sensei), and delete "
          "the title word you wrote in its place: the honorific alone, never both (\"Iruka-sensei\"). If the name stands "
          "for \"you\", keep it as a form of address and the sentence in the second person",
    "terim": "a glossary term was not used: write the glossary form",
    "romaji": "this song line must NOT be translated: write how the original SOUNDS in Latin letters (plain Hepburn), "
              "syllable for syllable, nothing else",
    "eksik": "this id was missing from your answer: translate it",
    "okunus": "this song line needs both: its translation in \"text\" and, in \"reading\", how the original SOUNDS in "
              "Latin letters (plain Hepburn), syllable for syllable",
}


class Cevirmen(CumleDagitimi, Denetimler, HitapDenetimi, UnvanDenetimi):
    def __init__(self, ayar, model, sistem, gunluk, sozluk=None):
        self.ayar, self.model, self.sistem, self.gunluk = ayar, model, sistem, gunluk
        # Sen/siz: her parça ayrı anahtar ([hitap]); kapalı olan istekte de denetimde de iz bırakmaz.
        self.hitap_tablo = acik(ayar, "hitap", "tablo")
        # Adın yanındaki unvanlar: unvan -> kalıp ("Bay {ad}"); sözlüğün görünümünden gelir (tablo ya da model kararı).
        self.unvan_kaydi = dict(sozluk.unvan_kaydi) if sozluk is not None else {}
        self.unvan_latin = self.unvan_diger = None       # kaynakta arama desenleri (_hazirla kurar)
        self.unvan_denetle = ayar["unvan"]["acik"] and ayar["unvan"]["denetim"] != "kapali"
        self.unvan_duzelt = ayar["unvan"]["acik"] and ayar["unvan"]["denetim"] == "duzelt"
        self.unvan_bicimleri, self.unvan_duzeldi = set(), 0
        self.birlesik, self.uye, self.korunan_birlesik = {}, {}, {}
        self.sure_birlesti = 0
        self.ek_onarildi = 0
        self.resmi_beklenen = {}   # index -> "A -> B": tabloya göre resmi hitap edilmesi kesin olan satırlar
        self.giden, self.jp_kanji_adlar = {}, set()
        self.model_boldu = self.script_boldu = 0   # bölünmüş cümlede kesme yerini kimin seçtiği
        self.ozel_adlar = set()
        self.sozluk = sozluk
        self.modlar = ayar["modlar"]
        self.latin = ayar["ceviri"]["cikti_alfabesi"] == "latin"
        self.ek_kontrol = self.modlar["anime_ekleri"] != "ceviri"
        self.tam_koruma = (self.modlar["kurgu_dili"] == "orijinal"
                           and self.modlar["anime_ekleri"] == "orijinal")
        self.jp_adlar = set()
        self.terimler = []   # (kaynak deseni ya da metni, hedefin kökü): çeviri sonrası tutarlılık kontrolü
        # Terim aranırken küçük harf: I -> ı yalnız Türkçe ve Azerbaycancada. Öteki dillerde büyük harfle yazılan
        # satırdaki ("TITAN") ya da cümle başındaki ("Imps") terim çeviride yok sayılır, satır yeniden sorulurdu.
        self.kucuk = temizlik.tr_kucuk if hedef_kodu(ayar) in ("tr", "az") else str.lower
        self.terim_maske = {}
        self.kacan_terim = {}   # index -> (kaynak, hedef): kontrolü geçmeyen satırda eksik bulunan sözlük terimi
        self.sahneler = []
        self.notlar, self.konusanlar, self.romanize = {}, {}, set()
        self.ozgun, self.okunus_iste, self.okunuslar = {}, set(), {}
        self.ust_uste_bozuk = 0      # üst üste kullanılamayan yanıt sayısı (_kullanilamaz)
        self.yanit_geldi = False     # bu dosyada kullanılabilir yanıt alındı mı (bu ya da önceki çalıştırmada)
        self.yarim = {}              # düzelmeyecek hatayla kesilen parçaların o ana kadar kabul edilen satırları
        self._kilit = threading.Lock()

    # ---- hazırlık: işaretler ve gönderilmeyecek satırlar ----------------------
    def _hazirla(self, satirlar, etiket):
        self.satirlar, self.etiket = satirlar, etiket
        self.giden = {}      # index -> modele gidecek (işaretli) metin
        self.korunan = {}    # index -> korunacak parçalar
        self.sabit = {}      # index -> çeviriye gitmeden yazılacak metin
        self.korunan_birlesik = {}
        self.sahneler = (self.sozluk.sahneler.get(etiket) or []) if self.sozluk is not None else []
        self._adlari_ve_terimleri_kur(satirlar)
        self._unvan_desenlerini_kur()
        self._satirlari_ayir(satirlar, etiket)
        self._ozel_adlari_topla(satirlar)
        self._cumleleri_birlestir(satirlar)

    def _adlari_ve_terimleri_kur(self, satirlar):
        """Japonca kişi adları (konuşmacı etiketlerinden ve sözlükten; ek kontrolü yalnızca bunlara uygulanır) ve
        çeviriden sonra satır satır aranacak sözlük terimleri."""
        self.jp_adlar = set()
        for s in satirlar:
            for m in JP_ETIKET.finditer(s.metin):
                self.jp_adlar.update(KATAKANA.findall(m.group(1)))
        self.terimler = []
        self.terim_maske = {}         # kaynak terim -> onu içeren uzun yazımlar (o satırlarda kısa terim aranmaz)
        self.jp_kanji_adlar = set()   # sözlükte ad olarak geçen, katakana olmayan kaynaklar (黄昏 -> Twilight, しんのすけ)
        if self.sozluk is not None:
            # "スワン先生" gibi ekiyle kaydedilmiş adlar da addır: eki atılıp ad kümesine eklenir.
            for ad in self.sozluk.degismez + list(self.sozluk.terimler):
                m = JP_AD_EK.fullmatch(ad.strip())
                if m:
                    # Yalnızca kişiye verilen unvanlar: "ワンちゃん" (köpecik) gibi sıradan sözcükler ad sayılmaz.
                    if m.group(2) in UNVAN_EKI:
                        self.jp_adlar.add(m.group(1))
                elif KATAKANA.fullmatch(ad.strip()) and ad.strip() in self.sozluk.karakter_adlari():
                    self.jp_adlar.add(ad.strip())
            for kaynak, hedef in self.sozluk.terimler.items():
                if hedef[:1].isupper():      # karşılığı küçük harfli terim ("ワン公" -> "it") kişi adı değildir
                    self.jp_adlar.update(KATAKANA.findall(kaynak))
                cekirdek = re.sub(r"\([^)]*\)|（[^）]*）", "", kaynak).strip()
                if (temizlik.latin_disi(cekirdek) and not KATAKANA.fullmatch(cekirdek) and 1 < len(cekirdek) <= 6
                        and hedef[:1].isupper()):
                    self.jp_kanji_adlar.add(cekirdek)
                # Sözlüğün karşılık verdiği her terim denetlenir. Küçük harfle yazılan dünya sözcükleri de (ranger,
                # wraith): onlar cümle başında büyük harfle ve çoğul da geçer, hepsi aynı karşılığı almalı.
                kucuk_harfli = kaynak.isascii() and kaynak == kaynak.lower()
                # Sıradan anlamı da olan sözcük ("黄昏": kod adı / alacakaranlık) satır satır denetlenmez: hangi
                # anlamda geçtiğini script bilemez, karşılığı dayatırsa sıradan kullanımı bozar.
                if len(hedef) < 3 or (kucuk_harfli and len(kaynak) < 4) or kaynak in self.sozluk.siradanlar:
                    continue
                kok = self.kucuk(hedef[:-1] if len(hedef) > 4 else hedef)
                if kaynak.isascii():
                    cogul = r"(?:e?s)?" if kaynak[-1:].isalpha() else ""
                    self.terimler.append((re.compile(r"(?<!\w)" + re.escape(kaynak) + cogul + r"(?!\w)",
                                                     re.I if kucuk_harfli else 0), kok, (kaynak, hedef)))
                else:
                    self.terimler.append((tuple(_terim_bicimleri(kaynak)), kok, (kaynak, hedef)))
            # Kısa terim uzun bir yazımın içinde geçiyorsa (モジャ / モジャモジャ伯爵, モジャジャジャ gülüşü) o satırda
            # kısa terim aranmaz: uzun yazım ya kendi karşılığıyla denetlenir ya da sıradan sözcüktür. Yoksa iki
            # karşılık birden istenir ve model ikisini karıştırır ("Kont Kıllı").
            uzunlar = [k for k, g in self.sozluk.girdiler.items()
                       if not k.isascii() and (k in self.sozluk.terimler or g.get("tur") == "common")]
            for desen, _kok, (kaynak, _h) in self.terimler:
                if isinstance(desen, tuple):
                    # Terimin hitap ekli hâli (兄 / 兄さん, お兄ちゃん) ayrı bir sözcük değildir: maskelemez, o satırda
                    # da aynı karşılık aranır (yoksa aynı bölümde "abi" ile "ağabey" karışıyordu).
                    icerenler = {u for u in uzunlar for d in desen if u != kaynak and len(u) > len(d) and d in u
                                 and not (ekli_ad_mi(u) and u.lstrip("おご").startswith(d))}
                    if icerenler:
                        self.terim_maske[kaynak] = sorted(icerenler, key=len, reverse=True)
            for yazim, girdi in self.sozluk.girdiler.items():
                if (girdi.get("tur") == "people" and girdi.get("hedef") == yazim and yazim.isascii() and len(yazim) >= 4
                        and yazim in self.sozluk.degismez):
                    self.terimler.append((re.compile(r"(?<!\w)" + re.escape(yazim), re.I), self.kucuk(yazim[:-1]),
                                          (yazim, yazim)))

    def _unvan_desenlerini_kur(self):
        """Kaynakta "unvan + ad" arayan desenler ve çeviride unvan yerinde görülebilecek sözcükler."""
        if self.unvan_kaydi:
            sirali = lambda liste: "|".join(sorted(map(re.escape, liste), key=len, reverse=True))
            latin = [u for u in self.unvan_kaydi if not temizlik.latin_disi(u)]
            diger = [u for u in self.unvan_kaydi if temizlik.latin_disi(u)]
            if latin:        # "Mr. Frodo", "Captain Venn's"
                self.unvan_latin = re.compile(r"(?<![\w'’-])(" + sirali(latin) + r")\.? ([A-ZÀ-Þ][\w-]*[a-zà-ÿ])")
            if diger:        # "リヴァイ隊長": katakana ad ya da sözlükteki kanji ad + unvan
                adlar = sirali(self.jp_kanji_adlar) + "|" if self.jp_kanji_adlar else ""
                self.unvan_diger = re.compile("(" + adlar + r"[\u30A1-\u30FA\u30FC]{2,})(" + sirali(diger) + ")")
            # Çeviride unvan yerinde görülebilecek sözcükler: script yalnızca bunları kayıttakiyle değiştirir.
            girdiler = self.sozluk.girdiler if self.sozluk is not None else {}
            bicimler = [k.replace("{ad}", "") for k in list(self.unvan_kaydi.values())
                        + list(unvan_tablosu(self.ayar).values())] + list(self.unvan_kaydi)
            bicimler += [g.get("hedef") or "" for g in girdiler.values() if g.get("tur") == "title"]
            self.unvan_bicimleri = {temizlik.tr_kucuk(b.strip().rstrip(".")) for b in bicimler if b.strip()}

    def _satirlari_ayir(self, satirlar, etiket):
        """Her altyazı için: modele ne gidecek (giden), ne korunacak (korunan), ne çevrilmeden yazılacak (sabit)."""
        self.notlar, self.konusanlar = {}, {}
        self.anlamli = {}    # index -> (kurgu dilindeki söz, italik mi): anlamı kaynakta verilmiş satırlar
        self.romanize = set()   # çevrilmeyecek, yalnızca Latin harfleriyle yazılacak şarkı satırları
        # Özgün söz + çeviri: özgün sözü script yazar (model yalnız çevirir). Latin harfli söz kaynaktan, kana söz
        # script'in okunuşuyla; kanjili sözün okunuşunu model "reading" alanında verir (okunus_iste -> okunuslar).
        self.ozgun, self.okunus_iste, self.okunuslar = {}, set(), {}
        koseli_ayir = self.modlar["koseli_not"] == "sil"
        parantez_ayir = self.modlar["parantez_not"] == "sil"
        etiket_ayir = self.modlar["isim_etiketi"] == "sil"
        sesler = self.sozluk.sesler(self.ayar) if self.sozluk is not None else {}
        for i, s in enumerate(satirlar):
            if not s.metin.strip():
                continue
            ses = ses_satiri(s.metin) if sesler else None
            if ses and ses[0] in sesler:      # sözü yalnızca bir ses sözcüğü (ボフ ボフ): çevrilmez, script yazar
                self.sabit[i] = ses_yaz(s.metin, ses[0], sesler[ses[0]].lower())
                continue
            if s.ekran and self.modlar["ekran_yazisi"] == "birak":
                self.sabit[i] = s.metin       # biçimin ekran yazısı dediği satır (ASS'te konumlanmış): aynen kalır
                continue
            metin = s.metin
            # Kurgu dilindeki söz + kaynağın parantezle verdiği anlam: modele yalnızca anlam gider, altyazıyı
            # seçilen moda göre script kurar. Böylece parantez "ses notu" sanılıp silinmez ve üç mod da
            # her satırda aynı biçimi verir.
            kurgu = ((self.sozluk.kurgu.get(etiket) or {}).get(str(i + 1), []) if self.sozluk is not None else [])
            ayrik = anlami_ayir(metin, kurgu)
            if ayrik:
                soz, anlam, italik = ayrik
                roma = self.sozluk.latin.get(soz)
                soz = roma if self.latin and roma else soz
                if self.modlar["kurgu_dili"] == "orijinal" and self.modlar["kurgu_anlami"] == "sil":
                    # Yalnızca söz kalır; kaynağın verdiği anlam yazılmaz, modele de gitmez. ("cevir" iken anlam
                    # çevrilip sözün altına parantezle yazılır: yoksa Elfçe konuşmalar anlaşılmaz kalır.)
                    self.sabit[i] = "<i>" + soz + "</i>" if italik else soz
                    continue
                self.anlamli[i] = (soz, italik)
                self.giden[i] = "<i>" + anlam + "</i>" if italik else anlam
                continue
            if kurgu:
                # Altyazının tamamı kurgu dilinde ve anlamı kendi içinde verilmemiş: her modda aynen kalır.
                # Anlam çoğu kez bir sonraki altyazıda zaten söylenir; çevrilirse aynı cümle iki kez çıkar.
                artan, yalin = temizlik.ETIKET.sub("", metin), metin
                for parca in kurgu:
                    artan = artan.replace(parca, "")
                    roma = self.sozluk.latin.get(parca)
                    if self.latin and roma:
                        yalin = yalin.replace(parca, roma)
                if not any(k.isalnum() for k in artan) and not (
                        self.latin and temizlik.latin_disi(temizlik.noktalama_latin(yalin))):
                    self.sabit[i] = yalin
                    continue
            if (koseli_ayir or parantez_ayir or etiket_ayir) and not sarki_mi(metin):
                metin, notlar, konusanlar = not_ve_konusmaci_ayir(metin, koseli_ayir, parantez_ayir, etiket_ayir)
                if notlar:
                    self.notlar[i] = notlar
                if konusanlar:
                    self.konusanlar[i] = konusanlar
                if not metin:                 # yalnızca nottan oluşan altyazı: gönderilmez, silinir
                    self.sabit[i] = ""
                    continue
            isaretli, parcalar, tamami = isaretle(metin, self.sozluk, etiket, i + 1, self.modlar)
            if tamami and self.tam_koruma:
                for parca in parcalar:   # Latin dışı kurgu dili: sözlükteki Latin hali
                    roma = self.sozluk.latin.get(parca) if self.sozluk else None
                    if self.latin and roma:
                        metin = metin.replace(parca, roma)
                if not (self.latin and temizlik.latin_disi(temizlik.noktalama_latin(metin))):
                    self.sabit[i] = metin
                    continue
            if sarki_mi(metin) and self.modlar["sarki"] != "ceviri":
                # Özgün söz: zaten Latin harfliyse olduğu gibi; değilse okunuşu (tamamı kana ise script yazar).
                ozgun = metin
                if self.latin and temizlik.latin_disi(temizlik.noktalama_latin(metin)):
                    if etiket_ayir:             # şarkı satırındaki konuşmacı etiketi: （アーニャ）
                        metin, _, konusanlar = not_ve_konusmaci_ayir(metin, False, False, True)
                        if konusanlar:
                            self.konusanlar[i] = konusanlar
                    ozgun = temizlik.kana_latin(metin)
                if self.modlar["sarki"] == "orijinal":
                    if ozgun:
                        self.sabit[i] = ozgun
                        continue
                    self.romanize.add(i)        # kanji de var: model yalnızca okunuşunu yazar
                elif ozgun:
                    self.ozgun[i] = ozgun       # orijinal_ceviri: söz script'ten, model yalnız çevirir
                else:
                    self.okunus_iste.add(i)     # kanjili söz: model çevirisini ve okunuşunu ayrı alanlarda verir
            self.giden[i] = isaretli
            if parcalar:
                self.korunan[i] = parcalar

    def _ozel_adlari_topla(self, satirlar):
        """Cümle ortasında büyük harfle geçen sözcükler ve sözlük adları: bölünmüş cümlede küçük harfe çevrilmez."""
        self.ozel_adlar = set()
        for s in satirlar:
            self.ozel_adlar.update(re.findall(r"(?<=[a-zçğıöşü,;] )[A-ZÇĞİÖŞÜ][\w'’-]+", temizlik.ETIKET.sub("", s.metin)))
        if self.sozluk is not None:
            for hedef in self.sozluk.terimler.values():
                self.ozel_adlar.update(x for x in re.findall(r"[A-ZÇĞİÖŞÜ][\w-]+", hedef))
            self.ozel_adlar.update(self.sozluk.degismez)
        self.ozel_adlar = {re.split(r"['’]", x)[0] for x in self.ozel_adlar}

    def _cumleleri_birlestir(self, satirlar):
        """Bölünmüş cümleler modele TEK satır olarak, kesintisiz gider; çeviriyi altyazılara script böler.
        Böylece aynı içerik iki parçaya yazılamaz ve söz dizimi kaynağın kesme yerlerine uymak zorunda kalmaz."""
        self.birlesik, self.uye = {}, {}     # baş index -> {grup, italik, metin} ; üye index -> baş index
        # "kapali": cümleler birleştirilmez; her altyazı kendi başına çevrilir.
        # Anlamı ayrıca verilen kurgu dili satırı ve ekran yazısı cümleye katılmaz.
        self.cumleye_uygun = set(self.giden) - set(self.anlamli) - {i for i, s in enumerate(satirlar) if s.ekran}
        gruplar = (cumle_gruplari(satirlar, self.cumleye_uygun)
                   if self.ayar["ceviri"]["bolunmus_cumle"] != "kapali" else [])
        for grup in gruplar:
            ham = [self.giden[j].replace("\n", " ").strip() for j in grup]
            italik = all(m.startswith("<i>") and m.endswith("</i>") and m.count("<i>") == 1 for m in ham)
            if not italik and any("<i>" in m or "</i>" in m for m in ham):
                continue                      # yalnızca bir parçası italik: ayrı ayrı çevrilir
            if len({tuple(self.konusanlar.get(j, ())) for j in grup}) > 1:
                continue                      # parçaları farklı kişiler söylüyor
            if italik:
                ham = [m[3:-4].strip() for m in ham]
            ham = [re.sub(r"^[—―]\s*|\s*[—―]$", "", m).strip() for m in ham]
            if not all(ham):
                continue
            bas = grup[0]
            self.birlesik[bas] = {"grup": grup, "italik": italik, "metin": " ".join(ham)}
            for j in grup[1:]:
                self.uye[j] = bas
            ortak = [p_ for j in grup for p_ in self.korunan.get(j, ())]
            if ortak:
                self.korunan_birlesik[bas] = ortak

    def _yalniz_bunlar(self, cevrilecek):
        """Aralık dışındaki satırlar çevrilmez, çıktıya da girmez; yalnız komşu parçanın bağlamında görünür.
        Aralığın sınırından geçen bölünmüş cümlenin aralıktaki parçası kendi başına çevrilir (başı bağlamda
        görünür)."""
        for bas in list(self.birlesik):
            if not set(self.birlesik[bas]["grup"]) <= cevrilecek:
                self._coz(self.birlesik[bas]["grup"])
        self.giden = {i: m for i, m in self.giden.items() if i in cevrilecek}
        self.sabit = {i: m for i, m in self.sabit.items() if i in cevrilecek}
        self.cumleye_uygun &= cevrilecek

    # ---- ana giriş ------------------------------------------------------------
    def cevir(self, satirlar, etiket, onbellek_yolu=None, ilerleme=None, cevrilecek=None):
        """index -> metin sözlüğü döndürür (çevrilenler ve olduğu gibi korunanlar). cevrilecek: verilirse yalnız
        bu sıralardaki satırlar çevrilir; ötekiler modele yalnız bağlam olarak gider."""
        self._hazirla(satirlar, etiket)
        if cevrilecek is not None:
            self._yalniz_bunlar(set(cevrilecek))
        sonuc = {}
        if onbellek_yolu and os.path.exists(onbellek_yolu):
            # Yarıda kalan çalıştırmanın kaydı: biten satırlar ve dosya sonu denetimlerinin kullandığı bilgiler.
            try:
                with open(onbellek_yolu, encoding="utf-8") as f:
                    kayit = json.load(f)
                sonuc = {int(k): v for k, v in kayit["sonuc"].items() if int(k) in self.giden}
                self.okunuslar.update({int(k): v for k, v in (kayit.get("okunus") or {}).items()})
            except (ValueError, OSError, KeyError, TypeError, AttributeError):
                sonuc = {}
            if sonuc:
                self.yanit_geldi = True
                self.gunluk.uyari(etiket, f"kaldığı yerden devam: {len(sonuc)}/{len(self.giden)} satır önceki çalıştırmadan alındı")
                self._say("önceki çalıştırmadan alınan satır", len(sonuc))
        isler = []
        for bas, bit in parcalara_bol(satirlar, self.ayar["parca"]):
            idler = [i for i in range(bas, bit) if i in self.giden and i not in sonuc]
            if idler:
                isler.append(idler)
        # Bir isteğe tam sığmayan (parça sınırına ya da önbelleğe bölünen) cümleler ayrı ayrı çevrilir.
        yeri = {i: n for n, idler in enumerate(isler) for i in idler}
        for bas in list(self.birlesik):
            grup = self.birlesik[bas]["grup"]
            if all(j in sonuc for j in grup):
                continue                      # tamamı önceki çalıştırmadan: dağıtılmış hâliyle kalır
            if len({yeri.get(j, -1 - j) for j in grup}) != 1:
                self._coz(grup)
        toplam, biten = len(self.giden), len(sonuc)
        if ilerleme:
            ilerleme(biten, toplam)
        with ThreadPoolExecutor(max_workers=max(1, self.ayar["model"]["es_zamanli_istek"])) as havuz:
            gelecekler = [havuz.submit(self._parca, idler) for idler in isler]
            try:
                for g in as_completed(gelecekler):
                    parca = g.result()
                    with self._kilit:
                        sonuc.update(parca)
                        biten += len(parca)
                        self._kayda_yaz(onbellek_yolu, sonuc)
                    if ilerleme:
                        ilerleme(biten, toplam)
            except BaseException:
                for g in gelecekler:
                    g.cancel()
                # Gönderilmiş parçalar yine de biter: sonuçları kayda geçer ki yeniden çalıştırınca bir daha ödenmesin.
                for g in gelecekler:
                    if not g.cancelled() and g.exception() is None:
                        sonuc.update(g.result())
                sonuc.update(self.yarim)
                self._kayda_yaz(onbellek_yolu, sonuc)
                raise
        if len(sonuc) < len(self.giden) and getattr(getattr(self.model, "iptal", None), "is_set", lambda: False)():
            # Durduruldu ve bazı parçalar yarım döndü (yolda olan yanıt geldi, ardından gereken istek gönderilmedi):
            # biten satırlar kayıtlı, iş tamamlanmış sayılmaz.
            self._kayda_yaz(onbellek_yolu, sonuc)
            raise Iptal()
        if self.model_boldu or self.script_boldu:
            self.gunluk.uyari(etiket, f"bölünmüş cümle: {self.model_boldu} tanesi duraklama yerlerinden kesildi, "
                                      f"{self.script_boldu} tanesini script böldü"
                              + (f"; {self.sure_birlesti} tanesinde altyazı süreleri birleştirildi" if self.sure_birlesti else ""))
        if self.resmi_beklenen and hedef_kodu(self.ayar) == "tr":
            aykiri = [i + 1 for i in sorted(self.resmi_beklenen)
                      if i in sonuc and SEN.search(temizlik.tr_kucuk(sonuc[i])) and not SIZ.search(temizlik.tr_kucuk(sonuc[i]))]
            self.gunluk.uyari(etiket, f"hitap: kime söylendiği kesin bilinen {len(self.resmi_beklenen)} resmi satırın "
                                      f"{len(aykiri)} tanesi tabloya aykırı" + (f" (satır {', '.join(map(str, aykiri[:12]))})" if aykiri else ""))
        if self.ek_onarildi:
            self.gunluk.uyari(etiket, f"hitap eki: {self.ek_onarildi} satırda düşen ek yerinde eklendi (yeniden sorulmadı)")
        if self.unvan_duzeldi:
            self._say("unvanı kayda göre düzeltilen satır", self.unvan_duzeldi)
        self._ardisik_yinelenen(sonuc)
        if self.ayar["kontrol"]["tekrar_eden_sahne"]:
            self._tekrar_eden_sahne(sonuc)
        if self.ayar["ceviri"]["bolunmus_cumle"] != "kapali":
            dengele(satirlar, sonuc, self.cumleye_uygun, self.gunluk, etiket, self.konusanlar)
        for i, (soz, italik) in self.anlamli.items():
            if i not in sonuc:
                continue
            anlam = " ".join(temizlik.ETIKET.sub("", sonuc[i]).split()).strip("()")
            # "ceviri": yalnızca anlam; "orijinal_ceviri": söz üstte, anlamı altında parantez içinde.
            metin = anlam if self.modlar["kurgu_dili"] == "ceviri" else f"{soz}\n({anlam})"
            sonuc[i] = "<i>" + metin + "</i>" if italik else metin
        for i in [i for i in sonuc if i in self.ozgun or i in self.okunus_iste]:
            sonuc[i] = self._ozgun_sozle(i, sonuc[i].replace("\\n", "\n").strip())
        sonuc.update(self.sabit)
        return sonuc

    def _kayda_yaz(self, yol, sonuc):
        """Kaldığı yerden devam kaydı: önce geçici dosyaya (yazarken kesilirse eski kayıt bozulmaz). Kayıt yalnızca
        bir kolaylıktır: yazılamazsa (Windows'ta virüs tarayıcısının kilidi gibi) uyarılır, çeviri sürer."""
        if not yol:
            return
        try:
            os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
            with open(yol + ".yeni", "w", encoding="utf-8") as f:
                json.dump({"sonuc": sonuc, **({"okunus": self.okunuslar} if self.okunuslar else {})}, f, ensure_ascii=False)
            os.replace(yol + ".yeni", yol)
        except OSError as e:
            self.gunluk.uyari(self.etiket, f"kaldığı yerden devam kaydı yazılamadı ({e}); çeviri sürüyor")

    def _say(self, ad, n=1):
        say = getattr(self.model, "say", None)
        if say:
            say(ad, n)

    def _kaynak(self, i):
        """Modele giden metin: bölünmüş cümlenin başıysa cümlenin tamamı."""
        return self.birlesik[i]["metin"] if i in self.birlesik else self.giden[i]

    def _yuk(self, idler, duzelt=None):
        kume, c = set(idler), self.ayar["parca"]
        bas, son = idler[0], idler[-1]
        satirlar = []
        for i in range(bas, son + 1):
            s = self.satirlar[i]
            if i in self.uye and self.uye[i] in kume:
                continue                       # metni baş satırın içinde
            if i in kume:
                oge = {"id": i + 1, "dur": round((s.bit - s.bas) / 1000, 1), "text": self._kaynak(i)}
                if i in self.birlesik:
                    grup = self.birlesik[i]["grup"]
                    oge["dur"] = round(sum(self.satirlar[j].bit - self.satirlar[j].bas for j in grup) / 1000, 1)
                    oge["parts"] = len(grup)
                elif i in self.romanize:
                    oge["romanize"] = True
                elif i in self.okunus_iste:
                    oge["reading"] = True
                if (self.ayar["ceviri"]["bolunmus_cumle"] != "kapali" and i not in self.birlesik
                      and i not in self.uye and not s.ekran
                      and not (i + 1 < len(self.satirlar) and self.satirlar[i + 1].ekran)
                      and cumle_suruyor_mu(self.satirlar, i)):
                    oge["continues"] = True
                konusanlar = self.konusanlar.get(i)
                if konusanlar:
                    farkli = {k for k in konusanlar if k}
                    if len(farkli) == 1:
                        oge["speaker"] = farkli.pop()
                    else:
                        oge["speakers"] = konusanlar
                kime = self._kime(i) if i not in self.birlesik else []
                if kime:
                    oge["says_to"] = "; ".join(f"{a} -> {b}: {'informal' if f == 'samimi' else 'formal'}"
                                               for a, b, f in kime)
                    if {f for _, _, f in kime} == {"resmi"}:
                        self.resmi_beklenen[i] = " and ".join(f"{a} -> {b}" for a, b, _ in kime)
                if self.notlar.get(i):
                    oge["note"] = "; ".join(self.notlar[i])
                # Çift dilli dosya: aynı andaki ikinci dil satırı (bölünmüş cümlede bütün parçalarınınki).
                ref = list(dict.fromkeys(self.satirlar[j].ref for j in (self.birlesik[i]["grup"] if i in self.birlesik else [i])
                                         if self.satirlar[j].ref))
                if ref:
                    oge["ref"] = " / ".join(ref)
                satirlar.append(oge)
            elif s.metin.strip():
                satirlar.append({"id": i + 1, "text": s.metin, "skip": True})
        once = [s.metin for s in self.satirlar[max(0, bas - c["baglam_once"]):bas] if s.metin.strip()]
        sonra = [s.metin for s in self.satirlar[son + 1:son + 1 + c["baglam_sonra"]] if s.metin.strip()]
        yuk = {"translate_to": dil_adi(self.ayar)}
        sahne = [{"ids": f"{a}-{b}", "summary": ozet} for a, b, ozet in self.sahneler
                 if a <= son + 1 and b >= bas + 1]
        if sahne:
            yuk["scene"] = sahne
        hitap = self._ilgili_hitap(bas, son)
        if hitap:
            yuk["address"] = hitap
        if duzelt:
            dil = dil_adi(self.ayar)
            def sorun(i, neden):
                if neden == "terim" and i in self.kacan_terim:
                    k, h = self.kacan_terim[i]
                    notu = self.sozluk.notlar.get(k) if self.sozluk is not None else None
                    return ((f'the glossary writes "{k}" as "{h}": use that rendering here, inflected as the sentence '
                             f'needs, not a synonym. If "{k}" in this line is an ordinary word and not this term, '
                             f"return your previous text unchanged" if k != h else
                             f'"{k}" stays "{h}" in this work: do not replace it with another word')
                            + (f". The entry means: {notu}" if notu else ""))
                return SORUN.get(neden, neden).replace("the target language", dil)
            yuk["fix"] = [{"id": i + 1, "your_previous_text": metin, "problem": sorun(i, neden)}
                          for i, (metin, neden) in sorted(duzelt.items()) if i in kume]
        yuk.update({"context_before": once, "lines": satirlar, "context_after": sonra})
        return json.dumps(yuk, ensure_ascii=False)

    def _ayristir(self, ham, idler):
        veri = json_coz(ham)
        liste = veri.get("lines") if isinstance(veri, dict) else veri
        if not isinstance(liste, list):
            raise ValueError("'lines' listesi yok")
        sonuc = {}
        for oge in liste:
            if isinstance(oge, dict) and isinstance(oge.get("text"), str):
                try:
                    i = int(oge["id"]) - 1
                except (KeyError, TypeError, ValueError):
                    continue
                sonuc[i] = oge["text"]
                if i in self.okunus_iste and isinstance(oge.get("reading"), str):
                    with self._kilit:
                        self.okunuslar[i] = " ".join(oge["reading"].split())
        bulunan = {i: sonuc[i] for i in idler if i in sonuc}
        if not bulunan:
            raise ValueError("istenen satırların hiçbiri yanıtta yok")
        return bulunan   # eksik kalan satırlar yalnızca kendileri yeniden istenir

    def _satir_kontrol(self, i, sonuc):
        """Sorun varsa kısa açıklama döndürür. Düzeltilebileni yerinde düzeltir."""
        kaynak, ceviri = self.satirlar[i].metin, sonuc[i].replace("\\n", "\n").strip()
        if i in self.birlesik:
            kaynak, ceviri = self.birlesik[i]["metin"], temizlik.ETIKET.sub("", ceviri).strip()
        elif "|" in ceviri and "|" not in kaynak:
            # Kesme işareti yalnızca bölünmüş cümleler içindir; sıradan satırda satır sonu olarak yorumlanır.
            ceviri = re.sub(r"[ \t]*\|[ \t]*", "\n" if "\n" not in ceviri else " ", ceviri)
            ceviri = "\n".join(x.strip() for x in ceviri.split("\n") if x.strip())
        ceviri = temizlik.I_KAPA.sub("</i>", temizlik.I_AC.sub("<i>", ceviri))
        if self.latin:
            ceviri = temizlik.noktalama_latin(ceviri)
        if (kaynak.startswith("<i>") and kaynak.endswith("</i>") and kaynak.count("<i>") == 1
                and "<i>" not in ceviri and "</i>" not in ceviri and ceviri):
            ceviri = "<i>" + ceviri + "</i>"
        sonuc[i] = ceviri
        if not temizlik.ETIKET.sub("", ceviri).replace("|", "").strip():
            return "bos"
        for parca in (self.korunan_birlesik.get(i, ()) if i in self.birlesik else self.korunan.get(i, ())):
            if parca not in ceviri and temizlik.noktalama_latin(parca) not in ceviri:
                return "isaret"
        if self.latin and temizlik.latin_disi(ceviri):
            return "romaji" if i in self.romanize else "latin"
        if ceviri.count("<i>") != ceviri.count("</i>"):
            return "italik"
        tire_k = sum(1 for s in kaynak.split("\n") if TIRELI.match(s))
        tire_c = sum(1 for s in ceviri.split("\n") if TIRELI.match(s))
        if tire_k >= 2 and tire_c != tire_k:
            return "tire"
        # Etiketle verilen iki konuşmacı (（アーニャ）ヒッ / （犬）ボフ): kaynakta çizgi yok, ama her satırı başka biri
        # söylüyor. Satır sayısı tutuyorsa script çizgileri koyar (model 13 altyazının çoğunda unutuyordu).
        konusan = self.konusanlar.get(i, [])
        if (tire_c == 0 and i not in self.birlesik and self.modlar["konusmaci_tiresi"] != "sil"
                and len(konusan) >= 2 and all(konusan) and len(set(konusan)) >= 2
                and len(ceviri.split("\n")) == len(konusan)):
            ceviri = sonuc[i] = "\n".join("- " + s.strip() for s in ceviri.split("\n"))
        giden = self._kaynak(i).replace("⟦", "").replace("⟧", "") if i in self.giden else kaynak
        if i in self.romanize:
            # şarkı sözü yalnızca Latin harfleriyle yazılır: terim ve ek aranmaz, ama çevrilmiş olmamalı
            if not romaji_mi(giden, ceviri):
                return "romaji"
            self._isaret_koru(i, giden, sonuc)
            return None
        if i in self.okunus_iste:
            # özgün söz + çeviri, kanjili söz: okunuş Latin harfli, kaynağın sesini taşıyan ve çeviriden farklı olmalı
            okunus = self.okunuslar.get(i, "")
            if (not okunus or temizlik.latin_disi(okunus) or not romaji_mi(giden, okunus)
                    or temizlik.tr_kucuk(okunus) == temizlik.tr_kucuk(" ".join(ceviri.split()))):
                return "okunus"
        if self.unvan_denetle and self.unvan_kaydi:
            sorun = self._unvan_kontrol(i, giden, sonuc)
            if sorun:
                return sorun
            ceviri = sonuc[i]
        if self.terimler:
            kucuk = " ".join(self.kucuk(ceviri).split())            # satır sonu terimi bölmüş olabilir
            for desen, kok, yazi in self.terimler:
                gecti = any(bicim_gecer(d, giden) for d in desen) if isinstance(desen, tuple) else bool(desen.search(giden))
                if gecti and yazi[0] in self.terim_maske:
                    maskeli = giden
                    for uzun in self.terim_maske[yazi[0]]:
                        maskeli = maskeli.replace(uzun, "\0")
                    gecti = any(bicim_gecer(d, maskeli) for d in desen)
                if gecti and kok not in kucuk:
                    self.kacan_terim[i] = yazi          # yeniden sorarken hangi terim olduğu söylenir
                    return "terim"
        # Ek Latin harfleriyle aranır ("-san"). Çıktı alfabesi serbestken başka yazıyla yazılmış satırda (Лойд-сан,
        # 卡卡西老师) aranmaz: doğru satır yeniden sorulur, Latin harfli ek o yazının içine yazdırılırdı.
        if self.ek_kontrol and (self.latin or not temizlik.latin_disi(ceviri)):
            if EK.search(kaynak) and not EK.search(ceviri):
                return "ek"
            # Japonca ad + ek (ヨルさん, スワン先生, 黄昏さん): Latin harfli ek çeviride olmalı.
            # Yalnızca bilinen adlara uygulanır (etiketlerde ya da sözlükte geçenler): "シッターさん" gibi
            # katakana bir sıradan ad + さん kişi adı değildir.
            for m in JP_AD_EK.finditer(giden):
                # 先生 / 先輩 / 様 yalnızca kişilere takılır: ad sözlükte olmasa da (yeni dizi) ek aranır.
                if (m.group(1) in self.jp_adlar or m.group(2) in UNVAN_EKI
                        or self._adi_yazmis(m.group(1), ceviri)) and not re.search(
                        r"\b" + _ek_latin(m.group(2)) + r"\b", ceviri, re.I):
                    onarilmis = self._eki_ekle(ceviri, m.group(1), _ek_latin(m.group(2)))
                    if onarilmis is None:
                        return "ek"
                    ceviri = sonuc[i] = onarilmis
            for m in OKUNUSLU_AD_EK.finditer(giden):
                if not re.search(r"\b" + _ek_latin(m.group(1)) + r"\b", ceviri, re.I):
                    return "ek"
            # Tek başına kullanılan 先生 / 先輩: "Sensei" / "Senpai" olarak kalır (hoca, öğretmen, abi değil).
            for m in YALNIZ_UNVAN.finditer(giden):
                if not re.search(r"\b" + _ek_latin(m.group(1)) + r"\b", ceviri, re.I):
                    return "ek"
            for ad in self.jp_kanji_adlar:
                for m in re.finditer(re.escape(ad) + r"(?:\([^)]*\)|（[^）]*）)?" + JP_EK_DESEN, giden):
                    if not re.search(r"\b" + _ek_latin(m.group(1)) + r"\b", ceviri, re.I):
                        onarilmis = self._eki_ekle(ceviri, ad, _ek_latin(m.group(1)))
                        if onarilmis is None:
                            return "ek"
                        ceviri = sonuc[i] = onarilmis
        if sarki_mi(giden) and i not in self.ozgun and i not in self.okunus_iste:
            self._isaret_koru(i, giden, sonuc)
        return None

    def _isaret_koru(self, i, giden, sonuc):
        """Şarkı satırında model ♪ işaretini düşürdüyse kaynaktaki yerine (başa, sona) konur."""
        duz = lambda m: temizlik.ETIKET.sub("", m).strip()
        satirlar = sonuc[i].replace("\\n", "\n").split("\n")
        if duz(giden)[:1] in ("♪", "♫") and duz(satirlar[0])[:1] not in ("♪", "♫"):
            satirlar[0] = "♪ " + satirlar[0]
        if duz(giden)[-1:] in ("♪", "♫") and duz(satirlar[-1])[-1:] not in ("♪", "♫"):
            satirlar[-1] += " ♪"
        sonuc[i] = "\n".join(satirlar)

    def _ozgun_sozle(self, i, ceviri):
        """Özgün söz + çeviri: script'in yazdığı söz üstte, modelin çevirisi altında parantez içinde. Kanjili sözün
        okunuşu modelden gelir; kaynağın ♪ işaretleri ve italiği ona da konur. Çevirideki ♪ ve uçtaki ～ atılır."""
        ozgun = self.ozgun.get(i)
        if ozgun is None:
            okunus = self.okunuslar.get(i)
            if not okunus:
                return ceviri
            kaynak = temizlik.ETIKET.sub("", self.satirlar[i].metin).strip()
            ozgun = ("♪ " if kaynak[:1] in ("♪", "♫") else "") + okunus + (" ♪" if kaynak[-1:] in ("♪", "♫") else "")
            cekirdek = re.sub(r"^[\s♪♫～〜~]+|[\s♪♫～〜~]+$", "", self.satirlar[i].metin)
            if cekirdek.startswith("<i>") and cekirdek.endswith("</i>"):
                ozgun = "<i>" + ozgun + "</i>"
        satirlar = [re.sub(r"^[\s～〜~]+|[\s～〜~]+$", "", s) for s in temizlik.isaretsiz(ceviri).split("\n")]
        return ozgun + "\n(" + "\n".join(s for s in satirlar if s) + ")"

    def _parca(self, idler):
        """Bir chunk'ı çevirir; bölünmüş cümleler tek satır olarak sorulur, yanıt altyazılara dağıtılır. Bir isteğe
        tam sığmayan cümleleri cevir önceden çözer: buraya gelen her cümlenin bütün parçaları idler'dedir."""
        birimler = [i for i in idler if i not in self.uye]
        hata = None
        try:
            kabul = self._parca_ic(birimler)
        except ModelHatasi as e:
            kabul, hata = e.yarim, e
        for bas in [i for i in birimler if i in self.birlesik and i in kabul]:     # durdurmada parça yarım dönebilir
            kabul.update(self._dagit(bas, kabul[bas]))
        if hata is not None:
            with self._kilit:
                self.yarim.update(kabul)
            raise hata
        return kabul

    def _kullanilamaz(self, neden):
        """Üst üste kullanılamayan yanıt (ret, JSON değil): model ya da adres yanlışsa her parça ikiye bölünerek satır
        başına onlarca istek harcanırdı. ARDISIK_BOZUK kez üst üste olunca çalışma nedeniyle durur; kullanılabilir yanıt
        alınmış dosyada sınır ARDISIK_BOZUK_SONRA'dır."""
        with self._kilit:
            self.ust_uste_bozuk += 1
            n = self.ust_uste_bozuk
        if n >= (ARDISIK_BOZUK_SONRA if self.yanit_geldi else ARDISIK_BOZUK):
            hata = ModelHatasi(f"model {n} kez üst üste kullanılabilir yanıt vermedi (son: {neden[:200]}); "
                               f"model adını, adresini ve json_modu ayarını denetle")
            durdur = getattr(self.model, "durdur", None)
            if durdur:
                durdur(hata)
            raise hata

    def _parca_ic(self, idler):
        """Parçayı sorar; düzelmeyecek hatayla (biten kredi, üst üste bozuk yanıt) kesilirse o ana kadar kabul edilen,
        ödenmiş satırları hataya iliştirir (yarim): kayda geçerler, yeniden çalıştırınca bir daha ödenmezler."""
        kabul = {}
        try:
            return self._parca_dene(idler, kabul)
        except ModelHatasi as e:
            e.yarim = {**kabul, **getattr(e, "yarim", {})}
            raise

    def _parca_dene(self, idler, kabul):
        """Kontrolü geçmeyen satırlar tek başlarına yeniden istenir."""
        son, kalan = {}, list(idler)
        sinir, deneme, tazelendi = self.ayar["kontrol"]["deneme_sayisi"], 0, False
        while deneme < sinir:
            deneme += 1
            aralik = f"satır {kalan[0] + 1}-{kalan[-1] + 1}"
            # Yeniden denemede model neyi düzelteceğini görür ve biraz düşünerek yanıtlar. Seviye "" ise alan hiç
            # gönderilmez (config.toml): onu tanımayan sunucu isteği reddeder, çalışma dururdu.
            yuk = self._yuk(kalan, son if deneme > 1 else None)
            dusunme = "low" if deneme > 1 and self.ayar["model"]["dusunme_seviyesi"] == "none" else None
            try:
                ham = self.model.iste(self.sistem, yuk, dusunme)
            except Iptal:
                # Durduruldu: ödenmiş önceki yanıtın kabul edilen satırları kaybolmasın (kayda geçer, yeniden
                # çalıştırınca yalnız kalanlar istenir). Hiç satır yoksa durdurma yukarı iletilir.
                if kabul:
                    return kabul
                raise
            except Ret as e:
                self.gunluk.ham(self.etiket, {"deneme": deneme, "giden": json.loads(yuk), "ret": str(e)})
                self.gunluk.hata(self.etiket, f"{aralik}: ret ({e}), deneme {deneme}")
                if getattr(e, "icerik", False):
                    self.yanit_geldi = True
                self._kullanilamaz(f"ret: {e}")
                continue
            self.gunluk.ham(self.etiket, {"deneme": deneme, "giden": json.loads(yuk), "gelen": ham})
            try:
                sonuc = self._ayristir(ham, kalan)
            except ValueError as e:
                self.gunluk.hata(self.etiket, f"{aralik}: bozuk yanıt ({e}), deneme {deneme}")
                self._kullanilamaz(f"bozuk yanıt: {e}")
                continue
            with self._kilit:
                self.ust_uste_bozuk = 0
                self.yanit_geldi = True
            eksik = [i for i in kalan if i not in sonuc]
            if eksik and len(sonuc) > 1 and min(eksik) > max(sonuc):
                if not tazelendi and len(kalan) > 2:
                    # Erken biten yanıtta model iki satırın içeriğini bir satıra yazıp sonrakileri birer kaydırmış
                    # olabilir: satırlar kontrollerden geçer ama yanlış altyazıdadır (olcum55'te 19 satır). Yanıt
                    # atılır, parça bir kez baştan sorulur; bu ek istek deneme hakkından sayılmaz.
                    tazelendi, sinir = True, sinir + 1
                    self.gunluk.uyari(self.etiket, f"{aralik}: yanıt {max(sonuc) + 1}. satırda erken bitti; "
                                                   f"içerik kaymış olabilir, parça baştan yeniden soruluyor")
                    continue
                # Yine erken bittiyse son gelen satır da güvenilmez (model kapanmadan önce oraya kendi notunu
                # yazmış olabilir): o da eksiklerle birlikte yeniden sorulur.
                self.gunluk.uyari(self.etiket, f"{aralik}: yanıt {max(sonuc) + 1}. satırda erken bitti; "
                                               f"o satır da yeniden soruluyor")
                del sonuc[max(sonuc)]
            sorunlu = []
            for i in kalan:
                if i not in sonuc:
                    son.setdefault(i, ("", "eksik"))
                    sorunlu.append(i)
                    continue
                neden = self._satir_kontrol(i, sonuc)
                if neden:
                    son[i] = (sonuc[i], neden)
                    sorunlu.append(i)
                else:
                    kabul[i] = sonuc[i]
                    son.pop(i, None)
            kalan = sorunlu
            if not kalan:
                if len(kabul) == len(idler):
                    self._sorgula(idler, kabul)
                return kabul
            if deneme >= 2 and all(son[i][1] == "terim" for i in kalan):
                break   # sözlük terimi bir kez yeniden istendi; ısrar etmeye değmez
            self.gunluk.uyari(self.etiket, f"{len(kalan)} satır kontrolü geçmedi "
                                           f"({', '.join(sorted({son[i][1] for i in kalan}))}), "
                                           f"deneme {deneme}; yalnızca bu satırlar yeniden isteniyor")
        yanitsiz = [i for i in kalan if i not in son or son[i][1] == "eksik"]
        for i in kalan:
            if i in yanitsiz:
                continue
            metin, neden = son[i]
            if neden == "bos":
                kabul[i] = self._kaynak(i)
                self.gunluk.hata(self.etiket, f"satır {i + 1}: boş çeviri, orijinal bırakıldı")
            elif neden == "latin":
                self.gunluk.hata(self.etiket, f"satır {i + 1}: Latin dışı harfler silindi ({temizlik.latin_disi(metin)})")
                kabul[i] = temizlik.latin_disi_sil(metin)
            elif neden == "terim":
                self.gunluk.uyari(self.etiket, f"satır {i + 1}: sözlük terimi çeviride görünmüyor: {metin!r}")
                kabul[i] = metin
            else:
                self.gunluk.hata(self.etiket, f"satır {i + 1}: '{neden}' kontrolü geçmedi, çeviri yine de kullanıldı: {metin!r}")
                kabul[i] = metin
        if len(yanitsiz) > 1:
            orta = len(yanitsiz) // 2
            self.gunluk.hata(self.etiket, f"satır {yanitsiz[0] + 1}-{yanitsiz[-1] + 1}: chunk ikiye bölünüp yeniden deneniyor")
            try:
                kabul.update(self._parca_ic(yanitsiz[:orta]))
                kabul.update(self._parca_ic(yanitsiz[orta:]))
            except Iptal:
                if kabul:
                    return kabul
                raise
        elif yanitsiz:
            i = yanitsiz[0]
            self.gunluk.hata(self.etiket, f"satır {i + 1}: çevrilemedi, orijinal bırakıldı")
            kabul[i] = self._kaynak(i)
        return kabul
