"""Kodi'nin altyazı penceresi: arama listesi ve seçilen satırın indirilmesi.

Liste sırası (yukarıdan aşağı):
  1. hedef dilde insan çevirileri (en fazla 10)
  2. hazır AI çevirileri + 'baştan yeniden çevir'  (+ süren çeviri)
  3. çevrilecek yabancı altyazılar (her kaynaktan ayardaki sayı kadar)
  4. 'yüklenen altyazıyı çevir'
Ücretli çeviri satırı, 'otomatik çeviri' kapalıyken hiçbir zaman ilk sırada olmaz: Kodi'nin
'ilk altyazıyı otomatik indir' ayarı açıksa en üste zararsız bir bilgi satırı konur."""
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
    kodi.log(f"Arama: {m.label()} imdb={m.imdb or '-'} dosya={m.filename or '-'} anime={m.is_anime}")
    tgt = kodi.target_lang()
    res = search.search(m)
    ready = store.ready_translations(m)
    running = [j for j in store.list_jobs() if j.get("status") in ("pending", "running")
               and Media.from_dict(j["media"]).key() == m.key()]
    md = json.dumps(m.to_dict(), ensure_ascii=False)
    free_first = bool(res.human or ready)
    if not free_first and res.foreign and not kodi.setting_bool("auto_translate") and _kodi_downloadfirst():
        _add(handle, langs.kodi_name(tgt), f"Defta: {langs.tr_name(tgt)} altyazı yok — çevirmek için "
             "aşağıdan bir altyazı seçin", tgt, _url(action="download", kind="info"))
    tname = langs.tr_name(tgt)
    for s in res.human:
        _add(handle, langs.kodi_name(tgt), f"{_short(s.release or s.sid)}  ·  {LABEL[s.source]}", tgt,
             _url(action="download", kind="human", sub=json.dumps(s.to_dict()), media=md),
             sync=s.sync, hi=s.hi, rating=_rating(s))
    for j in running:
        sub = Sub.from_dict(j["sub"])
        _add(handle, langs.kodi_name(tgt), f"[B]Defta AI çeviri sürüyor[/B]  ·  {_short(sub.release or sub.sid, 50)}",
             tgt, _url(action="download", kind="running", job=j["id"]), sync=True, rating="5")
    for e in ready:
        src = Sub.from_dict(e["src"])
        _add(handle, langs.kodi_name(tgt), f"[B]Defta AI[/B] ({langs.ablative(langs.tr_name(src.lang))})  ·  "
             f"{_short(src.release or src.sid, 50)}", tgt,
             _url(action="download", kind="ai", path=e["path"], media=md), sync=src.sync, rating="5")
        _add(handle, langs.kodi_name(tgt), f"[I]Baştan yeniden çevir[/I]  ·  {_short(src.release or src.sid, 50)}", tgt,
             _url(action="download", kind="retranslate", sub=json.dumps(src.to_dict()), media=md), rating="0")
    for s in res.foreign:
        _add(handle, langs.kodi_name(s.lang), f"→ {tname} AI çeviri  ·  {_short(s.release or s.sid, 50)}  ·  "
             f"{LABEL[s.source]}" + ("  ·  SDH" if s.hi else ""), s.lang,
             _url(action="download", kind="translate", sub=json.dumps(s.to_dict()), media=md),
             sync=s.sync, hi=s.hi, rating=_rating(s))
    _add(handle, langs.kodi_name(tgt), f"→ Yüklenen altyazıyı {langs.dative(tname)} çevir (başka eklentiyle seçtiğiniz)", tgt,
         _url(action="download", kind="loaded", media=md))
    kodi.log(f"Liste: {len(res.human)} insan çevirisi, {len(ready)} hazır AI, {len(res.foreign)} çevrilecek"
             + (f"; hatalar: {' | '.join(res.errors)}" if res.errors else ""))
    # Aynı hata (ör. erişim engeli) her aramada değil, günde bir kez bildirilir
    fresh = [e for e in res.errors if store.should_warn(e)]
    if fresh:
        kodi.notify(_short(fresh[0], 120), "Defta: kaynak hatası", 7000)


def _give(handle: int, path: str) -> None:
    li = xbmcgui.ListItem(label=path)
    xbmcplugin.addDirectoryItem(handle=handle, url=path, listitem=li, isFolder=False)


def _start_job(m: Media, sub: Sub, src: str, fresh: bool) -> None:
    if not kodi.secret("openai_key"):
        kodi.notify("OpenAI API anahtarı girilmemiş (Defta ayarları > Genel)", "Defta", 8000)
        return
    job = store.running_job_for(m, sub)
    if job is None:
        job = store.new_job(m, sub, src, fresh=fresh, load=True)
    mode = kodi.setting_int("mode", 0)
    kodi.notify("Çeviri başladı. " + ("Bitince yüklenecek." if mode == 1 else "Parça parça yüklenecek."),
                f"Defta: {langs.tr_name(kodi.target_lang())} çeviri", 6000)
    kodi.log(f"İş kuyruğa alındı: {job['id']} ({sub.source}:{sub.release or sub.sid})")


def do_download(handle: int, params: dict) -> None:
    kind = params.get("kind", "")
    if kind == "info":
        return
    m = Media.from_dict(json.loads(params["media"])) if params.get("media") else from_player()
    tgt = kodi.target_lang()
    try:
        if kind == "human":
            sub = Sub.from_dict(json.loads(params["sub"]))
            _give(handle, fetch.for_kodi(fetch.get(sub, m), m.label(), tgt))
        elif kind == "ai":
            _give(handle, fetch.for_kodi(params["path"], m.label() + " Defta", tgt))
        elif kind == "running":
            job = next((j for j in store.list_jobs() if j["id"] == params.get("job")), None)
            path = (job or {}).get("last_file") or (job or {}).get("src_file")
            if path and os.path.exists(path):
                _give(handle, fetch.for_kodi(path, m.label(), tgt))
        elif kind in ("translate", "retranslate"):
            sub = Sub.from_dict(json.loads(params["sub"]))
            src = fetch.get(sub, m)
            _start_job(m, sub, src, fresh=(kind == "retranslate"))
            # Çeviri gelene kadar orijinal altyazı gösterilir; Türkçe parçalar geldikçe yerine geçer
            _give(handle, fetch.for_kodi(src, m.label(), sub.lang))
        elif kind == "loaded":
            path = fetch.newest_loaded_subtitle()
            if not path:
                kodi.notify("Yüklenmiş bir altyazı dosyası bulunamadı. Videonun içindeki (gömülü) altyazılar "
                            "çevrilemiyor; önce başka bir eklentiyle bir altyazı seçin.", "Defta", 9000)
                return
            lang = kodi.source_langs(m.is_anime)[0]
            with open(path, "rb") as f:
                head = f.read(20000).decode("utf-8", "ignore")
            if any("぀" <= ch <= "ヿ" for ch in head):
                lang = "ja"
            sub = Sub(source="local", sid=path, lang=lang, release=os.path.basename(path), releases=[os.path.basename(path)])
            kodi.log(f"Yüklenen altyazı bulundu: {path}")
            _start_job(m, sub, path, fresh=False)
            _give(handle, fetch.for_kodi(path, m.label(), lang))
    except SourceError as e:
        kodi.log(f"İndirme hatası: {e}", "warning")
        kodi.notify(str(e)[:150], "Defta", 8000)


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
        kodi.notify(f"Hata: {type(e).__name__}: {str(e)[:100]}", "Defta", 8000)
    finally:
        xbmcplugin.endOfDirectory(handle)
