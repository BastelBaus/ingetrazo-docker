# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests of who may do what over MCP: MCP_TOKEN, personal tokens,
OAuth users, and their folders (no container needed):

    python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http import auth as mcp_auth  # noqa: E402
from mcp_http import folders  # noqa: E402
from mcp_http import server  # noqa: E402
from mcp_http.users import UserError, Users  # noqa: E402

TOKEN = "full-access-token-0123456789"


class FakeApp:
    """IngeTrazo's MCP handler: answers tools/list and records tool calls."""

    def __init__(self) -> None:
        self.open: str | None = None
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool: str, args: dict | None = None) -> dict:
        if tool == "list_documents":
            docs = [{"document_id": "d1", "path": self.open}] if self.open else []
            return {"ok": True, "result": {"documents": docs}}
        return {"ok": False}

    def handle(self, msg: dict) -> dict:
        if msg["method"] == "tools/list":
            return {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": n} for n in ("open_document", "save_document", "run_python",
                                      "list_sheets", "shutdown", "list_documents")]}}
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        self.calls.append((name, args))
        if name == "open_document":
            self.open = args["path"]
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [], "isError": False}}


class McpRightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        data = base / "data"
        for rel in ("work/a/m.igz", "work/b/n.igz", "lib/o.igz"):
            (data / rel).parent.mkdir(parents=True, exist_ok=True)
            (data / rel).write_bytes(b"x")
        self.saved = (folders.DATA_DIR, folders.FOLDERS,
                      {k: os.environ.get(k) for k in ("MCP_AUTH", "MCP_TOKEN", "USERS_DB",
                                                      "DEFAULT_SHARES")},
                      getattr(server, "app", None), getattr(server, "auth", None))
        folders.DATA_DIR = data
        folders.FOLDERS = folders.parse("work:rw; lib:ro", data_dir=data)
        os.environ.update(MCP_AUTH="token", MCP_TOKEN=TOKEN, USERS_DB=str(base / "users.db"))
        os.environ.pop("DEFAULT_SHARES", None)
        self.users = Users(base / "users.db")
        self.users.ensure_admin("boss", "secret-pass")
        self.users.create("anna", "anna-pass")
        self.users.set_shares("anna", [{"path": "work/a", "access": "rw"},
                                       {"path": "lib", "access": "r"}])
        server.auth = mcp_auth.Auth()
        server.app = self.app = FakeApp()

    def tearDown(self):
        folders.DATA_DIR, folders.FOLDERS, env, server.app, server.auth = self.saved
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def caller(self, token: str):
        return server.auth.caller({"Authorization": f"Bearer {token}"})

    def call(self, caller, name, **args):
        reply = server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": name, "arguments": args}}, caller)
        result = reply["result"]
        text = (result.get("content") or [{}])[0].get("text", "")
        return result.get("isError"), text

    def test_tokens(self):
        self.assertIs(self.caller(TOKEN), mcp_auth.FULL)
        self.assertIsNone(self.caller("wrong"))
        with self.assertRaises(UserError):
            self.users.create_token("anna", "laptop")          # no MCP right yet
        self.users.set_rights("anna", mcp=True)
        token = self.users.create_token("anna", "laptop")
        self.assertTrue(token.startswith("itz_"))
        self.assertEqual(self.caller(token).account.name, "anna")
        self.assertEqual(self.users.tokens("anna")[0]["label"], "laptop")
        # Rights are read on every request.
        self.users.set_rights("anna", mcp=False)
        self.assertIsNone(self.caller(token))
        self.users.set_rights("anna", mcp=True)
        self.users.set_active("anna", False)
        self.assertIsNone(self.caller(token))
        self.users.set_active("anna", True)
        self.assertTrue(self.users.revoke_token("anna", self.users.tokens("anna")[0]["id"]))
        self.assertIsNone(self.caller(token))

    def test_shares_over_mcp(self):
        self.users.set_rights("anna", mcp=True)
        anna = self.caller(self.users.create_token("anna"))
        # The listing: only what anna may read, with her access.
        reply = server._list_documents(anna)
        listing = json.loads(reply["content"][0]["text"])
        paths = [d["path"] for f in listing["folders"] for d in f["documents"]]
        self.assertEqual(paths, ["work/a/m.igz", "lib/o.igz"])
        self.assertEqual({f["name"]: f["mode"] for f in listing["folders"]},
                         {"work": "ro", "lib": "ro"})            # rw only in work/a
        # Open, save, export.
        self.assertTrue(self.call(anna, "open_document", path="work/b/n.igz")[0])
        self.assertFalse(self.call(anna, "open_document", path="lib/o.igz")[0])
        self.assertTrue(self.call(anna, "save_document")[0])             # lib is read-only
        self.assertTrue(self.call(anna, "export_pdf", path="lib/x.pdf")[0])
        self.assertFalse(self.call(anna, "export_pdf", path="work/a/x.pdf")[0])
        self.assertFalse(self.call(anna, "open_document", path="work/a/m.igz")[0])
        self.assertFalse(self.call(anna, "save_document")[0])
        # run_python is not hers, and not listed for her.
        err, text = self.call(anna, "run_python", code="print(1)")
        self.assertTrue(err)
        self.assertIn("not available to you", text)
        tools = server._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, anna)
        names = [t["name"] for t in tools["result"]["tools"]]
        self.assertNotIn("run_python", names)
        self.assertNotIn("shutdown", names)
        # A document someone else opened that anna may not read.
        self.call(mcp_auth.FULL, "open_document", path="work/b/n.igz")
        err, text = self.call(anna, "list_sheets")
        self.assertTrue(err)
        self.assertIn("may not read", text)

    def test_full_access_as_before(self):
        full = self.caller(TOKEN)
        self.assertFalse(self.call(full, "run_python", code="1")[0])
        self.assertFalse(self.call(full, "open_document", path="work/b/n.igz")[0])
        self.assertTrue(self.call(full, "export_pdf", path="lib/x.pdf")[0])  # ro folder
        boss = self.users.create_token("boss")
        self.assertFalse(self.call(self.caller(boss), "run_python", code="1")[0])

    def test_token_mode_without_mcp_token(self):
        os.environ["MCP_TOKEN"] = ""
        server.auth = mcp_auth.Auth()
        self.assertIsNone(self.caller(""))
        self.assertIsNotNone(self.caller(self.users.create_token("boss")))
        os.environ["MCP_TOKEN"] = "short"
        with self.assertRaises(SystemExit):
            mcp_auth.Auth()

    def test_oauth_users(self):
        from mcp_http.oauth import OAuthServer
        os.environ.update(PUBLIC_URL="https://mcp.example.org", OAUTH_PASSWORD="")
        try:
            o = OAuthServer(self.users)
            o.store.path = Path(self.tmp.name) / "oauth.json"
            o.store.clients["c1"] = {"token_endpoint_auth_method": "none"}
            req = {"client_id": "c1", "client_name": "x", "redirect_uri": "https://claude.ai/cb",
                   "code_challenge": "c", "state": ""}
            o.pending["tx"] = {**req, "t": __import__("time").time()}
            target, err = o.approve({"tx": "tx", "user": "anna", "password": "anna-pass",
                                     "decision": "allow"})
            self.assertIsNone(target)                                  # anna: no MCP
            self.users.set_rights("anna", mcp=True)
            target, err = o.approve({"tx": "tx", "user": "anna", "password": "anna-pass",
                                     "decision": "allow"})
            self.assertIn("code=", target)
            [code] = o.codes
            self.assertEqual(o.codes[code]["user"], "anna")
            tokens = o._issue("c1", "anna")
            self.assertEqual(o.access_user(tokens["access_token"]), "anna")
        finally:
            os.environ.pop("PUBLIC_URL", None)
            os.environ.pop("OAUTH_PASSWORD", None)


if __name__ == "__main__":
    unittest.main()
