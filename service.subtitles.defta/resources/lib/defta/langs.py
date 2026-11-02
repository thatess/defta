"""Dil tablosu: ayarlardaki sıra, Kodi'nin İngilizce dil adları ve sitelerin dil kodları."""
from __future__ import annotations

# (kod, Kodi adı, Türkçe ad, SubSource adı, SubDL kodu)
LANGS = [
    ("tr", "Turkish", "Türkçe", "turkish", "TR"),
    ("en", "English", "İngilizce", "english", "EN"),
    ("ja", "Japanese", "Japonca", "japanese", "JA"),
    ("de", "German", "Almanca", "german", "DE"),
    ("fr", "French", "Fransızca", "french", "FR"),
    ("es", "Spanish", "İspanyolca", "spanish", "ES"),
    ("it", "Italian", "İtalyanca", "italian", "IT"),
    ("pt", "Portuguese", "Portekizce", "portuguese", "PT"),
    ("ru", "Russian", "Rusça", "russian", "RU"),
    ("ar", "Arabic", "Arapça", "arabic", "AR"),
    ("ko", "Korean", "Korece", "korean", "KO"),
    ("zh", "Chinese", "Çince", "chinese bg code", "ZH"),
    ("nl", "Dutch", "Felemenkçe", "dutch", "NL"),
    ("pl", "Polish", "Lehçe", "polish", "PL"),
    ("sv", "Swedish", "İsveççe", "swedish", "SV"),
    ("el", "Greek", "Yunanca", "greek", "EL"),
    ("fa", "Persian", "Farsça", "farsi_persian", "FA"),
    ("hi", "Hindi", "Hintçe", "hindi", "HI"),
    ("id", "Indonesian", "Endonezce", "indonesian", "ID"),
]
_BY_CODE = {row[0]: row for row in LANGS}
_ALIASES = {
    "tur": "tr", "eng": "en", "jpn": "ja", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "spa": "es",
    "ita": "it", "por": "pt", "pob": "pt", "pb": "pt", "rus": "ru", "ara": "ar", "kor": "ko", "chi": "zh",
    "zho": "zh", "dut": "nl", "nld": "nl", "pol": "pl", "swe": "sv", "gre": "el", "ell": "el", "per": "fa",
    "fas": "fa", "hin": "hi", "ind": "id", "jp": "ja", "br": "pt", "cn": "zh", "farsi": "fa",
}
_NAMES = {}
for _row in LANGS:
    for _n in (_row[1], _row[2], _row[3]):
        _NAMES[_n.lower()] = _row[0]


def code_at(index) -> str:
    try:
        return LANGS[int(index)][0]
    except (ValueError, IndexError, TypeError):
        return "tr"


def norm(value: str) -> str:
    """Her türlü dil yazımını iki harfli koda çevirir: 'pt-BR' -> 'pt', 'Turkish' -> 'tr', 'TUR' -> 'tr'."""
    v = (value or "").strip().lower().replace("_", "-")
    if not v:
        return ""
    if v in _NAMES:
        return _NAMES[v]
    base = v.split("-")[0]
    if base in _BY_CODE:
        return base
    return _ALIASES.get(base, _ALIASES.get(v, base[:2] if len(base) == 2 else base))


def kodi_name(code: str) -> str:
    row = _BY_CODE.get(code)
    return row[1] if row else code


def tr_name(code: str) -> str:
    row = _BY_CODE.get(code)
    return row[2] if row else code.upper()


def subsource_name(code: str) -> str:
    row = _BY_CODE.get(code)
    return row[3] if row else ""


def subdl_code(code: str) -> str:
    row = _BY_CODE.get(code)
    return row[4] if row else code.upper()


def parse_list(text: str) -> list[str]:
    out = []
    for part in (text or "").replace(";", ",").split(","):
        c = norm(part)
        if c and c not in out:
            out.append(c)
    return out


def _back(word: str) -> bool:
    vowels = [ch for ch in word.lower() if ch in "aıouüöei"]
    return bool(vowels) and vowels[-1] in "aıou"


def ablative(name: str) -> str:
    """'İngilizce' -> 'İngilizceden', 'Japonca' -> 'Japoncadan' (dil adları kesme işareti almaz)."""
    return name + ("dan" if _back(name) else "den")


def dative(name: str) -> str:
    """'Türkçe' -> 'Türkçeye', 'Japonca' -> 'Japoncaya'."""
    return name + ("ya" if _back(name) else "ye")
