# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests of the user list (mcp_http/users.py) and the viewer's login,
admin panel and account page (no container needed):

    python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http.users import UserError, Users, check_password, hash_password  # noqa: E402


class UsersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users = Users(Path(self.tmp.name) / "users.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_password_hash(self):
        h = hash_password("correct horse")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertTrue(check_password("correct horse", h))
        self.assertFalse(check_password("wrong", h))
        self.assertFalse(check_password("x", None))

    def test_first_admin_once(self):
        self.assertTrue(self.users.ensure_admin("boss", "secret-pass"))
        self.assertFalse(self.users.ensure_admin("other", "secret-pass"))
        self.assertEqual([a.name for a in self.users.all()], ["boss"])
        self.assertIsNotNone(self.users.authenticate("boss", "secret-pass"))
        self.assertIsNotNone(self.users.authenticate("BOSS", "secret-pass"))  # any case
        self.assertIsNone(self.users.authenticate("boss", "nope"))

    def test_create_and_rules(self):
        self.users.ensure_admin("boss", "secret-pass")
        self.users.create("anna", "anna-pass")
        with self.assertRaises(UserError):
            self.users.create("Anna", "another-pass")             # names ignore case
        with self.assertRaises(UserError):
            self.users.create("bad name", "secret-pass")
        with self.assertRaises(UserError):
            self.users.create("short", "1234")
        # One active admin always stays.
        for change in (lambda: self.users.set_role("boss", "user"),
                       lambda: self.users.set_active("boss", False),
                       lambda: self.users.delete("boss")):
            with self.assertRaises(UserError):
                change()
        self.users.set_role("anna", "admin")
        self.users.set_active("boss", False)
        self.assertIsNone(self.users.authenticate("boss", "secret-pass"))   # switched off
        self.users.delete("boss")
        self.assertEqual(self.users.active_admins(), 1)

    def test_provider_users(self):
        acc = self.users.seen("carl", "oidc", ["staff"], None)
        self.assertEqual((acc.role, acc.source, acc.groups), ("user", "oidc", ["staff"]))
        self.users.set_role("carl", "admin")                         # the panel decides
        self.assertEqual(self.users.seen("carl", "oidc", [], None).role, "admin")
        self.assertEqual(self.users.seen("carl", "oidc", [], False).role, "user")  # the group does
        self.assertIsNone(self.users.authenticate("carl", ""))       # no password here
        with self.assertRaises(UserError):
            self.users.set_password("carl", "secret-pass")

    def test_import_users_json(self):
        old = Path(self.tmp.name) / "users.json"
        old.write_text(json.dumps({"alice": {"role": "admin", "groups": ["a"], "source": "oidc",
                                             "first_login": "2026-01-01T00:00:00+00:00"},
                                   "bob": {"role": "viewer", "source": "proxy"}}))
        self.assertEqual(self.users.import_json(old), 2)
        self.assertEqual(self.users.import_json(old), 0)
        self.assertEqual(self.users.get("alice").role, "admin")
        self.assertEqual(self.users.get("bob").source, "proxy")

    def test_settings(self):
        self.users.create("eve", "eve-password")
        self.assertEqual(self.users.settings_of(self.users.get("eve")),
                         {"nav_mode": None, "edges_3d": True})
        conf = self.users.set_settings("eve", {"nav_mode": "tree", "edges_3d": False})
        self.assertEqual(conf, {"nav_mode": "tree", "edges_3d": False})
        for bad in ({"nav_mode": "sideways"}, {"colour": "red"}, {"edges_3d": 1}):
            with self.assertRaises(UserError):
                self.users.set_settings("eve", bad)
        self.assertEqual(self.users.set_settings("eve", {"nav_mode": None})["nav_mode"], None)
        self.assertFalse(Users(self.users.path).get("eve").config["edges_3d"])

    def test_migrates_an_old_list(self):
        """A users.db of version 1 (before settings) gets the new column."""
        import sqlite3
        from mcp_http import users as mod
        path = Path(self.tmp.name) / "v1.db"
        db = sqlite3.connect(path)
        db.execute(mod.MIGRATIONS[0])
        db.execute("INSERT INTO users (name, created) VALUES ('old', 'x')")
        db.execute("PRAGMA user_version=1")
        db.commit()
        db.close()
        u = Users(path)
        self.assertEqual(u.get("old").config, {})
        u.set_settings("old", {"nav_mode": "list"})

    def test_shared_file(self):
        """The viewer and the MCP server open the same file."""
        self.users.create("dora", "dora-pass")
        other = Users(self.users.path)
        self.assertIsNotNone(other.authenticate("dora", "dora-pass"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ViewerLoginTest(unittest.TestCase):
    """The viewer's login, admin panel and account page over HTTP, with
    VIEWER_AUTH_MODE=local and no IngeTrazo (pages that need none)."""

    def setUp(self):
        from viewer import auth as auth_mod
        from viewer import server
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"VIEWER_AUTH_MODE": "local", "VIEWER_USER": "boss",
                    "VIEWER_PASSWORD": "boss-password",
                    "USERS_DB": str(Path(self.tmp.name) / "users.db")}
        self.saved_env = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.saved_state = auth_mod.STATE_DIR
        auth_mod.STATE_DIR = Path(self.tmp.name)
        self.server = server
        self.saved = getattr(server, "auth", None)
        server.auth = auth_mod.Auth()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.opener = urllib.request.build_opener(NoRedirect)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server.auth = self.saved
        from viewer import auth as auth_mod
        auth_mod.STATE_DIR = self.saved_state
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def req(self, path, data=None, cookie="", accept="text/html"):
        body = urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, body,
                                   {"Accept": accept, **({"Cookie": cookie} if cookie else {})})
        try:
            with self.opener.open(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()

    def login(self, name, pw):
        status, headers, _ = self.req("/login", {"user": name, "password": pw, "next": "/admin"})
        self.assertEqual(status, 303, (name, status))
        return headers["Set-Cookie"].split(";")[0]

    @staticmethod
    def csrf(page):
        return re.search(r'name="csrf" value="([0-9a-f]+)"', page).group(1)

    def test_admin_adds_a_user_who_logs_in(self):
        boss = self.login("boss", "boss-password")
        status, _, page = self.req("/admin", cookie=boss)
        self.assertEqual(status, 200)
        self.assertIn("boss", page)
        token = self.csrf(page)
        # Without the token: refused.
        status, _, _ = self.req("/admin", {"action": "create", "name": "anna",
                                           "password": "anna-pass", "role": "user"}, boss)
        self.assertEqual(status, 403)
        status, _, page = self.req("/admin", {"action": "create", "name": "anna",
                                              "password": "anna-pass", "role": "user",
                                              "csrf": token}, boss)
        self.assertEqual(status, 200, page)
        self.assertIn("Added anna.", page)
        anna = self.login("anna", "anna-pass")
        # anna is no admin.
        self.assertEqual(self.req("/admin", cookie=anna)[0], 403)
        self.assertNotIn('href="/admin"', self.req("/account", cookie=anna)[2])
        # Switched off: her session ends at once.
        self.req("/admin", {"action": "active", "name": "anna", "on": "0", "csrf": token}, boss)
        status, _, page = self.req("/account", cookie=anna)
        self.assertEqual(status, 403)
        self.assertIn("switched off", page)
        self.assertEqual(self.req("/login", {"user": "anna", "password": "anna-pass"})[0], 401)

    def test_the_last_admin_stays(self):
        boss = self.login("boss", "boss-password")
        token = self.csrf(self.req("/admin", cookie=boss)[2])
        status, _, page = self.req("/admin", {"action": "role", "name": "boss",
                                              "role": "user", "csrf": token}, boss)
        self.assertEqual(status, 400)
        self.assertIn("no active admin", page)

    def test_change_own_password(self):
        boss = self.login("boss", "boss-password")
        page = self.req("/account", cookie=boss)[2]
        token = self.csrf(page)
        status, _, page = self.req("/account", {"action": "password", "old": "wrong-one",
                                                "password": "new-password", "csrf": token}, boss)
        self.assertEqual(status, 400)
        status, _, page = self.req("/account", {"action": "password", "old": "boss-password",
                                                "password": "new-password", "csrf": token}, boss)
        self.assertEqual(status, 200, page)
        self.login("boss", "new-password")

    def test_settings_follow_the_user(self):
        boss = self.login("boss", "boss-password")
        page = self.req("/account", cookie=boss)[2]
        self.assertIn('data-nav="list"', page)
        token = self.csrf(page)
        status, _, body = self.req("/account/settings", {"nav_mode": "tree", "csrf": token}, boss,
                                   accept="application/json")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["nav_mode"], "tree")
        self.assertIn('data-nav="tree"', self.req("/account", cookie=boss)[2])
        # Another session (another browser) gets the same.
        self.assertIn('data-nav="tree"', self.req("/account", cookie=self.login("boss", "boss-password"))[2])
        status, _, _ = self.req("/account/settings", {"nav_mode": "x", "csrf": token}, boss)
        self.assertEqual(status, 400)
        status, _, _ = self.req("/account/settings", {"nav_mode": "list"}, boss)
        self.assertEqual(status, 403)                                  # no token
        # The account page's form; "" goes back to the default.
        status, _, page = self.req("/account", {"action": "settings", "nav_mode": "",
                                                "edges_3d": "0", "csrf": token}, boss)
        self.assertEqual(status, 200, page)
        self.assertIn('data-nav="list" data-edges="0"', page)

    def test_basic_auth_for_scripts(self):
        import base64
        r = urllib.request.Request(self.base + "/account", headers={
            "Authorization": "Basic " + base64.b64encode(b"boss:boss-password").decode()})
        with self.opener.open(r, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
        r = urllib.request.Request(self.base + "/account", headers={
            "Authorization": "Basic " + base64.b64encode(b"boss:nope").decode()})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.opener.open(r, timeout=10)
        self.assertEqual(ctx.exception.code, 401)

    def test_restart_keeps_users_but_not_the_env_password(self):
        """VIEWER_PASSWORD only makes the first admin."""
        from viewer import auth as auth_mod
        boss = self.login("boss", "boss-password")
        token = self.csrf(self.req("/account", cookie=boss)[2])
        self.req("/account", {"action": "password", "old": "boss-password",
                              "password": "changed-pass", "csrf": token}, boss)
        self.server.auth = auth_mod.Auth()           # a restart
        self.assertIsNone(self.server.auth.login("boss", "boss-password"))
        self.assertIsNotNone(self.server.auth.login("boss", "changed-pass"))

    def test_no_admin_no_password_refuses_to_start(self):
        from viewer import auth as auth_mod
        os.environ["USERS_DB"] = str(Path(self.tmp.name) / "empty.db")
        os.environ.pop("VIEWER_PASSWORD")
        with self.assertRaises(SystemExit) as ctx:
            auth_mod.Auth()
        self.assertIn("VIEWER_AUTH_MODE=none", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
