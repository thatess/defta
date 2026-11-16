"""HTTP istekleri (yalnız standart kütüphane). Anahtar başlıkta gider; adres kayda gizlenerek yazılır.

Bütün bağlantıların soketi kayda geçer: Kodi kapanırken kes() yoldaki istekleri keser, yenileri hemen hata verir
(iş parçacığı açık kalmasın). Başka sunucuya yönlendirmede anahtar başlıkları gönderilmez.
"""
import http.client
import json
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import weakref

from . import gizli

# Android'de (Shield) Kodi'nin Python'u sistem sertifikalarını göremeyebilir: certifi varsa onun listesi kullanılır.
# Açıcı kurulmadan önce yapılır: Python 3.13'te (Kodi 22) HTTPS işleyicisi TLS bağlamını kurulurken alır; sonradan
# yapılan değişiklik site isteklerine uygulanmazdı. Çekirdeğin urllib istekleri de bu bağlamı kullanır.
_BAGLAM = None
try:
    import certifi
    _BAGLAM = ssl.create_default_context(cafile=certifi.where())
    ssl._create_default_https_context = lambda: _BAGLAM
except Exception:                       # certifi yoksa sistemin sertifikaları
    pass

ZAMAN_ASIMI = (TimeoutError, socket.timeout)          # Python 3.8'de (Kodi 21) socket.timeout ayrı sınıf
EN_BUYUK_YANIT = 64 * 1024 * 1024       # bayt; en büyük beklenen yanıt eşleme listesi (~9 MB). Fazlası belleğe alınmaz


class AgHatasi(Exception):
    """Bağlantı kurulamadı, zaman aşımı, DNS, yarım yanıt."""


class YanitHatasi(Exception):
    def __init__(self, kod, veri=None, basliklar=None):
        super().__init__(f"HTTP {kod}")
        self.kod, self.veri, self.basliklar = kod, veri, basliklar or {}


# ------------------------------------------------------------------------------------- kapanışta kesilebilen bağlantı
_soketler = weakref.WeakSet()
_soket_kilidi = threading.Lock()
kapaniyor = threading.Event()


def _kaydet(sock):
    with _soket_kilidi:
        _soketler.add(sock)
    if kapaniyor.is_set():
        _kapat(sock)


def _kapat(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def kes():
    """Kodi kapanıyor: yoldaki bütün istekler kesilir, yenileri hemen hata verir."""
    kapaniyor.set()
    with _soket_kilidi:
        soketler = list(_soketler)
    for s in soketler:
        _kapat(s)


class _KayitliHTTPS(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _kaydet(self.sock)


class _KayitliHTTP(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _kaydet(self.sock)


class KayitliHTTPSIsleyici(urllib.request.HTTPSHandler):
    def https_open(self, istek_):
        return self.do_open(_KayitliHTTPS, istek_, context=self._context)


class KayitliHTTPIsleyici(urllib.request.HTTPHandler):
    def http_open(self, istek_):
        return self.do_open(_KayitliHTTP, istek_)


class _GuvenliYonlendirme(urllib.request.HTTPRedirectHandler):
    """Yönlendirme başka sunucuyaysa anahtar başlıkları (Authorization, Api-Key, X-API-Key) taşınmaz."""
    _GIZLI = ("authorization", "api-key", "x-api-key")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        yeni = super().redirect_request(req, fp, code, msg, headers, newurl)
        if yeni is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            for sozluk in (yeni.headers, yeni.unredirected_hdrs):
                for ad in [a for a in sozluk if a.lower() in self._GIZLI]:
                    del sozluk[ad]
        return yeni


_ACICI = urllib.request.build_opener(_GuvenliYonlendirme, KayitliHTTPSIsleyici, KayitliHTTPIsleyici)

_kullanici = "Defta"


def kullanici_ajani(surum):
    global _kullanici
    _kullanici = f"Defta v{surum}"


def istek(yontem, adres, basliklar=None, sorgu=None, govde=None, zaman_asimi=15, ham=False, deneme=2):
    """(kod, veri, başlıklar). veri: JSON ise çözülmüş, ham=True ise bayt. 429 ve 5xx'te en çok bir kez, kısa
    bekleyerek yeniden dener (Retry-After 5 sn'den uzunsa beklemez, hatayı döndürür)."""
    if sorgu:
        adres += ("&" if "?" in adres else "?") + urllib.parse.urlencode(sorgu)
    b = {"User-Agent": _kullanici, "Accept": "*/*" if ham else "application/json"}
    b.update(basliklar or {})
    veri = None
    if govde is not None:
        veri = json.dumps(govde).encode("utf-8")
        b["Content-Type"] = "application/json"
    for tur in range(deneme):
        if kapaniyor.is_set():
            raise AgHatasi("Kodi kapanıyor")
        try:
            with _ACICI.open(urllib.request.Request(adres, data=veri, headers=b, method=yontem),
                             timeout=zaman_asimi) as y:
                icerik = y.read(EN_BUYUK_YANIT + 1)
                if len(icerik) > EN_BUYUK_YANIT:        # yanlış bağlantı (video dosyası) cihazın belleğini doldurmasın
                    raise AgHatasi("yanıt çok büyük")
                return y.status, (icerik if ham else _json(icerik)), dict(y.headers)
        except urllib.error.HTTPError as e:
            try:
                icerik = e.read(1024 * 1024)
            except Exception:
                icerik = b""
            cozulmus = _json(icerik)
            basliklar_y = dict(e.headers or {})
            if e.code == 429 or 500 <= e.code < 600:
                bekleme = _retry_after(basliklar_y, 1.0 + tur)
                if tur < deneme - 1 and bekleme <= 5:
                    kapaniyor.wait(bekleme)
                    continue
            raise YanitHatasi(e.code, cozulmus, basliklar_y) from None
        except ZAMAN_ASIMI as e:
            if tur < deneme - 1:
                continue
            raise AgHatasi("zaman aşımı") from e
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            neden = getattr(e, "reason", e)
            if tur < deneme - 1 and isinstance(neden, ZAMAN_ASIMI):
                continue
            if isinstance(neden, socket.gaierror):
                raise AgHatasi(f"{urllib.parse.urlsplit(adres).hostname} adresi çözülemedi "
                               "(DNS engeli olabilir)") from None
            if isinstance(e, (ValueError, http.client.InvalidURL)):
                # Bu hataların yazısı adresin kendisini ya da başlık değerini taşır ("unknown url type: '...'", "URL
                # can't contain control characters. '/yol?sorgu'", "Invalid header value b'Bearer ...'"): adresin yolu
                # jeton, başlık anahtar olabilir. Kayda ve bildirime yalnız hatanın türü gider.
                raise AgHatasi(f"bağlantı kurulamadı: {type(e).__name__}") from None
            raise AgHatasi(gizli.gizle(f"bağlantı kurulamadı: {neden}")) from None
    raise AgHatasi("yanıt yok")


def _json(icerik):
    if not icerik:
        return None
    try:
        return json.loads(icerik.decode("utf-8", "replace"))
    except ValueError:
        return None


def _retry_after(basliklar, varsayilan):
    for ad in ("Retry-After", "retry-after"):
        deger = basliklar.get(ad)
        if deger:
            try:
                return float(deger)
            except ValueError:
                return varsayilan
    return varsayilan


# ------------------------------------------------------------------------------------------- DNS tanısı
def _dns_sorgusu(ad, sunucu, zaman=3.0):
    """Ham UDP DNS sorgusu (A kaydı): {"ip": [...], "kod": rcode} ya da {"hata": yazı}. Yalnız tanı için."""
    import random
    import struct
    kimlik = random.randint(0, 65535)
    soru = b"".join(bytes([len(p)]) + p.encode("ascii") for p in ad.strip(".").split(".")) + b"\0"
    paket = struct.pack(">HHHHHH", kimlik, 0x0100, 1, 0, 0, 0) + soru + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(zaman)
    try:
        s.sendto(paket, (sunucu, 53))
        veri, _ = s.recvfrom(4096)
    except socket.timeout:
        return {"hata": "yanıt yok"}
    except OSError as e:
        return {"hata": str(e)}
    finally:
        s.close()
    try:
        rid, bayrak, qd, an, _, _ = struct.unpack(">HHHHHH", veri[:12])
        if rid != kimlik:
            return {"hata": "yanlış yanıt"}
        i = 12
        for _ in range(qd):
            while veri[i] != 0:
                i += veri[i] + 1
            i += 5
        ipler = []
        for _ in range(an):
            if veri[i] & 0xC0 == 0xC0:
                i += 2
            else:
                while veri[i] != 0:
                    i += veri[i] + 1
                i += 1
            rtur, _, _, uzunluk = struct.unpack(">HHIH", veri[i:i + 10])
            i += 10
            if rtur == 1 and uzunluk == 4:
                ipler.append(".".join(str(b) for b in veri[i:i + 4]))
            i += uzunluk
        return {"ip": ipler, "kod": bayrak & 0xF}
    except (struct.error, IndexError):
        return {"hata": "bozuk yanıt"}


def _doh(ad):
    """Cloudflare DNS-over-HTTPS (1.1.1.1, adres çözmeden): IP listesi ya da hata yazısı."""
    try:
        _, veri, _ = istek("GET", "https://1.1.1.1/dns-query", {"Accept": "application/dns-json"},
                           sorgu={"name": ad, "type": "A"}, zaman_asimi=8, deneme=1)
    except (AgHatasi, YanitHatasi) as e:
        return str(e)
    return [c.get("data") for c in (veri or {}).get("Answer") or [] if c.get("type") == 1]


def _tls_dene(ad, ip, zaman=8.0):
    import ssl
    import time
    bas = time.time()
    try:
        with socket.create_connection((ip, 443), timeout=zaman) as ham:
            with ssl._create_default_https_context().wrap_socket(ham, server_hostname=ad):
                return f"bağlantı tamam ({time.time() - bas:.1f} sn)"
    except ZAMAN_ASIMI:
        return f"bağlantı {zaman:.0f} sn'de kurulamadı (zaman aşımı)"
    except ssl.SSLError as e:
        return f"TLS hatası ({getattr(e, 'reason', '') or e})"
    except OSError as e:
        return f"bağlantı hatası ({e})"


def dns_tanisi(ad, yonlendirici=""):
    """Bir site adı çözülemeyince nedenini anlamak için: Windows/Kodi DNS'i, yönlendiriciye ve 1.1.1.1'e doğrudan
    sorgu, DNS-over-HTTPS ve bulunan adrese TLS bağlantısı. Dönüş: tek satırlık özet."""
    def yaz(sonuc):
        if "hata" in sonuc:
            return sonuc["hata"]
        return ", ".join(sonuc["ip"][:2]) if sonuc["ip"] else f"boş yanıt (kod {sonuc['kod']})"
    parcalar = []
    try:
        socket.getaddrinfo(ad, 443)
        parcalar.append("sistem DNS'i: çözdü")
    except socket.gaierror as e:
        parcalar.append(f"sistem DNS'i: çözemedi ({e.errno})")
    if yonlendirici:
        parcalar.append(f"modem ({yonlendirici}): {yaz(_dns_sorgusu(ad, yonlendirici))}")
    parcalar.append(f"1.1.1.1 (53): {yaz(_dns_sorgusu(ad, '1.1.1.1'))}")
    doh = _doh(ad)
    parcalar.append("DoH: " + (", ".join(doh[:2]) if isinstance(doh, list) and doh else (doh or "boş")))
    if isinstance(doh, list) and doh:
        parcalar.append("TLS: " + _tls_dene(ad, doh[0]))
    return " · ".join(parcalar)
