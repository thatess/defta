"""Ayarlardaki düğmeler: temizlik, bağlantı testi, kurulum rehberi."""
from __future__ import annotations

import sys

import xbmcgui

from . import kodi, store
from .sources import ALL, LABEL

CLEAN_TEXT = {
    "ai": "Tüm hazır AI çevirileri silinsin mi?",
    "search": "Kaydedilmiş arama sonuçları silinsin mi?",
    "downloads": "İndirilmiş yabancı altyazılar silinsin mi?",
    "memory": "Çeviri hafızası silinsin mi?",
    "jobs": "Yarım kalan ve başarısız çeviri işleri silinsin mi?",
    "apicache": "API yanıt önbelleği silinsin mi? (Aynı altyazı yeniden çevrilirse tekrar ücret ödenir.)",
    "all": "Defta'nın TÜM verileri silinsin mi? (AI çevirileri, sözlükler, önbellek, işler.)\n"
           "API anahtarları ve ayarlar silinmez.",
}

GUIDE = """[B]Defta kurulum rehberi[/B]

[B]1. Anahtarlar[/B]  (Ayarlar > Genel ve Kaynaklar)
 • OpenAI API anahtarı: platform.openai.com > API keys
 • OpenSubtitles: opensubtitles.com > hesabım > API consumers. 'Allow anonymous downloads' ve 'Under dev' işaretli olsun.
   Kullanıcı adı ve şifre isteğe bağlıdır (girilirse kendi hesabınızın indirme hakkı kullanılır).
 • SubDL: subdl.com > hesap > API
 • SubSource: subsource.net > hesap > API sekmesi
 • Jimaku (anime, isteğe bağlı): jimaku.cc > hesap
 Anahtarı olmayan kaynak atlanır. Her kaynak ayrı ayrı kapatılabilir.
 Kumandayla yazmak zorsa: anahtarları "OPENAI_API_KEY=..." satırlarıyla bir metin dosyasına yazın,
 USB belleğe ya da ağ klasörüne koyun ve Genel > "Anahtar dosyasını seç" ile gösterin.

[B]2. Kodi'de varsayılan altyazı servisi[/B]
 Ayarlar > Oynatıcı > Dil > Varsayılan film/dizi servisi: Defta

[B]3. Kullanım[/B]
 Video oynarken altyazı simgesi > İndir > Defta. Liste:
 • En üstte hedef dildeki insan çevirileri
 • Defta AI: daha önce yaptığınız çeviriler (ücretsiz) ve 'baştan yeniden çevir'
 • → AI çeviri: seçince çeviri başlar. Önce orijinal altyazı görünür, çevrilen parçalar geldikçe yerine geçer;
   anlam kontrolü bitince kontrol edilmiş hali yüklenir.
 • Yüklenen altyazıyı çevir: başka eklentiyle (a4k vb.) seçtiğiniz altyazıyı çevirir.
   Videonun içine gömülü altyazılar çevrilemez.

[B]4. Maliyet[/B]
 Bir dizi bölümü genelde birkaç sent tutar. Aynı altyazı ikinci kez açıldığında hazır çeviri kullanılır, ücret yoktur.

[B]5. Sorun olursa[/B]
 Ayarlar > Yardım > Bağlantıları test et. Temizlik sekmesinden kalıntıları silebilirsiniz.
"""


def clean(what: str) -> None:
    dlg = xbmcgui.Dialog()
    series = ""
    if what == "glossary":
        names = store.glossary_names()
        if not names:
            dlg.ok("Defta", "Kayıtlı seri sözlüğü yok.")
            return
        i = dlg.select("Hangi seri sözlüğü silinsin?", ["[Tümü]"] + [n[1] for n in names])
        if i < 0:
            return
        series = "" if i == 0 else names[i - 1][0]
        if not dlg.yesno("Defta", f"{'Tüm sözlükler' if i == 0 else names[i - 1][1]} silinsin mi?"):
            return
    elif not dlg.yesno("Defta", CLEAN_TEXT.get(what, "Silinsin mi?")):
        return
    msg = store.clean(what, series)
    kodi.log(f"Temizlik ({what}): {msg}")
    dlg.notification("Defta", msg, xbmcgui.NOTIFICATION_INFO, 4000, False)


def test() -> None:
    lines = []
    prog = xbmcgui.DialogProgress()
    prog.create("Defta", "Bağlantılar test ediliyor...")
    try:
        from .translate import build_settings
        from subtrans.provider import ProviderError, make_provider
        key_state = kodi.describe_secret("openai_key")
        if kodi.secret("openai_key"):
            settings, _ = build_settings()
            try:
                p = make_provider(settings["provider"])
                try:
                    status, payload = p._request("GET", "/models/" + p.model)   # eşzamanlı: asyncio gerekmez
                finally:
                    p.pool.shutdown(wait=True)
                if status == 200:
                    lines.append(f"[B]OpenAI[/B]: çalışıyor, '{p.model}' modeline erişim var")
                elif status == 401:
                    lines.append(f"[B]OpenAI[/B]: HATA: {p._auth_401(p._err(payload))}")
                else:
                    lines.append(f"[B]OpenAI[/B]: HATA (HTTP {status}): {p._err(payload)[:150]}")
            except ProviderError as e:
                lines.append(f"[B]OpenAI[/B]: HATA: {e}")
        else:
            lines.append(f"[B]OpenAI[/B]: anahtar {key_state}")
        for i, (name, mod) in enumerate(ALL.items()):
            prog.update(int(100 * (i + 1) / (len(ALL) + 1)), f"{LABEL[name]}...")
            flag = {"opensubtitles": "os", "subdl": "subdl", "subsource": "subsource", "jimaku": "jimaku"}[name]
            on = kodi.setting_bool(f"{flag}_enabled", name != "jimaku")
            keyname = f"{flag}_key"
            if not on:
                lines.append(f"[B]{LABEL[name]}[/B]: kapalı")
            elif not kodi.secret(keyname):
                lines.append(f"[B]{LABEL[name]}[/B]: anahtar girilmemiş, atlanıyor")
            else:
                try:
                    lines.append(f"[B]{LABEL[name]}[/B]: {mod.check()}")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"[B]{LABEL[name]}[/B]: HATA: {type(e).__name__}: {e}")
    finally:
        prog.close()
    lines.append("")
    lines.append(f"Çeviri dili: {kodi.target_lang()}  ·  kaynak dilleri: {', '.join(kodi.source_langs())}")
    lines.append(f"Anahtar dosyası: {kodi.setting('key_file') or 'yok'}  ·  OpenAI anahtarı: {kodi.describe_secret('openai_key')}")
    lines.append(f"Ayrıntılı kayıt: {'açık' if kodi.setting_bool('debug') else 'kapalı'}  ·  "
                 f"kayıt kopyası: {kodi.setting('debug_dir') or 'yok'}")
    lines.append(f"Defta {kodi.version()}  ·  Python {sys.version.split()[0]}")
    text = "\n".join(lines)
    kodi.log("Bağlantı testi:\n" + text.replace("[B]", "").replace("[/B]", ""))
    xbmcgui.Dialog().textviewer("Defta: bağlantı testi", text)


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "clean":
            clean(argv[2] if len(argv) > 2 else "")
        elif cmd == "test":
            test()
        elif cmd == "pickkeys":
            path = xbmcgui.Dialog().browse(1, "Anahtar dosyasını seçin (.env ya da .txt)", "files")
            if path:
                kodi.addon().setSetting("key_file", path)
                n = sum(1 for k in ("openai_key", "os_key", "subdl_key", "subsource_key", "jimaku_key")
                        if kodi.secret(k))
                xbmcgui.Dialog().ok("Defta", f"Anahtar dosyası: {path}\n{n} anahtar bulundu.")
        elif cmd == "logs":
            dest = kodi.setting("debug_dir").strip()
            if not dest:
                dest = xbmcgui.Dialog().browse(3, "Kayıtlar hangi klasöre kopyalansın?", "files")
            copied = kodi.export_kodi_logs(dest) if dest else []
            xbmcgui.Dialog().ok("Defta", ("Kopyalandı: " + ", ".join(copied) + f"\n{dest}") if copied
                                else "Kayıt kopyalanmadı (klasör seçilmedi ya da erişilemedi).")
        elif cmd == "guide":
            xbmcgui.Dialog().textviewer("Defta", GUIDE)
    except Exception as e:  # noqa: BLE001
        import traceback
        kodi.log(f"Araç hatası ({cmd}): {type(e).__name__}: {e}\n{traceback.format_exc()}", "error")
        xbmcgui.Dialog().ok("Defta", f"Hata: {type(e).__name__}: {e}")
