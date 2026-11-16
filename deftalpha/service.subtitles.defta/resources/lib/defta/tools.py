"""Ayarlardaki düğmeler: temizlik, bağlantı testi, dışa aktarma, kayıt klasörü, kurulum rehberi."""
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
    "syncmarks": "'Kaymış' işaretleri ve dizilerde 'önceki bölümde kullandığın' tercihleri silinsin mi?",
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
 Kumandayla yazmak zorsa: anahtarları "OPENAI_API_KEY=..." satırlarıyla bir metin dosyasına (.env / .txt) yazın,
 USB belleğe, Shield'ın kendi belleğine ya da ağ klasörüne koyun ve Genel > "Anahtarları dosyadan içe aktar"
 ile seçin. Anahtarlar Defta'nın ayarlarına kopyalanır; sonra dosya gerekmez (silebilirsiniz).

[B]2. Kodi'de varsayılan altyazı servisi[/B]
 Ayarlar > Oynatıcı > Dil > Varsayılan film/dizi servisi: Defta

[B]3. Kullanım[/B]
 Video oynarken altyazı simgesi > İndir > Defta. Liste:
 • En üstte '•' işaretli satır: şu an yüklü (etkin) altyazı. Hemen altında 'Kaymış · sıradakine geç':
   altyazı senkron değilse seçin; işaretlenir, alta iner, sıradaki yüklenir ya da çevrilir.
 • Hedef dildeki insan çevirileri. 'senkron uyumlu' yazanlar tam bu video dosyası için yapılmıştır.
   Dizide 'önceki bölümde kullandığın' yazan, bir önceki bölümde seçtiğiniz çevirmenin/sürümün devamıdır.
 • AI (EN): daha önce yaptığınız çeviriler (ücretsiz; parantezde hangi dilden çevrildiği).
 • → Çevir: seçince çeviri başlar. Önce orijinal altyazı görünür, birkaç saniyede hızlı çeviri,
   anlam kontrolü bitince kontrol edilmiş hali yüklenir. Başka bir satır seçerseniz önceki çeviri durur;
   aynı satırı yeniden seçerseniz kaldığı yerden sürer (biten kısım tekrar ödenmez).
 • Baştan çevir (ücretli): yalnızca etkin AI çevirisi için. Başkasını baştan çevirmek için önce onu seçin.
 • Yükleneni çevir: bu video başladıktan sonra başka eklentiyle (a4k vb.) altyazı seçtiyseniz
   dosya adıyla görünür ve onu çevirir. Videonun içine gömülü altyazılar çevrilemez.
 • Jimaku açıksa her videoda sorulur; animenin Japonca altyazısı (doğru sezonun) listeye gelir.
   "Animede Japonca altyazıyı öne al" açıksa en üstte, kapalıysa İngilizcelerin altında.
 • 'hak doldu · 03:00': o sitenin günlük indirme hakkı doldu, satır o saate kadar alta iner.
   'bekleniyor · 10 dk': site yoğun. Aynı altyazı başka sitede de varsa oradan sessizce indirilir.
 Altyazı videodan uzunsa uyarılır; fark yalnızca kare hızındansa (25 / 23.976) zamanlama kendiliğinden düzeltilir.

[B]4. Sıradaki bölüm[/B]  (Ayarlar > Otomatik)
 Bölümün %90'ı (ayardan %80–95) izlenince sıradaki bölüm arkada çevrilir. 'Sor' seçiliyse sağ üstte küçük bir kutu çıkar,
 video durmaz; alttaki çubuk 15 saniyede boşalınca çevrilir. 'İptal' ya da Geri tuşuyla vazgeçersiniz.
 Kutuyu Yardım > 'Sıradaki bölüm kutusunu dene' ile görebilirsiniz. Sıradaki bölümde Türkçe altyazı varsa sorulmaz.
 Sezon finalinde yeni sezonun ilk bölümü çevrilir. Olmazsa bildirim gelir.
 Canlı yayında (TV kanalı) ve tanınmayan videolarda kendiliğinden altyazı aranmaz.

[B]Şarkı sözleri[/B]  (Ayarlar > Altyazı)
 Çevir: yalnızca anlamı. Orijinal bırak: sözler Latin harfleriyle olduğu gibi (Japonca yazı romajiye çevrilir).
 İkisi birden: orijinal, hemen altında parantez içinde anlamı.

[B]5. Maliyet[/B]
 Bir dizi bölümü genelde birkaç sent tutar. Aynı altyazı ikinci kez açıldığında hazır çeviri kullanılır, ücret yoktur.
 OpenAI kredisi biterse çeviri hemen durur, bildirim gelir; kendiliğinden çeviriler başlamaz. Kredi yükleyince
 listeden seçmen yeter: biten kısım tekrar ödenmez.

[B]6. Sorun olursa[/B]
 Ayarlar > Yardım > Bağlantıları test et. Temizlik sekmesinden kalıntıları silebilirsiniz.
 Kayıtları bilgisayara almak için Yardım > kayıt klasörü olarak paylaşılan bir ağ klasörü (smb://...) seçin.
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
    old_file = kodi.setting("key_file").strip()
    lines.append(f"OpenAI anahtarı: {kodi.describe_secret('openai_key')}"
                 + (f"  ·  hâlâ dosyadan okunuyor: {old_file} (Genel > Anahtarları dosyadan içe aktar)" if old_file else ""))
    lines.append(f"Ayrıntılı kayıt: {'açık' if kodi.setting_bool('debug') else 'kapalı'}  ·  "
                 f"kayıt kopyası: {kodi.setting('debug_dir') or 'yok'}")
    lines.append(f"Defta {kodi.version()}  ·  Python {sys.version.split()[0]}")
    text = "\n".join(lines)
    kodi.log("Bağlantı testi:\n" + text.replace("[B]", "").replace("[/B]", ""))
    xbmcgui.Dialog().textviewer("Defta: bağlantı testi", text)


KEY_FILE_NAMES = (".env", "defta-anahtarlar.txt", "anahtarlar.txt", "defta.env", ".env.txt", "env.txt", "keys.txt")


def _find_key_file(folder: str) -> str:
    import xbmcvfs
    for name in KEY_FILE_NAMES:
        p = kodi.vfs_join(folder, name)
        try:
            if xbmcvfs.exists(p):
                return p
        except Exception:  # noqa: BLE001
            continue
    return ""


def _tail(v: str) -> str:
    return f"…{v[-4:]}" if len(v) > 6 else "…"


def import_keys(path: str = "") -> dict:
    """Anahtar dosyasındaki (.env / .txt) değerleri Defta ayarlarına kopyalar. Dosyaya dokunulmaz.
    Sonra dosya gerekmez: bilgisayar kapalıyken ya da USB çıkarılınca da çalışır.
    Dönüş: {ayar: "yeni" | "değişti" | "aynı"}"""
    dlg = xbmcgui.Dialog()
    if not path:
        # Kodi'nin dosya seçicisi adı noktayla başlayan dosyaları (.env) gizler: klasör seçilince içinde aranır
        i = dlg.select("Anahtarlar nerede?", ["Klasörü seç (içindeki .env ya da defta-anahtarlar.txt)",
                                              "Dosyayı seç (.txt)"])
        if i < 0:
            return {}
        if i == 0:
            folder = dlg.browse(0, "Anahtar dosyasının olduğu klasör", "files")
            if not folder:
                return {}
            path = _find_key_file(folder)
            if not path:
                dlg.ok("Defta", "Bu klasörde anahtar dosyası bulunamadı.\nAranan adlar: " + ", ".join(KEY_FILE_NAMES))
                return {}
        else:
            path = dlg.browse(1, "Anahtar dosyasını seçin (.env ya da .txt)", "files")
            if not path:
                return {}
    try:
        values = kodi.parse_env(kodi.read_text(path))
    except (OSError, RuntimeError) as e:
        dlg.ok("Defta", f"Dosya okunamadı: {e}")
        return {}
    found = {}
    for key in kodi.KEY_SETTINGS:
        v = values.get(kodi.ENV_NAMES[key], "").strip()
        if v:
            found[key] = v
    if not found:
        dlg.ok("Defta", "Dosyada tanınan anahtar yok.\nSatırlar şöyle olmalı: OPENAI_API_KEY=...  SUBDL_API_KEY=...")
        return {}
    addon = kodi.addon()
    status = {}
    for key, v in found.items():
        cur = (addon.getSetting(key) or "").strip()
        status[key] = "aynı" if cur == v else ("değişti" if cur else "yeni")
    changed = [kodi.KEY_LABELS[k] for k, st in status.items() if st == "değişti"]
    if changed and not dlg.yesno("Defta", ", ".join(changed) + " değişecek. Üzerine yazılsın mı?",
                                 nolabel="İptal", yeslabel="Yaz"):
        return {}
    for key, v in found.items():
        addon.setSetting(key, v)
    addon.setSetting("key_file", "")                 # artık dosyaya bağlı değil
    kodi.forget_secret_cache()
    lines = []
    for key in kodi.KEY_SETTINGS:
        label = kodi.KEY_LABELS[key]
        if key in found:
            note = {"değişti": "  ← değişti", "aynı": "  (aynıydı)", "yeni": ""}[status[key]]
            shown = "girildi" if key in ("os_user", "os_pass") else f"var ({_tail(found[key])})"
            lines.append(f"{label}: {shown}{note}")
        else:
            lines.append(f"{label}: dosyada yok" + (" (ayardaki kaldı)" if (addon.getSetting(key) or "").strip() else ""))
    kodi.log(f"Anahtarlar içe aktarıldı: {len(found)} değer ({', '.join(kodi.KEY_LABELS[k] for k in found)})")
    dlg.textviewer("Defta: anahtarlar içe aktarıldı", "\n".join(lines) + "\n\nArtık dosya gerekmiyor. "
                   "Güvenlik için dosyayı USB'den ya da indirilenlerden silebilirsin.")
    return status


def export() -> None:
    """Hazır AI çevirilerini ve dizi sözlüklerini okunur adlarla bir klasöre kopyalar (ağ klasörü olabilir)."""
    import os
    import re
    dest = xbmcgui.Dialog().browse(3, "AI çevirileri hangi klasöre kopyalansın?", "files",
                                   defaultt=kodi.setting("debug_dir").strip())
    if not dest:
        return
    root = kodi.profile_dir("translations")
    n, failed = 0, 0
    for key in sorted(os.listdir(root)):
        d = os.path.join(root, key)
        meta = store._read(os.path.join(d, "meta.json"), []) or []
        for e in meta:
            src = os.path.join(d, e.get("file", ""))
            if not e.get("file") or not os.path.exists(src):
                continue
            title = e.get("title") or key
            rel = (e.get("src") or {}).get("release") or ""
            name = re.sub(r'[\\/:*?"<>|]+', " ", f"{title} - {rel}" if rel else title).strip()[:120]
            lang = e["file"].rsplit(".", 2)[-2] if e["file"].count(".") >= 2 else "tr"
            if kodi.vfs_copy(src, kodi.vfs_join(dest, f"{name}.{lang}.srt")):
                n += 1
            else:
                failed += 1
    # Dizi sözlükleri de (isimler, terimler, hitaplar): okunur adla, incelemek için
    gdir = kodi.profile_dir("glossaries")
    g = 0
    for name in sorted(os.listdir(gdir)):
        if not name.endswith(".json") or name.startswith("."):          # .tmp_*: yarım kalmış yazım
            continue
        if kodi.vfs_copy(os.path.join(gdir, name), kodi.vfs_join(dest, "Sözlük - " + name)):
            g += 1
        else:
            failed += 1
    kodi.log(f"Dışa aktarma: {n} çeviri, {g} sözlük -> {dest}" + (f", {failed} kopyalanamadı" if failed else ""))
    xbmcgui.Dialog().ok("Defta", f"{n} AI çevirisi ve {g} dizi sözlüğü kopyalandı.\n{dest}"
                        + (f"\n{failed} dosya kopyalanamadı." if failed else ""))


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "clean":
            clean(argv[2] if len(argv) > 2 else "")
        elif cmd == "test":
            test()
        elif cmd == "importkeys":
            import_keys()
        elif cmd == "logs":
            dest = kodi.setting("debug_dir").strip()
            if not dest:
                dest = xbmcgui.Dialog().browse(3, "Kayıtlar hangi klasöre kopyalansın?", "files")
            copied = kodi.export_kodi_logs(dest) if dest else []
            xbmcgui.Dialog().ok("Defta", ("Kopyalandı: " + ", ".join(copied) + f"\n{dest}") if copied
                                else "Kayıt kopyalanmadı (klasör seçilmedi ya da erişilemedi).")
        elif cmd == "export":
            export()
        elif cmd == "pickdebugdir":
            path = xbmcgui.Dialog().browse(3, "Kayıtların kopyalanacağı klasör (ağ klasörü olabilir)", "files")
            if path:
                kodi.addon().setSetting("debug_dir", path)
                xbmcgui.Dialog().ok("Defta", f"Kayıt klasörü: {path}")
        elif cmd == "nextdemo":
            from . import nextdialog
            ok, why = nextdialog.ask("Sıradaki bölüm çevrilsin mi?", "Deneme · S01E05 (bir şey çevrilmez)", 15)
            kodi.log(f"Kutu denemesi: {'çevrilirdi' if ok else 'vazgeçildi'} ({why})")
            kodi.notify(("Çevrilirdi" if ok else "Vazgeçildi") + f" · {why}")
        elif cmd == "guide":
            xbmcgui.Dialog().textviewer("Defta", GUIDE)
    except Exception as e:  # noqa: BLE001
        import traceback
        kodi.log(f"Araç hatası ({cmd}): {type(e).__name__}: {e}\n{traceback.format_exc()}", "error")
        xbmcgui.Dialog().ok("Defta", f"Hata: {type(e).__name__}: {e}")
    finally:
        kodi.flush_log()
