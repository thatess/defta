"""Defta testleri (Kodi'siz). Önce: python tools/build.py   Sonra: python -m unittest tests/test_defta.py

Sahte Kodi modülleri (tests/fakekodi) ve yerel sahte OpenAI sunucusu kullanılır; hiçbir siteye istek gitmez."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "service.subtitles.defta"
sys.path.insert(0, str(Path(__file__).resolve().parent / "fakekodi"))
sys.path.insert(0, str(ADDON / "resources" / "lib"))

sys.modules.setdefault("_asyncio", None)    # Kodi'deki gibi saf Python asyncio
import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcplugin  # noqa: E402
import xbmcvfs  # noqa: E402

SAMPLE = Path(os.environ.get("SUBTRANS_DIR", ROOT.parent / "altyazi_cevirici")) / "ornek" / "kaynak" / \
    "Ornek.Anime.S01E01.en.srt"


class FakeOpenAI(BaseHTTPRequestHandler):
    requests = 0
    delay = 0.0
    credit_out = False          # True: OpenAI "kredi bitti" (429 insufficient_quota) döner

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer sk-test-key-000000000000000000000000000000000000":
            return self._send(401, {"error": {"message": "bad key"}})
        self._send(200, {"id": "gpt-6-luna"})

    def do_POST(self):
        FakeOpenAI.requests += 1
        if FakeOpenAI.delay:
            import time
            time.sleep(FakeOpenAI.delay)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if FakeOpenAI.credit_out:
            return self._send(429, {"error": {"message": "You exceeded your current quota.", "type": "insufficient_quota",
                                              "code": "insufficient_quota"}})
        user = json.loads(body["messages"][1]["content"])
        if "lines" in user:
            out = {"items": [{"i": it["i"], "translation": "TR-" + it["line"][::-1]} for it in user["lines"]]}
        elif "check" in user:
            out = {"fixes": []}
        elif "subtitle_text" in user:
            out = {"terms": [{"source": "Kael", "target": "Kael", "type": "character"}]}
        else:
            out = {"translations": [{"id": it["id"], "text": f"TR[{it['text']}]"} for it in user["translate"]]}
        self._send(200, {"choices": [{"message": {"content": json.dumps(out)}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        xbmcvfs.MAP.clear()
        xbmcvfs.MAP.update({
            "special://profile/addon_data/service.subtitles.defta/": str(cls.tmp / "profile"),
            "special://profile/addon_data/": str(cls.tmp / "addon_data"),
            "special://home/addons/service.subtitles.defta/": str(ADDON),
            "special://temp/": str(cls.tmp / "kodi_temp"),
        })
        (cls.tmp / "kodi_temp").mkdir()
        (cls.tmp / "addon_data").mkdir()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        xbmcaddon.SETTINGS.clear()
        xbmcaddon.SETTINGS.update({
            "openai_key": "sk-test-key-000000000000000000000000000000000000",
            "api_base": f"http://127.0.0.1:{cls.server.server_address[1]}/v1",
            "target_lang": "0", "src_lang": "1", "mode": "0", "credit": "Thates", "per_source": "1",
            "os_enabled": "true", "os_key": "x", "subdl_enabled": "true", "subdl_key": "y",
            "subsource_enabled": "false", "jimaku_enabled": "false", "demote_sdh": "true",
        })

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestMedia(Base):
    def test_parse(self):
        from defta.media import parse_filename
        p = parse_filename("Interstellar.2014.1080p.BluRay.x264-SPARKS.mkv")
        self.assertEqual((p["title"], p["year"], p["episode"]), ("Interstellar", 2014, 0))
        p = parse_filename("[SubsPlease] Sousou no Frieren - 05 (1080p) [ABCD1234].mkv")
        self.assertEqual((p["title"], p["season"], p["episode"], p["anime"]), ("Sousou no Frieren", 1, 5, True))
        p = parse_filename("https://x.y/dl/Spy.x.Family.S02E03.1080p.WEB-DL.mkv?token=abc")
        self.assertEqual((p["title"], p["season"], p["episode"]), ("Spy x Family", 2, 3))
        p = parse_filename("The.Office.US.3x07.HDTV.mkv")
        self.assertEqual((p["title"], p["season"], p["episode"]), ("The Office US", 3, 7))

    def test_from_player_library(self):
        from defta.media import from_player
        xbmc.INFO.clear()
        xbmc.INFO.update({"VideoPlayer.TVShowTitle": "Spy x Family", "VideoPlayer.Season": "1",
                          "VideoPlayer.Episode": "2", "VideoPlayer.IMDBNumber": "tt13706018",
                          "Player.Filenameandpath": "http://debrid/x/Spy.x.Family.S01E02.1080p.WEB-DL-GRP.mkv"})
        m = from_player()
        self.assertEqual((m.title, m.season, m.episode, m.imdb), ("Spy x Family", 1, 2, "tt13706018"))
        self.assertEqual(m.key(), "spy-x-family-s01e02")          # dizi adıyla: sıradaki bölüm önceden bilinir
        self.assertEqual(m.legacy_key(), "tt13706018-s01e02")     # 0.2'nin hazır çevirileri bulunabilsin
        xbmc.INFO.clear()

    def test_next_episode(self):
        from defta.media import Media
        m = Media(title="Spy x Family", season=1, episode=12, imdb="tt1", path="x.mkv", hash="ab",
                  filename="Spy.x.Family.S01E12.1080p-GRP.mkv")
        n = m.next_episode()
        self.assertEqual((n.season, n.episode, n.imdb, n.path, n.hash), (1, 13, "", "", ""))
        self.assertEqual(n.filename, "Spy.x.Family.S01E13.1080p-GRP.mkv")
        self.assertEqual(n.key(), "spy-x-family-s01e13")
        n2 = m.next_episode(next_season=True)
        self.assertEqual((n2.season, n2.episode), (2, 1))
        self.assertEqual(n2.filename, "Spy.x.Family.S02E01.1080p-GRP.mkv")


class TestRanking(Base):
    def test_score(self):
        from defta.search import match_score
        from defta.sources.base import Sub
        v = "Interstellar.2014.1080p.BluRay.x264-SPARKS.mkv"
        a = Sub("opensubtitles", "1", "en", release="Interstellar.2014.720p.BluRay.x264-SPARKS")
        b = Sub("opensubtitles", "2", "en", release="Interstellar.2014.1080p.WEB-DL.DD5.1.H264-RARBG")
        self.assertGreater(match_score(v, a), match_score(v, b))

    def test_subsource_episode_names(self):
        from defta.sources.subsource import _ep_of
        self.assertEqual(_ep_of(["[Coalgirls]_Shingeki_no_Kyojin_01_(1280x720_Blu-ray_FLAC)"]), (0, 1))
        self.assertEqual(_ep_of(["[Zero-Raws] Shingeki no Kyojin - 03 (MBS 1280x720 x264 AAC)"]), (0, 3))
        self.assertEqual(_ep_of(["[gg] Shingeki no Kyojin 1-6"]), (0, 0))
        self.assertEqual(_ep_of(["Shingeki no Kyojin (Attack on Titan) Season 1"]), (0, 0))
        self.assertEqual(_ep_of(["Attack.on.Titan.S01E03.1080p"]), (1, 3))

    def test_archive_episode(self):
        from defta.sources.base import pick_from_archive
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for e in (1, 2, 3):
                z.writestr(f"Show.S01E0{e}.srt", f"ep{e}")
        name, data = pick_from_archive(buf.getvalue(), 1, 2)
        self.assertEqual((name, data), ("Show.S01E02.srt", b"ep2"))


class TestSearchList(Base):
    def test_order_and_info_row(self):
        from defta import plugin
        from defta.sources import opensubtitles, subdl
        from defta.sources.base import Sub
        xbmc.INFO.clear()
        xbmc.INFO["Player.Filenameandpath"] = r"C:\kodi\Interstellar.2014.1080p.BluRay.x264-SPARKS.mkv"
        orig = (opensubtitles.search, subdl.search)
        opensubtitles.search = lambda m, w: [
            Sub("opensubtitles", "11", "en", release="Interstellar.2014.1080p.WEB-DL", downloads=900),
            Sub("opensubtitles", "12", "en", release="Interstellar.2014.BluRay.x264-SPARKS", downloads=50, hi=True),
            Sub("opensubtitles", "13", "en", release="Interstellar.2014.BluRay.x264-SPARKS", downloads=40)]
        subdl.search = lambda m, w: [Sub("subdl", "/subtitle/1.zip", "en", release="Interstellar.2014.BluRay.x264-SPARKS")]
        try:
            xbmc.JSONRPC["subtitles.downloadfirst"] = True
            xbmcplugin.ITEMS.clear()
            sys.argv = ["plugin://service.subtitles.defta/", "1", "?action=search&languages=Turkish"]
            plugin.main()
            labels = [li.label2 for _, li in xbmcplugin.ITEMS]
            self.assertIn("altyazı yok", labels[0])            # Kodi otomatik indirse de ücretli satır ilk değil
            self.assertIn("SPARKS", labels[1])                 # sürümü uyan, SDH olmayan önde
            self.assertNotIn("SDH", labels[1])
            self.assertFalse(any("Yüklenen altyazıyı" in x for x in labels), "başka eklentinin dosyası yokken satır çıkmaz")
        finally:
            opensubtitles.search, subdl.search = orig
            xbmc.JSONRPC.clear()
            xbmc.INFO.clear()


class TestKodiSafety(Base):
    def test_pure_python_asyncio(self):
        # Kodi'deki gibi temiz bir yorumlayıcıda: giriş betiği asyncio'dan önce _asyncio'yu engeller
        import subprocess
        code = ("import runpy, sys; sys.argv=['x']; sys.path.insert(0, %r); sys.path.insert(0, %r);"
                "import defta, asyncio, asyncio.tasks;"
                "print(sys.modules.get('_asyncio') is None, hasattr(asyncio.tasks, '_CTask'))"
                % (str(Path(__file__).resolve().parent / "fakekodi"), str(ADDON / "resources" / "lib")))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.split(), ["True", "False"], out.stderr)

    def test_dns_fallback(self):
        import socket
        from defta import net
        calls = []

        def broken(host, *a, **k):
            calls.append(host)
            if host == "api.subdl.com":
                raise socket.gaierror(11004, "getaddrinfo failed")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

        orig_gai, orig_lookup = net._orig_getaddrinfo, net._doh_lookup
        net._orig_getaddrinfo = broken
        net._doh_lookup = lambda host: ["104.21.1.1"] if host == "api.subdl.com" else []
        try:
            res = socket.getaddrinfo("api.subdl.com", 443, 0, socket.SOCK_STREAM)
            self.assertEqual(res[0][4], ("104.21.1.1", 443))
            with self.assertRaises(socket.gaierror):
                net._doh_lookup = lambda host: []
                socket.getaddrinfo("api.subdl.com", 443)
        finally:
            net._orig_getaddrinfo, net._doh_lookup = orig_gai, orig_lookup

    def test_sinkhole_and_refused_use_secure_dns(self):
        import socket
        from defta import net
        # Sunucu: yalnızca 127.0.0.1'de dinler; "sağlayıcı DNS'i" engel adresi verir
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        orig_gai, orig_lookup, orig_bogus = net._orig_getaddrinfo, net._doh_lookup, net._bogus

        def isp(host, p, *a, **k):
            if host == "api.example.org":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.sink, int(p or 0)))]
            return orig_gai(host, p, *a, **k)
        net._orig_getaddrinfo = isp
        net._doh_lookup = lambda host: ["127.0.0.1"] if host == "api.example.org" else []
        try:
            # 1) sahte adres (0.0.0.0 / 127.x): doğrudan güvenli DNS
            self.sink = "0.0.0.0"
            self.assertEqual(socket.getaddrinfo("api.example.org", port)[0][4], ("127.0.0.1", port))
            # 2) gerçek görünen ama bağlantıyı reddeden adres: bir kez güvenli DNS adresiyle yeniden denenir
            self.sink = "127.0.0.2"
            net._bogus = lambda addrs: False
            net._force_doh.clear()
            status, raw, _ = net.request("GET", f"http://api.example.org:{port}/models/x",
                                         headers={"Authorization": "Bearer sk-test-key-000000000000000000000000000000000000"},
                                         retries=0)
            self.assertEqual(status, 200)
            self.assertIn("api.example.org", net._force_doh)
            # yerel adlara (bilgisayar adı, .local) dokunulmaz
            self.assertTrue(net._is_local_name("mypc.local") and net._is_local_name("nas"))
            # ikisi de olmazsa hata, çözülen adresi de söyler
            net._doh_lookup = lambda host: []
            net._force_doh.clear()
            with self.assertRaises(net.HttpError) as cm:
                net.request("GET", f"http://api.example.org:{port}/x", retries=0)
            self.assertEqual(cm.exception.status, 0)
            self.assertIn("127.0.0.2", str(cm.exception))
        finally:
            net._orig_getaddrinfo, net._doh_lookup, net._bogus = orig_gai, orig_lookup, orig_bogus
            net._force_doh.clear()
            srv.shutdown()


class TestSecrets(Base):
    def test_subdl_key_never_stored(self):
        from defta.sources.base import Sub
        s = Sub("subdl", "/subtitle/1-2.zip?api_key=SECRETSECRET", "en")
        self.assertEqual(s.sid, "/subtitle/1-2.zip")
        self.assertNotIn("SECRET", s.uid())
        s2 = Sub.from_dict({"source": "subdl", "sid": "/subtitle/1-2.zip?api_key=SECRETSECRET", "lang": "en"})
        self.assertNotIn("SECRET", s2.sid + s2.uid())

    def test_log_scrubs_keys(self):
        from defta import kodi
        xbmcaddon.SETTINGS["subdl_key"] = "FAKEsubdlKXXXXXXXX"
        kodi._secret_cache["t"] = 0
        try:
            xbmc.LOGS.clear()
            kodi.log("adres https://dl.subdl.com/x.zip?api_key=FAKEsubdlKXXXXXXXX")
            self.assertNotIn("FAKEsubdlKXXXXXXXX", xbmc.LOGS[-1])
            with open(os.path.join(kodi.profile_dir(), "defta.log"), encoding="utf-8") as f:
                self.assertNotIn("FAKEsubdlKXXXXXXXX", f.read())
        finally:
            xbmcaddon.SETTINGS["subdl_key"] = "y"
            kodi._secret_cache["t"] = 0

    def test_scrub_old_files(self):
        from defta import kodi, store
        bad = os.path.join(kodi.profile_dir("downloads"), "subdl-_subtitle_1.zip_api_key_ABCDEFGHIJ.srt")
        open(bad, "w").write("x")
        j = os.path.join(kodi.profile_dir("jobs"), "old.json")
        open(j, "w").write('{"sid": "/subtitle/1.zip?api_key=ABCDEFGHIJKL"}')
        self.assertGreaterEqual(store.scrub_secrets(), 2)
        self.assertFalse(os.path.exists(bad))
        self.assertNotIn("ABCDEFGHIJKL", open(j).read())
        os.remove(j)


class TestTranslate(Base):
    def test_full_job_then_cache(self):
        from defta import store
        from defta.media import Media
        from defta.sources.base import Sub
        from defta.translate import Translator
        m = Media(title="Ornek Anime", season=1, episode=1, filename="Ornek.Anime.S01E01.mkv")
        sub = Sub("opensubtitles", "99", "en", release="Ornek.Anime.S01E01")
        src = store.download_path(sub, ".srt")
        shutil.copy(SAMPLE, src)
        updates = []
        job = store.new_job(m, sub, src, fresh=False, load=True)
        t = Translator(job, lambda p, st, pct: updates.append((st, pct, p)))
        out = t.run()
        stages = [u[0] for u in updates]
        self.assertEqual(stages, ["fast", "final"], "en çok iki yükleme: hızlı + kaliteli")
        fast_path = updates[0][2]
        self.assertTrue(os.path.exists(fast_path))
        self.assertIn("TR[", Path(fast_path).read_text(encoding="utf-8"))
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("TR[", text)
        self.assertNotIn("⟦", text)
        self.assertTrue(store.ready_translations(m))
        self.assertEqual(len([e for e in store.tr_meta(m)]), 1, "yalnızca kaliteli çeviri saklanır")
        n1 = FakeOpenAI.requests
        up2 = []
        Translator(store.new_job(m, sub, src, fresh=False, load=False), lambda p, st, pct: up2.append(st)).run()
        self.assertEqual(FakeOpenAI.requests, n1, "ikinci çeviri önbellekten gelmeli")
        self.assertEqual(up2, ["final"], "yüklenmeyecek işte hızlı çeviri yapılmaz")
        # 0.4.3: aynı altyazı yeniden ya da baştan çevrilince de (analiz kayıtlı) hızlı çeviri gelir
        up3 = []
        Translator(store.new_job(m, sub, src, fresh=True, load=True), lambda p, st, pct: up3.append(st)).run()
        self.assertEqual(up3, ["fast", "final"], "baştan çeviride de önce hızlı çeviri yüklenir")
        up4 = []
        Translator(store.new_job(m, sub, src, fresh=False, load=True), lambda p, st, pct: up4.append(st)).run()
        self.assertIn("final", up4)
        # Temizlik
        self.assertIn("silindi", store.clean("ai"))
        self.assertFalse(store.ready_translations(m))

    def test_stop_cancels_job(self):
        import asyncio
        from defta import store
        from defta.media import Media
        from defta.sources.base import Sub
        from defta.translate import Translator
        m = Media(title="Durdurma Testi", season=1, episode=2)
        sub = Sub("opensubtitles", "77", "en", release="x")
        src = store.download_path(sub, ".srt")
        shutil.copy(SAMPLE, src)
        t = Translator(store.new_job(m, sub, src, fresh=True, load=False), lambda *a: None)
        box = {}

        def go():
            try:
                t.run()
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
        FakeOpenAI.delay = 0.6
        try:
            th = threading.Thread(target=go)
            th.start()
            import time
            time.sleep(0.3)
            t.stop()
            th.join(15)
        finally:
            FakeOpenAI.delay = 0.0
        self.assertFalse(th.is_alive(), "durdurma sonrası iş parçacığı bitmeli")
        self.assertIsInstance(box.get("e"), asyncio.CancelledError)

    def test_bad_key(self):
        from defta.translate import build_settings
        from subtrans.provider import make_provider
        xbmcaddon.SETTINGS["openai_key"] = "sk-wrong"
        try:
            s, _ = build_settings()
            p = make_provider(s["provider"])
            status, payload = p._request("GET", "/models/" + p.model)
            p.pool.shutdown(wait=True)
            self.assertEqual(status, 401)
            self.assertNotIn("sk-wrong", p._auth_401(p._err(payload)))
        finally:
            xbmcaddon.SETTINGS["openai_key"] = "sk-test-key-000000000000000000000000000000000000"


class TestSync(Base):
    def test_moviehash(self):
        import struct
        from defta import sync
        path = self.tmp / "video.mkv"
        data = bytes((i * 7) % 256 for i in range(300000))
        path.write_bytes(data)
        h, size = sync.moviehash(str(path))
        ref = len(data)
        for block in (data[:65536], data[-65536:]):
            for (q,) in struct.iter_unpack("<Q", block):
                ref = (ref + q) & 0xFFFFFFFFFFFFFFFF
        self.assertEqual((h, size), ("%016x" % ref, len(data)))
        self.assertEqual(sync.moviehash("plugin://x/y"), ("", 0))
        self.assertEqual(sync.moviehash(str(self.tmp / "yok.mkv")), ("", 0))

    def test_check_and_fps_fix(self):
        from defta import sync
        d = 1420.0
        self.assertEqual(sync.check(1380, d)[0], "ok")
        self.assertEqual(sync.check(500, d)[0], "short")
        self.assertEqual(sync.check(1600, d)[0], "long")
        self.assertEqual(sync.check(0, d)[0], "unknown")
        st, f = sync.check(d * 25 / 23.976 - 5, d)
        self.assertEqual(st, "fps")
        self.assertAlmostEqual(f, 23.976 / 25, places=4)
        src = self.tmp / "fps.srt"
        src.write_text("1\n00:00:10,000 --> 00:00:12,000\nHello\n\n2\n00:20:00,000 --> 00:20:02,000\nBye\n",
                       encoding="utf-8")
        out = self.tmp / "fps.fixed.srt"
        self.assertTrue(sync.fix_fps(str(src), 0.5, str(out)))
        text = out.read_text(encoding="utf-8")
        self.assertIn("00:00:05,000 --> 00:00:06,000", text)
        self.assertIn("00:10:00,000 --> 00:10:01,000", text)

    def test_get_checked_fixes_fps(self):
        from defta import fetch, store
        from defta.media import Media
        from defta.sources.base import Sub
        sub = Sub("opensubtitles", "555", "en", release="x")
        p = store.download_path(sub, ".srt")
        end = 1000 * 25 / 23.976
        Path(p).write_text(f"1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:{int(end // 60):02d}:{int(end % 60):02d},"
                           f"{int(end * 1000 % 1000):03d} --> 00:{int(end // 60):02d}:{int(end % 60):02d},"
                           f"{int(end * 1000 % 1000):03d}\nB\n", encoding="utf-8")
        path, state = fetch.get_checked(sub, Media(title="x", duration=1000.0))
        self.assertEqual(state, "fps")
        self.assertTrue(path.endswith(".fps.srt") and os.path.exists(path))
        self.assertEqual(fetch.get_checked(sub, Media(title="x"))[1], "unknown")


class TestSyncRanking(Base):
    def _patch(self, os_subs, subdl_subs=()):
        from defta.sources import opensubtitles, subdl
        self._orig = (opensubtitles.search, subdl.search)
        self.seen_hash = []

        def os_search(m, w):
            self.seen_hash.append(m.hash)
            return [s for s in os_subs]
        opensubtitles.search = os_search
        subdl.search = lambda m, w: list(subdl_subs)

    def tearDown(self):
        from defta.sources import opensubtitles, subdl
        if hasattr(self, "_orig"):
            opensubtitles.search, subdl.search = self._orig

    def test_hash_then_continuation_then_score_bad_last(self):
        from defta import search, store
        from defta.media import Media
        from defta.sources.base import Sub
        video = self.tmp / "Show.S01E02.1080p.WEB-DL-GRP.mkv"
        video.write_bytes(b"\0" * 200000)
        m = Media(title="Show", season=1, episode=2, filename=video.name, path=str(video))
        a = Sub("opensubtitles", "1", "en", release="Show.S01E02.1080p.WEB-DL-GRP", downloads=900)
        b = Sub("opensubtitles", "2", "en", release="Show.S01E02.720p.HDTV-OTHER", uploader="ali", downloads=10)
        c = Sub("opensubtitles", "3", "en", release="Show.S01E02.480p", downloads=5, hash_match=True)
        d = Sub("opensubtitles", "4", "en", release="Show.S01E02.1080p.AMZN.WEB-DL-GRP", downloads=800)
        self._patch([a, b, c, d])
        prev = Media(title="Show", season=1, episode=1)
        store.remember_choice(prev, Sub("opensubtitles", "9", "en", release="Show.S01E01.720p.HDTV-OTHER",
                                        uploader="ali"))
        store.mark_bad(m, a)
        res = search.search(m, use_cache=False)
        self.assertTrue(self.seen_hash and self.seen_hash[0], "video karması OpenSubtitles'a gitmeli")
        order = [s.sid for s in res.foreign]
        self.assertEqual(order, ["3", "2", "4", "1"])
        notes = {s.sid: s.note for s in res.foreign}
        self.assertEqual(notes["3"], "senkron uyumlu")
        self.assertEqual(notes["2"], "önceki bölümde kullandığın")
        self.assertEqual(notes["1"], "kaymış")
        self.assertEqual(search.best_foreign(res).sid, "3")
        kind, nxt = search.next_candidate(res, "opensubtitles-3", "ai")
        self.assertEqual((kind, nxt.sid), ("translate", "2"))
        # kaymış işaretlenince o yükleyenin ayrıcalığı kalkar
        store.mark_bad(m, b)
        self.assertIsNone(store.series_pref(m))
        store.clean("syncmarks")

    def test_badsync_loads_next_human(self):
        from defta import plugin, store
        from defta.media import Media
        from defta.sources.base import Sub
        h1 = Sub("opensubtitles", "21", "tr", release="Film.2020.1080p-A", downloads=50)
        h2 = Sub("opensubtitles", "22", "tr", release="Film.2020.720p-B", downloads=40)
        for h in (h1, h2):
            Path(store.download_path(h, ".srt")).write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n",
                                                           encoding="utf-8")
        self._patch([h1, h2])
        m = Media(title="Film", year=2020, filename="Film.2020.1080p-A.mkv")
        xbmcplugin.ITEMS.clear()
        sys.argv = ["plugin://x/", "1", "?" + __import__("urllib.parse").parse.urlencode(
            {"action": "download", "kind": "human", "sub": json.dumps(h1.to_dict()), "media": json.dumps(m.to_dict())})]
        plugin.main()
        self.assertEqual(store.get_loaded(m)["sub"]["sid"], "21")
        # liste: etkin altyazı işaretle en üstte, "Kaymış" ikinci; etkin olan aşağıda tekrarlanmaz
        xbmc.INFO.clear()
        xbmc.INFO["Player.Filenameandpath"] = "Film.2020.1080p-A.mkv"
        xbmcplugin.ITEMS.clear()
        sys.argv = ["plugin://x/", "1", "?action=search"]
        plugin.main()
        labels = [li.label2 for _, li in xbmcplugin.ITEMS]
        self.assertTrue(labels[0].startswith(plugin.MARK) and "1080p-A" in labels[0])
        self.assertTrue(labels[1].startswith("Kaymış"))
        self.assertEqual(sum("1080p-A" in x for x in labels), 1)
        self.assertFalse(any("Baştan çevir" in x for x in labels), "insan çevirisi baştan çevrilmez")
        xbmcplugin.ITEMS.clear()
        sys.argv = ["plugin://x/", "1", "?" + __import__("urllib.parse").parse.urlencode(
            {"action": "download", "kind": "badsync", "media": json.dumps(m.to_dict())})]
        plugin.main()
        self.assertEqual(len(xbmcplugin.ITEMS), 1)
        self.assertEqual(store.get_loaded(m)["sub"]["sid"], "22")
        self.assertIn("opensubtitles-21", store.bad_uids(m))
        # Kodi ilk satırı kendiliğinden indirse de ilk satır etkin altyazıdır, "Kaymış" yine ikinci
        xbmc.JSONRPC["subtitles.downloadfirst"] = True
        try:
            xbmcplugin.ITEMS.clear()
            sys.argv = ["plugin://x/", "1", "?action=search"]
            plugin.main()
            labels = [li.label2 for _, li in xbmcplugin.ITEMS]
            self.assertTrue(labels[0].startswith(plugin.MARK) and "720p-B" in labels[0])
            self.assertTrue(labels[1].startswith("Kaymış"))
        finally:
            xbmc.JSONRPC.clear()
            xbmc.INFO.clear()
            store.clean("syncmarks")


    def test_list_with_active_ai(self):
        from defta import plugin, store
        from defta.media import Media
        from defta.sources.base import Sub
        m = Media(title="Liste", season=1, episode=3, filename="Liste.S01E03.mkv")
        en = Sub("opensubtitles", "81", "en", release="Liste.S01E03.WEB")
        subs = [Sub("subdl", f"/s/{i}", "en", release=f"Liste.S01E03.v{i}") for i in range(3)]
        old = [Sub("jimaku", f"https://jimaku.cc/x{i}", "ja", release=f"Liste - 03 v{i}") for i in range(5)]
        for s_ in [en] + old:
            p = store.tr_path(m, s_, "tr")
            Path(p).write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n", encoding="utf-8")
            store.tr_record(m, s_, p, "final")
        store.set_loaded(m, "ai", en)
        self._patch([en], subs)
        xbmc.INFO.clear()
        xbmc.INFO["Player.Filenameandpath"] = "Liste.S01E03.mkv"
        try:
            xbmcplugin.ITEMS.clear()
            sys.argv = ["plugin://x/", "1", "?action=search"]
            plugin.main()
            labels = [li.label2 for _, li in xbmcplugin.ITEMS]
            self.assertTrue(labels[0].startswith(plugin.MARK) and "AI[/B] (EN)" in labels[0])
            self.assertTrue(labels[1].startswith("Kaymış"))
            self.assertEqual(sum("(JA)" in x for x in labels), 3, "en çok 3 hazır çeviri")
            self.assertEqual(sum("Baştan çevir" in x for x in labels), 1)
            self.assertTrue(labels[-1].startswith("Baştan çevir (ücretli)"), "başka eklenti dosyası yokken en altta")
            self.assertTrue(any(x.startswith("→ Çevir") for x in labels))
            self.assertFalse(any("İngilizceden" in x or "Defta AI" in x for x in labels))
            self.assertFalse(any(x.startswith("→ Çevir") and "Liste.S01E03.WEB" in x for x in labels),
                             "etkin çevirinin kaynağı ayrıca 'Çevir' satırı olarak çıkmaz")
        finally:
            xbmc.INFO.clear()
            store.clean("ai")
            store.clean("syncmarks")

class TestJobsAndPrefetch(Base):
    def test_latest_selection_wins(self):
        from defta import plugin, store
        from defta.media import Media
        from defta.sources.base import Sub
        m = Media(title="Secim", season=1, episode=3)
        a, b = Sub("opensubtitles", "31", "en", release="a"), Sub("opensubtitles", "32", "en", release="b")
        plugin._start_job(m, a, "x.srt", fresh=False)
        plugin._start_job(m, b, "y.srt", fresh=False)
        st = {Sub.from_dict(j["sub"]).sid: j["status"] for j in store.list_jobs()
              if Media.from_dict(j["media"]).key() == m.key()}
        self.assertEqual(st, {"31": "cancelled", "32": "pending"})
        # aynı altyazı yeniden seçilince yeni iş açılır (biten parçalar önbellekten gelir)
        plugin._start_job(m, a, "x.srt", fresh=False)
        st = sorted(j["status"] for j in store.list_jobs() if Media.from_dict(j["media"]).key() == m.key())
        self.assertEqual(st, ["cancelled", "cancelled", "pending"])
        store.clean("jobs")

    def test_prefetch_ask(self):
        import xbmcgui
        from defta import service_main, store
        from defta.media import Media
        from defta.sources import opensubtitles, subdl
        from defta.sources.base import Sub
        nxt = Sub("opensubtitles", "41", "en", release="Dizi.S01E05.1080p")
        Path(store.download_path(nxt, ".srt")).write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
        orig = (opensubtitles.search, subdl.search)
        asked = []
        opensubtitles.search = lambda m, w: asked.append((m.season, m.episode)) or (
            [nxt] if (m.season, m.episode) == (1, 5) else [])
        subdl.search = lambda m, w: []
        svc = service_main.Service()
        m = Media(title="Dizi", season=1, episode=4, filename="Dizi.S01E04.1080p.mkv")
        svc.player.is_playing = lambda mm: True
        try:
            xbmcgui.DIALOGS["script"] = lambda d: d.onClick(3002)      # sağ üst kutuda "İptal"
            svc._prefetch_inner(m, 1)
            self.assertFalse([j for j in store.list_jobs() if j["media"]["episode"] == 5])
            box = xbmcgui.DIALOGS["opened"][-1]
            self.assertEqual(box.xml, "defta-next.xml")
            self.assertEqual(box.focus, 3001, "imleç Çevir'de başlamalı")
            self.assertIn("S01E05", box.controls[3011].label)
            xbmcgui.DIALOGS["script"] = lambda d: d.onAction(xbmcgui.Action(92))   # Geri tuşu = İptal
            store.clean("search")
            svc._prefetch_inner(m, 1)
            self.assertFalse([j for j in store.list_jobs() if j["media"]["episode"] == 5])
            xbmcgui.DIALOGS["script"] = None                             # hiçbir şeye basılmaz: süre dolunca çevrilir
            from defta import nextdialog
            orig_ask = nextdialog.ask
            nextdialog.ask = lambda t, l2, sec, should_close: orig_ask(t, l2, 0.6, should_close)
            store.clean("search")
            try:
                svc._prefetch_inner(m, 1)
            finally:
                nextdialog.ask = orig_ask
            self.assertLess(xbmcgui.DIALOGS["opened"][-1].controls[3003].percent, 100)
            jobs = [j for j in store.list_jobs() if j["media"]["episode"] == 5]
            self.assertEqual(len(jobs), 1)
            self.assertFalse(jobs[0]["load"])
            self.assertEqual(store.want_load(Media.from_dict(jobs[0]["media"])), 1)
            # sezon sonu: E+1 yoksa S+1E1 aranır
            asked.clear()
            store.clean("search")
            svc._prefetch_inner(Media(title="Dizi", season=1, episode=9), 2)
            self.assertEqual(asked[0], (1, 10))
            self.assertIn((2, 1), asked)
        finally:
            opensubtitles.search, subdl.search = orig
            xbmcgui.DIALOGS["script"] = None
            store.clean("jobs")
            store.clean("search")

    def test_progress_trigger(self):
        from defta import service_main
        from defta.media import Media
        svc = service_main.Service()
        started = []
        svc._prefetch = lambda m, mode: started.append(m.key())
        svc.player.media = Media(title="Dizi", season=1, episode=4)
        xbmc.Player.playing, xbmc.Player.total = "x.mkv", 1400.0
        xbmcaddon.SETTINGS["prefetch_next"] = "2"
        try:
            xbmc.Player.time = 600.0
            svc._check_prefetch()
            self.assertEqual(started, [])
            xbmc.Player.time = 1225.0                        # %87: henüz değil
            svc._check_prefetch()
            self.assertEqual(started, [])
            xbmc.Player.time = 1270.0                        # %91
            svc._check_prefetch()
            svc.prefetcher.join(2)
            svc._check_prefetch()                            # aynı bölüm için bir kez
            self.assertEqual(started, ["dizi-s01e04"])
            xbmcaddon.SETTINGS["prefetch_next"] = "0"
            svc.prefetched.clear()
            svc._check_prefetch()
            self.assertEqual(len(started), 1)
        finally:
            xbmc.Player.playing, xbmc.Player.total, xbmc.Player.time = "", 0.0, 0.0
            xbmcaddon.SETTINGS.pop("prefetch_next", None)


class TestLoadedAndJimaku(Base):
    def test_loaded_only_after_playback_start(self):
        import time as _t
        import xbmcgui
        from defta import fetch, plugin
        tmp = self.tmp / "kodi_temp" / "a4k"
        tmp.mkdir(parents=True, exist_ok=True)
        old = tmp / "Dizi.S01E02.en.srt"
        old.write_text("1\n00:00:01,000 --> 00:00:02,000\nOld episode\n", encoding="utf-8")
        os.utime(old, (_t.time() - 600, _t.time() - 600))          # önceki bölümün altyazısı
        xbmcgui.Window.PROPS["defta.play_start"] = str(_t.time() - 60)
        try:
            self.assertEqual(fetch.loaded_subtitle(fetch.playback_started(), "tr"), "")
            tr = tmp / "Dizi.S01E03.tr.srt"
            tr.write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n", encoding="utf-8")
            self.assertEqual(fetch.loaded_subtitle(fetch.playback_started(), "tr"), "", "Türkçe olan sayılmaz")
            new = tmp / "Dizi.S01E03.en.srt"
            new.write_text("1\n00:00:01,000 --> 00:00:02,000\nNew episode\n", encoding="utf-8")
            self.assertEqual(fetch.loaded_subtitle(fetch.playback_started(), "tr"), str(new))
            # Defta'nın kendi verdiği dosyanın kopyası "başka eklentinin" sayılmaz
            given = fetch.for_kodi(str(new), "Dizi S01E03", "en")
            self.assertTrue(os.path.exists(given))
            self.assertEqual(fetch.loaded_subtitle(fetch.playback_started(), "tr"), "")
            os.remove(given)
            # listede dosya adıyla görünür
            from defta.sources import opensubtitles, subdl
            orig = (opensubtitles.search, subdl.search)
            opensubtitles.search = lambda m, w: []
            subdl.search = lambda m, w: []
            try:
                xbmc.INFO.clear()
                xbmc.INFO["Player.Filenameandpath"] = "Dizi.S01E03.mkv"
                xbmcplugin.ITEMS.clear()
                sys.argv = ["plugin://x/", "1", "?action=search"]
                plugin.main()
                rows = [li.label2 for _, li in xbmcplugin.ITEMS if "Yüklenen" in li.label2]
                self.assertEqual(len(rows), 1)
                self.assertIn("Dizi.S01E03.en.srt", rows[0])
            finally:
                opensubtitles.search, subdl.search = orig
                xbmc.INFO.clear()
        finally:
            xbmcgui.Window.PROPS.pop("defta.play_start", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_jimaku_entry_and_always_asked(self):
        from defta import search, net
        from defta.media import Media
        from defta.sources import jimaku, opensubtitles, subdl
        from defta.sources.base import Sub
        entries = [{"id": 1, "name": "Mob Psycho 100", "english_name": "Mob Psycho 100"},
                   {"id": 2, "name": "Mob Psycho 100 II", "english_name": "Mob Psycho 100 Season 2"},
                   {"id": 4, "name": "Interstellar Tensei", "english_name": ""},
                   {"id": 6, "name": "Shingeki no Kyojin", "english_name": "Attack on Titan"},
                   {"id": 8, "name": "Kimi no Na wa.", "english_name": "Your Name.", "flags": {"movie": True}}]
        pick = lambda t, s=0, e=0: (jimaku.pick_entry(Media(title=t, season=s, episode=e), entries) or {}).get("id")
        self.assertEqual(pick("Mob Psycho 100", 1, 3), 1)
        self.assertEqual(pick("Mob Psycho 100", 2, 3), 2)
        self.assertEqual(pick("Attack on Titan", 2, 1), 6)       # tek kayıtta süren dizi
        self.assertIsNone(pick("Interstellar"))                   # anime olmayan film eşleşmez
        self.assertIsNone(pick("Mob Psycho", 1, 1))               # zayıf eşleşme kabul edilmez
        self.assertEqual(pick("Your Name"), 8)
        calls = []

        def fake_get_json(url, params=None, headers=None, timeout=15.0):
            calls.append((url, dict(params or {})))
            if url.endswith("/entries/search"):
                return entries
            return [{"name": "Mob Psycho 100 - 03.srt", "url": "https://jimaku.cc/entry/1/download/x.srt"}]
        orig = (net.get_json, opensubtitles.search, subdl.search)
        net.get_json = fake_get_json
        opensubtitles.search = lambda m, w: [Sub("opensubtitles", "71", "en", release="Mob.Psycho.100.S01E03")]
        subdl.search = lambda m, w: []
        xbmcaddon.SETTINGS.update({"jimaku_enabled": "true", "jimaku_key": "jk-test", "anime_japanese": "true"})
        try:
            # dosya adı anime gibi görünmüyor ama Jimaku'da kaydı var: Japonca altyazı gelir ve öne geçer
            m = Media(title="Mob Psycho 100", season=1, episode=3, filename="Mob.Psycho.100.S01E03.1080p.WEB.mkv")
            res = search.search(m, use_cache=False)
            self.assertEqual([s.source for s in res.foreign][:2], ["jimaku", "opensubtitles"])
            self.assertTrue(m.is_anime)
            self.assertIn(("https://jimaku.cc/api/entries/1/files", {"episode": 3}), calls)
            xbmcaddon.SETTINGS["anime_japanese"] = "false"         # ayar kapalı: İngilizcenin altında
            res = search.search(Media(title="Mob Psycho 100", season=1, episode=3), use_cache=False)
            self.assertEqual([s.source for s in res.foreign][:2], ["opensubtitles", "jimaku"])
        finally:
            net.get_json, opensubtitles.search, subdl.search = orig
            for k in ("jimaku_enabled", "jimaku_key", "anime_japanese"):
                xbmcaddon.SETTINGS.pop(k, None)


class TestV310(Base):
    def _long_srt(self, path, minutes=30):
        lines = []
        for i in range(minutes * 6):                     # her 10 sn bir satır
            t = i * 10
            lines.append(f"{i + 1}\n00:{t // 60:02d}:{t % 60:02d},000 --> 00:{t // 60:02d}:{t % 60:02d},900\n"
                         f"Line number {i} here.\n")
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def test_fast_covers_five_minutes_from_position(self):
        from defta import store
        from defta.media import Media
        from defta.sources.base import Sub
        from defta.translate import Translator
        m = Media(title="Uzun Film", year=2001)
        sub = Sub("opensubtitles", "901", "en", release="Uzun.Film.2001")
        src = store.download_path(sub, ".srt")
        self._long_srt(src)
        ups = []
        job = store.new_job(m, sub, src, fresh=True, load=True, start_at=20 * 60)   # 20. dakikada seçildi
        Translator(job, lambda p, st, pct: ups.append((st, p))).run()
        self.assertEqual([u[0] for u in ups], ["fast", "final"])
        fast = Path(ups[0][1]).read_text(encoding="utf-8")
        self.assertIn("Line number 2", fast)                                  # 5 dakikanın dışı: orijinal
        self.assertNotIn("TR[Line number 2 here.]", fast)
        self.assertIn("TR[Line number 125 here.]", fast)                      # 20:50 → çevrildi
        self.assertNotIn("TR[Line number 160 here.]", fast)                  # 26:40: film için 5 dk penceresinin dışı
        final = Path(ups[1][1]).read_text(encoding="utf-8")
        self.assertIn("TR[Line number 2 here.]", final)
        store.clean("ai")

    def test_fast_window_setting_and_small_chunks(self):
        from defta import store
        from defta.sources.base import Sub
        from defta.translate import FAST_CHUNK, fast_window, window_chunks
        from subtrans.subformats import read_subtitle
        self.assertEqual((fast_window(True), fast_window(False)), (120, 300))        # Otomatik
        xbmcaddon.SETTINGS["fast_window"] = "1"
        try:
            self.assertEqual(fast_window(False), 60)
        finally:
            xbmcaddon.SETTINGS.pop("fast_window", None)
        sub = Sub("opensubtitles", "903", "en", release="x")
        src = store.download_path(sub, ".srt")
        self._long_srt(src)

        class J:                                              # window_chunks için yeterli iş taklidi
            srt = read_subtitle(Path(src))
            translate_pos = [b.pos for b in srt.blocks]
        chunks = window_chunks(J, 10 * 60 * 1000, 120)
        targets = [p for c in chunks for p in c.targets]
        # ~12'lik parçalar, doğal durakta kesilir (en fazla 1,2 katı); 2 dk = 12-13 satır
        self.assertTrue(all(len(c.targets) <= FAST_CHUNK * 1.2 for c in chunks))
        self.assertIn(len(chunks), (1, 2))
        self.assertIn(60, targets)                                             # 10:00
        self.assertNotIn(80, targets)                                          # 13:20 pencere dışı
        self.assertEqual(len(chunks[0].before), 3)                             # bağlam için önceki 3 satır

    def test_requested_next_episode_loads_regardless_of_setting(self):
        from defta import service_main, store
        from defta.media import Media
        from defta.sources.base import Sub
        m = Media(title="Istek", season=1, episode=5)
        sub = Sub("opensubtitles", "904", "en", release="Istek.S01E05")
        p = store.tr_path(m, sub, "tr")
        Path(p).write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n", encoding="utf-8")
        store.tr_record(m, sub, p, "final")
        store.request_load(m, sub)
        self.assertEqual(store.take_requested(m), sub.uid())
        self.assertEqual(store.take_requested(m), "", "bir kez döner")
        svc = service_main.Service()
        svc.player.is_playing = lambda mm: True
        svc.monitor.waitForAbort = lambda t=0: False
        xbmc.Player.loaded.clear()
        xbmcaddon.SETTINGS["auto_ai"] = "false"
        try:
            svc.load_requested(m, sub.uid())
        finally:
            xbmcaddon.SETTINGS.pop("auto_ai", None)
        self.assertEqual(len(xbmc.Player.loaded), 1)
        self.assertEqual(store.get_loaded(m)["sub"]["sid"], "904")
        store.clean("ai")
        store.clean("syncmarks")

    def test_unchecked_when_review_step_fails(self):
        from defta import store
        from defta.media import Media
        from defta.sources.base import Sub
        from defta.translate import Translator
        from subtrans.engine import Engine
        m = Media(title="Kontrol", season=1, episode=1)
        sub = Sub("opensubtitles", "902", "en", release="Kontrol.S01E01")
        src = store.download_path(sub, ".srt")
        shutil.copy(SAMPLE, src)
        orig = Engine.finish_phase

        async def broken(self_, job):
            raise RuntimeError("review service down")
        Engine.finish_phase = broken
        try:
            ups = []
            t = Translator(store.new_job(m, sub, src, fresh=True, load=False), lambda p, st, pct: ups.append(st))
            out = t.run()
        finally:
            Engine.finish_phase = orig
        self.assertTrue(t.unchecked)
        self.assertEqual(ups[-1], "unchecked")
        self.assertIn("TR[", Path(out).read_text(encoding="utf-8"))
        self.assertEqual(store.tr_meta(m)[0]["stage"], "unchecked")
        store.clean("ai")

    def test_dedupe_across_sources(self):
        from defta import search, store
        from defta.sources.base import Sub
        self.assertEqual(search.release_key("attack.on.titan.s01e03.720p.bluray.x264-debtvid.en"),
                         search.release_key("Attack.on.Titan.S01E03.720p.BluRay.x264-DEBTVID"))
        a = Sub("opensubtitles", "1", "en", release="Show.S01E03.720p-GRP.en.srt", downloads=10)
        b = Sub("subdl", "/s/2", "en", release="Show S01E03 720p GRP", hash_match=False, sync=True)
        c = Sub("subsource", "3", "en", release="Totally.Different.Name")
        d = Sub("subdl", "/s/4", "en", release="Another.Name.Again")
        for x in (c, d):                                      # içerikleri birebir aynı
            pth = store.download_path(x, ".srt")
            self._long_srt(pth, minutes=1)
            store.remember_fingerprint(x, pth)
        out = search._dedupe([a, b, c, d])
        self.assertEqual(len(out), 2)
        kept = next(x for x in out if search.release_key(x.release) == search.release_key(a.release))
        self.assertEqual(kept.source, "opensubtitles")
        self.assertTrue(kept.sync, "birleşen satırın senkron bilgisi korunur")
        e = Sub("subdl", "/s/5", "en", release="Shifted")
        pth = store.download_path(e, ".srt")
        Path(pth).write_text(Path(store.download_path(c, ".srt")).read_text().replace("00:00:1", "00:00:2"))
        store.remember_fingerprint(e, pth)
        self.assertEqual(len(search._dedupe([c, d, e])), 2, "zamanlaması farklıysa ayrı altyazıdır")

    def test_auto_clean(self):
        import sqlite3
        import time as _t
        from defta import kodi, store
        old = _t.time() - 7 * 3600
        tmp_old = Path(kodi.temp_dir()) / "eski.tr.srt"
        tmp_new = Path(kodi.temp_dir()) / "yeni.tr.srt"
        for f in (tmp_old, tmp_new):
            f.write_text("x")
        os.utime(tmp_old, (old, old))
        dl_old = Path(kodi.profile_dir("downloads")) / "opensubtitles-old.srt"
        dl_old.write_text("x")
        very_old = _t.time() - 31 * 24 * 3600
        os.utime(dl_old, (very_old, very_old))
        db = Path(kodi.profile_dir()) / "cache.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, data TEXT, created REAL)")
        con.execute("INSERT OR REPLACE INTO calls VALUES ('eski', '{}', ?)", (very_old,))
        con.execute("INSERT OR REPLACE INTO calls VALUES ('yeni', '{}', ?)", (_t.time(),))
        con.commit()
        con.close()
        j = store.new_job(__import__("defta.media", fromlist=["Media"]).Media(title="x"),
                          __import__("defta.sources.base", fromlist=["Sub"]).Sub("opensubtitles", "1", "en"), "x", False, False)
        j["status"], j["finished"] = "cancelled", old
        store.save_job(j)
        n = store.auto_clean()
        self.assertFalse(tmp_old.exists())
        self.assertTrue(tmp_new.exists())
        self.assertFalse(dl_old.exists())
        self.assertFalse(any(x["id"] == j["id"] for x in store.list_jobs()))
        con = sqlite3.connect(db)
        keys = {r[0] for r in con.execute("SELECT key FROM calls")}
        con.close()
        self.assertIn("yeni", keys)
        self.assertNotIn("eski", keys)
        self.assertGreaterEqual(n["cache"], 1)
        self.assertGreater(store.last_clean(), 0)
        tmp_new.unlink()

    def test_manual_cache_clean(self):
        import sqlite3
        from defta import kodi, store
        db = Path(kodi.profile_dir()) / "cache.sqlite"
        con = sqlite3.connect(db)
        for t in ("calls", "stages"):
            con.execute(f"CREATE TABLE IF NOT EXISTS {t} (key TEXT PRIMARY KEY, data TEXT, created REAL)")
            con.execute(f"INSERT OR REPLACE INTO {t} VALUES ('k', '{{}}', 0)")
        con.commit()
        con.close()
        self.assertRegex(store.clean("apicache"), r"[1-9]\d* kayıt silindi")
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 0)
        con.close()
        self.assertIn("silindi", store.clean("all"))

    def test_kodi_log_key_traces_and_env(self):
        from defta import kodi
        logdir = self.tmp / "kodilog"
        logdir.mkdir(exist_ok=True)
        xbmcvfs.MAP["special://logpath/"] = str(logdir)
        fake = "FAKEsubdlK" + "Q" * 10
        line = f"İndirildi: x -> subdl-_subtitle_1-2.zip_api_key_{fake}.srt\n"
        (logdir / "kodi.old.log").write_text("baş\n" + line, encoding="utf-8")
        (logdir / "kodi.log").write_text(line, encoding="utf-8")
        dest = self.tmp / "kayitlar"
        try:
            copied = kodi.export_kodi_logs(str(dest))
            self.assertIn("kodi.log", copied)
            for name in ("kodi.log", "kodi.old.log"):
                self.assertNotIn(fake, (dest / name).read_text(encoding="utf-8"), "kopya temizlenmiş olmalı")
            self.assertIn(fake, (logdir / "kodi.log").read_text(encoding="utf-8"), "açık kodi.log'a dokunulmaz")
            self.assertTrue(kodi.scrub_kodi_old_log())
            self.assertNotIn(fake, (logdir / "kodi.old.log").read_text(encoding="utf-8"))
            self.assertFalse(kodi.scrub_kodi_old_log(), "ikinci kez bulacak bir şey kalmaz")
            # Windows ortam değişkenindeki eski anahtar kullanılmaz
            os.environ["SUBSOURCE_API_KEY"] = "eski-windows-anahtari-123456"
            xbmcaddon.SETTINGS.pop("subsource_key", None)
            self.assertEqual(kodi.secret("subsource_key"), "")
        finally:
            os.environ.pop("SUBSOURCE_API_KEY", None)
            xbmcvfs.MAP.pop("special://logpath/", None)

    def test_import_keys_from_file(self):
        import xbmcgui
        from defta import kodi, tools
        env = self.tmp / "anahtarlar.env"
        env.write_text("# yorum\nOPENAI_API_KEY=sk-test-key-000000000000000000000000000000000000\n"
                       "export SUBDL_API_KEY=\"yeniSubdlAnahtar12345\"\nJIMAKU_API_KEY=jimakuAnahtar98765\n"
                       "BASKA_BIR_SEY=1\n", encoding="utf-8")
        keep = {k: xbmcaddon.SETTINGS.get(k) for k in ("subdl_key", "jimaku_key", "key_file", "openai_key")}
        xbmcaddon.SETTINGS["subdl_key"] = "eskiSubdl"
        xbmcaddon.SETTINGS["key_file"] = "smb://PC/subtitles/.env"
        xbmcgui.YESNO["answer"] = True
        xbmcgui.NOTES.clear()
        xbmc.LOGS.clear()
        try:
            st = tools.import_keys(str(env))
            self.assertEqual(st["subdl_key"], "değişti")
            self.assertEqual(st["jimaku_key"], "yeni")
            self.assertEqual(st["openai_key"], "aynı")
            self.assertEqual(xbmcaddon.SETTINGS["subdl_key"], "yeniSubdlAnahtar12345")
            self.assertEqual(xbmcaddon.SETTINGS["key_file"], "", "artık dosyaya bağlı değil")
            self.assertEqual(kodi.describe_secret("jimaku_key").split(", ")[-1], "ayarlar)")
            summary = xbmcgui.NOTES[-1][1]
            self.assertIn("…2345", summary)
            self.assertNotIn("yeniSubdlAnahtar12345", summary + " ".join(xbmc.LOGS), "anahtar ekrana/kayda yazılmaz")
            self.assertTrue(env.exists(), "dosyaya dokunulmaz")
            # Klasör seçilince içindeki .env bulunur (Kodi'nin seçicisi .env'i göstermez)
            xbmcgui.SELECT["answer"], xbmcgui.BROWSE["path"] = 0, str(self.tmp)
            (self.tmp / ".env").write_text(env.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertIn("subdl_key", tools.import_keys())
            xbmcgui.BROWSE["path"] = ""
            # İptal: değişecek anahtar varsa ve hayır denirse hiçbir şey yazılmaz
            xbmcaddon.SETTINGS["subdl_key"] = "baskaEski"
            xbmcgui.YESNO["answer"] = False
            self.assertEqual(tools.import_keys(str(env)), {})
            self.assertEqual(xbmcaddon.SETTINGS["subdl_key"], "baskaEski")
        finally:
            xbmcgui.YESNO["answer"] = True
            for k, v in keep.items():
                if v is None:
                    xbmcaddon.SETTINGS.pop(k, None)
                else:
                    xbmcaddon.SETTINGS[k] = v
            kodi.forget_secret_cache()

    def test_active_sync_flag_is_honest(self):
        from defta import plugin, store
        from defta.media import Media
        from defta.sources.base import Sub
        m = Media(title="Senkron", year=2010, filename="Senkron.2010.mkv")
        h = Sub("opensubtitles", "61", "tr", release="Senkron.2010.WEB", sync=False)
        store.set_loaded(m, "human", h)
        from defta.sources import opensubtitles, subdl
        orig = (opensubtitles.search, subdl.search)
        opensubtitles.search = lambda mm, w: [h]
        subdl.search = lambda mm, w: []
        try:
            xbmc.INFO.clear()
            xbmc.INFO["Player.Filenameandpath"] = "Senkron.2010.mkv"
            xbmcplugin.ITEMS.clear()
            sys.argv = ["plugin://x/", "1", "?action=search"]
            plugin.main()
            first = xbmcplugin.ITEMS[0][1]
            self.assertTrue(first.label2.startswith(plugin.MARK))
            self.assertEqual(first.props["sync"], "false", "etkin satır her zaman SYNC almaz")
        finally:
            opensubtitles.search, subdl.search = orig
            xbmc.INFO.clear()
            store.clean("syncmarks")


class TestTools(Base):
    def test_export_and_smb_log(self):
        import xbmcgui
        from defta import kodi, store, tools
        from defta.media import Media
        from defta.sources.base import Sub
        m = Media(title="Film", year=2001)
        sub = Sub("opensubtitles", "61", "en", release="Film.2001.1080p")
        p = store.tr_path(m, sub, "tr")
        Path(p).write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n", encoding="utf-8")
        store.tr_record(m, sub, p, "final", 0.01)
        dest = self.tmp / "export"
        xbmcgui.BROWSE["path"] = str(dest)
        try:
            tools.export()
        finally:
            xbmcgui.BROWSE["path"] = ""
        self.assertEqual([x.name for x in dest.iterdir()], ["Film (2001) - Film.2001.1080p.tr.srt"])
        self.assertEqual(kodi.vfs_join("smb://PC/subtitles/", "defta.log"), "smb://PC/subtitles/defta.log")
        xbmcvfs.COPIES.clear()
        xbmcaddon.SETTINGS["debug_dir"] = "smb://PC/subtitles"
        try:
            kodi._mirror_state["t"] = 0.0
            kodi.log("bir")
            kodi.log("iki")                                   # 5 sn dolmadan kopyalanmaz
            self.assertEqual(len(xbmcvfs.COPIES), 1)
            kodi.flush_log()
            self.assertEqual(len(xbmcvfs.COPIES), 2)
            self.assertEqual(xbmcvfs.COPIES[-1][1], "smb://PC/subtitles/defta.log")
        finally:
            xbmcaddon.SETTINGS.pop("debug_dir", None)
        store.clean("ai")


if __name__ == "__main__":
    unittest.main()


SONG_SRT = """1
00:00:01,000 --> 00:00:03,000
♪ Sono yume wa kokoro no ibasho
(That dream is the place of my heart)

2
00:00:03,500 --> 00:00:06,000
Tooku made yukou yo
(Let's go far away)

3
00:00:06,500 --> 00:00:09,000
Hikari no naka e ♪
(Into the light)

4
00:00:15,000 --> 00:00:17,000
What are you doing here?

5
00:01:00,000 --> 00:01:03,000
<i>Akai ya wo ima hanate</i>

6
00:01:00,000 --> 00:01:03,000
<i>(Shoot the red arrow now)</i>

7
00:01:10,000 --> 00:01:12,000
I am going home.
"""


class TestV040(Base):
    def _job(self, name, text, load=False):
        from defta import store
        from defta.media import Media
        from defta.sources.base import Sub
        m = Media(title=name, season=1, episode=1)
        sub = Sub("opensubtitles", name, "en", release=name)
        src = store.download_path(sub, ".srt")
        Path(src).write_text(text, encoding="utf-8")
        return store.new_job(m, sub, src, fresh=True, load=load)

    def _blocks(self, path):
        from subtrans.subformats import read_subtitle
        return [b.text for b in read_subtitle(Path(path)).blocks]

    def test_lyrics_three_modes(self):
        from defta.translate import Translator
        out = {}
        try:
            for mode, idx in (("translate", "0"), ("keep", "1"), ("both", "3")):
                xbmcaddon.SETTINGS["lyrics"] = idx
                out[mode] = self._blocks(Translator(self._job("Sarki" + mode, SONG_SRT), lambda *a: None).run())
        finally:
            xbmcaddon.SETTINGS.pop("lyrics", None)
        t, k, b = out["translate"], out["keep"], out["both"]
        # Çevir: yalnızca anlamın çevirisi, romaji satırı yok
        self.assertTrue(all("Sono yume" not in x and "Akai ya" not in x for x in t), t)
        self.assertTrue(any("That dream" in x for x in t))
        self.assertEqual(len(t), 6, "aynı anda gösterilen romaji bloğu atılır")
        # Orijinal bırak: bütün sözler Latin harfleriyle olduğu gibi, İngilizce anlam satırı yok
        self.assertIn("♪ Sono yume wa kokoro no ibasho", k[0])
        self.assertEqual(k[1], "Tooku made yukou yo", "şarkının ilk satırından sonrası da orijinal kalmalı")
        self.assertEqual(k[2], "Hikari no naka e ♪")
        self.assertTrue(all("dream" not in x and "Shoot" not in x for x in k), k)
        self.assertIn("Akai ya wo ima hanate", "\n".join(k))
        self.assertTrue(k[3].startswith("TR["), "şarkıdan sonraki konuşma çevrilir")
        # İkisi birden: orijinal, hemen altında parantez içinde çeviri
        self.assertEqual(b[0].split("\n")[0], "♪ Sono yume wa kokoro no ibasho")
        self.assertTrue(b[0].split("\n")[1].startswith("(TR["), b[0])
        self.assertTrue(b[1].startswith("Tooku made yukou yo\n("), b[1])
        self.assertTrue(b[3].startswith("TR[") and "(" not in b[3][:1])

    def test_credit_out_stops_and_blocks_auto(self):
        import xbmcgui
        from defta import plugin, service_main, store
        from defta.translate import CreditError, Translator
        from subtrans.provider import is_credit_error
        self.assertTrue(is_credit_error(429, {"error": {"code": "insufficient_quota", "message": "x"}}))
        self.assertTrue(is_credit_error(400, {"error": {"code": "billing_hard_limit_reached"}}))
        self.assertFalse(is_credit_error(429, {"error": {"message": "Rate limit reached for requests"}}))
        FakeOpenAI.credit_out = True
        n0 = FakeOpenAI.requests
        try:
            job = self._job("Kredi", SAMPLE.read_text(encoding="utf-8"))
            with self.assertRaises(CreditError):
                Translator(job, lambda *a: None).run()
            self.assertLess(FakeOpenAI.requests - n0, 40, "kredi bitince istekler hemen durmalı")
            # servis: bekleyen işler başlamaz, otomatik çeviri durur, bildirim
            xbmcgui.NOTES.clear()
            svc = service_main.Service()
            waiting = self._job("Kredi2", SONG_SRT)
            svc._run_job(self._job("Kredi3", SAMPLE.read_text(encoding="utf-8")))
            self.assertTrue(store.credit_out())
            self.assertEqual(store.get_job(waiting["id"])["status"], "failed")
            self.assertIn("OpenAI kredisi bitti", [n[1] for n in xbmcgui.NOTES])
            auto = self._job("Kredi4", SONG_SRT)
            auto["auto"] = True
            store.save_job(auto)
            n1 = FakeOpenAI.requests
            svc._run_job(auto)
            self.assertEqual(FakeOpenAI.requests, n1, "otomatik iş kredi bitince hiç istek göndermez")
        finally:
            FakeOpenAI.credit_out = False
        # elle seçince yeniden denenir
        from defta.media import Media
        from defta.sources.base import Sub
        plugin._start_job(Media(title="Elle", season=1, episode=1), Sub("opensubtitles", "e1", "en"), str(SAMPLE), False)
        self.assertFalse(store.credit_out())
        for j in store.list_jobs("pending"):
            j["status"] = "cancelled"
            store.save_job(j)

    def test_live_tv_and_unknown_video_skip_auto(self):
        from defta.media import Media
        self.assertEqual(Media(title="NBA TV", extra={"live": True}).auto_skip(), "canlı yayın")
        self.assertEqual(Media(title="index").auto_skip(), "video tanınmadı")
        self.assertEqual(Media(title="Dizi", season=1, episode=2).auto_skip(), "")
        self.assertEqual(Media(title="Film", year=2014).auto_skip(), "")

    def test_series_pref_not_in_same_episode(self):
        from defta import search, store
        from defta.media import Media
        from defta.sources import opensubtitles, subdl
        from defta.sources.base import Sub
        m = Media(title="Pref", season=1, episode=3)
        subs = [Sub("opensubtitles", f"p{i}", "en", release=f"Pref.S01E03.{q}-GRP", uploader="ali")
                for i, q in enumerate(("1080p", "720p", "480p"))]
        orig = (opensubtitles.search, subdl.search)
        opensubtitles.search = lambda mm, w: [Sub.from_dict(s.to_dict()) for s in subs]
        subdl.search = lambda mm, w: []
        try:
            store.remember_choice(m, subs[0])       # bu bölümde seçildi (sonra başka çeviriye geçildi)
            res = search.search(m, use_cache=False)
            self.assertFalse([s for s in res.foreign if s.note], "aynı bölümde 'önceki bölümde' notu çıkmamalı")
            nxt = m.next_episode()
            res = search.search(nxt, use_cache=False)
            self.assertEqual(len([s for s in res.foreign if s.note == search.PREF]), 1, "yalnızca tek satır")
        finally:
            opensubtitles.search, subdl.search = orig
            store.clean("syncmarks")

    def test_season_finale_goes_to_next_season(self):
        import xbmcgui
        from defta import service_main, store
        from defta.media import Media
        from defta.sources import opensubtitles, subdl
        from defta.sources.base import Sub
        pack = Sub("subdl", "https://dl.subdl.com/subtitle/pack.zip", "en", release="Fin.S01.Complete", pack=True)
        s2 = Sub("opensubtitles", "f201", "en", release="Fin.S02E01.1080p", season=2, episode=1)
        Path(store.download_path(s2, ".srt")).write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
        orig = (opensubtitles.search, subdl.search)
        opensubtitles.search = lambda m, w: [s2] if (m.season, m.episode) == (2, 1) else []
        subdl.search = lambda m, w: [pack] if m.season == 1 else []
        svc = service_main.Service()
        svc.player.is_playing = lambda mm: True
        try:
            svc._prefetch_inner(Media(title="Fin", season=1, episode=10), 2)
            jobs = [j for j in store.list_jobs("pending") if j["media"]["title"] == "Fin"]
            self.assertEqual([(j["media"]["season"], j["media"]["episode"]) for j in jobs], [(2, 1)])
            self.assertTrue(jobs[0]["auto"])
            for j in jobs:
                j["status"] = "cancelled"
                store.save_job(j)
            # yalnızca paket var ve indirilemiyor: sessiz kalmaz
            opensubtitles.search = lambda m, w: []
            store.clean("search")
            xbmcgui.NOTES.clear()
            from defta.sources import subdl as sd
            orig_dl = sd.download

            def bad(sub, m):
                from defta.sources.base import SourceError
                raise SourceError("arşivde 11. bölümün altyazısı bulunamadı")
            sd.download = bad
            try:
                svc._prefetch_inner(Media(title="Fin", season=1, episode=10), 2)
            finally:
                sd.download = orig_dl
            self.assertIn("Sıradaki bölüm indirilemedi", [n[1] for n in xbmcgui.NOTES])
        finally:
            opensubtitles.search, subdl.search = orig
            store.clean("search")

    def test_fallback_source_and_quota(self):
        import time
        import xbmcgui
        from defta import fetch, search, store
        from defta.media import Media
        from defta.sources import opensubtitles, subdl
        from defta.sources.base import QuotaError, Sub
        m = Media(title="Yedek", season=1, episode=1)
        a = Sub("opensubtitles", "y1", "en", release="Yedek.S01E01.1080p-GRP", downloads=50)
        b = Sub("subdl", "https://dl.subdl.com/subtitle/y1.zip", "en", release="Yedek.S01E01.1080p-GRP.en.srt")
        only_os = Sub("opensubtitles", "y2", "en", release="Yedek.S01E01.720p-XYZ", downloads=999)
        orig = (opensubtitles.search, subdl.search, opensubtitles.download, subdl.download)
        opensubtitles.search = lambda mm, w: [Sub.from_dict(a.to_dict()), Sub.from_dict(only_os.to_dict())]
        subdl.search = lambda mm, w: [Sub.from_dict(b.to_dict())]
        reset = time.time() + 3 * 3600

        def os_dl(sub, mm):
            raise QuotaError("OpenSubtitles hakkı doldu", reset)
        opensubtitles.download = os_dl
        subdl.download = lambda sub, mm: ("y1.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        xbmcgui.NOTES.clear()
        try:
            res = search.search(m, use_cache=False)
            row = next(s for s in res.foreign if s.sid == "y1")
            self.assertEqual(len(row.alts), 1, "birleşen satır başka kaynağı yedek olarak taşır")
            row = Sub.from_dict(json.loads(json.dumps(row.to_dict())))        # listedeki adresten geri okunur
            path = fetch.get(row, m)
            self.assertTrue(os.path.exists(path))
            self.assertIn("SubDL'den indirildi", [n[1] for n in xbmcgui.NOTES])
            note = store.quota_blocked()["opensubtitles"]
            self.assertEqual(note, "hak doldu · " + time.strftime("%H:%M", time.localtime(reset)))
            res = search.search(m, use_cache=False)
            self.assertEqual(res.foreign[-1].sid, "y2", "hakkı dolan kaynağın yedeksiz satırı alta iner")
            self.assertEqual(res.foreign[-1].note, note)
            with self.assertRaises(Exception) as cm:
                fetch.get(next(s for s in res.foreign if s.sid == "y2"), m)
            self.assertEqual(str(cm.exception), "OpenSubtitles hakkı doldu · " + note.split(" · ")[1])
            # başarılı indirmede kendiliğinden kalkar
            opensubtitles.download = lambda sub, mm: ("y2.srt", b"1\n00:00:01,000 --> 00:00:02,000\nYo\n")
            store.clear_quota("opensubtitles")
            fetch.get(Sub.from_dict(only_os.to_dict()), m)
            self.assertNotIn("opensubtitles", store.quota_blocked())
            store.set_quota("subdl", time.time() + 600, False)
            self.assertEqual(store.quota_blocked()["subdl"], "bekleniyor · 10 dk")
            store.set_quota("subdl", time.time() - 1, False)
            self.assertNotIn("subdl", store.quota_blocked(), "süresi geçince kendiliğinden kalkar")
        finally:
            opensubtitles.search, subdl.search, opensubtitles.download, subdl.download = orig
            store.clean("search")

    def test_sidecar_subtitle(self):
        from defta import fetch
        video = self.tmp / "Yanında.S01E01.mkv"
        video.write_bytes(b"\0" * 100)
        self.assertEqual(fetch.loaded_subtitle(0, "tr", str(video)), "")
        side = self.tmp / "Yanında.S01E01.en.srt"
        side.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
        (self.tmp / "Yanında.S01E01.tr.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n", encoding="utf-8")
        self.assertEqual(fetch.loaded_subtitle(0, "tr", str(video)), str(side))
        self.assertEqual(fetch.loaded_subtitle(0, "tr", "http://x/y.mkv"), "")

    def test_lyrics_no_false_positives(self):
        """İnceleme bulguları: konuşma şarkı sanılmasın (Orijinal bırak modunda çevrilmeden kalırdı)."""
        from subtrans import lyrics
        from subtrans.srt_io import Block

        def B(p, s, e, t):
            return Block(p, p + 1, s, e, t)
        cases = {
            "baştaki ♪ sonra konuşma": ([B(0, 0, 2000, "♪ I've been waiting all night"), B(1, 2100, 4000, "♪ for you"),
                                        B(2, 5000, 6500, "Hey, you're late."), B(3, 7000, 9000, "I know, I'm sorry.")],
                                       {0, 1}),
            "ses notu": ([B(0, 0, 2000, "♪ upbeat music playing"), B(1, 2500, 4000, "So what did the doctor say?")],
                         set()),
            "romajiye benzeyen İngilizce": ([B(0, 0, 2000, "No, no, no!"), B(1, 3000, 5000, "No, no. Go to Mama."),
                                             B(2, 6000, 8000, "Take me home")], set()),
            "şarkıdan sonra isimler": ([B(0, 0, 3000, "Sono yume wa kokoro no ibasho"), B(1, 4000, 5000, "Naruto!"),
                                        B(2, 6000, 7000, "Sasuke!")], {0}),
            "hashtag": ([B(0, 0, 2000, "#1 fan of the team")], set()),
            "Japonca ♪～": ([B(0, 0, 2000, "♪～"), B(1, 2500, 4000, "何してるの？"), B(2, 4500, 6000, "べつに")], set()),
        }
        for name, (blocks, want) in cases.items():
            self.assertEqual(lyrics.find_lyrics(blocks), want, name)
        # romaji şarkı + aynı anda başlayan italik olmayan konuşma: konuşma atılmaz, normal çevrilir
        bl = [B(0, 0, 3000, "♪ Sono yume wa kokoro no ibasho"), B(1, 100, 3000, "Where are you going?")]
        found = lyrics.find_lyrics(bl) | {1}
        for mode in ("translate", "keep", "both"):
            p = lyrics.plan(bl, found, mode)[1]
            self.assertTrue(p.plain and not p.drop, mode)

    def test_review_fixes_041(self):
        """0.4.1: bağımsız incelemenin bulduğu hatalar."""
        import time
        from defta import fetch, search, store
        from defta.media import Media
        from defta.sources.base import Sub
        from subtrans.provider import is_credit_error
        # hız sınırı mesajındaki "billing" bağlantısı kredi bitti sayılmaz
        msg = ("Rate limit reached for gpt in organization org-x on requests per day (RPD): Limit 200. "
               "Add a payment method to your account to increase your rate limit. Visit "
               "https://platform.openai.com/account/billing to add a payment method.")
        self.assertFalse(is_credit_error(429, {"error": {"message": msg, "type": "requests", "code": "rate_limit_exceeded"}}))
        self.assertTrue(is_credit_error(429, {"error": {"message": "x", "type": "insufficient_quota"}}))
        # kaymış işareti, tercihin seçildiği bölümde de yükleyenin önceliğini kaldırır
        m = Media(title="Kay", season=1, episode=2)
        s = Sub("opensubtitles", "k1", "en", release="Kay.S01E02-GRP", uploader="bob")
        store.remember_choice(m, s)
        store.mark_bad(m, s)
        self.assertIsNone(store.series_pref(m.next_episode()))
        # OpenSubtitles son hakkı kullanılınca işaret kalır
        from defta.sources import opensubtitles
        orig = opensubtitles.download

        def last(sub, mm):
            store.set_quota("opensubtitles", time.time() + 3600, True)
            return "x.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
        opensubtitles.download = last
        try:
            fetch.get(Sub("opensubtitles", "last1", "en"), m)
        finally:
            opensubtitles.download = orig
        self.assertIn("opensubtitles", store.quota_blocked())
        # yedeği aynı (engelli) sitedeyse "indirilebilir" sayılmaz
        blocked = store.quota_blocked()
        same = Sub("opensubtitles", "a1", "en", alts=[Sub("opensubtitles", "a2", "en").to_dict()])
        other = Sub("opensubtitles", "b1", "en", alts=[Sub("subdl", "b2", "en").to_dict()])
        self.assertFalse(search._reachable(same, blocked))
        self.assertTrue(search._reachable(other, blocked))
        store.clear_quota("opensubtitles")
        # "Ep1" için "Ep10.en.srt" alınmaz
        video = self.tmp / "Ep1.mkv"
        video.write_bytes(b"\0")
        (self.tmp / "Ep10.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nX\n", encoding="utf-8")
        self.assertEqual(fetch.loaded_subtitle(0, "tr", str(video)), "")

    def test_next_season_needs_exact_season_041(self):
        """Yayındaki anime: sıradaki bölüm yok diye Jimaku'nun sezonsuz 1. bölümü '2. sezon' sanılmaz."""
        from defta import service_main, store
        from defta.media import Media
        from defta.sources import jimaku, opensubtitles, subdl
        from defta.sources.base import Sub
        wrong = Sub("jimaku", "https://jimaku.cc/x/ep1.srt", "ja", release="Anime - 01", season=2, episode=1)
        orig = (opensubtitles.search, subdl.search, jimaku.search, jimaku.download)
        jimaku.download = lambda sub, mm: ("ep1.srt", "1\n00:00:01,000 --> 00:00:02,000\nやあ\n".encode())
        opensubtitles.search = lambda m, w: []
        subdl.search = lambda m, w: []
        jimaku.search = lambda m, w: [wrong] if (m.season, m.episode) == (2, 1) else []
        xbmcaddon.SETTINGS.update(jimaku_enabled="true", jimaku_key="test")
        from defta import kodi
        kodi.forget_secret_cache()
        svc = service_main.Service()
        svc.player.is_playing = lambda mm: True
        called = []
        jimaku_search = jimaku.search
        jimaku.search = lambda m, w: called.append((m.season, m.episode)) or jimaku_search(m, w)
        try:
            svc._prefetch_inner(Media(title="Anime", season=1, episode=5), 2)
            self.assertIn((2, 1), called, "yeni sezon da arandı")
            self.assertFalse([j for j in store.list_jobs("pending") if j["media"]["title"] == "Anime"])
        finally:
            opensubtitles.search, subdl.search, jimaku.search, jimaku.download = orig
            xbmcaddon.SETTINGS.update(jimaku_enabled="false", jimaku_key="")
            kodi.forget_secret_cache()
            store.clean("search")

    def test_requested_failed_falls_back_to_autoload_041(self):
        from defta import service_main
        from defta.media import Media
        svc = service_main.Service()
        svc.player.is_playing = lambda mm: True
        svc.monitor.waitForAbort = lambda t=0: False
        called = []
        svc._autoload = lambda m, f: called.append(m.key())
        m = Media(title="Istek", season=1, episode=3)
        xbmcaddon.SETTINGS["auto_human"] = "true"
        try:
            svc.load_requested(m, "opensubtitles-yok")
        finally:
            xbmcaddon.SETTINGS.pop("auto_human", None)
        self.assertEqual(called, [m.key()], "önceden çeviri olmadıysa normal otomatik yol denenir")

    def test_048_autoload_prefers_ready_ai(self):
        """İnsan ve AI otomatik yükleme ikisi de açıksa hazır AI çevirisi önce gelir, arama yapılmaz."""
        from defta import search, service_main
        from defta.media import Media
        svc = service_main.Service()
        svc.player.is_playing = lambda mm: True
        svc.monitor.waitForAbort = lambda t=0: False
        svc._has_target_stream = lambda tgt: False
        ready = {"uid": "opensubtitles-1", "path": "x.tr.srt", "src": {}}
        svc._good_ready = lambda mm: ready
        loaded, searched = [], []
        svc._load_ready = lambda mm, e, tgt: loaded.append(e["uid"])
        orig = search.search
        search.search = lambda mm: searched.append(1)
        xbmcaddon.SETTINGS.update(auto_human="true", auto_ai="true")
        try:
            svc._autoload(Media(title="Hazir", season=1, episode=1), "")
        finally:
            search.search = orig
            xbmcaddon.SETTINGS.pop("auto_human", None)
            xbmcaddon.SETTINGS.pop("auto_ai", None)
        self.assertEqual(loaded, ["opensubtitles-1"])
        self.assertEqual(searched, [], "hazır AI varken arama yapılmaz")

    def test_042_scan_both_review_estimate(self):
        import time as _t
        import xbmcgui
        from defta import fetch
        # "Yükleneni çevir": altyazı eklentisi ve video eklentisi klasörü taranır, diğer eklentiler taranmaz
        data = self.tmp / "addon_data"
        sub_dir = data / "service.subtitles.a4ksubtitles" / "temp"
        other = data / "script.weather.x"
        sub_dir.mkdir(parents=True, exist_ok=True)
        other.mkdir(parents=True, exist_ok=True)
        xbmcgui.Window.PROPS["defta.play_start"] = str(_t.time() - 60)
        try:
            (other / "x.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nNo\n", encoding="utf-8")
            self.assertEqual(fetch.loaded_subtitle(fetch.playback_started(), "tr"), "")
            a4k = sub_dir / "Dizi.S01E01.en.srt"
            a4k.write_text("1\n00:00:01,000 --> 00:00:02,000\nYes\n", encoding="utf-8")
            self.assertEqual(fetch.loaded_subtitle(fetch.playback_started(), "tr"), str(a4k))
        finally:
            xbmcgui.Window.PROPS.pop("defta.play_start", None)
            os.remove(a4k)
        # İkisi birden: anlam tek satırda
        from subtrans.lyrics import wrap_meaning
        self.assertEqual(wrap_meaning("Rüzgârda sana sesleniyorum,\n♪ uzak gökte ışık düşüyor. ♪"),
                         "(Rüzgârda sana sesleniyorum, uzak gökte ışık düşüyor)")
        # küfür kuralı: dublaj kalıbı örnek olarak verilmiyor
        rules = json.loads((ADDON / "resources" / "lib" / "rules" / "tr.json").read_text(encoding="utf-8"))["instructions"]
        self.assertIn("dubbed film", rules)
        self.assertNotIn("'Damn it!' -> 'Kahretsin!'", rules)

    def test_042_review_context_has_translation_and_no_estimate(self):
        from types import SimpleNamespace
        from subtrans.chunking import Chunk
        from subtrans.reviewer import ReviewMixin
        from subtrans.textprep import Prepared
        prepared = [Prepared(send_text=t) for t in ("If you don't come tomorrow...", "...I'll never forgive you.",
                                                    "Go home.")]
        job = SimpleNamespace(prepared=prepared, results={0: "Eğer yarın gelmezsen...", 1: "...seni affetmem.",
                                                          2: "Eve git."}, glossary=None, draft={})
        me = SimpleNamespace(_trivial=lambda t: False, _scene=lambda j, c: "")
        user, n = ReviewMixin._review_request(me, job, Chunk(2, targets=[1], before=[0], after=[2]))
        payload = json.loads(user)
        self.assertEqual(payload["context_before"][0]["translation"], "Eğer yarın gelmezsen...")
        self.assertEqual(payload["context_after"][0]["translation"], "Eve git.")
        # Kodi'de maliyet tahmini yapılmaz
        from defta import translate
        src = Path(translate.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count(".estimate = False"), 2)

    def test_043_song_flag_reading_speed_spelling(self):
        import io
        from defta.translate import Translator
        seen = []
        orig = FakeOpenAI.do_POST

        def spy(h):
            raw = h.rfile.read(int(h.headers["Content-Length"]))
            seen.append(json.loads(raw))
            h.rfile = io.BytesIO(raw)
            return orig(h)
        FakeOpenAI.do_POST = spy
        try:
            Translator(self._job("Hiz", SONG_SRT), lambda *a: None).run()
        finally:
            FakeOpenAI.do_POST = orig
        items, systems = [], []
        for b in seen:
            if "subtitle localizer" in b["messages"][0]["content"]:
                systems.append(b["messages"][0]["content"])
                items += json.loads(b["messages"][1]["content"])["translate"]
        song = [it for it in items if it.get("song")]
        self.assertTrue(song and all("dream" in it["text"] or "far away" in it["text"] or "light" in it["text"]
                                     or "arrow" in it["text"] for it in song), song)
        self.assertFalse([it for it in items if it.get("song") and "doing here" in it["text"]])
        self.assertTrue(any(it.get("max_chars") for it in items), "kısa süreli uzun satıra sınır")
        self.assertTrue(any("READING SPEED" in s and "SONG LYRICS" in s for s in systems))
        rules = json.loads((ADDON / "resources" / "lib" / "rules" / "tr.json").read_text(encoding="utf-8"))
        self.assertIn("question particle", rules["instructions"])

    def test_043_fast_chunks_cut_at_sentence_end(self):
        from types import SimpleNamespace
        from defta.translate import window_chunks
        blocks = []
        for i in range(30):
            text = "and then we" if i % 13 == 11 else "It is over."     # 11 ve 24 cümle ortası
            blocks.append(SimpleNamespace(pos=i, start_ms=i * 3000, end_ms=i * 3000 + 2500, text=text))
        job = SimpleNamespace(srt=SimpleNamespace(blocks=blocks), translate_pos=list(range(30)))
        chunks = window_chunks(job, 0, 90)
        for c in chunks[:-1]:
            self.assertNotEqual(blocks[c.targets[-1]].text, "and then we", "cümle ortasında kesilmez")

class TestBadSyncAI(TestSyncRanking):
    def test_badsync_on_ai_translates_next(self):
        from defta import plugin, store
        from defta.media import Media
        from defta.sources.base import Sub
        import re as _re
        e1 = Sub("opensubtitles", "31", "en", release="Dizi.S01E04.1080p-A", downloads=50)
        copy = Sub("subdl", "https://dl.subdl.com/subtitle/c.zip", "en", release="Dizi.S01E04.WEB-C", downloads=45)
        e2 = Sub("opensubtitles", "32", "en", release="Dizi.S01E04.720p-B", downloads=40)
        text = SAMPLE.read_text(encoding="utf-8")
        shifted = _re.sub(r"(\d\d:\d\d:)(\d\d)", lambda mm: mm.group(1) + f"{(int(mm.group(2)) + 1) % 60:02d}", text)
        for e, t in ((e1, text), (e2, shifted)):
            Path(store.download_path(e, ".srt")).write_text(t, encoding="utf-8")
            store.remember_fingerprint(e, store.download_path(e, ".srt"))
        store.clean("syncmarks")
        self._patch([e1, copy, e2])
        from defta.sources import subdl
        orig_dl = subdl.download
        subdl.download = lambda sub, mm: ("c.srt", text.encode("utf-8"))   # henüz indirilmemiş aynı içerik
        self.addCleanup(setattr, subdl, "download", orig_dl)
        m = Media(title="Dizi", season=1, episode=4, filename="Dizi.S01E04.1080p-A.mkv")
        from defta.translate import Translator
        Translator(store.new_job(m, e1, store.download_path(e1, ".srt"), fresh=False, load=False),
                   lambda *a: None).run()
        store.set_loaded(m, "ai", e1)                       # e1'den yapılan AI çevirisi yüklü
        xbmc.INFO.clear()
        xbmc.INFO["Player.Filenameandpath"] = m.filename
        xbmcplugin.ITEMS.clear()
        sys.argv = ["plugin://x/", "1", "?action=search"]
        plugin.main()
        labels = [li.label2 for _, li in xbmcplugin.ITEMS]
        self.assertTrue(any(x.startswith("Kaymış") for x in labels), labels)
        xbmcplugin.ITEMS.clear()
        sys.argv = ["plugin://x/", "1", "?" + __import__("urllib.parse").parse.urlencode(
            {"action": "download", "kind": "badsync", "media": json.dumps(m.to_dict())})]
        plugin.main()
        self.assertIn("opensubtitles-31", store.bad_uids(m))
        self.assertIn(copy.uid(), store.bad_uids(m), "kaymış olanın başka sitedeki aynısı da atlanır")
        jobs = store.active_jobs(m)
        self.assertEqual([j["sub"]["sid"] for j in jobs], ["32"], "sıradaki (farklı) kaynak çevrilir")
        self.assertEqual(store.get_loaded(m)["sub"]["sid"], "32")
        for j in jobs:
            j["status"] = "cancelled"
            store.save_job(j)

    def test_048_badsync_skips_unreachable_site(self):
        """Ulaşılamayan sitenin diğer adayları denenmez (her deneme Kodi penceresinde ~1 dk sürebilir)."""
        from defta import plugin, store
        from defta.media import Media
        from defta.sources.base import Sub, SourceError
        import re as _re
        e1 = Sub("opensubtitles", "41", "en", release="Dizi.S01E05.1080p-A", downloads=50)
        c1 = Sub("subdl", "https://dl.subdl.com/subtitle/d1.zip", "en", release="Dizi.S01E05.WEB-C", downloads=45)
        c2 = Sub("subdl", "https://dl.subdl.com/subtitle/d2.zip", "en", release="Dizi.S01E05.WEB-D", downloads=44)
        e2 = Sub("opensubtitles", "42", "en", release="Dizi.S01E05.720p-B", downloads=40)
        text = SAMPLE.read_text(encoding="utf-8")
        shifted = _re.sub(r"(\d\d:\d\d:)(\d\d)", lambda mm: mm.group(1) + f"{(int(mm.group(2)) + 2) % 60:02d}", text)
        for e, t in ((e1, text), (e2, shifted)):
            Path(store.download_path(e, ".srt")).write_text(t, encoding="utf-8")
        self._patch([e1, c1, c2, e2])
        from defta.sources import subdl
        calls = []

        def boom(sub, mm):
            calls.append(sub.sid)
            raise SourceError("bağlantı kurulamadı")
        orig_dl = subdl.download
        subdl.download = boom
        self.addCleanup(setattr, subdl, "download", orig_dl)
        m = Media(title="Dizi", season=1, episode=5, filename="Dizi.S01E05.1080p-A.mkv")
        store.set_loaded(m, "ai", e1)
        xbmc.INFO.clear()
        xbmc.INFO["Player.Filenameandpath"] = m.filename
        xbmcplugin.ITEMS.clear()
        sys.argv = ["plugin://x/", "1", "?" + __import__("urllib.parse").parse.urlencode(
            {"action": "download", "kind": "badsync", "media": json.dumps(m.to_dict())})]
        plugin.main()
        self.assertEqual(len(calls), 1, "aynı siteden ikinci aday denenmez")
        jobs = store.active_jobs(m)
        self.assertEqual([j["sub"]["sid"] for j in jobs], ["42"])
        for j in jobs:
            j["status"] = "cancelled"
            store.save_job(j)

    def test_044_bleeped_swearing_kept_for_model(self):
        from subtrans.textprep import prepare
        dec = {"sound_notes": "remove", "speaker_labels": "remove", "speaker_dashes": "keep", "lyrics": "translate",
               "format_tags": "remove"}
        self.assertEqual(prepare("It's (bleep) rotten, you (bleep) idiot!", dec).send_text,
                         "It's (bleep) rotten, you (bleep) idiot!")
        self.assertEqual(prepare("(BLEEP) off!", dec).send_text, "(BLEEP) off!")
        self.assertEqual(prepare("[DOOR SLAMS] Get out!", dec).send_text, "Get out!")
        rules = json.loads((ADDON / "resources" / "lib" / "rules" / "tr.json").read_text(encoding="utf-8"))
        self.assertIn("Censored swearing", rules["instructions"])

    def test_046_glossary_twins_and_nationality(self):
        from subtrans.glossary import Glossary
        g = Glossary(self.tmp / "g.json", "Test", "tr", lower_races=True)
        ep1 = "Connie Springer Shiganshina Italian Hank Frank elves Anna Smith Daniel Hikaru Hitachiin"
        g.merge([{"source": "Connie Springer", "target": "Connie Springer", "type": "character"},
                 {"source": "Shiganshina", "target": "Shiganshina", "type": "place"},
                 {"source": "Italian", "target": "italyan", "type": "race"},
                 {"source": "Hank", "target": "Hank", "type": "character"},
                 {"source": "Frank", "target": "Frank", "type": "character"},
                 {"source": "Anna Smith", "target": "Anna Smith", "type": "character"},
                 {"source": "Daniel", "target": "Daniel", "type": "character"},
                 {"source": "Hikaru Hitachiin", "target": "Hikaru Hitachiin", "type": "character"},
                 {"source": "elves", "target": "Elfler", "type": "race"}], ep1)
        # Sonraki bölüm başka altyazı grubundan: aynı adlar farklı yazılmış
        ep2 = "Conny Springer Zhiganshina Danielle Kaoru Hitachiin Anne Smith Anna Smith"
        g.merge([{"source": "Conny Springer", "target": "Conny Springer", "type": "character"},
                 {"source": "Zhiganshina", "target": "Zhiganshina", "type": "place"},
                 {"source": "Danielle", "target": "Danielle", "type": "character"},
                 {"source": "Kaoru Hitachiin", "target": "Kaoru Hitachiin", "type": "character"},
                 {"source": "Anne Smith", "target": "Anne Smith", "type": "character"}], ep2)
        by = {t["target"]: t for t in g.terms}
        self.assertIn("Conny Springer", by["Connie Springer"]["forms"], "aynı adın başka yazımı tek terim")
        self.assertIn("Zhiganshina", by["Shiganshina"]["forms"])
        for other in ("Danielle", "Kaoru Hitachiin", "Anne Smith"):
            self.assertIn(other, by, "farklı kişi birleştirilmez")
        self.assertNotIn("italyan", by, "milliyet sözlüğe alınmaz")
        self.assertIn("Hank", by)
        self.assertIn("Frank", by)
        self.assertIn("elfler", by, "kurgusal ırk küçük harf")

    def test_048_beep_is_a_sound(self):
        from subtrans.textprep import prepare
        dec = {"sound_notes": "remove", "speaker_labels": "remove", "speaker_dashes": "keep", "lyrics": "translate",
               "format_tags": "remove"}
        self.assertEqual(prepare("Leave a message after the beep. [beep]", dec).send_text,
                         "Leave a message after the beep.")
        self.assertEqual(prepare("[bleeps] you!", dec).send_text, "[bleeps] you!")


class TestReviewCache(unittest.TestCase):
    def test_047_review_context_stable_on_rerun(self):
        """Parçalar aynı anda kontrol edilir; komşunun düzeltmesi bağlamı değiştirmemeli,
        ikinci çalışmada anlam kontrolü tamamen önbellekten gelmeli."""
        import asyncio
        from subtrans.cache import CachedProvider, Store
        from subtrans.config import DEFAULTS, Settings, _merge, load_rule_pack
        from subtrans.engine import Engine, FileJob
        from subtrans.provider import Provider, Usage
        from subtrans.srt_io import Block, SrtFile

        class Fake(Provider):
            name = "fake"

            def __init__(self):
                self.reviews = 0

            async def complete_json(self, system, user, max_out, schema=None, schema_name=""):
                await asyncio.sleep(0.01)
                u = json.loads(user)
                if "check" in u:
                    self.reviews += 1
                    return {"fixes": [{"id": it["id"], "text": it["translation"] + " düzeltildi", "problem": "x",
                                       "certainty": "certain"} for it in u["check"] if it["id"] == 10]}, Usage(requests=1)
                return {"translations": [{"id": it["id"], "text": "Çeviri " + it["text"]}
                                         for it in u["translate"]]}, Usage(requests=1)

            async def close(self):
                pass

        tmp = Path(tempfile.mkdtemp())
        raw = _merge(DEFAULTS, {"language": {"target": "tr", "source": "en"},
                                "provider": {"model": "m", "base_url": "http://x"}, "chunking": {"chunk_size": 10},
                                "quality": {"review": True, "episode_analysis": False}})

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)        # 3.8: asyncio.Condition döngü ister
            eng = Engine(Settings(raw=raw, base_dir=tmp), None, load_rule_pack("tr"),
                         {"sound_notes": "remove", "speaker_labels": "remove", "speaker_dashes": "keep",
                          "lyrics": "translate"})
            eng.estimate = False
            st = Store(tmp / "c.sqlite")
            fake = Fake()
            eng.provider = CachedProvider(fake, st, "m")
            eng.store = st
            blocks = [Block(i, i + 1, i * 3000, i * 3000 + 2500, f"This is sentence number {i} for testing.")
                      for i in range(20)]
            job = FileJob(srt=SrtFile(path=Path("x.srt"), encoding="utf-8", had_bom=False, encoding_confidence=1.0,
                                      blocks=blocks), rel=Path("x.srt"), out_path=Path("/dev/null"), src_lang="en")
            eng.plan(job)
            loop.run_until_complete(eng.translate_file(job))
            st.close()
            loop.close()
            asyncio.set_event_loop(None)
            return job, fake

        j1, f1 = run()
        self.assertEqual(j1.review_fixes, 1)
        self.assertEqual(f1.reviews, 2)
        _, f2 = run()
        self.assertEqual(f2.reviews, 0, "ikinci çalışmada kontrol önbellekten")


class TestSubdlV2(unittest.TestCase):
    def setUp(self):
        from defta import net
        self.net = net
        self.calls = []
        self._orig = (net.get_json, net.get_bytes)
        self.addCleanup(self._restore)

    def _restore(self):
        self.net.get_json, self.net.get_bytes = self._orig

    def test_049_search_chain_header_and_download(self):
        from defta.media import Media
        from defta.sources import subdl
        replies = {"/subtitles/search": [{}, {"status": True, "subtitles": [
            {"subtitlePage": "/s/info/abc123", "url": "/subtitle/1-2.zip?api_key=SECRET",
             "release_name": "Kitchen.Nightmares.S01E01.HDTV", "language": "EN", "lang": "english", "season": 1,
             "episode": 1, "author": "x", "hi": True},
            {"n_id": "zzz", "release_name": "Kitchen.Nightmares.S01E02", "language": "EN", "season": 1, "episode": 2},
            {"release_name": "no-id"}]}], "/files/search": [{"status": True, "subtitles": []}]}

        def fake_json(url, params=None, headers=None, timeout=15.0):
            path = url.split("/api/v2")[1]
            self.calls.append((path, dict(params or {}), dict(headers or {})))
            return replies[path].pop(0)

        def fake_bytes(url, params=None, headers=None, timeout=30.0):
            self.calls.append((url, dict(params or {}), dict(headers or {})))
            return SAMPLE.read_bytes()
        self.net.get_json, self.net.get_bytes = fake_json, fake_bytes
        m = Media(title="Kitchen Nightmares US", season=1, episode=1,
                  filename="Kitchen.Nightmares.US.S01E01.720p.HDTV.x264.mkv")
        subs = subdl.search(m, ["en"])
        self.assertEqual([s.sid for s in subs], ["abc123"], "başka bölüm ve kimliksiz kayıt atlanır")
        self.assertTrue(subs[0].hi, "SDH bilgisi gelir")
        self.assertEqual(subs[0].lang, "en")
        self.assertEqual([c[0] for c in self.calls], ["/files/search", "/subtitles/search", "/subtitles/search"])
        self.assertEqual(self.calls[2][1]["film_name"], "Kitchen Nightmares", "ülke ekisiz ikinci ad araması")
        self.assertEqual(self.calls[1][1]["season"], 1)
        for _, params, headers in self.calls:
            self.assertNotIn("api_key", params, "anahtar adrese yazılmaz")
            self.assertTrue(headers["Authorization"].startswith("Bearer "))
        name, data = subdl.download(subs[0], m)
        self.assertTrue(self.calls[-1][0].endswith("/api/v2/subtitles/abc123/download"))
        self.assertTrue(data)

    def test_049_quota_and_errors(self):
        from defta.media import Media
        from defta.sources import subdl
        from defta.sources.base import QuotaError, SourceError
        net = self.net

        def quota(url, params=None, headers=None, timeout=30.0):
            raise net.HttpError(429, "x", {"error": {"code": "quota_exceeded", "message": "Daily"}},
                                {"X-RateLimit-Reset": str(int(time.time()) + 3600)})
        net.get_bytes = quota
        from defta.sources.base import Sub
        with self.assertRaises(QuotaError) as cm:
            subdl.download(Sub("subdl", "abc", "en"), Media(title="X"))
        self.assertAlmostEqual(cm.exception.until, time.time() + 3600, delta=5)
        with self.assertRaises(SourceError):
            subdl.download(Sub("subdl", "/subtitle/1-2.zip", "en"), Media(title="X"))
        self.assertIn("zaman aşımı", net.explain("SubDL araması başarısız (HTTP 0: bağlantı kurulamadı: timed out)"))
        self.assertIn("adı bulunamadı", net.explain("HTTP 0: bağlantı kurulamadı: [Errno 11001] getaddrinfo failed"))
        self.assertIn("HTTP 503", net.explain("SubDL araması başarısız (HTTP 503: x)"))
        self.assertEqual(net.explain("SubDL API anahtarı kabul edilmedi"), "")

    def test_049_bare_title(self):
        from defta.media import bare_title
        self.assertEqual(bare_title("Kitchen Nightmares US"), "Kitchen Nightmares")
        self.assertEqual(bare_title("The Office (UK)"), "The Office")
        self.assertEqual(bare_title("Lupus"), "")


class TestRealForeignLanguage(unittest.TestCase):
    def test_050_real_languages_are_translated(self):
        from subtrans import prompts
        self.assertIn("Na zdorovje", prompts.EXTRACT_SYSTEM, "gerçek diller uydurma dil listesine alınmaz")
        sp = prompts.SYSTEM_TEMPLATE
        self.assertIn("REAL foreign languages", sp)
        self.assertIn("{tgt}", sp.split("REAL foreign languages")[1].split("\n")[0])
