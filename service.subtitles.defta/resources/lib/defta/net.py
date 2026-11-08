"""Küçük HTTP yardımcısı (yalnızca Python'un urllib'i + certifi sertifikaları).

URL'lerdeki anahtarlar kayda yazılmadan önce gizlenir."""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import kodi

_CTX = None


def ssl_context():
    global _CTX
    if _CTX is None:
        try:
            import certifi
            _CTX = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # noqa: BLE001  (certifi yoksa sistem sertifikaları)
            _CTX = ssl.create_default_context()
    return _CTX


# ------------------------------------------------------------------ DNS engeli
# Bazı internet sağlayıcıları altyazı sitelerinin adını DNS'te engelliyor (ör. api.subdl.com). Tarayıcılar
# kendi güvenli DNS'ini (DoH) kullandığı için site açılır ama Kodi bulamaz. Ad çözülemezse Cloudflare ve
# Google'ın güvenli DNS'ine IP adresiyle sorulur. Bağlantı yine asıl ada (TLS sertifikasıyla) yapılır.

_orig_getaddrinfo = socket.getaddrinfo
_DOH = (("1.1.1.1", "/dns-query?name={}&type=A", {"Accept": "application/dns-json"}),
        ("8.8.8.8", "/resolve?name={}&type=A", {}))
_doh_cache: dict = {}
_doh_lock = threading.Lock()
_IP_RE = re.compile(r"^[\d.]+$|:")


def _doh_lookup(host: str) -> list:
    now = time.time()
    with _doh_lock:
        hit = _doh_cache.get(host)
        if hit and now - hit[0] < 3600:
            return hit[1]
    for ip, path, headers in _DOH:
        try:
            conn = http.client.HTTPSConnection(ip, 443, timeout=6, context=ssl_context())
            try:
                conn.request("GET", path.format(host), headers=dict(headers, **{"User-Agent": "Defta"}))
                data = json.loads(conn.getresponse().read().decode("utf-8") or "{}")
            finally:
                conn.close()
        except (OSError, ValueError, http.client.HTTPException):
            continue
        ips = [a.get("data") for a in data.get("Answer") or [] if a.get("type") == 1 and a.get("data")]
        if ips:
            with _doh_lock:
                _doh_cache[host] = (now, ips)
            kodi.log(f"DNS: {host} internet sağlayıcısının DNS'inde bulunamadı, güvenli DNS ile bulundu ({ip})")
            return ips
    return []


_force_doh: dict = {}          # ad -> zaman: bağlantısı reddedilen adlar bir süre doğrudan güvenli DNS'le çözülür
_LOCAL_SUFFIX = (".local", ".lan", ".home", ".localdomain", ".internal", ".arpa")


def _is_local_name(name: str) -> bool:
    n = name.lower().rstrip(".")
    return not n or n == "localhost" or "." not in n or n.endswith(_LOCAL_SUFFIX) or bool(_IP_RE.search(n))


def _bogus(addrs) -> bool:
    """Sağlayıcının ya da modemin engelli sitelere verdiği sahte yanıt: 0.0.0.0, 127.x, ::, ::1."""
    ips = [a[4][0] for a in addrs if a and len(a) > 4 and a[4]]
    return bool(ips) and all(ip.startswith(("0.", "127.")) or ip in ("::", "::1") for ip in ips)


def _doh_addrs(name: str, port, type, proto):
    ips = _doh_lookup(name)
    return [(socket.AF_INET, type or socket.SOCK_STREAM, proto or 6, "", (ip, int(port or 0))) for ip in ips]


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    name = host.decode() if isinstance(host, bytes) else str(host or "")
    public = not _is_local_name(name) and family != socket.AF_INET6
    if public and name in _force_doh and time.time() - _force_doh[name] < 3600:
        res = _doh_addrs(name, port, type, proto)
        if res:
            return res
    try:
        res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        if not public:
            raise
        res = _doh_addrs(name, port, type, proto)
        if not res:
            raise
        return res
    if public and _bogus(res):
        alt = _doh_addrs(name, port, type, proto)
        if alt:
            kodi.log(f"DNS: {name} için sahte adres geldi ({res[0][4][0]}), güvenli DNS'in adresi kullanılıyor")
            return alt
    return res


def _resolved(name: str) -> str:
    try:
        return ", ".join(sorted({a[4][0] for a in socket.getaddrinfo(name, 443, 0, socket.SOCK_STREAM)}))[:80]
    except OSError:
        return "?"


if getattr(socket.getaddrinfo, "__name__", "") != "_getaddrinfo":
    socket.getaddrinfo = _getaddrinfo


def user_agent() -> str:
    return f"Defta v{kodi.version()}"


_SECRET_RE = re.compile(r"(api_key|apikey|key|token|password)=[^&\s]+", re.I)


def redact(url: str) -> str:
    return _SECRET_RE.sub(r"\1=***", url)


class HttpError(Exception):
    def __init__(self, status: int, message: str, payload=None, headers: dict | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.payload = payload
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def retry_after(self) -> float:
        """Sunucunun 'şu kadar sonra dene' dediği süre (sn), yoksa 0."""
        try:
            return max(0.0, float(self.headers.get("retry-after", 0)))
        except ValueError:
            return 0.0


def request(method: str, url: str, params: dict | list | None = None, headers: dict | None = None,
            body=None, timeout: float = 15.0, retries: int = 1) -> tuple[int, bytes, dict]:
    if params:
        items = params.items() if isinstance(params, dict) else params
        q = urllib.parse.urlencode([(k, v) for k, v in items if v is not None and v != ""])
        url = url + ("&" if "?" in url else "?") + q
    h = {"User-Agent": user_agent(), "Accept": "*/*"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        h.setdefault("Content-Type", "application/json")
    last = None
    attempt = -1
    while attempt < retries:
        attempt += 1
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read() if hasattr(e, "read") else b""
            if e.code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                last = e
                continue
            return e.code, raw, dict(e.headers or {})
        except (urllib.error.URLError, OSError) as e:
            last = e
            host = urllib.parse.urlparse(url).hostname or ""
            reason = getattr(e, "reason", e)
            if isinstance(reason, (ConnectionRefusedError, ConnectionResetError, socket.timeout, TimeoutError)) \
                    and not _is_local_name(host) and host not in _force_doh:
                # Bağlantı reddedildi/kesildi: sağlayıcının DNS'i yanlış (engel) adres vermiş olabilir.
                # Güvenli DNS'in verdiği adres farklıysa bir kez onunla denenir.
                before = _resolved(host)
                _force_doh[host] = time.time()
                after = _resolved(host)
                if after not in ("?", before):
                    kodi.log(f"Bağlantı: {host} ({before}) {type(reason).__name__}; güvenli DNS adresiyle "
                             f"({after}) yeniden deneniyor")
                    attempt -= 1                      # bu deneme sayılmaz
                    continue
                _force_doh.pop(host, None)
            if attempt < retries:
                time.sleep(1.0)
                continue
    host = urllib.parse.urlparse(url).hostname or ""
    raise HttpError(0, f"bağlantı kurulamadı: {getattr(last, 'reason', last)} ({redact(url.split('?')[0])}"
                       + (f" → {_resolved(host)}" if host else "") + ")")


def _checked(method: str, url: str, params, headers, body, timeout: float) -> bytes:
    status, raw, rh = request(method, url, params, headers, body=body, timeout=timeout)
    if status not in (200, 201):
        payload = _loads(raw)
        raise HttpError(status, _msg(payload, raw), payload, rh)
    return raw


def get_json(url: str, params=None, headers=None, timeout: float = 15.0):
    return _loads(_checked("GET", url, params, headers, None, timeout))


def post_json(url: str, body: dict, headers=None, timeout: float = 15.0):
    return _loads(_checked("POST", url, None, headers, body, timeout))


def get_bytes(url: str, params=None, headers=None, timeout: float = 30.0) -> bytes:
    return _checked("GET", url, params, headers, None, timeout)


def _loads(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        return None


def _msg(payload, raw: bytes) -> str:
    if isinstance(payload, dict):
        for k in ("message", "error", "errors", "detail"):
            if payload.get(k):
                return str(payload[k])[:200]
    return raw[:200].decode("utf-8", "replace")
