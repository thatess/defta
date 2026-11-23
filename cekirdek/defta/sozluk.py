"""Dizi başına kalıcı sözlük: terim girdileri, hitap tablosu, karakter adları ve dosya başına bilgiler.

Her bilgi dosyada tek yerde durur. Bir sözcüğün karşılığı yalnızca "girdiler"dedir; çeviride kullanılan üç liste
(terimler, korunacak, degismez) ondan türetilir ve dosyaya yazılmaz. Script yeni girdi ekler; var olan kararı
yalnızca tutarlılık geçişleri değiştirir (çoğul = tekil, ortuşen lakap, ad ekli lakap, Japon ad sırası, etiketteki
okunuş: terim.py) ve bunlar "karar": "elle" olan girdiye dokunmaz. Elle düzeltmek isteyen "girdiler"deki "hedef"i
(ve varsa "anlam"ı) değiştirir ve "karar"ı "elle" yapar.

Dosyanın bölümleri:
  baslik       yapımın adı
  girdiler     yazım -> {tur, hedef, yazimlar, karar, sayi, ornek, [kalip], [anlam], [siradan], [terim], [ses]}
  unvanlar     bu yapımda bir adın yanında geçen unvan sözcükleri
  hitap        "A -> B": "samimi" | "resmi"
  karakterler  Latin kısa ad -> kısa not (yalnızca okuyan için; istemde kullanılmaz)
  adlar        kaynaktaki yazım -> Latin kısa ad
  latin        Latin dışı kurgu dili parçası -> Latin harfli hali
  dosyalar     dosya -> {dil, sahneler, kurgu}; ilk geçişi yapılmış dosyalar burada listelenir
"""
import copy
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher

from . import temizlik
from .ayar import acik, unvan_tablosu
from .kaynak import okunus_yazimi

EK_DESENI = re.compile(
    r"(?<![\w-])[^\W\d_][\w'’]*-(?:san|sama|kun|chan|tan|senpai|sempai|sensei|dono|shi|jou)\b"
    r"|(?<![\w-])(?:sensei|senpai|sempai)\b", re.I)
UNVAN = re.compile(r"^(?:[\w'’]+-)?(?:san|sama|kun|chan|tan|senpai|sempai|sensei|dono|shi|jou)$", re.I)


def _anahtar(metin):
    """Karşılaştırma için: küçük harf, baştaki İngilizce tanımlık ve sondaki noktalama atılır."""
    metin = re.sub(r"^(the|a|an)\s+", "", metin.strip().lower())
    return re.sub(r"[\s.!?,;:]+$", "", metin)


def _duz(metin):
    """Karşılaştırma için: küçük harf, harf işaretleri atılmış ("Udûn" = "Udun")."""
    return "".join(k for k in unicodedata.normalize("NFKD", metin.lower()) if not unicodedata.combining(k))


JP_HITAP_EKLERI = ("さん", "さま", "様", "ちゃん", "くん", "君", "殿", "どの", "氏", "嬢", "たん", "先輩", "せんぱい", "先生", "せんせい")
# Adın yanında geçse de unvan gibi yönetilmeyen akrabalık sözcükleri: Türkçede adın ardına gelir ("Bilbo Amca"),
# iyelik eki alır ("amcan Bilbo"); tek kalıba bağlanırsa doğru çeviri bozulur.
AKRABALIK = {"uncle", "aunt", "auntie", "aunty", "grandpa", "grandma", "granny", "grandfather", "grandmother",
             "cousin", "brother", "sister", "father", "mother", "papa", "mama", "daddy", "mommy", "mum", "mom", "dad"}
JP_EKLI = re.compile(r".*(" + "|".join(JP_HITAP_EKLERI) + ")$")


ARTIKELLER = {"the", "a", "an"}


def seri_adi(baslik, bilinenler=()):
    """Sözlüğün bağlandığı seri (kullanıcının kararı, 18.11.2026: aynı serinin filmleri tek sözlüğü paylaşır).
    "Ad: Alt başlık" ya da "Ad - Alt başlık" biçiminde başlığın ilk bölümü ("The Lord of the Rings: The Two Towers" ->
    "The Lord of the Rings"); iki nokta bitişikse ("Re:Zero") bölünmez. Ayırıcı yoksa (klasör adlarında iki nokta
    olamaz) sözlüğü zaten olan bir başlık bu başlığın sözcük sözcük başıysa ve artikel dışında en az iki sözcükse o
    seri alınır ("The Lord of the Rings The Two Towers"). Yoksa başlığın kendisi."""
    baslik = " ".join(str(baslik or "").split())
    for ayirici in (": ", " - ", " – ", " — "):
        if ayirici in baslik:
            on = baslik.split(ayirici, 1)[0].strip()
            if len(on) >= 3:
                return on
    sozcukler = baslik.casefold().split()
    uyan = [b for b in bilinenler if b and b.casefold().split() == sozcukler[:len(b.split())]
            and len(b.split()) < len(sozcukler) and len([w for w in b.casefold().split() if w not in ARTIKELLER]) >= 2]
    return max(uyan, key=lambda b: len(b.split())) if uyan else baslik


def tek_sozcuk_ad(yazim, latin):
    """Tek sözcüklük katakana adın Latin yazımı tek sözcüktür: model bazen tam adı veriyordu (アーニャ ->
    "Anya Forger"); terim denetimi o zaman yalnızca "Anya" geçen her satırı yeniden sorduruyordu. Okunuşa en
    yakın sözcük alınır. Eski sözlüklerdeki böyle kayıtlar da okunurken düzelir."""
    if not latin or " " not in latin or "・" in yazim or not re.fullmatch(r"[ァ-ヺー]{2,}", yazim):
        return latin
    okunus = temizlik.kana_latin(yazim) or ""
    en_iyi = max(latin.split(), key=lambda s: (_iskelet_yakinligi(okunus, s), SequenceMatcher(None, okunus, s.lower()).ratio()))
    tek = _iskelet_yakinligi(okunus, en_iyi)
    return en_iyi if tek >= 0.85 and tek > _iskelet_yakinligi(okunus, latin.replace(" ", "")) else latin


def _iskelet_yakinligi(okunus, yazim):
    # Japonca okunuşta r = l; İngilizce yazımın aynı sesleri: qu = ku, c (e/i/y önünde) = s, c = k, x = ks, sh = s
    # (クインシー "Kuinshii" ile "Quincy" aynı ad: eskiden benzemez çıkıyor, okunuş adı tanınmıyordu)
    def iskelet(m):
        m = re.sub(r"qu", "k", m.lower().replace("l", "r"))
        m = re.sub(r"c(?=[eiy])", "s", m).replace("c", "k").replace("x", "ks").replace("sh", "s")
        return re.sub(r"[aeiouy]", "", m)
    return SequenceMatcher(None, iskelet(okunus), iskelet(yazim)).ratio()


# Bir terimden başka bir sözcük türeten ekler (kişi, topluluk, nitelik): uzun yazım kısa terimin "fazlası olan adı"
# değil, ondan türemiş ayrı bir sözcüktür. Karşılıkları ortak sözcük taşımasa da kısa terim silinmez.
TUREME_EKI = {"者", "人", "家", "師", "士", "員", "使い", "達", "たち", "ら", "的", "性", "化", "界", "学", "力"}


def japonca_adlari_esitle(terimler, turler):
    """Japonca adların Latin yazımları birbirini tutsun (terimler sözlüğü yerinde düzeltilir). Model her adayı ayrı
    yazdığı için çelişebiliyordu; terim denetimi de o zaman doğru satırları yeniden sorduruyordu:
    - Ön eki bilinen bir ad olan ad: ロイド -> Loid ise ロイドマン "Lloydman" değil "Loidman".
    - Parçaları bilinen tam ad: ロイド・フォージャー parçalardan kurulur ("Roido Foja" yazılmıştı); parçalardan biri
      biliniyor ama tam ad onunla çelişiyorsa tam ad listeden çıkar (parçaların girdileri o adı zaten karşılar).
    - Biri ötekinin başı olan iki yazım (ニューストン / ニューストン城): ortak adın yazımı aynı olur (okunuşa en yakın
      olanı), kısa yazımın karşılığı uzun yazımın fazladan parçasını ("Şatosu") taşımaz."""
    katakana = re.compile(r"[ァ-ヺー]{2,}")
    for k in [k for k in terimler if katakana.fullmatch(k)]:
        # tek katakana sözcüğün yazımı tire taşımaz: "Loid-man" -> "Loidman", "News-ton Kalesi" -> "Newston Kalesi"
        terimler[k] = re.sub(r"(?<=[a-z])-(?=[a-z])", "", terimler[k])
    adlar = {k: v for k, v in terimler.items() if turler.get(k) == "name" and katakana.fullmatch(k) and " " not in v}
    for k in sorted(adlar, key=len):
        on = max((a for a in adlar if a != k and k.startswith(a)), key=len, default=None)
        if on and (not terimler[k].lower().startswith(terimler[on].lower())
                   or terimler[k].lower() == terimler[on].lower()):
            kalan = temizlik.kana_latin(k[len(on):]) or ""
            bas = terimler[k][:len(terimler[k]) - len(kalan)]
            if kalan and (terimler[k].lower() == terimler[on].lower()
                          or (terimler[k].lower().endswith(kalan.lower()) and _ses(bas) == _ses(terimler[on]))):
                terimler[k] = adlar[k] = terimler[on] + kalan.lower()
    # Ad değil de lakap sayılan uzantı da (ロイドマン "Lloyd Adam"): adın yanlış yazımını taşıyorsa addan kurulur.
    for k in [k for k in terimler if katakana.fullmatch(k) and k not in adlar and turler.get(k) != "common"]:
        on = max((a for a in adlar if k.startswith(a)), key=len, default=None)
        ilk = (terimler[k].split() or [""])[0]
        if (on and ilk[:1].isupper() and not ilk.lower().startswith(terimler[on].lower())
                and _ses(ilk).startswith(_ses(terimler[on]))):
            kalan = temizlik.kana_latin(k[len(on):]) or ""
            if kalan:
                terimler[k] = terimler[on] + kalan.lower()
    for k in [k for k in terimler if "・" in k and turler.get(k) == "name"]:
        parcalar = [terimler.get(p) for p in k.split("・")]
        bilinen = [p for p in parcalar if p and " " not in p]
        if len(bilinen) == len(parcalar):
            terimler[k] = " ".join(parcalar)
        elif bilinen and any(p not in terimler[k].split() for p in bilinen):
            del terimler[k]
    for kisa in [k for k in terimler if temizlik.latin_disi(k)]:
        # uzun yazımın fazlası ayrı bir sözcük olmalı (城, 姫); katakana fazlası adın kendisine eklenir (ロイドマン)
        for uzun in [k for k in terimler if k != kisa and k.startswith(kisa) and temizlik.latin_disi(k)
                     and not re.match(r"[ァ-ヺー]", k[len(kisa):])]:
            if kisa not in terimler or uzun not in terimler or (
                    terimler[kisa] == terimler[uzun] and uzun[len(kisa):][:1] in "(（"):
                continue                       # okunuşlu yazımın parçası (梟 / 梟(ストリクス)): aynı şeyin adı, dokunulmaz
            ks, us = terimler[kisa].split(), terimler[uzun].split()
            if not ks or not us:
                continue
            if uzun[len(kisa):] in TUREME_EKI:
                continue                       # 超能力 "güç" / 超能力者 "o güce sahip kişi": iki ayrı sözcük, ikisi de kalır
            if SequenceMatcher(None, ks[0].lower(), us[0].lower()).ratio() < 0.7:
                ortak = {s.lower() for s in ks} & {s.lower() for s in us}
                kalan = [s for s in us if s.lower() not in ortak]
                if all(s.lower() in ortak for s in ks):
                    continue                   # アーニャ "Anya" / アーニャ姫 "Prenses Anya": kısa, uzunun içinde geçiyor
                if not ortak:
                    del terimler[kisa]         # モジャモジャ "Dağınık" / モジャモジャ伯爵 "Kıvırcık Kont": uzun yazım kalır
                elif kalan:                    # モジャモジャ "Kont Mojamoja" / 伯爵 "Kabarık Kont": kısa "Kabarık" olur
                    terimler[kisa] = " ".join(kalan)
                continue
            okunus = temizlik.kana_latin(re.sub(r"[^ァ-ヺー]", "", kisa)) or ""
            # ikisi aynı adın yazımı: harf harf okunuşun kendisi ("Newsuton") değil, sessizleri okunuşa uyan resmî yazım
            benzerlik = lambda s: SequenceMatcher(None, okunus.lower(), s.lower()).ratio()
            ks[0] = us[0] = min((ks[0], us[0]), key=lambda s: (benzerlik(s) == 1, -_iskelet_yakinligi(okunus, s), benzerlik(s)))
            if len(ks) > 1 and len(us) > 1 and (ks[1:] == us[len(us) - len(ks) + 1:] or katakana.fullmatch(kisa)):
                # "Newston Kalesi" / 城 "Newston Şatosu" -> "Newston": yapı 城'nin parçası. Katakana kısa yazımda kalan
                # sözcük okunuşa en yakın olandır (アーニャ / アーニャ姫 "Prenses Anya" -> "Anya", "Prenses" değil).
                ks = [max(ks, key=lambda s: _iskelet_yakinligi(okunus, s))] if okunus else ks[:1]
            terimler[kisa], terimler[uzun] = " ".join(ks), " ".join(us)


def ekli_ad_mi(kaynak):
    """Ad + hitap eki (ロイドさん, ヘンダーソン先生, Kenji-senpai) ya da ekin kendisi (先生): sözlüğe girmez, ek
    otomatik korunur. Girerse "Bay Loid", "Öğretmen" gibi bir karşılık hitap eki kuralıyla çelişir."""
    kaynak = kaynak.strip()
    return bool(UNVAN.match(kaynak) or JP_EKLI.match(kaynak)
                or re.search(r"-(san|sama|kun|chan|tan|senpai|sempai|sensei|dono|shi|jou)$", kaynak, re.I))


def _json(veri, derinlik):
    """Sözlük dosyasının yazımı: her girdi tek satırda durur ki dosya göz atılabilir kalsın.
    derinlik: bölüm adı -> kaç düzey açılacağı (girdiler 1: girdi başına bir satır); listede olmayan tek satır."""
    def yaz(deger, kalan, ic):
        ogeler = list(deger.items()) if isinstance(deger, dict) else list(enumerate(deger)) if isinstance(deger, list) else []
        if kalan <= 0 or not ogeler:
            return json.dumps(deger, ensure_ascii=False)
        satirlar = [ic + "  " + (json.dumps(k, ensure_ascii=False) + ": " if isinstance(deger, dict) else "")
                    + yaz(v, kalan - 1, ic + "  ") for k, v in ogeler]
        ac, kapa = ("{", "}") if isinstance(deger, dict) else ("[", "]")
        return ac + "\n" + ",\n".join(satirlar) + "\n" + ic + kapa
    return "{\n" + ",\n".join(f'  {json.dumps(k, ensure_ascii=False)}: {yaz(v, derinlik.get(k, 0), "  ")}'
                               for k, v in veri.items()) + "\n}\n"


def _ses(ad):
    """Japonca okunuşla Latin adı karşılaştırmak için kaba ses anahtarı: l=r, uzun ünlü tek, sondaki u/o yok."""
    a = re.sub(r"[^a-z]", "", ad.lower().replace("l", "r").replace("y", "i").replace("th", "s").replace("v", "b"))
    a = re.sub(r"(.)\1+", r"\1", a)
    return re.sub(r"(?<=[^aeiou])[uo]$", "", a)


class Sozluk:
    def __init__(self, yol):
        self.yol = yol
        # Terim sözlüğünün TEK kaydı. terimler / korunacak / degismez bundan türetilir (aşağıdaki özellikler);
        # değiştirmenin yolu girdi_ekle, girdi_yaz, girdi_sil ve terimden_cikar'dır.
        self.girdiler = {}
        self._turetilen, self._kirli = ({}, [], [], {}, {}), True
        self.hitap = {}           # "A -> B": "samimi" | "resmi"
        self.karakterler = {}     # Latin kısa ad -> hitap isteğinin kısa notu (rol, ilişki); istemde kullanılmaz
        self.adlar = {}           # kaynaktaki yazım (etiketteki ad: アーニャ) -> Latin kısa ad (Anya)
        self.unvanlar = []        # bu yapımda bir adın yanında geçen unvan sözcükleri ("Mr", "Warden", "隊長")
        self.unvan_kaydi = {}     # unvan -> kalıp ("Bay {ad}"); yalnızca gorunum() doldurur (tablo, yoksa model kararı)
        self.baslik = ""
        self.latin = {}           # Latin dışı kurgu parçası -> Latin harfli hali
        # Dosya başına bilgiler (dosyada tek bölüm: "dosyalar"); her biri kendi ayarıyla kapatılabildiği için ayrı tutulur.
        self.dil = {}             # dosya -> kaynak dil kodu
        self.sahneler = {}        # dosya -> [[ilk satır, son satır, özet], ...]
        self.kurgu = {}           # dosya -> {satır no (metin): [kurgu dili parçaları]}
        self.analiz_edilen = []   # ilk geçişi yapılmış dosyalar
        self.eski_duzen = False   # dosya eski düzendeydi: ilk fırsatta yeni düzenle yeniden yazılır (ceviri.py)
        self.bozuk = ""           # okunamayan sözlük dosyasının saklandığı yol (dizi_analiz rapora yazar)
        if os.path.exists(yol):
            try:
                with open(yol, encoding="utf-8-sig") as f:
                    veri = json.load(f)
                if not isinstance(veri, dict):
                    raise ValueError("JSON nesnesi değil")
            except ValueError:
                # Boş, yarım yazılmış ya da bozulmuş dosya (elektrik kesintisi, dolu disk) çeviriyi durdurmaz: içeriği
                # ".bozuk" adıyla saklanır (üzerine yazılıp kaybolmasın), sözlük boş başlar ve yeniden kurulur.
                veri = None
                try:
                    os.replace(yol, yol + ".bozuk")
                    self.bozuk = yol + ".bozuk"
                except OSError:           # aynı anda başka bir okuyan taşımış olabilir
                    pass
            self.eski_duzen = isinstance(veri, dict) and "dosyalar" not in veri
            if isinstance(veri, dict) and not any(a in veri for a in ("girdiler", "terimler", "korunacak", "dosyalar")):
                # 1. aşamanın düz biçimi: {"kaynak": "hedef"}
                duz = {k: v for k, v in veri.items() if isinstance(v, str)}
                self.elle_ekle({k: v for k, v in duz.items() if v and v != k},
                               [k for k, v in duz.items() if not v or v == k])
            elif isinstance(veri, dict):
                self.girdiler = dict(veri.get("girdiler") or {})
                self.hitap = dict(veri.get("hitap") or {})
                self.karakterler = dict(veri.get("karakterler") or {})
                self.adlar = dict(veri.get("adlar") or {})
                self.unvanlar = list(veri.get("unvanlar") or [])
                self.baslik = str(veri.get("baslik") or "")
                self.latin = dict(veri.get("latin") or {})
                if "dosyalar" in veri:
                    for ad, d in (veri.get("dosyalar") or {}).items():
                        for alan, hedef in (("dil", self.dil), ("sahneler", self.sahneler), ("kurgu", self.kurgu)):
                            if d.get(alan):
                                hedef[ad] = d[alan]
                        if d.get("analiz", True):
                            self.analiz_edilen.append(ad)
                else:                     # eski düzen: beş ayrı bölüm
                    self.dil = dict(veri.get("dil") or {})
                    self.sahneler = dict(veri.get("sahneler") or {})
                    self.kurgu = dict(veri.get("kurgu") or {})
                    self.analiz_edilen = list(veri.get("analiz_edilen") or [])
                if any(a in veri for a in ("terimler", "korunacak", "degismez")):
                    self._eski_listeleri_isle(veri)

    # ---- girdilerden türetilen listeler ----------------------------------------------------------------------
    def _turet(self):
        """(terimler, korunacak, degismez, notlar, siradanlar): girdilerin çeviride kullanılan hâli.
        Hiçbirine girmeyenler: karşılığı olmayan girdi (sıradan sözcük), "terim": false olan (bilinen unvan sözcüğü),
        ses sözcüğü, "ad + hitap eki" (sevimli adı olan "sözcük + ek" girer: 犬さん -> köpekçik), akrabalık unvanı (Uncle: modele kalır, "Amca Bilbo" yazdırıyordu) ve
        tanımlıkla başlayıp olduğu gibi kalan, ad olmayan kalıp ("the One": İngilizce bırakılırdı)."""
        if self._kirli:
            terimler, korunacak, degismez, notlar, siradanlar = {}, [], [], {}, {}
            for yazim, g in self.girdiler.items():
                hedef, tur = g.get("hedef"), g.get("tur")
                if (not hedef or g.get("terim") is False or tur == "sound" or (ekli_ad_mi(yazim) and tur != "epithet")
                        or (tur == "title" and yazim.lower() in AKRABALIK)
                        or (hedef == yazim and tur not in ("name", "codename") and re.match(r"(?i)(the|a|an)\s", yazim))):
                    continue
                if tur == "fictional":
                    korunacak.append(yazim)
                    continue
                if tur == "name":
                    hedef = tek_sozcuk_ad(yazim, hedef)
                if hedef == yazim and tur != "codename":
                    degismez.append(yazim)
                else:
                    terimler[yazim] = hedef
                    # Latin dışı öteki yazımlar (東国 / オスタニア) aynı karşılığı alır; çoğul gibi Latin biçimler almaz.
                    for y in [b for b in g.get("yazimlar") or [] if temizlik.latin_disi(b) and not ekli_ad_mi(b)
                              and not okunus_yazimi(yazim, b)]:
                        terimler.setdefault(y, hedef)
                if g.get("anlam"):
                    notlar[yazim] = g["anlam"]
                if g.get("siradan"):
                    siradanlar[yazim] = g["siradan"]
            japonca_adlari_esitle(terimler, {k: g.get("tur") for k, g in self.girdiler.items()})
            self._turetilen, self._kirli = (terimler, korunacak, degismez, notlar, siradanlar), False
        return self._turetilen

    @property
    def terimler(self):
        """kaynak -> hedef dildeki karşılık (hedef == kaynak: kod adı; olduğu gibi kalır, isteme yazılır)."""
        return self._turet()[0]

    @property
    def korunacak(self):
        """Her yerde olduğu gibi kalacak sözcükler (kurgu dili)."""
        return self._turet()[1]

    @property
    def degismez(self):
        """Olduğu gibi kalan adlar."""
        return self._turet()[2]

    @property
    def notlar(self):
        """yazım -> anlam notu (girdinin neyi kastettiği); [sozluk] anlam_notu kapalıysa görünümde boştur."""
        return self._turet()[3]

    @property
    def siradanlar(self):
        """yazım -> aynı yazımın sıradan sözcük olarak anlamı ("dusk"): böyle bir girdi yalnızca kastedilen şey
        için kullanılır, satır denetimine girmez."""
        return self._turet()[4]

    def elle_ekle(self, terimler=None, korunacak=(), degismez=()):
        """Elle verilmiş karşılıkları girdilere işler: verilen kazanır. Girdisi olmayan yazım girdi olur."""
        def yeni(tur, hedef):
            return {"tur": tur, "hedef": hedef, "yazimlar": [], "karar": "elle", "sayi": 0, "ornek": ""}
        for k, v in (terimler or {}).items():
            if ekli_ad_mi(k) or not v or self.terimler.get(k) == v:
                continue
            g = self.girdiler.setdefault(k, yeni("term", v))
            g.pop("terim", None)
            g["hedef"] = v
            if v == k or g.get("tur") in ("fictional", "common", "sound", None):
                g["tur"] = "codename" if v == k else "term"
            self._kirli = True
        for k in korunacak:
            if k not in self.korunacak:
                self.girdiler.setdefault(k, yeni("fictional", k)).update({"tur": "fictional", "hedef": k})
                self.girdiler[k].pop("terim", None)
                self._kirli = True
        for k in degismez:
            if k not in self.degismez:
                g = self.girdiler.setdefault(k, yeni("name", k))
                g.pop("terim", None)
                g["hedef"] = k
                if g.get("tur") in ("codename", "fictional", "common", "sound", None):
                    g["tur"] = "name"
                self._kirli = True

    def _eski_listeleri_isle(self, veri):
        """Eski düzendeki dosya: karşılıklar hem "girdiler"de hem üç listede (terimler, korunacak, degismez) duruyordu.
        Listeler girdilere işlenir ve bir sonraki kayıtta dosyadan kalkar. Listede olmayan karar terim değildir
        (bilinen unvan sözcüğü ya da elle silinmiş); listedeki karşılık girdidekinden farklıysa liste kazanır."""
        terimler = veri.get("terimler") or {}
        korunacak, degismez = list(veri.get("korunacak") or []), list(veri.get("degismez") or [])
        for yazim in list(self.terimler) + self.korunacak + self.degismez:
            if yazim in self.girdiler and yazim not in terimler and yazim not in korunacak and yazim not in degismez:
                self.girdiler[yazim]["terim"] = False
        self._kirli = True
        self.elle_ekle(terimler, korunacak, degismez)

    def gorunum(self, ayar):
        """Çeviride kullanılacak hâli: ayarla kapatılan bölümleri boşaltılmış bir kopya.

        Kapatılan özellik böylece istemde, isteklerde ve kontrollerde iz bırakmaz (sözlük dosyasında eski
        kayıt dursa bile); asıl sözlük değişmez. Açıp kapatmanın tek yeri burasıdır."""
        s, g = ayar["sozluk"], copy.copy(self)
        g.adlar = dict(self.adlar)                 # latin_ad bulduğu yazımı buraya not eder
        if not s["anlam_notu"]:
            g.girdiler = {k: {a: b for a, b in v.items() if a not in ("anlam", "siradan")}
                          for k, v in self.girdiler.items()}
            g._kirli = True
        if not s["terimler"]:
            g._turetilen, g._kirli = ({}, [], [], {}, {}), False
        if not s["karakterler"]:
            g.karakterler, g.adlar = {}, {}
        if not s["sahne_ozeti"]:
            g.sahneler = {}
        if not s["kurgu_tespiti"]:
            g.kurgu, g.latin = {}, {}
        if not acik(ayar, "hitap", "tablo"):
            g.hitap = {}
        # Unvan kaydı: önce hedef dilin tablosu, tabloda yoksa modelin bu yapım için verdiği karar.
        g.unvan_kaydi = {}
        if ayar["unvan"]["acik"]:
            tablo, model = unvan_tablosu(ayar), ayar["unvan"]["model_karari"]
            for unvan in self.unvanlar:
                if unvan.lower() in AKRABALIK:     # "Uncle Bilbo": sırası ve iyelik eki dile göre değişir, modele kalır
                    continue
                girdi = self.girdiler.get(unvan) or {}
                kalip = tablo.get(unvan) or (girdi.get("kalip") if model and girdi.get("tur") == "title" else "")
                if kalip:
                    g.unvan_kaydi[unvan] = kalip
        return g

    def girdi_ekle(self, yazim, tur, hedef, bicimler=(), karar="model", sayi=0, ornek="", kalip="", terim=True,
                   anlam="", siradan=""):
        """Terim sözlüğüne bir karar işler. İlk karar kalır: var olan girdi değişmez.

        hedef == "": sözlüğe girmeyen aday (sıradan sözcük); yalnızca "karar verildi" diye saklanır ki bir daha
        sorulmasın. hedef == yazim: olduğu gibi kalan ad. Kod adı olduğu gibi kalsa da isteme terim olarak yazılır
        ve satır satır denetlenir, çünkü model onu çevirmeye yatkındır.
        kalip: unvanın adla birlikte yazılışı ("Muhafız {ad}"). terim=False: karar saklanır ama terim listesine
        girmez (bilinen unvan sözcüğü: tek başına kullanımı modele bırakılır).
        anlam: girdinin bu yapımda neyi kastettiği (kısa not); istemde karşılığın yanına yazılır.
        siradan: yazım aynı zamanda sıradan bir sözcükse o anlamı ("dusk"); istemde iki anlam yan yana yazılır.
        """
        # Olduğu gibi kalan adlar da sayılır: yalnız terimlere bakılınca "Rivendell" varken "rivendell" ayrı girdi oluyordu.
        if yazim in self.girdiler or _anahtar(yazim) in {_anahtar(k) for k in list(self.terimler) + self.degismez
                                                         + self.korunacak}:
            return
        adsiz = " ".join(kalip.replace("{ad}", " ").split())
        if hedef and adsiz and adsiz.casefold() == hedef.casefold():
            hedef = adsiz                   # unvanın tek başına yazımı adla yazılışının harfini alır ("bay" / "Bay {ad}")
        self.girdiler[yazim] = {"tur": tur, "hedef": hedef, "yazimlar": list(bicimler), "karar": karar,
                                "sayi": sayi, "ornek": ornek, **({"kalip": kalip} if kalip else {}),
                                **({"anlam": anlam} if anlam and hedef else {}),
                                **({"siradan": siradan} if siradan and hedef else {}),
                                **({} if terim or not hedef else {"terim": False})}
        self._kirli = True

    def girdi_yaz(self, yazim, **alanlar):
        """Var olan girdinin alanlarını değiştirir; girdi yoksa verilen alanlarla kurar."""
        self.girdiler.setdefault(yazim, {"tur": "common", "hedef": "", "yazimlar": [], "karar": "model",
                                         "sayi": 0, "ornek": ""}).update(alanlar)
        self._kirli = True

    def girdi_sil(self, yazim):
        self.girdiler.pop(yazim, None)
        self._kirli = True

    def sesler(self, ayar):
        """Çevrilmeden Latin harfleriyle yazılacak ses sözcükleri: kaynak -> yazım. Ayar kapalıysa boştur."""
        if not ayar["sozluk"]["terimler"] or ayar["modlar"]["ses_sozcukleri"] != "orijinal":
            return {}
        return {k: g["hedef"] for k, g in self.girdiler.items() if g.get("tur") == "sound" and g.get("hedef")}

    def terimden_cikar(self, yazim):
        """Girdiyi terim ve ad listelerinden çıkarır (karar kaydı kalır): "Warden Hale" tek ad sanılmıştı,
        oysa "Warden" unvan; karşılığını artık unvan kaydı ile adın kendi girdisi verir."""
        if yazim in self.girdiler:
            self.girdiler[yazim]["hedef"] = ""
            self.girdiler[yazim].pop("anlam", None)
            self.girdiler[yazim].pop("siradan", None)
            self._kirli = True

    def kaydet(self):
        os.makedirs(os.path.dirname(self.yol) or ".", exist_ok=True)
        # Bu nesne açıkken kullanıcı sözlüğü elle düzeltmiş olabilir (Kodi: "Sözlüğü göster ve düzelt"): diskteki
        # "elle" kararlar korunur; yoksa o sırada süren bir çeviri kaydederken düzeltmeyi ezerdi.
        try:
            with open(self.yol, encoding="utf-8-sig") as f:
                diskteki = (json.load(f) or {}).get("girdiler") or {}
        except (OSError, ValueError, AttributeError):
            diskteki = {}
        for yazim, g in diskteki.items():
            if (isinstance(g, dict) and g.get("karar") == "elle" and self.girdiler.get(yazim) != g
                    and (self.girdiler.get(yazim) or {}).get("karar") != "elle"):
                self.girdiler[yazim] = g
                self._kirli = True
        dosyalar = {}
        for ad in dict.fromkeys(self.analiz_edilen + list(self.dil) + list(self.sahneler) + list(self.kurgu)):
            bilgi = {alan: kaynak[ad] for alan, kaynak in (("dil", self.dil), ("sahneler", self.sahneler),
                                                           ("kurgu", self.kurgu))
                     if kaynak.get(ad)}
            if ad not in self.analiz_edilen:        # deneme modunda yarım okundu: sonra yeniden analiz edilir
                bilgi["analiz"] = False
            dosyalar[ad] = bilgi
        veri = {"baslik": self.baslik, "girdiler": self.girdiler, "unvanlar": self.unvanlar, "hitap": self.hitap,
                "karakterler": self.karakterler, "adlar": self.adlar, "latin": self.latin, "dosyalar": dosyalar}
        with open(self.yol + ".yeni", "w", encoding="utf-8") as f:       # yazarken kesilirse eski dosya bozulmaz
            f.write(_json(veri, {"girdiler": 1, "hitap": 1, "karakterler": 1, "adlar": 1, "latin": 1, "dosyalar": 3}))
        os.replace(self.yol + ".yeni", self.yol)

    def latin_ad(self, etiket):
        """Konuşmacı etiketindeki adın (ヨル・フォージャー, ヨル) hitap tablosundaki Latin karşılığı; yoksa None."""
        ham = (etiket or "").strip()
        etiket = re.sub(r"\([^)]*\)", "", ham).strip()
        adaylar = (ham, etiket, etiket.split("・")[0], etiket.split(" ")[0])
        for aday in adaylar:
            if aday in self.adlar:
                return self.adlar[aday]
            if aday in self.karakterler:
                return aday
        # Model yazımı bildirmediyse: kana okunuşu karakter adlarından birine açıkça benziyorsa odur
        # (アーニャ ~ Anya, ロイド ~ Loid, ヘンダーソン ~ Henderson).
        if not self.karakterler:
            return None
        for parca in [x for x in re.split(r"[・ ]", etiket) if x]:
            okunus = temizlik.kana_latin(parca)
            if not okunus:
                continue
            # Karakter tam adla kayıtlıysa ("Yor Briar") etiketteki tek ad ("ヨル") adın sözcükleriyle ölçülür;
            # iki karakterin ortak soyadı iki adaya eşit puan verir, eşleşme olmaz.
            puanlar = sorted(((max(SequenceMatcher(None, _ses(okunus), _ses(s)).ratio() for s in [ad] + ad.split()), ad)
                              for ad in self.karakterler), reverse=True)
            if puanlar[0][0] >= 0.72 and (len(puanlar) == 1 or puanlar[0][0] - puanlar[1][0] >= 0.1):
                self.adlar[parca] = puanlar[0][1]
                return puanlar[0][1]
        return None

    def karakter_adlari(self):
        """Kaynakta geçen, kişi adı olduğu bilinen yazımlar: karşılığı bir karakter adı olan terimler."""
        return {k for k, v in self.terimler.items() if v in self.karakterler}

    def tutarsizliklar(self):
        """Sözlüğün kendi içindeki çelişkiler (yalnız bildirilir, düzeltilmez; koşu günlüğüne yazılır):
        aynı sözün yalnız harf büyüklüğü ya da tanımlığı farklı iki girdisi; Latin harfli kısa terimin karşılığı, onu
        içeren uzun terimin karşılığında geçmiyor ("Dark Lord" -> "Karanlıklar Efendisi" / "Dark Lord Sauron" ->
        "Karanlık Lord Sauron")."""
        sorunlar, anahtarlar = [], {}
        for yazim, g in self.girdiler.items():
            if g.get("hedef") and g.get("tur") != "sound":
                anahtarlar.setdefault(_anahtar(yazim), []).append(yazim)
        for grup in anahtarlar.values():
            if len(grup) > 1:
                sorunlar.append("aynı söz birden çok girdi: " + ", ".join(
                    f"{y!r} -> {self.girdiler[y]['hedef']!r}" for y in grup))
        terimler = {k: v for k, v in self.terimler.items() if k.isascii() and v and v != k}
        for kisa, hedef in terimler.items():
            govde = _duz(hedef)[:max(4, len(hedef) - 2)]            # ek alabilir: "Dağı" / "Dağı'nın"
            desen = re.compile(r"(?<![\w'’-])" + re.escape(_anahtar(kisa)) + r"(?![\w'’-])")
            for uzun, uzun_hedef in terimler.items():
                if (uzun != kisa and len(_anahtar(kisa)) >= 4 and _anahtar(uzun) != _anahtar(kisa)
                        and desen.search(_anahtar(uzun)) and govde not in _duz(uzun_hedef)):
                    sorunlar.append(f"{kisa!r} -> {hedef!r}, ama {uzun!r} -> {uzun_hedef!r}")
        return sorunlar

    def kurulmus(self):
        """Sözlük daha önceki bölümlerle kurulmuş mu (en az bir dosya incelendi, en az 20 karar var)? Kurulmuş
        sözlükte sözlük adımının istekleri bir oy az sorulur: yeni adaylar azdır, adlar ve hitap zaten bellidir."""
        return bool(self.analiz_edilen) and len(self.girdiler) >= 20

    def karar_verilenler(self):
        """Hakkında karar verilmiş tüm kaynak sözcükler (karşılaştırma anahtarı olarak)."""
        yazimlar = [y for k, g in self.girdiler.items() for y in [k] + list(g.get("yazimlar") or [])]
        return {_anahtar(k) for k in list(self.terimler) + self.korunacak + self.degismez + yazimlar}

    def birlestir(self, dosya, analiz, satirlar, latin_zorunlu, gunluk):
        """İlk geçişin terim dışındaki sonuçlarını (hitap, sahne, iç ses, karakter, kurgu dili) sözlüğe işler.
        Terimler terim.py tarafından girdi_ekle ile işlenir."""
        tum_metin = "\n".join(s.metin for s in satirlar).lower()
        for h in analiz.get("address") or []:
            if not isinstance(h, dict) or h.get("form") not in ("informal", "formal"):
                continue
            kimden, kime = str(h.get("from") or "").strip(), str(h.get("to") or "").strip()
            if not kimden or not kime:
                continue
            anahtar, deger = f"{kimden} -> {kime}", "samimi" if h["form"] == "informal" else "resmi"
            if anahtar in self.hitap:   # ilk karar kalır; değiştirmek istersen sözlükten elle düzelt
                if self.hitap[anahtar] != deger:
                    gunluk.uyari(dosya, f"hitap çelişkisi: {anahtar} sözlükte {self.hitap[anahtar]}, bu bölümde {deger} görünüyor")
                continue
            self.hitap[anahtar] = deger
        dil = str(analiz.get("language") or "").strip().lower()[:8]
        if dil:
            self.dil[dosya] = dil
        sahneler = []
        for sh in analiz.get("scenes") or []:
            try:
                ilk, son, ozet = int(sh["from"]), int(sh["to"]), str(sh["summary"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if ozet and 1 <= ilk <= son <= len(satirlar):
                sahneler.append([ilk, son, ozet[:400]])   # ayrıntılı özet iki cümle; 240 ikinciyi kesiyordu (S54)
        if sahneler:
            self.sahneler[dosya] = sahneler
        for k in analiz.get("characters") or []:
            if not isinstance(k, dict) or len(self.karakterler) >= 80:      # seri sözlüğü birkaç filmi taşır
                continue
            ad, notu = str(k.get("name") or "").strip(), str(k.get("note") or "").strip()
            yazim = str(k.get("source") or "").strip()
            if ad and yazim and yazim != ad and not temizlik.latin_disi(ad) and yazim.lower() in tum_metin:
                self.adlar.setdefault(yazim, ad)       # etiketteki ad -> Latin ad (hitap tablosuyla eşleşsin)
            if ad and ad not in self.karakterler and not temizlik.latin_disi(ad):
                self.karakterler[ad] = notu[:160]
        isaretler = {}
        for k in analiz.get("fictional") or []:
            try:
                i, parca = int(k["id"]) - 1, str(k["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not parca or not 0 <= i < len(satirlar):
                continue
            if parca not in satirlar[i].metin:
                gunluk.hata(dosya, f"satır {i + 1}: kurgu dili işareti kaynakta yok, yok sayıldı: {parca!r}")
                continue
            isaretler.setdefault(str(i + 1), [])
            if parca not in isaretler[str(i + 1)]:
                isaretler[str(i + 1)].append(parca)
            roma = str(k.get("romanized") or "").strip()
            if roma and temizlik.latin_disi(parca) and not temizlik.latin_disi(roma):
                self.latin[parca] = roma
        self.kurgu[dosya] = isaretler
        if dosya not in self.analiz_edilen:
            self.analiz_edilen.append(dosya)


def _araliklar(metin, sozluk, dosya, satir_no, modlar):
    """Korunacak parçaların (başlangıç, bitiş) aralıklarını döndürür; çakışanları eler."""
    adaylar = []
    if modlar["kurgu_dili"] != "ceviri" and sozluk is not None:
        for parca in (sozluk.kurgu.get(dosya) or {}).get(str(satir_no), []):
            bas = metin.find(parca)
            while bas != -1:
                adaylar.append((bas, bas + len(parca)))
                bas = metin.find(parca, bas + len(parca))
    if modlar["kurgu_dili"] != "ceviri" and sozluk is not None:          # sözlükteki kurgu dili sözcükleri
        for sozcuk in sozluk.korunacak:
            for m in re.finditer(r"(?<!\w)" + re.escape(sozcuk) + r"(?!\w)", metin):
                adaylar.append(m.span())
    if modlar["anime_ekleri"] != "ceviri":
        adaylar.extend(m.span() for m in EK_DESENI.finditer(metin))
    secilen = []
    for bas, bit in sorted(adaylar, key=lambda a: (-(a[1] - a[0]), a[0])):
        if all(bit <= b or bas >= e for b, e in secilen):
            secilen.append((bas, bit))
    return sorted(secilen)


def isaretle(metin, sozluk, dosya, satir_no, modlar):
    """(işaretli metin, korunan parçalar, tamamı korunuyor mu) döndürür."""
    araliklar = _araliklar(metin, sozluk, dosya, satir_no, modlar)
    if not araliklar:
        return metin, [], False
    parcalar, cikti, kalan, son = [], [], [], 0
    for bas, bit in araliklar:
        cikti.append(metin[son:bas])
        kalan.append(metin[son:bas])
        cikti.append("⟦" + metin[bas:bit] + "⟧")
        parcalar.append(metin[bas:bit])
        son = bit
    cikti.append(metin[son:])
    kalan.append(metin[son:])
    artan = temizlik.ETIKET.sub("", "".join(kalan))
    tamami = not any(k.isalnum() for k in artan)
    return "".join(cikti), parcalar, tamami
