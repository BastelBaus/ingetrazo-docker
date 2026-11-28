# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests of the engine (engine/server.py): its checks, its secret and
its HTTP API, with a fake IngeTrazo behind a real HTTP server (no
container needed):

    python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import server as engine_server  # noqa: E402

TOKEN = "test-token-0123456789abcdef"


def post(url, path, body, token=TOKEN):
    """(HTTP status, JSON body) of a POST to the engine."""
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def get(url, path):
    try:
        with urllib.request.urlopen(url + path, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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

    def handle(self, msg):
        self.calls.append(("rpc", msg, None))
        if "id" not in msg:
            return None
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {"echo": msg.get("method")}}


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

    def test_mcp_mode(self):
        def check(tool, args):
            return engine_server.check(tool, args, self.data, self.cache, "mcp")
        self.assertIsNone(check("save_document", {}))
        self.assertIsNone(check("save_document", {"path": str(self.data / "b.igz")}))
        self.assertIsNone(check("export_pdf", {"path": str(self.data / "out" / "s.pdf")}))
        self.assertIsNone(check("import_file", {"path": str(self.data / "x.skp")}))
        self.assertIsNone(check("query_model", {}))
        self.assertIn("not available", check("shutdown", {}))
        self.assertIn("below", check("export_pdf", {"path": str(self.cache / "s.pdf")}))
        self.assertIn("below", check("save_document", {"path": "/etc/x.igz"}))
        self.assertIsNone(check("render_sheet", {"document_id": 1}))   # inline image
        self.assertIn("below", check("import_file", {"path": "/etc/passwd"}))
        # The render engine never saves.
        self.assertIn("not available", self.check("save_document", {}))

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



class RoundTripTest(unittest.TestCase):
    """HTTP → engine → fake app, and back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cache = root / "cache"
        self.cache.mkdir()
        self.fake = FakeApp()
        self.patches = [
            mock.patch.object(engine_server, "app", self.fake, create=True),
            mock.patch.object(engine_server, "TOKEN", TOKEN),
            mock.patch.object(engine_server, "MODE", "mcp"),
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

    def test_health(self):
        self.assertEqual(get(self.url, "/healthz"), (200, {"status": "ok"}))
        self.fake.up = False
        self.assertEqual(get(self.url, "/healthz")[0], 503)

    def test_health_names_the_build(self):
        with mock.patch.object(engine_server, "APP_REF", "IngeTrazo abc (date)"):
            self.assertEqual(get(self.url, "/healthz"),
                             (200, {"status": "ok", "build": "IngeTrazo abc (date)"}))

    def test_call(self):
        status, reply = post(self.url, "/call", {"tool": "list_sheets", "args": {}})
        self.assertEqual((status, reply), (200, {"ok": True, "result": {"tool": "list_sheets"}}))
        path = str(self.cache.parent / "data" / "s.pdf")
        status, reply = post(self.url, "/call", {"tool": "export_pdf", "args": {"path": path},
                                                 "timeout": 5})
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.calls[-1], ("export_pdf", {"path": path}, 5.0))

    def test_rpc(self):
        status, reply = post(self.url, "/rpc", {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        self.assertEqual(reply, {"jsonrpc": "2.0", "id": 7, "result": {"echo": "tools/list"}})
        status, reply = post(self.url, "/rpc", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, reply), (200, None))
        status, refused = post(self.url, "/rpc", {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                                  "params": {"name": "shutdown", "arguments": {}}})
        self.assertTrue(refused["result"]["isError"])
        self.assertEqual([c[1].get("method") for c in self.fake.calls],
                         ["tools/list", "notifications/initialized"])

    def test_rpc_only_in_mcp_mode(self):
        with mock.patch.object(engine_server, "MODE", "render"):
            status, _ = post(self.url, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            self.assertEqual(status, 404)

    def test_wrong_token(self):
        status, reply = post(self.url, "/call", {"tool": "list_sheets"}, token="wrong-token-0123456789")
        self.assertEqual((status, reply), (401, {"error": "unauthorized"}))
        status, _ = post(self.url, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                         token="wrong-token-0123456789")
        self.assertEqual(status, 401)
        self.assertEqual(self.fake.calls, [])

    def test_refused_tool(self):
        status, reply = post(self.url, "/call", {"tool": "shutdown", "args": {}})
        self.assertEqual(status, 403)
        self.assertIn("not available", reply["error"])
        self.assertEqual(self.fake.calls, [])

    def test_engine_down(self):
        self.fake.up = False
        status, reply = post(self.url, "/call", {"tool": "list_sheets"})
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
