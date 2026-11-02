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
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "service.subtitles.defta"
sys.path.insert(0, str(Path(__file__).resolve().parent / "fakekodi"))
sys.path.insert(0, str(ADDON / "resources" / "lib"))

import xbmc  # noqa: E402
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402
import xbmcvfs  # noqa: E402

SAMPLE = Path(os.environ.get("SUBTRANS_DIR", ROOT.parent / "altyazi_cevirici")) / "ornek" / "kaynak" / \
    "Ornek.Anime.S01E01.en.srt"


class FakeOpenAI(BaseHTTPRequestHandler):
    requests = 0

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
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
        self.assertEqual(m.key(), "tt13706018-s01e02")
        xbmc.INFO.clear()


class TestRanking(Base):
    def test_score(self):
        from defta.search import match_score
        from defta.sources.base import Sub
        v = "Interstellar.2014.1080p.BluRay.x264-SPARKS.mkv"
        a = Sub("opensubtitles", "1", "en", release="Interstellar.2014.720p.BluRay.x264-SPARKS")
        b = Sub("opensubtitles", "2", "en", release="Interstellar.2014.1080p.WEB-DL.DD5.1.H264-RARBG")
        self.assertGreater(match_score(v, a), match_score(v, b))

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
            self.assertTrue(labels[-1].startswith("→ Yüklenen altyazıyı"))
        finally:
            opensubtitles.search, subdl.search = orig
            xbmc.JSONRPC.clear()
            xbmc.INFO.clear()


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
        self.assertIn("partial", stages)
        self.assertIn("fast", stages)
        self.assertEqual(stages[-1], "final")
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("TR[", text)
        self.assertTrue(store.ready_translations(m))
        n1 = FakeOpenAI.requests
        Translator(store.new_job(m, sub, src, fresh=False, load=False), lambda *a: None).run()
        self.assertEqual(FakeOpenAI.requests, n1, "ikinci çeviri önbellekten gelmeli")
        # Temizlik
        self.assertIn("silindi", store.clean("ai"))
        self.assertFalse(store.ready_translations(m))

    def test_bad_key(self):
        import asyncio
        from defta.translate import build_settings
        from subtrans.provider import ProviderError, make_provider
        xbmcaddon.SETTINGS["openai_key"] = "sk-wrong"
        try:
            s, _ = build_settings()
            p = make_provider(s["provider"])
            with self.assertRaises(ProviderError):
                __import__("defta.kodi").kodi.run_async(p.check())
        finally:
            xbmcaddon.SETTINGS["openai_key"] = "sk-test-key-000000000000000000000000000000000000"


if __name__ == "__main__":
    unittest.main()
