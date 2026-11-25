# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests of the folder shares (mcp_http/shares.py) and how the viewer
keeps to them (no container needed):

    python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http import folders, shares  # noqa: E402
from mcp_http.users import UserError, Users  # noqa: E402


class AccessTest(unittest.TestCase):
    def test_most_specific_wins(self):
        s = shares.parse("*:r; projects:rw; projects/secret:none; archive/2020/x:r")
        self.assertEqual(shares.access(s, "other/a.igz"), "r")
        self.assertEqual(shares.access(s, "projects/a.igz"), "rw")
        self.assertEqual(shares.access(s, "projects/secret/b.igz"), "none")
        self.assertEqual(shares.access(s, "projects/secrets/b.igz"), "rw")   # not a prefix
        self.assertEqual(shares.access([], "anything"), "none")

    def test_patterns(self):
        s = shares.parse("*:r; **/private:none; projects/*/plans:rw; **/*.bak.igz:none; "
                         "archive/20??/**:rw")
        self.assertEqual(shares.access(s, "projects/a/house.igz"), "r")
        self.assertEqual(shares.access(s, "projects/a/private/x.igz"), "none")
        self.assertEqual(shares.access(s, "private/x.igz"), "none")             # ** = none too
        self.assertEqual(shares.access(s, "projects/a/plans/p.igz"), "rw")
        self.assertEqual(shares.access(s, "projects/a/b/plans/p.igz"), "r")     # * is one level
        self.assertEqual(shares.access(s, "projects/a/plans/private/p.igz"), "none")  # deeper
        self.assertEqual(shares.access(s, "projects/a/plans/old.bak.igz"), "none")    # a file
        self.assertEqual(shares.access(s, "archive/2024/x/y.igz"), "rw")
        self.assertEqual(shares.access(s, "archive/1999/y.igz"), "r")
        self.assertTrue(shares.is_pattern("**/x") and not shares.is_pattern("a/b"))
        self.assertFalse(shares.is_pattern(shares.ALL))

    def test_ties_between_shares(self):
        # On one level a plain folder beats a pattern, either order.
        for spec in ("projects/*:none; projects/house:r", "projects/house:r; projects/*:none"):
            s = shares.parse(spec)
            self.assertEqual(shares.access(s, "projects/house/a.igz"), "r", spec)
            self.assertEqual(shares.access(s, "projects/other/a.igz"), "none", spec)
        # Of two patterns on one level, less access wins, either order.
        for spec in ("projects/*:rw; */house:none", "*/house:none; projects/*:rw"):
            self.assertEqual(shares.access(shares.parse(spec), "projects/house/a.igz"),
                             "none", spec)
        # decide() names the share that decided.
        s = shares.parse("*:r; **/private:none")
        self.assertEqual(shares.decide(s, "a/private/b.igz"),
                         ("none", {"path": "**/private", "access": "none"}))
        self.assertEqual(shares.decide([], "a.igz"), ("none", None))

    def test_parse_and_clean(self):
        self.assertEqual(shares.parse("/projects/2026/ : rw"),
                         [{"path": "projects/2026", "access": "rw"}])
        self.assertEqual(shares.parse("projects"), [{"path": "projects", "access": "r"}])
        self.assertEqual(shares.parse(r"\**\private\:none"),
                         [{"path": "**/private", "access": "none"}])
        for bad in ("../etc:r", "projects:write", "a/.hidden:r", "a**/b:r", "**/.*:none"):
            with self.assertRaises(ValueError):
                shares.parse(bad)
        self.assertEqual(shares.validate([{"path": "a", "access": "r"},
                                          {"path": "/a/", "access": "rw"}]),
                         [{"path": "a", "access": "rw"}])


class EffectiveTest(unittest.TestCase):
    """Shares on real model folders: one read/write, one read-only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name).resolve() / "data"
        for rel in ("work/a/m.igz", "work/b/n.igz", "lib/o.igz"):
            (data / rel).parent.mkdir(parents=True, exist_ok=True)
            (data / rel).write_bytes(b"x")
        self.saved = folders.DATA_DIR, folders.FOLDERS, os.environ.get("DEFAULT_SHARES")
        folders.DATA_DIR = data
        folders.FOLDERS = folders.parse("work:rw; lib:ro", data_dir=data)
        os.environ.pop("DEFAULT_SHARES", None)
        self.users = Users(Path(self.tmp.name) / "users.db")
        self.users.ensure_admin("boss", "secret-pass")
        self.users.create("anna", "anna-pass")

    def tearDown(self):
        folders.DATA_DIR, folders.FOLDERS, default = self.saved
        if default is None:
            os.environ.pop("DEFAULT_SHARES", None)
        else:
            os.environ["DEFAULT_SHARES"] = default
        self.tmp.cleanup()

    def test_default_is_everything_read(self):
        anna = self.users.get("anna")
        self.assertIsNone(anna.shares)
        self.assertEqual(shares.effective(anna, "work/a/m.igz"), "r")
        self.assertEqual(shares.effective(anna, "lib/o.igz"), "r")
        os.environ["DEFAULT_SHARES"] = "*:none"
        self.assertEqual(shares.effective(anna, "work/a/m.igz"), "none")

    def test_own_shares_and_readonly_folders(self):
        self.users.set_shares("anna", [{"path": "work/a", "access": "rw"},
                                       {"path": "lib", "access": "rw"}])
        anna = self.users.get("anna")
        self.assertTrue(shares.can_write(anna, "work/a/m.igz"))
        self.assertFalse(shares.can_read(anna, "work/b/n.igz"))
        self.assertEqual(shares.effective(anna, "lib/o.igz"), "r")           # ro folder
        self.assertEqual(shares.effective(anna, "elsewhere/x.igz"), "none")  # no folder
        boss = self.users.get("boss")
        self.assertTrue(shares.can_write(boss, "work/b/n.igz"))
        self.assertFalse(shares.can_write(boss, "lib/o.igz"))
        self.users.set_active("anna", False)
        self.assertFalse(shares.can_read(self.users.get("anna"), "work/a/m.igz"))
        with self.assertRaises(UserError):
            self.users.set_shares("anna", [{"path": "../x", "access": "r"}])
        self.users.set_shares("anna", None)
        self.assertIsNone(self.users.get("anna").shares)

    def test_patterns_on_real_folders(self):
        os.environ["DEFAULT_SHARES"] = "*:none; work/*:rw; work/b:r; **/o.igz:r"
        anna = self.users.get("anna")
        self.assertTrue(shares.can_write(anna, "work/a/m.igz"))
        self.assertEqual(shares.effective(anna, "work/b/n.igz"), "r")       # plain beats pattern
        self.assertEqual(shares.effective(anna, "lib/o.igz"), "r")          # one file
        self.users.set_shares("anna", [{"path": "**/a", "access": "none"},
                                       {"path": "*", "access": "rw"}])
        anna = self.users.get("anna")
        self.assertFalse(shares.can_read(anna, "work/a/m.igz"))
        self.assertTrue(shares.can_write(anna, "work/b/n.igz"))
        self.assertEqual(shares.effective(anna, "lib/o.igz"), "r")          # ro folder

    def test_admin_page_explains(self):
        from viewer import admin
        self.users.set_shares("anna", [{"path": "*", "access": "r"},
                                       {"path": "**/b", "access": "none"}])
        anna = self.users.get("anna")
        self.assertEqual(admin.explain(anna, "work/b/n.igz")[0], "none")
        self.assertIn("**/b:none", admin.explain(anna, "work/b/n.igz")[1])
        self.assertIn("capped by the read-only", admin.explain(
            self.users.get("boss"), "lib/o.igz")[1])
        self.users.set_shares("anna", [{"path": "lib", "access": "rw"}])
        got, why = admin.explain(self.users.get("anna"), "lib/o.igz")
        self.assertEqual(got, "r")
        self.assertIn("capped by the read-only model folder", why)
        self.assertEqual(admin.explain(self.users.get("anna"), "work/a/m.igz"),
                         ("none", "no share covers it"))

    def test_tree_for_the_admin_page(self):
        self.assertEqual(shares.tree(), ["work", "work/a", "work/b", "lib"])
        (folders.DATA_DIR / "work/a/x").mkdir()
        (folders.DATA_DIR / "work/b/y").mkdir()
        # Depth first: each folder right above the folders in it.
        self.assertEqual(shares.tree(), ["work", "work/a", "work/a/x", "work/b", "work/b/y", "lib"])

    def test_viewer_shows_only_what_is_shared(self):
        from viewer import server
        saved = getattr(server, "auth", None)
        server.auth = SimpleNamespace(mode="local", users=self.users)
        try:
            self.users.set_shares("anna", [{"path": "work/a", "access": "r"}])
            may = server._reader(SimpleNamespace(name="anna"))
            self.assertTrue(may("work/a/m.igz"))
            self.assertFalse(may("work/b/n.igz"))
            self.assertFalse(may("lib/o.igz"))
            self.assertTrue(server._reader(SimpleNamespace(name="boss"))("lib/o.igz"))
            self.assertFalse(server._reader(SimpleNamespace(name="nobody"))("work/a/m.igz"))
        finally:
            server.auth = saved


if __name__ == "__main__":
    unittest.main()
