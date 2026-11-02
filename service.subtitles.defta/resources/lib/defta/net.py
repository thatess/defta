"""Küçük HTTP yardımcısı (yalnızca Python'un urllib'i + certifi sertifikaları).

URL'lerdeki anahtarlar kayda yazılmadan önce gizlenir."""
from __future__ import annotations

import json
import re
import ssl
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


def user_agent() -> str:
    return f"Defta v{kodi.version()}"


_SECRET_RE = re.compile(r"(api_key|apikey|key|token|password)=[^&\s]+", re.I)


def redact(url: str) -> str:
    return _SECRET_RE.sub(r"\1=***", url)


class HttpError(Exception):
    def __init__(self, status: int, message: str, payload=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.payload = payload


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
    for attempt in range(retries + 1):
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
            if attempt < retries:
                time.sleep(1.0)
                continue
    raise HttpError(0, f"bağlantı kurulamadı: {getattr(last, 'reason', last)} ({redact(url.split('?')[0])})")


def get_json(url: str, params=None, headers=None, timeout: float = 15.0):
    status, raw, _ = request("GET", url, params, headers, timeout=timeout)
    payload = _loads(raw)
    if status != 200:
        raise HttpError(status, _msg(payload, raw), payload)
    return payload


def post_json(url: str, body: dict, headers=None, timeout: float = 15.0):
    status, raw, _ = request("POST", url, None, headers, body=body, timeout=timeout)
    payload = _loads(raw)
    if status not in (200, 201):
        raise HttpError(status, _msg(payload, raw), payload)
    return payload


def get_bytes(url: str, params=None, headers=None, timeout: float = 30.0) -> bytes:
    status, raw, _ = request("GET", url, params, headers, timeout=timeout)
    if status != 200:
        raise HttpError(status, _msg(_loads(raw), raw))
    return raw


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
