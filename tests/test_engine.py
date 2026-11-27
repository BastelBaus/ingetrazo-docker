# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests of the render engine (mcp_http/engine_server.py) and the
viewer's client for it (viewer/engine.py), with a fake IngeTrazo behind a
real HTTP server (no container needed):

    python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http import engine_server  # noqa: E402
from viewer import engine  # noqa: E402

TOKEN = "test-token-0123456789abcdef"


class FakeApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, float | None]] = []
        self.up = True

    def alive(self) -> bool:
        return self.up

    def probe(self, max_busy: float):
        return (200, {"status": "ok"}) if self.up else (503, {"status": "ingetrazo not running"})

    def call(self, tool, args=None, timeout=None):
        self.calls.append((tool, args or {}, timeout))
        return {"ok": True, "result": {"tool": tool}}


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.cache = root / "data", root / "cache"
        self.data.mkdir()
        self.cache.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, tool, args):
        return engine_server.check(tool, args, self.data, self.cache)

    def test_allowed(self):
        self.assertIsNone(self.check("open_document", {"path": str(self.data / "a.igz")}))
        self.assertIsNone(self.check("export_pdf", {"path": str(self.cache / "x" / "s.pdf")}))
        self.assertIsNone(self.check("run_python", {"code": "print(1)"}))
        self.assertIsNone(self.check("list_sheets", {}))

    def test_unknown_tool(self):
        self.assertIn("not available", self.check("save_document", {}))
        self.assertIn("not available", self.check("shutdown", {}))

    def test_paths_confined(self):
        self.assertIn("below", self.check("open_document", {"path": str(self.cache / "a.igz")}))
        self.assertIn("below", self.check("open_document", {"path": str(self.data / ".." / "a.igz")}))
        self.assertIn("below", self.check("export_view", {"path": str(self.data / "v.png")}))
        self.assertIn("below", self.check("export_file", {}))
        self.assertIn("args", self.check("list_sheets", ["x"]))


class TokenTest(unittest.TestCase):
    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"ENGINE_TOKEN": " abc ", "ENGINE_TOKEN_FILE": ""}):
            self.assertEqual(engine_server.engine_token(create=True), "abc")

    def test_file_made_once_and_shared(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "sub" / "token"
            env = {"ENGINE_TOKEN": "", "ENGINE_TOKEN_FILE": str(f)}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(engine_server.engine_token(), "")      # viewer: not yet
                made = engine_server.engine_token(create=True)          # engine
                self.assertEqual(len(made), 64)
                self.assertEqual(f.stat().st_mode & 0o777, 0o600)
                self.assertEqual(engine_server.engine_token(create=True), made)
                self.assertEqual(engine_server.engine_token(), made)    # viewer reads it
                self.assertEqual(sorted(p.name for p in f.parent.iterdir()), ["token"])

    def test_urls(self):
        with mock.patch.dict(os.environ, {"ENGINE_URLS": "", "ENGINE_TOKEN": ""}):
            self.assertEqual(engine.engine_urls(), [])
        with mock.patch.dict(os.environ, {"ENGINE_URLS": "http://a:1/, http://b:2",
                                          "ENGINE_TOKEN": TOKEN}):
            self.assertEqual(engine.engine_urls(), ["http://a:1", "http://b:2"])
        with mock.patch.dict(os.environ, {"ENGINE_URLS": "a:1", "ENGINE_TOKEN": TOKEN}):
            with self.assertRaises(SystemExit):
                engine.engine_urls()
        with mock.patch.dict(os.environ, {"ENGINE_URLS": "http://a:1", "ENGINE_TOKEN": "",
                                          "ENGINE_TOKEN_FILE": ""}):
            with self.assertRaises(SystemExit):
                engine.engine_urls()


class RoundTripTest(unittest.TestCase):
    """RemoteApp → engine HTTP server → fake app, and back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cache = root / "cache"
        self.cache.mkdir()
        self.fake = FakeApp()
        self.patches = [
            mock.patch.object(engine_server, "app", self.fake, create=True),
            mock.patch.object(engine_server, "TOKEN", TOKEN),
            mock.patch.object(engine_server, "DATA_DIR", root),
            mock.patch.dict(os.environ, {"INGETRAZO_CACHE": str(self.cache)}),
        ]
        for p in self.patches:
            p.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), engine_server.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_call(self):
        app = engine.RemoteApp(self.url, TOKEN)
        app.start()
        self.assertTrue(app.alive())
        reply = app.call("list_sheets", {})
        self.assertEqual(reply, {"ok": True, "result": {"tool": "list_sheets"}})
        reply = app.call("export_pdf", {"path": str(self.cache / "s.pdf")}, timeout=5)
        self.assertTrue(reply["ok"])
        self.assertEqual(self.fake.calls[-1], ("export_pdf", {"path": str(self.cache / "s.pdf")}, 5.0))

    def test_wrong_token(self):
        reply = engine.RemoteApp(self.url, "wrong-token-0123456789").call("list_sheets")
        self.assertFalse(reply["ok"])
        self.assertIn("unauthorized", reply["error"])
        self.assertEqual(self.fake.calls, [])

    def test_refused_tool(self):
        reply = engine.RemoteApp(self.url, TOKEN).call("save_document", {"path": "/x"})
        self.assertFalse(reply["ok"])
        self.assertIn("not available", reply["error"])
        self.assertEqual(self.fake.calls, [])

    def test_engine_down(self):
        app = engine.RemoteApp(self.url, TOKEN)
        app.ready = True
        self.fake.up = False
        self.assertFalse(app.alive())
        reply = app.call("list_sheets")
        self.assertFalse(reply["ok"])

    def test_unreachable(self):
        app = engine.RemoteApp("http://127.0.0.1:9", TOKEN)
        reply = app.call("list_sheets")
        self.assertFalse(reply["ok"])
        self.assertIn("render engine", reply["error"])


if __name__ == "__main__":
    unittest.main()
