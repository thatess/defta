"""Çeviri işi (servis tarafında çalışır): hızlı çeviri, kaliteli çeviri, kaldığı yerden devam, durdurma.

Hızlı çeviri: bulunduğun yerden (ekrandaki satır dahil) seçilen süre kadarı sözlük beklemeden çevrilir; dizinin önceki
bölümlerinden sözlük varsa o kullanılır; kalanı orijinal yüklenir. Düşünmeden ve küçük parçalarla gider (birkaç saniye).
Kaliteli çeviri: önce yapımın sözlüğü (1. geçiş), sonra bütün dosya. Sözlük çevirinin başında işin klasörüne
kopyalanır: iş yarıda kalırsa başladığı sözlükle sürer (arada başka bölüm sözlüğe ad eklese de baştan başlamaz).
Yarım işin biten satırları çekirdeğin kaydında durur: para iki kez gitmez.
İkisi aynı anda başlar. Kaliteli çeviri bitince iş klasörünü silmeden önce süren hızlı çeviriyi bekler: hızlı çeviri hep
önce yüklenir, dosyası da yüklenmeden silinmez.
"""
import copy
import glob
import json
import os
import re
import shutil
import threading
import time

from defta import ayar as cekirdek_ayar
from defta import srt as cekirdek_srt
from defta import temizlik
from defta.analiz import dizi_analiz
from defta.cumle import BIRLESTI
from defta.dosya import dosya_cevir, dosya_oku
from defta.gunluk import Gunluk
from defta.istem import istem_olustur
from defta.model import Iptal, Kayitli, Model, ModelHatasi
from defta.sozluk import Sozluk

from . import kayit

# Aynı yapımın sözlüğüne aynı anda iki iş yazmasın (izlenen bölüm ve sıradaki bölümün ön çevirisi)
_sozluk_kilitleri = {}
_kilitler_kilidi = threading.Lock()


def _sozluk_kilidi(yol):
    with _kilitler_kilidi:
        return _sozluk_kilitleri.setdefault(yol, threading.Lock())


class BakiyeBitti(Exception):
    pass


class CeviriHatasi(Exception):
    pass


def _model_hatasi(e):
    """Düzelmeyecek model hatası: kota/kredi bittiyse BakiyeBitti, değilse CeviriHatasi."""
    yazi = str(e)
    if "kredi" in yazi.lower() or "quota" in yazi.lower():
        return BakiyeBitti(yazi)
    return CeviriHatasi(yazi)


# Latin harfli hedef diller; ötekilerde (ja, ru, uk, el, ar, fa, ko, zh) çıktı kendi alfabesiyle yazılır
LATIN_DILLER = {"tr", "en", "de", "fr", "es", "it", "pt", "pt-BR", "nl", "pl", "az", "sv"}

# Hızlı çeviri: parçalar aynı anda gider, süreyi en yavaşı belirler. Düşünmeyle (low) 2 satırlık parça bile 6,8 sn
# sürüyordu (16.11.2026 ölçümü): süreyi uzunluk değil düşünme belirliyordu. Düşünmeden giden istekte süre çıktının
# uzunluğuna bağlı olduğu için parçalar da küçük. Hızlı çeviri ekranda yalnız kaliteli çeviri gelene kadar kalır.
HIZLI_DUSUNME = "none"
HIZLI_PARCA = 8             # parça başına altyazı; parça cümle sonundan kesilir (en çok ~10 altyazı)
HIZLI_BEKLEME = 60          # kaliteli çeviri bitince süren hızlı çeviriyi en çok bu kadar bekler (sn)


def cekirdek_ayari(ayarlar):
    """Eklentinin ayarlarından çekirdeğin ayarı. ayarlar: hedef_dil ve biçim ayarları (b_*)."""
    ses = ayarlar.get("b_ses_etiketi", "sil")
    hedef = ayarlar.get("hedef_dil", "tr")
    ustune = {
        "ceviri": {"hedef_dil": hedef, "cikti_alfabesi": "latin" if hedef in LATIN_DILLER else "serbest"},
        "modlar": {"sarki": ayarlar.get("b_sarki", "orijinal"), "konusmaci_tiresi": ayarlar.get("b_tire", "korunsun"),
                   "italik": ayarlar.get("b_italik", "korunsun"), "koseli_not": ses, "parantez_not": ses,
                   "isim_etiketi": ayarlar.get("b_konusan", "sil"), "ekran_yazisi": ayarlar.get("b_ekran", "cevir"),
                   "kurgu_dili": ayarlar.get("b_kurgu", "orijinal"), "anime_ekleri": ayarlar.get("b_hitap_ekleri", "orijinal"),
                   "ses_sozcukleri": ayarlar.get("b_ses_sozcukleri", "orijinal"),
                   "ustte_gosterme": "korunsun" if ayarlar.get("b_ustte") == "korunsun" else "sil",
                   "reklam": ayarlar.get("b_reklam", "sil")},
        "hitap": {"acik": bool(ayarlar.get("b_sen_siz", True))},
        # Bir bölüm 24 istekte zaten aynı anda gider; 64 iş parçacığı (yedek kopyalarla ~130) Shield'ı yorar
        "model": {"es_zamanli_istek": 24},
    }
    return cekirdek_ayar.ayar_birlestir(ustune)


class CeviriIsi:
    """Bir altyazının bir hedef dile çevirisi. Klasörü: isler/<is_id>/ (is.json, kaynak, kayıtlar)."""

    def __init__(self, durum, bilgi, anahtar, ayarlar):
        self.durum, self.bilgi, self.anahtar = durum, bilgi, anahtar
        self.ayar = cekirdek_ayari(ayarlar)
        self.klasor = durum.yol("isler", bilgi["is_id"])
        self.kaynak_yol = os.path.join(self.klasor, bilgi["kaynak_dosya"])
        self.goreli = bilgi["goreli"]
        self._modeller = []                 # hızlı ve kaliteli çeviri aynı anda sürer: durdurma hepsini durdurur
        self._modeller_kilidi = threading.Lock()
        self.iptal = threading.Event()
        self._sert = False
        self.yuzde = 0.0
        self.basla = None
        self._hizli_kosul = threading.Condition()
        self._hizli_suren = 0               # süren hızlı çeviri akışı (sonucunun eklentiye verilmesi dahil)
        self._hizli_kilidi = threading.Lock()
        self._gunluk_nesnesi = None
        self._gunluk_kilidi = threading.Lock()

    # --------------------------------------------------------------------------------------- yardımcılar
    def _model(self, ayar=None):
        m = Model(ayar or self.ayar, self.anahtar)
        with self._modeller_kilidi:
            self._modeller.append(m)
            if self.iptal.is_set():
                if self._sert:
                    m.iptal.set()
                else:
                    m.yavas_durdur()
        return m

    def durdur(self, sert=False):
        """Yeni istek gitmez; yolda olanların yanıtı beklenip kayda yazılır (madde 24: ödenen boşa gitmez).
        sert=True (bakım silecek): yoldakiler beklenmez. Kodi kapanırken servis ayrıca bağlantıları keser."""
        with self._modeller_kilidi:
            self.iptal.set()
            self._sert = self._sert or sert
            for m in self._modeller:
                if self._sert:
                    m.gelenleri_bekle = False
                    m.iptal.set()
                else:
                    m.yavas_durdur()

    def _gunluk(self):
        """Bu çalıştırmanın kaydı: hızlı ve kaliteli çeviri aynı anda sürdüğü için ikisi tek klasöre yazar."""
        with self._gunluk_kilidi:
            if self._gunluk_nesnesi is None:
                self._gunluk_nesnesi = Gunluk(self.durum.yol("kayitlar"))
            return self._gunluk_nesnesi

    # ----------------------------------------------------------------------------- hızlı akışın beklenmesi
    def hizli_basladi(self):
        """Servis hızlı çeviri akışını başlatmadan önce çağırır (akış: çeviri + sonucun eklentiye verilmesi)."""
        with self._hizli_kosul:
            self._hizli_suren += 1

    def hizli_bitti(self):
        with self._hizli_kosul:
            self._hizli_suren = max(0, self._hizli_suren - 1)
            self._hizli_kosul.notify_all()

    def hizli_bitsin(self, azami=HIZLI_BEKLEME):
        """Süren hızlı çeviri akışı bitene kadar bekler (en çok azami sn)."""
        son = time.time() + azami
        with self._hizli_kosul:
            while self._hizli_suren and time.time() < son:
                self._hizli_kosul.wait(min(1.0, max(0.05, son - time.time())))

    def _oku(self):
        return dosya_oku(self.kaynak_yol, self.ayar)

    def kaydet(self, **alanlar):
        self.bilgi.update(alanlar)
        if not os.path.isdir(self.klasor):          # iş silindi (bakım) ya da bitti: klasör yeniden yaratılmaz
            return
        kayit.yaz(os.path.join(self.klasor, "is.json"), self.bilgi)

    def oturum_maliyeti(self):
        with self._modeller_kilidi:
            return sum(m.maliyet() for m in self._modeller)

    def oturumu_kaydet(self):
        """İş durunca ya da hata verince bu oturumun maliyeti ve süresi işin kaydına eklenir: bitince bildirilen
        maliyet ve süre bütün oturumların toplamıdır. Süren hızlı çeviri önce beklenir (onun maliyeti de girsin)."""
        self.hizli_bitsin(30)
        if not os.path.isdir(self.klasor):
            return
        sure = time.time() - self.basla if self.basla else 0
        self.kaydet(onceki_maliyet=self.bilgi.get("onceki_maliyet", 0) + self.oturum_maliyeti(),
                    onceki_sure=self.bilgi.get("onceki_sure", 0) + sure)

    def _srt_yaz(self, yol, satirlar):
        satirlar = sorted(satirlar, key=lambda s: (s.bas, s.bit))
        for n, s in enumerate(satirlar, 1):
            s.no = n
        return kayit.metin_yaz(yol, cekirdek_srt.srt_yaz(satirlar))

    def _ozgun(self, s):
        metin = temizlik.isaretsiz(s.metin) if s.sarki_eki else s.metin
        return cekirdek_srt.Satir(0, s.bas, s.bit, metin)

    def _sozluk_yolu(self):
        return self.durum.sozluk_yolu(self.bilgi["yapim"], self.ayar["ceviri"]["hedef_dil"])

    def _basvurulu(self, satirlar):
        return any(s.ref for s in satirlar)

    # -------------------------------------------------------------------------------------------- kayıt
    def onbellek(self):
        """Kaliteli çevirinin yarım kaydı: satır sırası -> çeviri (yoksa boş). Birden çok kayıt varsa en yenisi
        (ayar değişince eskileri kaliteli() siler)."""
        kayitlar = sorted(glob.glob(os.path.join(self.klasor, "onbellek", "*.json")), key=_degisme_zamani, reverse=True)
        for yol in kayitlar:
            veri = kayit.oku(yol, {}) or {}
            try:
                return {int(k): v for k, v in (veri.get("sonuc") or {}).items()}
            except (ValueError, AttributeError):
                return {}
        return {}

    def _ayar_imzasi(self):
        return kayit.ozet(json.dumps(self.ayar, sort_keys=True, ensure_ascii=False, default=str), 20)

    def ayar_degistiyse_sifirla(self):
        """Çeviriyi etkileyen bir ayar iş yarıdayken değiştiyse iş baştan başlar (madde 26): yarım kayıtlar ve
        sözlüğün başlangıç kopyası silinir."""
        imza = self._ayar_imzasi()
        eski = self.bilgi.get("ayar_imzasi")
        if eski and eski != imza:
            for ad in ("onbellek", "hizli_kayit"):
                shutil.rmtree(os.path.join(self.klasor, ad), ignore_errors=True)
            kayit.sil(os.path.join(self.klasor, "sozluk_anlik.json"))
        if eski != imza:
            self.kaydet(ayar_imzasi=imza)

    def _taban(self, satirlar, biten, haric=()):
        """Biten satırlar çevirisiyle, kalanı orijinal; haric'teki sıralar (yeniden çevrilecekler) atlanır."""
        modlar, latin = self.ayar["modlar"], self.ayar["ceviri"]["cikti_alfabesi"] == "latin"
        cikti = []
        for i, s in enumerate(satirlar):
            if i in haric:
                continue
            ceviri = biten.get(i)
            if ceviri == BIRLESTI:
                continue
            if ceviri is None:
                cikti.append(self._ozgun(s))
                continue
            metin = temizlik.son_temizlik(ceviri, modlar, latin)
            if s.sarki_eki:
                metin = temizlik.isaretsiz(metin)
            if metin:
                cikti.append(cekirdek_srt.Satir(0, s.bas, s.bit, metin))
        return cikti

    def kismi(self, satirlar=None):
        """Biten satırlar çevirisiyle, kalanı orijinal: kismi.srt (yarım iş yeniden açılınca hemen yüklenir)."""
        if satirlar is None:
            _, satirlar, _ = self._oku()
        biten = self.onbellek()
        return self._srt_yaz(os.path.join(self.klasor, f"kismi.{self._dil_eki()}.srt"), self._taban(satirlar, biten)), len(biten)

    # ------------------------------------------------------------------------------------------ hızlı
    def hizli(self, baslangic_sn, sure_dk):
        """Bulunduğun yerden sure_dk dakika çevrilir; daha önce çevrilmiş satırlar (kaliteli çevirinin kaydı) olduğu
        gibi kalır, kalanı orijinal. Aralığın önündeki ve arkasındaki satırlar modele yalnız bağlam olarak gider
        (kaliteli çevirideki gibi): bölümün ortasından başlayınca ilk satırlar da öncesini görerek çevrilir.
        Aynı işin iki hızlı çevirisi (süren iş yeniden seçildi) sırayla çalışır: ikisi aynı dosyalara yazar.
        Dönüş: hizli.srt yolu."""
        with self._hizli_kilidi:
            return self._hizli_cevir(baslangic_sn, sure_dk)

    def _hizli_cevir(self, baslangic_sn, sure_dk):
        self.basla = self.basla or time.time()
        icerik, satirlar, toplam = self._oku()
        bas, son = int(baslangic_sn * 1000), int((baslangic_sn + sure_dk * 60) * 1000)
        biten = self.onbellek()
        secili_idx = [i for i, s in enumerate(satirlar) if s.bit > bas and s.bas < son and i not in biten]
        if not secili_idx:
            return self._srt_yaz(os.path.join(self.klasor, f"hizli.{self._dil_eki()}.srt"), self._taban(satirlar, biten))
        secili = [satirlar[i] for i in secili_idx]
        ayar = copy.deepcopy(self.ayar)
        ayar["kontrol"]["supheli_satir_kontrolu"] = False       # hız için: kaliteli çeviri zaten denetler
        ayar["kontrol"]["tekrar_eden_sahne"] = False
        ayar["model"]["dusunme_seviyesi"] = HIZLI_DUSUNME       # yeniden denemede çekirdek "low" ile sorar
        ayar["parca"]["altyazi_sayisi"] = HIZLI_PARCA
        c = ayar["parca"]
        ilk = max(0, secili_idx[0] - c["baglam_once"])
        dilim = satirlar[ilk:secili_idx[-1] + 1 + c["baglam_sonra"]]
        cevrilecek = [i - ilk for i in secili_idx]
        sozluk_yolu = self._sozluk_yolu()
        gorunum = Sozluk(sozluk_yolu).gorunum(ayar) if os.path.exists(sozluk_yolu) else None
        sistem = istem_olustur(ayar, gorunum, self._basvurulu(secili))
        hedef = os.path.join(self.klasor, "hizli_parca.srt")
        gunluk = self._gunluk()
        model = self._model(ayar)
        # Kayıt, çevrilen satır kümesine özgü: başka yerde süren hızlı çeviri eski parçaları yanlış satırlara koymasın
        goreli = self.goreli + ".hizli." + kayit.ozet(",".join(map(str, secili_idx)), 12)
        try:
            dosya_cevir(self.kaynak_yol, hedef, goreli, ayar, model, sistem, gunluk,
                        os.path.join(self.klasor, "hizli_kayit"), None, gorunum, (icerik, dilim, toplam), cevrilecek)
        except Iptal:
            if isinstance(getattr(model, "hata", None), ModelHatasi):   # asıl neden kota/model hatası
                raise _model_hatasi(model.hata) from None
            raise
        except ModelHatasi as e:
            raise _model_hatasi(e) from None
        with open(hedef, encoding="utf-8") as f:
            parca = cekirdek_srt.srt_ayristir(f.read())
        taban = self._taban(satirlar, biten, set(secili_idx))       # seçimle aynı anlık kayıt: satır kaybolmaz
        return self._srt_yaz(os.path.join(self.klasor, f"hizli.{self._dil_eki()}.srt"), taban + parca)

    # ---------------------------------------------------------------------------------------- kaliteli
    def sozluk_adimi(self, satirlar, gunluk, model):
        """Yapımın sözlüğü (1. geçiş) ve işin başladığı sözlüğün kopyası. Kopya varsa (yarım iş) dokunulmaz."""
        anlik = os.path.join(self.klasor, "sozluk_anlik.json")
        if os.path.exists(anlik):
            return anlik
        yol = self._sozluk_yolu()
        with _sozluk_kilidi(yol):
            sozluk = Sozluk(yol)
            sozluk.baslik = sozluk.baslik or self.bilgi.get("baslik", "")
            if sozluk.eski_duzen:
                sozluk.kaydet()
                sozluk.eski_duzen = False
            yenile = bool(self.bilgi.get("sozlukle_yenile"))
            if yenile:
                self._sozlukten_cikar(sozluk, satirlar)
            if self.ayar["sozluk"]["otomatik"] and self.goreli not in sozluk.analiz_edilen:
                etiket = f"{sozluk.baslik} / {self.bilgi.get('bolum_etiketi', '')}".strip(" /")
                # Ödenmiş her yanıt iş klasörüne yazılır: adım yarıda durursa (video kapandı, başka altyazı seçildi)
                # yeniden başlayınca yalnız eksik istekler modele gider.
                kayitli = Kayitli(model, os.path.join(self.klasor, "analiz_kayit.json"))
                dizi_analiz([(self.goreli, satirlar, etiket)], self.ayar, kayitli, sozluk, gunluk)
            if yenile:
                self._eskileri_geri_koy(sozluk)
            if not os.path.exists(yol):
                sozluk.kaydet()
            kayit.kopyala(yol, anlik)
        return anlik

    def _sozlukten_cikar(self, sozluk, satirlar):
        """Sözlükle birlikte yenile: bu bölümde geçen adların kararları silinir, bölüm yeniden analiz edilir. Ad tam
        sözcük olarak aranır ("Al" "Also" içinde sayılmaz). Silinenler iş klasöründe saklanır: yeniden karar
        verilemeyenler analizden sonra geri konur (_eskileri_geri_koy)."""
        metin = "\n".join(s.metin for s in satirlar)
        silinen = {}
        for yazim in list(sozluk.girdiler):
            if yazim and re.search(r"(?<!\w)" + re.escape(yazim) + r"(?!\w)", metin):
                silinen[yazim] = sozluk.girdiler[yazim]
                sozluk.girdi_sil(yazim)
        # Yarıda kalıp yeniden başlayan işte önceki turun sakladıkları korunur (sözlükten çoktan silinmişlerdir)
        eski_yolu = os.path.join(self.klasor, "sozluk_eski.json")
        kayit.yaz(eski_yolu, dict(silinen, **(kayit.oku(eski_yolu, {}) or {})))
        if self.goreli in sozluk.analiz_edilen:
            sozluk.analiz_edilen.remove(self.goreli)
        sozluk.kaydet()

    def _eskileri_geri_koy(self, sozluk):
        """Yeniden analizde karar verilemeyen girdinin eski kararı kalır: başka bölümde öğrenilmiş, bu bölümde yalnız
        bir kez geçen terim (aday bile olmaz) sözlükten kaybolmasın, sonraki bölümler başka türlü çevirmesin."""
        eski = kayit.oku(os.path.join(self.klasor, "sozluk_eski.json"), {}) or {}
        geri = [y for y in eski if y not in sozluk.girdiler]
        for yazim in geri:
            sozluk.girdi_yaz(yazim, **eski[yazim])
        if geri:
            sozluk.kaydet()

    def kaliteli(self, ilerleme=None):
        """Bütün dosya. Bitince çeviriler/<içerik>/<kimlik>.srt yazılır, iş klasörü silinir. Dönüş: (yol, bilgi)."""
        self.basla = self.basla or time.time()
        self.ayar_degistiyse_sifirla()
        icerik, satirlar, toplam = self._oku()
        gunluk = self._gunluk()
        model = self._model()

        def ilerle(biten, toplam_satir):
            self.yuzde = 100.0 * biten / max(toplam_satir, 1)
            if ilerleme:
                ilerleme(self.yuzde)
        try:
            anlik = self.sozluk_adimi(satirlar, gunluk, model)
            gorunum = Sozluk(anlik).gorunum(self.ayar)
            sistem = istem_olustur(self.ayar, gorunum, self._basvurulu(satirlar))
            gunluk.istem(self.goreli, sistem)
            gecici = os.path.join(self.klasor, "kaliteli.srt")
            dosya_cevir(self.kaynak_yol, gecici, self.goreli, self.ayar, model, sistem, gunluk,
                        os.path.join(self.klasor, "onbellek"), ilerle, gorunum, (icerik, satirlar, toplam))
        except Iptal:
            if isinstance(getattr(model, "hata", None), ModelHatasi):   # başka parçanın Iptal'i önce gelebilir
                raise _model_hatasi(model.hata) from None
            raise
        except ModelHatasi as e:
            raise _model_hatasi(e) from None
        if isinstance(getattr(model, "hata", None), ModelHatasi):
            raise _model_hatasi(model.hata)
        return self._bitir(gecici, model)

    def _dil_eki(self):
        return kayit.kodi_dil_eki(self.ayar["ceviri"]["hedef_dil"])

    def _bilgi_yolu(self, klasor, kimlik):
        """Bitmiş çevirinin bilgi dosyası: <kimlik>.json. O ad aynı kaynağın başka hedef dildeki çevirisininse
        <kimlik>.<dil>.json: öteki dilin çevirisi yerinde kalır (yalnız aynı dildeki eski sürümün yerine geçilir)."""
        hedef = self.ayar["ceviri"]["hedef_dil"]
        yollar = [os.path.join(klasor, kimlik + ".json"), os.path.join(klasor, f"{kimlik}.{self._dil_eki()}.json")]
        for yol in yollar:
            if (kayit.oku(yol, {}) or {}).get("hedef_dil") == hedef:
                return yol
        return yollar[1] if os.path.exists(yollar[0]) else yollar[0]

    def _bitir(self, gecici, model):
        """Bitmiş çeviri: çeviriler/<içerik>/AI.<kimlik>_<zaman>.<dil>.srt (adı her sürümde yeni: eskisi Kodi'de açıkken
        üstüne yazılamaz) + kaynağın kopyası (yeniden çevirmek için) + bilgi dosyası. İş klasörü silinir. Kimliğin
        tiresi atılır: Kodi dili dosya adından okuyor, "os-..." Osetçe sanılıyordu. Süren hızlı çeviri önce bitsin:
        sonucu önce yüklenir, dosyası klasörle birlikte yüklenmeden silinmez."""
        self.hizli_bitsin()
        klasor = self.durum.ceviri_klasoru(self.bilgi["icerik"])
        kimlik = self.bilgi["ceviri_kimligi"]
        os.makedirs(klasor, exist_ok=True)
        dosya = f"AI.{kimlik.replace('-', '_')}_{int(time.time())}.{self._dil_eki()}.srt"
        hedef = os.path.join(klasor, dosya)
        os.replace(gecici, hedef)
        kaynak_dosya = kimlik + ".kaynak" + os.path.splitext(self.kaynak_yol)[1]
        shutil.copyfile(self.kaynak_yol, os.path.join(klasor, kaynak_dosya))
        bilgi_yolu = self._bilgi_yolu(klasor, kimlik)
        eski = (kayit.oku(bilgi_yolu, {}) or {}).get("dosya")
        sure = time.time() - (self.basla or time.time()) + self.bilgi.get("onceki_sure", 0)
        bilgi = {"kimlik": kimlik, "icerik": self.bilgi["icerik"], "hedef_dil": self.ayar["ceviri"]["hedef_dil"],
                 "kaynak": self.bilgi["kaynak"], "kaynak_dil": self.bilgi.get("kaynak_dil", ""), "zaman": time.time(),
                 "sure": sure, "maliyet": self.oturum_maliyeti() + self.bilgi.get("onceki_maliyet", 0),
                 "yapim": self.bilgi["yapim"], "goreli": self.goreli, "dosya": dosya, "kaynak_dosya": kaynak_dosya,
                 "baslik": self.bilgi.get("baslik", ""), "bolum_etiketi": self.bilgi.get("bolum_etiketi", ""),
                 "video_karma": self.bilgi.get("video_karma"), "on_ceviri": bool(self.bilgi.get("on_ceviri"))}
        kayit.yaz(bilgi_yolu, bilgi)
        if eski and eski != dosya:
            kayit.sil(os.path.join(klasor, eski))         # Kodi'de açıksa silinemez; kendiliğinden temizlik alır
        shutil.rmtree(self.klasor, ignore_errors=True)       # hızlı çevirinin ve yarım işin kalıntıları
        bilgi["yol"] = hedef
        return hedef, bilgi


def _degisme_zamani(yol):
    try:
        return os.path.getmtime(yol)
    except OSError:
        return 0


def _gecerli(durum, bilgi):
    """Kaynak dosyası duran iş (bakımda silinirken geç biten bir işin yeniden yarattığı yarım klasör sayılmaz)."""
    return bool(bilgi and bilgi.get("is_id") and bilgi.get("kaynak_dosya")
                and os.path.exists(durum.yol("isler", bilgi["is_id"], bilgi["kaynak_dosya"])))


def yarim_isler(durum):
    """Kaydı duran (bitmemiş) işler."""
    sonuc = []
    for yol in glob.glob(durum.yol("isler", "*", "is.json")):
        bilgi = kayit.oku(yol)
        if _gecerli(durum, bilgi):
            sonuc.append(bilgi)
    return sonuc


def is_bilgisi(durum, is_id):
    bilgi = kayit.oku(durum.yol("isler", is_id, "is.json"))
    return bilgi if _gecerli(durum, bilgi) else None


def is_kur(durum, bilgi, kaynak_metni, uzanti):
    """İşin klasörünü ve kaynak dosyasını yazar (eklenti tarafında, indirme sırasında)."""
    klasor = durum.yol("isler", bilgi["is_id"])
    os.makedirs(klasor, exist_ok=True)
    bilgi["kaynak_dosya"] = "kaynak" + uzanti
    kayit.metin_yaz(os.path.join(klasor, bilgi["kaynak_dosya"]), kaynak_metni)
    bilgi.setdefault("olusturma", time.time())
    kayit.yaz(os.path.join(klasor, "is.json"), bilgi)
    return bilgi
