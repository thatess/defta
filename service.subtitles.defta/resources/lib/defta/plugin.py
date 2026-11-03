"""Kodi'nin altyazı penceresi: arama listesi ve seçilen satırın indirilmesi.

Liste sırası (yukarıdan aşağı):
  1. ▸ etkin altyazı: Defta'nın bu videoya en son yüklediği (insan çevirisi, hazır ya da süren AI çevirisi);
     aşağıda tekrarlanmaz. Kodi ilk satırı kendiliğinden indirirse bunu indirir (zararsız, ücretsiz).
  2. 'Kaymış · sıradakine geç' (etkin altyazı varsa; hep ikinci, Kodi kendiliğinden seçmez)
  3. hedef dilde insan çevirileri (en fazla 10)
  4. hazır AI çevirileri (etkin olan hariç, en çok 3) ve süren çeviriler
  5. çevrilecek yabancı altyazılar (her kaynaktan ayardaki sayı kadar)
  6. 'Baştan çevir (ücretli)' — yalnızca etkin AI çevirisi için
  7. 'Yükleneni çevir' — yalnızca bu video başladıktan sonra başka eklentiyle altyazı yüklendiyse
Her grupta: video karması eşleşen ('senkron uyumlu') en üstte, sonra önceki bölümde seçilenin devamı,
sonra sürüm uyumu; 'kaymış' işaretlenenler en altta.
Etkin altyazı yokken ücretli çeviri satırı, 'otomatik çeviri' kapalıyken hiçbir zaman ilk sırada olmaz:
Kodi'nin 'ilk altyazıyı otomatik indir' ayarı açıksa en üste zararsız bir bilgi satırı konur."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin

from . import fetch, kodi, langs, search, store
from .media import Media, from_player, parse_filename
from .sources import LABEL
from .sources.base import SourceError, Sub


def _url(**kw) -> str:
    return sys.argv[0] + "?" + urllib.parse.urlencode(kw)


def _rating(sub: Sub) -> str:
    if sub.rating:
        return str(max(0, min(5, int(round(sub.rating / 2)))))
    d = sub.downloads
    return "5" if d > 20000 else "4" if d > 5000 else "3" if d > 1000 else "2" if d > 100 else "1" if d else "0"


def _add(handle: int, label: str, label2: str, flag: str, url: str, sync=False, hi=False, rating="0") -> None:
    li = xbmcgui.ListItem(label=label, label2=label2)
    li.setArt({"icon": rating, "thumb": flag})
    li.setProperty("sync", "true" if sync else "false")
    li.setProperty("hearing_imp", "true" if hi else "false")
    xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=li, isFolder=False)


def _kodi_downloadfirst() -> bool:
    try:
        r = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "Settings.GetSettingValue",
                                                       "params": {"setting": "subtitles.downloadfirst"}})))
        return bool((r.get("result") or {}).get("value"))
    except Exception:  # noqa: BLE001
        return False


def _short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _note(s: Sub) -> str:
    return f"  ·  [I]{s.note}[/I]" if s.note else ""


MARK = "[B]•[/B]"     # etkin altyazının işareti (beyaz: seçili mavi satırda da görünsün; ▸ Kodi yazı tipinde yok)
READY_MAX = 3


def _synced(sub: Sub, bad: set) -> bool:
    """SYNC işareti yalnızca hak ediliyorsa: video karması eşleşmiş ya da sürüm adı uyuyor, kaymış değil.
    AI çevirisi zamanlamayı değiştirmez; kaynağının durumunu taşır."""
    return bool(sub.sync or sub.hash_match) and sub.uid() not in bad


def _ai(src: Sub) -> str:
    """'AI (EN)': hangi dilden çevrildiği."""
    return f"[B]AI[/B] ({(langs.norm(src.lang) or src.lang or '?').upper()})"


def do_search(handle: int, params: dict) -> None:
    m = from_player()
    manual = (params.get("searchstring") or "").strip()
    if manual:
        p = parse_filename(manual)
        m.title = p["title"] or manual
        if p["episode"]:
            m.season, m.episode = p["season"] or 1, p["episode"]
        if p["year"]:
            m.year = p["year"]
        m.imdb = ""
    kodi.log(f"Arama: {m.label()} imdb={m.imdb or '-'} dosya={m.filename or '-'} anime={m.is_anime} "
             f"süre={m.duration:.0f}")
    tgt = kodi.target_lang()
    res = search.search(m)
    ready = store.ready_translations(m)
    bad = store.bad_uids(m)
    running = [j for j in store.list_jobs() if j.get("status") in ("pending", "running")
               and Media.from_dict(j["media"]).key() == m.key()]
    md = json.dumps(m.to_dict(), ensure_ascii=False)
    tname = langs.tr_name(tgt)
    loaded = store.get_loaded(m)
    cur = Sub.from_dict(loaded["sub"]) if loaded else None
    cur_kind = (loaded or {}).get("kind", "")
    cur_uid = cur.uid() if cur else ""
    run_cur = next((j for j in running if Sub.from_dict(j["sub"]).uid() == cur_uid), None) if cur else None
    prints = store.fingerprints()
    cur_fp = prints.get(cur_uid, "") if cur else ""

    def same_as_active(uid: str) -> bool:
        """Etkin altyazının kendisi ya da içeriği aynı başka kaynaktaki kopyası."""
        return bool(cur) and (uid == cur_uid or (bool(cur_fp) and prints.get(uid) == cur_fp))
    ready_cur = next((e for e in ready if e.get("uid") == cur_uid), None) if cur else None

    # 1. Etkin altyazı (Defta'nın bu videoya en son yüklediği): en üstte, yalnızca işaretle; aşağıda tekrarlanmaz.
    #    Kodi ilk satırı kendiliğinden indirirse bu satırı indirir: zararsız ve ücretsiz.
    active = False
    if cur is not None and cur_kind == "human":
        _add(handle, langs.kodi_name(tgt), f"{MARK} {_short(cur.release or cur.sid, 60)}  ·  "
             f"{LABEL.get(cur.source, cur.source)}", tgt,
             _url(action="download", kind="human", sub=json.dumps(cur.to_dict()), media=md), sync=_synced(cur, bad), rating="5")
        active = True
    elif run_cur is not None:
        _add(handle, langs.kodi_name(tgt), f"{MARK} {_ai(cur)} çevriliyor  ·  {_short(cur.release or cur.sid, 50)}",
             tgt, _url(action="download", kind="running", job=run_cur["id"]), sync=_synced(cur, bad), rating="5")
        active = True
    elif ready_cur is not None:
        _add(handle, langs.kodi_name(tgt), f"{MARK} {_ai(cur)}  ·  {_short(cur.release or cur.sid, 55)}", tgt,
             _url(action="download", kind="ai", path=ready_cur["path"], sub=json.dumps(cur.to_dict()), media=md),
             sync=_synced(cur, bad), rating="5")
        active = True
    if not active:
        free_first = bool(res.human or ready)
        if not free_first and res.foreign and not kodi.setting_bool("auto_translate") and _kodi_downloadfirst():
            # Kodi ilk satırı kendiliğinden indirecek: ücretli çeviri ilk sırada olmasın
            _add(handle, langs.kodi_name(tgt), f"{tname} altyazı yok · çevirmek için aşağıdan seç", tgt,
                 _url(action="download", kind="info"))
    else:
        # 2. Kaymış: her zaman ikinci (Kodi'nin kendiliğinden seçtiği ilk satır asla bu değildir)
        _add(handle, langs.kodi_name(tgt), "Kaymış  ·  sıradakine geç", tgt,
             _url(action="download", kind="badsync", media=md))
    # 3. İnsan çevirileri
    for s in res.human:
        if active and cur_kind == "human" and same_as_active(s.uid()):
            continue
        _add(handle, langs.kodi_name(tgt), f"{_short(s.release or s.sid)}  ·  {LABEL[s.source]}{_note(s)}", tgt,
             _url(action="download", kind="human", sub=json.dumps(s.to_dict()), media=md),
             sync=s.sync, hi=s.hi, rating=_rating(s))
    # 4. Hazır AI çevirileri (etkin olan hariç, en çok 3) ve süren başka çeviriler
    for j in running:
        sub = Sub.from_dict(j["sub"])
        if run_cur is not None and j["id"] == run_cur["id"]:
            continue
        _add(handle, langs.kodi_name(tgt), f"{_ai(sub)} çevriliyor  ·  {_short(sub.release or sub.sid, 50)}",
             tgt, _url(action="download", kind="running", job=j["id"]), sync=_synced(sub, bad), rating="5")
    shown, seen_fp = 0, set()
    for e in ready:
        if active and same_as_active(e.get("uid", "")):
            continue
        fp = prints.get(e.get("uid", ""))
        if fp and fp in seen_fp:
            continue                            # aynı kaynaktan (başka siteden) yapılmış ikinci çeviri
        if shown >= READY_MAX:
            break
        shown += 1
        if fp:
            seen_fp.add(fp)
        src = Sub.from_dict(e["src"])
        mark = "  ·  [I]kaymış[/I]" if e.get("uid") in bad else ""
        _add(handle, langs.kodi_name(tgt), f"{_ai(src)}  ·  {_short(src.release or src.sid, 55)}{mark}", tgt,
             _url(action="download", kind="ai", path=e["path"], sub=json.dumps(src.to_dict()), media=md),
             sync=src.sync and not mark, rating="5" if not mark else "1")
    # 5. Çevrilecek yabancı altyazılar
    for s in res.foreign:
        if active and cur_kind != "human" and same_as_active(s.uid()):
            continue                            # etkin çevirinin kaynağı: yeniden çevirmek için "Baştan çevir"
        _add(handle, langs.kodi_name(s.lang), f"→ Çevir  ·  {_short(s.release or s.sid, 50)}  ·  "
             f"{LABEL[s.source]}" + ("  ·  SDH" if s.hi else "") + _note(s), s.lang,
             _url(action="download", kind="translate", sub=json.dumps(s.to_dict()), media=md),
             sync=s.sync, hi=s.hi, rating=_rating(s))
    # 6. Sondan ikinci: etkin AI çevirisini baştan çevir (yalnızca etkin olan için; başkası için önce onu seç)
    if ready_cur is not None and run_cur is None and cur_kind != "human":
        _add(handle, langs.kodi_name(tgt), f"Baştan çevir (ücretli)  ·  {_short(cur.release or cur.sid, 50)}", tgt,
             _url(action="download", kind="retranslate", sub=json.dumps(cur.to_dict()), media=md), rating="0")
    # 7. En altta: bu video başladıktan sonra başka eklentiyle yüklenen altyazı
    other = fetch.loaded_subtitle(fetch.playback_started(), tgt)
    if other:
        _add(handle, langs.kodi_name(tgt), f"Yükleneni çevir  ·  {_short(os.path.basename(other), 55)}", tgt,
             _url(action="download", kind="loaded", path=other, media=md))
    kodi.log(f"Liste: {len(res.human)} insan çevirisi, {len(ready)} hazır AI, {len(res.foreign)} çevrilecek"
             + (f", {n_hash} karma eşleşmesi" if (n_hash := sum(1 for s in res.human + res.foreign if s.hash_match)) else "")
             + (f"; hatalar: {' | '.join(res.errors)}" if res.errors else ""))
    # Aynı hata (ör. erişim engeli) her aramada değil, günde bir kez bildirilir
    fresh = [e for e in res.errors if store.should_warn(e)]
    if fresh:
        kodi.notify(_short(fresh[0], 90), "Defta: kaynak hatası", error=True)


def _give(handle: int, path: str) -> None:
    li = xbmcgui.ListItem(label=path)
    xbmcplugin.addDirectoryItem(handle=handle, url=path, listitem=li, isFolder=False)


def _position() -> float:
    """Videonun şu anki yeri (sn); oynatılmıyorsa 0."""
    try:
        p = xbmc.Player()
        return float(p.getTime() or 0) if p.isPlayingVideo() else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _start_job(m: Media, sub: Sub, src: str, fresh: bool) -> bool:
    if not kodi.secret("openai_key"):
        kodi.notify("OpenAI anahtarı yok (Ayarlar > Genel)", error=True)
        return False
    n = store.cancel_jobs_for(m, except_uid=sub.uid())          # son seçilen kazanır
    if n:
        kodi.log(f"Bu video için önceki {n} çeviri işi iptal edildi (yeni seçim)")
    job = store.running_job_for(m, sub)
    if job is None:
        job = store.new_job(m, sub, src, fresh=fresh, load=True, start_at=_position())
    elif not job.get("load"):
        job["load"] = True                                       # önceden başlamış (sıradaki bölüm) iş: bitince yüklensin
        store.save_job(job)
    kodi.notify("Çeviri başladı")
    kodi.log(f"İş kuyruğa alındı: {job['id']} ({sub.source}:{sub.release or sub.sid})")
    return True


def _warn_sync(state: str) -> None:
    if state in fetch.SYNC_NOTE:
        kodi.notify(fetch.SYNC_NOTE[state])


def _load_human(handle: int, m: Media, sub: Sub, tgt: str) -> None:
    path, state = fetch.get_checked(sub, m)
    n = store.cancel_jobs_for(m)                                 # insan çevirisi seçildi: süren çeviri durur
    if n:
        kodi.log(f"İnsan çevirisi seçildi, {n} çeviri işi iptal edildi")
    _give(handle, fetch.for_kodi(path, m.label(), tgt))
    store.set_loaded(m, "human", sub)
    store.remember_choice(m, sub)
    _warn_sync(state)


def _translate(handle: int, m: Media, sub: Sub, fresh: bool) -> None:
    if sub.source == "local":                   # başka eklentinin yüklediği dosya: indirilmez, kendisi çevrilir
        if not os.path.exists(sub.sid):
            kodi.notify("Altyazı dosyası artık yok", error=True)
            return
        src, state = sub.sid, "unknown"
    else:
        src, state = fetch.get_checked(sub, m)
    if _start_job(m, sub, src, fresh=fresh):
        store.set_loaded(m, "ai", sub)
        store.remember_choice(m, sub)
    _warn_sync(state)
    # Çeviri gelene kadar orijinal altyazı gösterilir
    _give(handle, fetch.for_kodi(src, m.label(), sub.lang))


def _bad_sync(handle: int, m: Media, tgt: str) -> None:
    loaded = store.get_loaded(m)
    if not loaded:
        kodi.notify("Defta altyazı yüklememiş")
        return
    cur = Sub.from_dict(loaded["sub"])
    store.mark_bad(m, cur)
    kodi.log(f"Kaymış işaretlendi: {cur.source}:{cur.release or cur.sid}")
    if loaded.get("kind") != "human":
        bad = store.bad_uids(m)
        e = next((e for e in store.ready_translations(m) if e.get("uid") not in bad), None)
        if e is not None:                                         # başka kaynaktan hazır çeviri: ücretsiz
            src = Sub.from_dict(e["src"])
            _give(handle, fetch.for_kodi(e["path"], m.label() + " Defta", tgt))
            store.set_loaded(m, "ai", src)
            kodi.notify("Kaymış · hazır çeviri yüklendi")
            return
    res = search.search(m)
    kind, nxt = search.next_candidate(res, cur.uid(), loaded.get("kind", ""))
    if nxt is None:
        store.cancel_jobs_for(m)
        kodi.notify("Kaymış · başka altyazı yok")
        return
    kodi.notify("Kaymış · sıradaki " + ("yüklendi" if kind == "human" else "çevriliyor"))
    if kind == "human":
        _load_human(handle, m, nxt, tgt)
    else:
        _translate(handle, m, nxt, fresh=False)


def do_download(handle: int, params: dict) -> None:
    kind = params.get("kind", "")
    if kind == "info":
        return
    m = Media.from_dict(json.loads(params["media"])) if params.get("media") else from_player()
    tgt = kodi.target_lang()
    try:
        if kind == "human":
            _load_human(handle, m, Sub.from_dict(json.loads(params["sub"])), tgt)
        elif kind == "ai":
            _give(handle, fetch.for_kodi(params["path"], m.label() + " Defta", tgt))
            if params.get("sub"):
                sub = Sub.from_dict(json.loads(params["sub"]))
                store.set_loaded(m, "ai", sub)
                store.remember_choice(m, sub)
        elif kind == "running":
            job = next((j for j in store.list_jobs() if j["id"] == params.get("job")), None)
            path = (job or {}).get("last_file") or (job or {}).get("src_file")
            if path and os.path.exists(path):
                _give(handle, fetch.for_kodi(path, m.label(), tgt))
        elif kind in ("translate", "retranslate"):
            _translate(handle, m, Sub.from_dict(json.loads(params["sub"])), fresh=(kind == "retranslate"))
        elif kind == "badsync":
            _bad_sync(handle, m, tgt)
        elif kind == "loaded":
            path = params.get("path", "")
            if not path or not os.path.exists(path):
                kodi.notify("Yüklenen altyazı bulunamadı", error=True)
                return
            lang = kodi.source_langs(m.is_anime)[0]
            with open(path, "rb") as f:
                head = f.read(20000).decode("utf-8", "ignore")
            if any("぀" <= ch <= "ヿ" for ch in head):
                lang = "ja"
            sub = Sub(source="local", sid=path, lang=lang, release=os.path.basename(path), releases=[os.path.basename(path)])
            kodi.log(f"Yüklenen altyazı bulundu: {path}")
            if _start_job(m, sub, path, fresh=False):
                store.set_loaded(m, "ai", sub)
            _give(handle, fetch.for_kodi(path, m.label(), lang))
    except SourceError as e:
        kodi.log(f"İndirme hatası: {e}", "warning")
        kodi.notify(str(e)[:90], error=True)


def main() -> None:
    handle = int(sys.argv[1])
    params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip("?")))
    action = params.get("action", "")
    try:
        if action in ("search", "manualsearch"):
            do_search(handle, params)
        elif action == "download":
            do_download(handle, params)
    except Exception as e:  # noqa: BLE001
        import traceback
        kodi.log(f"Beklenmeyen hata ({action}): {type(e).__name__}: {e}\n{traceback.format_exc()}", "error")
        kodi.notify(f"Hata: {str(e)[:80]}", error=True)
    finally:
        xbmcplugin.endOfDirectory(handle)
        kodi.flush_log()
