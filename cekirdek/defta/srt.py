"""SRT okuma ve yazma. Zaman kodları burada kalır, modele gitmez."""
import re
from dataclasses import dataclass

ZAMAN = re.compile(
    r"^\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})")


@dataclass
class Satir:
    no: int
    bas: int      # milisaniye
    bit: int      # milisaniye
    metin: str
    ustte: bool = False
    ekran: bool = False    # biçimin ekran yazısı dediği satır (ASS'te konumlanmış yazı); srt'de bilinemez
    ref: str = ""         # çift dilli dosyada aynı andaki ikinci dil satırı: çevrilmez, modele başvuru olarak gider
    sarki_eki: bool = False   # şarkının iç dizesi: ♪ işaretini script ekledi, çıktıda yine yok (kaynak.sarki_bloklari)


def metin_oku(yol):
    """Dosyayı okur; kodlamayı sırayla dener (metin_coz)."""
    with open(yol, "rb") as f:
        return metin_coz(f.read())


def metin_coz(veri):
    """Baytları yazıya çevirir; kodlamayı sırayla dener. UTF-8 olmayan Japonca altyazı çoğunlukla Shift-JIS'tir
    (cp932): cp1254 onu da hatasız ama anlamsız okuyabildiği için önce cp932 denenir ve metinde kana varsa o alınır."""
    if veri[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # Yarıda kesilmiş son bayt ya da eşi olmayan vekil dosyanın tamamını okunmaz yapmasın: o karakter "?" olur.
        return veri.decode("utf-16", errors="replace").replace("�", "?")
    try:
        return veri.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # Birkaç eski bayt (Windows'un kıvrık kesme işareti 0x92 gibi) bütün dosyayı başka kodlamayla okutmasın: dosya
    # neredeyse tümüyle UTF-8 ise o birkaç karakter "?" olur, gerisi doğru kalır. Dosyanın UTF-8 olduğu, doğru okunan
    # UTF-8 karakterlerinin bozuklardan açıkça çok olmasıyla anlaşılır: hiç UTF-8 karakteri olmayan eski kodlamalı
    # dosya (lotr.srt: cp1252, 54 bayt) bu yoldan geçerse Elfçe harfler ve kesme işaretleri "?" olur.
    metin = veri.decode("utf-8-sig", errors="replace")
    bozuk = metin.count("\ufffd")
    dogru = sum(1 for k in metin if ord(k) > 0x7F) - bozuk
    if bozuk <= max(3, len(metin) // 2000) and dogru >= 2 * bozuk:
        return metin.replace("\ufffd", "?")
    try:
        metin = veri.decode("cp932")
        kana = sum(1 for k in metin if "ぁ" <= k <= "ヿ")
        if kana >= 20 and kana * 3 >= sum(1 for k in metin if ord(k) > 0x7F):
            return metin
    except UnicodeDecodeError:
        pass
    try:
        return veri.decode("cp1254")
    except UnicodeDecodeError:
        return veri.decode("latin-1")


def _ms(s, d, sn, ms):
    return ((int(s) * 60 + int(d)) * 60 + int(sn)) * 1000 + int(ms.ljust(3, "0"))


def _zaman(ms):
    s, ms = divmod(max(ms, 0), 3600000)
    d, ms = divmod(ms, 60000)
    sn, ms = divmod(ms, 1000)
    return f"{s:02d}:{d:02d}:{sn:02d},{ms:03d}"


def _numara(satir):
    """Altyazı numarası satırındaki sayı (dosyanın başındaki BOM sayılmaz); numara satırı değilse None."""
    s = satir.strip().lstrip("\ufeff")
    return int(s) if s.isascii() and s.isdigit() and len(s) <= 9 else None


def srt_ayristir(icerik):
    satirlar = icerik.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    zamanlar = [i for i, s in enumerate(satirlar) if ZAMAN.match(s)]
    sonuc = []
    for sira, i in enumerate(zamanlar):
        m = ZAMAN.match(satirlar[i])
        son = zamanlar[sira + 1] if sira + 1 < len(zamanlar) else len(satirlar)
        govde = satirlar[i + 1:son]
        # Sonraki altyazının numara satırını ayıkla. Önünde boş satır yoksa metnin sayıyla biten son satırı olabilir
        # ("I have" / "42"): yalnız sıradaki numaraysa (bu altyazınınki + 1) ayıklanır; araya boş satır konmamış dosyada
        # numaralar yoksa her altyazının sonuna yapışırdı.
        if sira + 1 < len(zamanlar) and govde and govde[-1].strip().isdigit():
            no = _numara(satirlar[i - 1]) if i else None
            if len(govde) == 1 or not govde[-2].strip() or (no is not None and _numara(govde[-1]) == no + 1):
                govde = govde[:-1]
        while govde and not govde[-1].strip():
            govde.pop()
        while govde and not govde[0].strip():
            govde.pop(0)
        sonuc.append(Satir(len(sonuc) + 1, _ms(*m.groups()[:4]), _ms(*m.groups()[4:]),
                           "\n".join(govde)))
    return sonuc


def srt_yaz(satirlar):
    bloklar = [f"{n}\n{_zaman(s.bas)} --> {_zaman(s.bit)}\n{s.metin}\n"
               for n, s in enumerate(satirlar, 1)]
    return "\n".join(bloklar)
