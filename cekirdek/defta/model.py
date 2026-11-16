"""OpenAI uyumlu sohbet API'si istemcisi. Yalnızca standart kütüphane kullanır."""
import hashlib
import http.client
import json
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request


# Python 3.8'de (Kodi 21) zaman aşımı socket.timeout'tur, TimeoutError değil; 3.10'dan beri ikisi aynı sınıf.
ZAMAN_ASIMI = (TimeoutError, socket.timeout)


class ModelHatasi(Exception):
    """Düzelmeyecek hata (yanlış anahtar, bilinmeyen model...). Çalışmayı durdurur."""


class Ret(ModelHatasi):
    """Model içeriği reddetti ya da boş döndü. Yeniden denenebilir."""
    icerik = True       # ret içerik yüzünden (süzgeç, modelin reddi): model ve adres çalışıyor demektir


def _bekle(olay, sure):
    """Yeniden denemeden önceki bekleme; durdurulunca hemen biter (testler bunu susturur)."""
    olay.wait(sure)


class Iptal(Exception):
    """Çalışma durduruldu (Ctrl+C ya da başka bir yerdeki ölümcül hata): yeni istek gönderilmez, bekleyen bırakılır."""


def json_coz(ham):
    """Modelin JSON yanıtını çözer; model yanıtı ``` çitinin içine yazdıysa çit atılır."""
    ham = ham.strip()
    if ham.startswith("```"):
        ham = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", ham)
    return json.loads(ham)


DENEME = 7                  # bir istek en çok kaç kez gönderilir (hız sınırı, sunucu hatası, bağlantı hatası)
ZAMAN_ASIMI_DENEME = 3      # bunların en çok kaçı zaman aşımı olabilir


class _YonlendirmeYok(urllib.request.HTTPRedirectHandler):
    """Yönlendirme izlenmez: urllib yönlendirmede Authorization başlığını yeni adrese de taşır (anahtar sızardı).
    3xx yanıtı HTTPError olarak döner ve düzelmeyecek hata sayılır."""

    def redirect_request(self, *a, **k):
        return None


_ACICI = urllib.request.build_opener(_YonlendirmeYok)


def _ac(istek, zaman_asimi):
    return _ACICI.open(istek, timeout=zaman_asimi)


def _sayi(deger):
    """Kullanım sayısı: bazı uyumlu sunucular null ya da yazı gönderiyor."""
    try:
        return int(deger or 0)
    except (TypeError, ValueError):
        return 0


def _bicim_denetle(cevap):
    """Yanıtın beklenen biçimde olduğunu doğrular; değilse ValueError (geçici sayılır, istek yeniden gider)."""
    if not isinstance(cevap, dict):
        raise ValueError("yanıt JSON nesnesi değil")
    secimler = cevap.get("choices")
    if not isinstance(secimler, list) or not secimler or not isinstance(secimler[0], dict):
        raise ValueError("yanıtta choices yok")
    if not isinstance(secimler[0].get("message") or {}, dict):
        raise ValueError("yanıtta message nesne değil")


class Kapi:
    """Aynı anda kaç istek gideceğini yönetir: 429 gelince yarıya iner, başarılı yanıtlarla geri yükselir."""

    def __init__(self, azami):
        self.azami = self.sinir = max(1, azami)
        self.etkin = self.seri = 0
        self.son_429 = 0.0
        self._kosul = threading.Condition()

    def gir(self):
        with self._kosul:
            while self.etkin >= self.sinir:
                self._kosul.wait()
            self.etkin += 1

    def cik(self):
        with self._kosul:
            self.etkin -= 1
            self._kosul.notify_all()

    def basari(self):
        with self._kosul:
            self.seri += 1
            if self.sinir < self.azami and self.seri >= 3:
                self.sinir = min(self.azami, self.sinir + max(1, self.sinir // 2))
                self.seri = 0
                self._kosul.notify_all()

    def kalan_kota(self, kalan):
        """Sunucunun yanıtla bildirdiği kalan istek hakkı sınırın altındaysa 429 beklenmeden yavaşlanır."""
        with self._kosul:
            if kalan < self.sinir:
                self.sinir, self.seri = min(self.azami, max(2, kalan)), 0

    def hiz_siniri(self):
        with self._kosul:
            simdi = time.time()
            if simdi - self.son_429 < 2:   # aynı anda gelen birkaç 429 tek sayılır
                return
            self.son_429, self.seri = simdi, 0
            self.sinir = min(self.azami, max(2, self.sinir // 2))     # ayardaki sınır (1 olabilir) aşılmaz


class Model:
    def __init__(self, ayar, anahtar):
        m = ayar["model"]
        self.m = m
        self.url = m["api_adresi"].rstrip("/") + "/chat/completions"
        self.anahtar = anahtar
        self.girdi = self.cikti = self.onbellekten = self.istek = 0
        self.sureler = []                      # yanıt alınan her isteğin son denemesinin süresi (sn)
        self._tur_sureleri = {}                # sistem metni -> [başlayan istek sayısı, biten isteklerin süreleri]
        self.sayac = {}                        # özet için serbest sayaçlar (süre uzatma, hitap...)
        self.bekleme_sayisi = 0                # hız sınırı / sunucu hatası yüzünden kaç kez beklendi
        self.bekleme_suresi = 0.0
        self.yeniden_gonderilen = 0            # yanıtı geciktiği için kopyası gönderilen istek sayısı
        self.kapi = Kapi(m["es_zamanli_istek"])
        self._kilit = threading.Lock()
        self.bildir = None                     # bekleme olunca çağrılır (mesaj): konsola ve loga yazmak için
        self._son_bildirim = 0.0
        self.iptal = threading.Event()         # kurulunca her istek Iptal yükseltir (durdurmanın tek yeri)
        self.gelenleri_bekle = False           # durdurulunca yolda olan isteklerin yanıtı beklensin mi (durdur, yavas_durdur)
        self.hata = None                       # çalışmayı durduran ilk düzelmeyecek hata (özette nedeni yazılsın)

    def _bildir(self, mesaj):
        """Beklemeler sessiz kalmasın; aynı anda bekleyen onlarca istek tek satır yazsın diye 15 sn'de bir."""
        with self._kilit:
            if self.bildir is None or time.time() - self._son_bildirim < 15:
                return
            self._son_bildirim = time.time()
        self.bildir(mesaj)

    def _govde(self, sistem, kullanici, dusunme=None):
        # dusunme: bu isteğin düşünme seviyesi; boşsa ayardaki değer (o da boşsa alan hiç gönderilmez)
        govde = {"model": self.m["model"],
                 "messages": [{"role": "system", "content": sistem},
                              {"role": "user", "content": kullanici}]}
        if self.m["json_modu"]:
            govde["response_format"] = {"type": "json_object"}
        if dusunme or self.m["dusunme_seviyesi"]:
            govde["reasoning_effort"] = dusunme or self.m["dusunme_seviyesi"]
        if self.m["sicaklik"] >= 0:
            govde["temperature"] = self.m["sicaklik"]
        govde.update(self.m.get("ek") or {})
        return govde

    # İş parçacığına özgü: son isteğin kaç saniye sürdüğü (ham loga yazılır) ve gönderen iş parçacığında, yanıtı
    # öteki kopyadan alınmış isteğin işareti (vazgec).
    yerel = threading.local()

    def _gecikti_mi(self, tur, gecen):
        """Aynı türden (aynı sistem metni) başlayan isteklerin en az yarısı bittiyse ve bu istek onların
        ortanca süresinin 2 katını aştıysa gecikmiş sayılır. Yarı koşulu, yalnızca en hızlı ilk birkaç
        yanıta bakıp sıradan istekleri gecikmiş saymayı önler."""
        with self._kilit:
            baslayan, sureler = self._tur_sureleri.get(tur, (0, ()))
            sureler = sorted(sureler)
        return len(sureler) >= max(4, baslayan / 2) and gecen > max(8.0, 2 * sureler[len(sureler) // 2])

    def iste(self, sistem, kullanici, dusunme=None):
        """Yanıt, benzer isteklerin olağan süresini aşarsa isteğin bir kopyası gönderilir; önce dönen kullanılır."""
        tur, kutu, bitti = (hash(sistem), dusunme), queue.Queue(), threading.Event()

        def calis():
            Model.yerel.vazgec = bitti      # yanıt öteki kopyadan alındıysa bu kopya yeniden gönderilmez (_iste)
            try:
                kutu.put((True, self._iste(sistem, kullanici, dusunme)))
            except BaseException as e:      # hata öteki kopyayı beklemeye engel olmasın diye kutuya konur
                kutu.put((False, e))

        if self.iptal.is_set():
            raise Iptal()
        self.kapi.gir()
        try:
            if self.iptal.is_set():
                raise Iptal()
            basla, bekleyen, kopya = time.time(), 1, False
            with self._kilit:
                kayit = self._tur_sureleri.setdefault(tur, [0, []])
                kayit[0] += 1
            threading.Thread(target=calis, daemon=True).start()
            while True:
                try:
                    tamam, deger = kutu.get(timeout=1)
                except queue.Empty:
                    durdu = self.iptal.is_set()
                    if durdu and not self.gelenleri_bekle:  # yanıt beklenmez; istek arka planda kendiliğinden biter
                        raise Iptal() from None
                    gecen = time.time() - basla
                    if not durdu and not kopya and self._gecikti_mi(tur, gecen):
                        with self._kilit:
                            self.yeniden_gonderilen += 1
                        threading.Thread(target=calis, daemon=True).start()
                        kopya, bekleyen = True, bekleyen + 1
                    if gecen > 60:
                        self._bildir(f"bir istek {gecen:.0f} sn'dir yanıt bekliyor")
                    continue
                bekleyen -= 1
                if tamam or not bekleyen:   # yanıt geldi, ya da gönderilenlerin hepsi hata verdi
                    break
            if not tamam:
                if isinstance(deger, ModelHatasi) and not isinstance(deger, Ret):
                    self.durdur(deger)          # düzelmeyecek hata: öteki istekler de boşuna gitmesin
                raise deger
            sure = time.time() - basla
            with self._kilit:
                kayit[1].append(sure)
            Model.yerel.sure = round(sure, 1)
            self.kapi.basari()
            return deger
        finally:
            bitti.set()
            self.kapi.cik()

    def _iste(self, sistem, kullanici, dusunme=None):
        veri = json.dumps(self._govde(sistem, kullanici, dusunme)).encode("utf-8")
        basliklar = {"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.anahtar}
        bekleme, cevap, asim = 2.0, None, 0
        vazgec = getattr(Model.yerel, "vazgec", None)

        def bekle(sure, neden, tur):
            with self._kilit:
                self.bekleme_sayisi += 1
                self.bekleme_suresi += sure
            self._bildir(f"{neden}; {sure:.0f} sn sonra yeniden denenecek (deneme {tur + 2}/{DENEME})")
            _bekle(self.iptal, sure)               # durdurulursa beklemeden çıkılır (sonraki tur Iptal yükseltir)

        for tur in range(DENEME):
            # Durdurulduysa ya da yanıt öteki kopyadan alındıysa bekleyen yeniden deneme gönderilmez: yanıtını
            # kimsenin okumayacağı istek ödenmesin.
            if self.iptal.is_set() or (vazgec is not None and vazgec.is_set()):
                raise Iptal()
            istek = urllib.request.Request(self.url, data=veri, headers=basliklar, method="POST")
            basla = time.time()
            try:
                with _ac(istek, self.m["zaman_asimi"]) as y:
                    cevap = json.loads(y.read().decode("utf-8"))
                    kalan = y.headers.get("x-ratelimit-remaining-requests", "")
                    if kalan.isdigit():
                        self.kapi.kalan_kota(int(kalan))
                _bicim_denetle(cevap)
                break
            except urllib.error.HTTPError as e:
                try:
                    metin = e.read().decode("utf-8", "replace")[:600]
                except (OSError, http.client.HTTPException):
                    metin = ""
                if e.code == 429 and "insufficient_quota" in metin:      # beklemekle düzelmez
                    raise ModelHatasi("Hesabın kotası ya da kredisi bitmiş (HTTP 429 insufficient_quota): " + metin[:300])
                if e.code == 429:
                    self.kapi.hiz_siniri()
                # Geçici olanlar: zaman aşımı, çakışma, hız sınırı ve bütün sunucu hataları (520-524 Cloudflare dahil).
                if (e.code in (408, 409, 429) or e.code >= 500) and tur < DENEME - 1:
                    ra = e.headers.get("Retry-After", "")
                    sure = float(ra) if ra.replace(".", "", 1).isdigit() else bekleme
                    bekle(min(sure, 60), "hız sınırı (HTTP 429)" if e.code == 429 else f"sunucu hatası (HTTP {e.code})", tur)
                    bekleme = min(bekleme * 2, 60)
                    continue
                if e.code == 400 and ("content_policy" in metin or "content_filter" in metin):
                    raise Ret("içerik filtresi: " + metin)
                raise ModelHatasi(f"HTTP {e.code}: {metin}")
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as e:
                # ValueError / HTTPException: yanıt yarım kaldı ya da JSON değil (vekil sunucunun HTML sayfası gibi)
                zaman_asimi = isinstance(e, ZAMAN_ASIMI) or isinstance(getattr(e, "reason", None), ZAMAN_ASIMI)
                asim += zaman_asimi
                neden = (f"sunucu {self.m['zaman_asimi']} sn içinde yanıt vermedi" if zaman_asimi
                         else f"bağlantı kurulamadı ({e})" if isinstance(e, OSError)
                         else f"yanıt yarım ya da bozuk geldi ({type(e).__name__})")
                # Yanıtsız kalan istek uzun sürer: en çok ZAMAN_ASIMI_DENEME kez beklenir, sonra çalışma hatayla durur.
                if tur < DENEME - 1 and asim < ZAMAN_ASIMI_DENEME:
                    bekle(bekleme, neden, tur)
                    bekleme = min(bekleme * 2, 60)
                    continue
                raise ModelHatasi(f"Sunucu {asim} kez {self.m['zaman_asimi']} sn içinde yanıt vermedi." if zaman_asimi
                                  else f"İstek {tur + 1} denemede de olmadı: {neden}")
        k = cevap.get("usage") if isinstance(cevap.get("usage"), dict) else {}
        ayrinti = k.get("prompt_tokens_details") if isinstance(k.get("prompt_tokens_details"), dict) else {}
        with self._kilit:
            self.sureler.append(time.time() - basla)
            self.istek += 1
            self.girdi += _sayi(k.get("prompt_tokens"))
            self.cikti += _sayi(k.get("completion_tokens"))
            self.onbellekten += _sayi(ayrinti.get("cached_tokens"))

        secim = cevap["choices"][0]
        mesaj = secim.get("message") or {}
        if mesaj.get("refusal"):
            raise Ret("model reddetti: " + str(mesaj["refusal"])[:300])
        icerik = mesaj.get("content")
        if isinstance(icerik, list):          # bazı uyumlu sunucular içeriği parçalar hâlinde verir
            icerik = "".join(p.get("text") or "" for p in icerik if isinstance(p, dict))
        if secim.get("finish_reason") == "content_filter" or not isinstance(icerik, str) or not icerik.strip():
            ret = Ret("boş yanıt (finish_reason=%s)" % secim.get("finish_reason"))
            ret.icerik = secim.get("finish_reason") == "content_filter"     # boş yanıt bozuk ayardan da olabilir
            raise ret
        return icerik

    def durdur(self, hata):
        """Düzelmeyecek hata (biten bakiye, yanlış anahtar): hata özette asıl neden olarak yazılır, kalan istekler
        gönderilmez. O an yolda olan isteklerin yanıtı beklenir: ödenmiş yanıt kayda geçer, bakiye yüklenip yeniden
        başlatılınca kaldığı yerden sürer."""
        with self._kilit:
            self.hata = self.hata or hata
        self.gelenleri_bekle = True
        self.iptal.set()

    def yavas_durdur(self):
        """Kullanıcı durdurdu: yeni istek (yeniden deneme ve kopya dahil) gönderilmez, yolda olanların yanıtı
        beklenir; dönen sonuçlar önbelleğe yazılır, kaldığı yerden sürünce bir daha ödenmez."""
        self.gelenleri_bekle = True
        self.iptal.set()

    def say(self, ad, n=1):
        with self._kilit:
            self.sayac[ad] = self.sayac.get(ad, 0) + n

    def maliyet(self):
        m = self.m
        return ((self.girdi - self.onbellekten) * m["fiyat_girdi"] + self.onbellekten * m["fiyat_onbellek"]
                + self.cikti * m["fiyat_cikti"]) / 1_000_000


class Kayitli:
    """Modelin önünde duran kayıt: ödenmiş her yanıt dosyaya yazılır, aynı istek yeniden gelince dosyadan döner.

    Sözlük adımı (1. geçiş) yarıda durdurulursa ödenmiş yanıtlar kaybolmasın diye: yeniden başlayınca aynı istekler
    kayıttan karşılanır, yalnız eksikler modele gider. Oylamada aynı istek birkaç kez gönderilir: her çağrı kayıttaki
    sıradaki yanıtı alır. Ret ve durdurma kayda geçmez. Öteki her şey (maliyet, durdurma) asıl modele iletilir."""

    def __init__(self, model, yol):
        self._model, self._yol, self._kilit = model, yol, threading.Lock()
        self._alinan = {}                    # anahtar -> bu çalıştırmada kayıttan kaç yanıt verildi
        try:
            with open(yol, encoding="utf-8") as f:
                kayit = json.load(f)
            self._kayit = {k: [y for y in v if isinstance(y, str)] for k, v in kayit.items() if isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            self._kayit = {}

    def __getattr__(self, ad):
        return getattr(self._model, ad)

    def iste(self, sistem, kullanici, dusunme=None):
        anahtar = hashlib.sha1("\0".join((sistem, kullanici, str(dusunme))).encode("utf-8")).hexdigest()
        with self._kilit:
            sira = self._alinan.get(anahtar, 0)
            self._alinan[anahtar] = sira + 1
            if sira < len(self._kayit.get(anahtar, [])):
                return self._kayit[anahtar][sira]
        yanit = self._model.iste(sistem, kullanici, dusunme)
        with self._kilit:
            self._kayit.setdefault(anahtar, []).append(yanit)
            gecici = self._yol + ".yeni"
            try:
                with open(gecici, "w", encoding="utf-8") as f:
                    json.dump(self._kayit, f, ensure_ascii=False)
                os.replace(gecici, self._yol)
            except OSError:
                pass                         # kayıt yazılamadıysa yalnız bu güvence yoktur; çeviri sürer
        return yanit
