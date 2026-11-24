# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests of the model folders (no container needed):

    python -m unittest discover -s tests -p "test_*.py"
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http import folders  # noqa: E402


def touch(root: Path, *names: str) -> None:
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


class ParseTest(unittest.TestCase):
    def test_default_is_the_data_folder(self):
        [f] = folders.parse("", data_dir=Path("/data"))
        self.assertEqual((f.name, f.writable, f.depth), ("data", True, None))

    def test_entries(self):
        got = folders.parse("projects:rw; archive:ro:depth=1:exclude=old*,*.bak\n"
                            "plans", default_exclude=["tmp"], data_dir=Path("/d"))
        self.assertEqual([f.name for f in got], ["projects", "archive", "plans"])
        self.assertEqual([f.mode for f in got], ["rw", "ro", "rw"])
        self.assertEqual(got[1].depth, 1)
        self.assertEqual(got[1].exclude, ["tmp", "old*", "*.bak"])
        self.assertEqual(got[0].root.name, "projects")

    def test_mistakes_are_refused(self):
        for spec in ("../etc", "a:rx", "a; a", "a:depth=x", ".hidden"):
            with self.assertRaises(ValueError, msg=spec):
                folders.parse(spec, data_dir=Path("/d"))


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        touch(self.root, "a.igz", "b.IGZ", "notes.txt", "sub/c.igz", "sub/deep/d.igz",
              "archive-2023/e.igz", "x.bak.igz", ".hidden/f.igz", "sub/.g.igz",
              "old/h.igz", "keep/old/i.igz")

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, **kw):
        f = folders.Folder("t", self.root, **kw)
        return [p.relative_to(self.root).as_posix() for p in f.models()]

    def test_everything_but_hidden(self):
        self.assertEqual(self.names(), [
            "a.igz", "archive-2023/e.igz", "b.IGZ", "keep/old/i.igz", "old/h.igz",
            "sub/c.igz", "sub/deep/d.igz", "x.bak.igz"])

    def test_depth(self):
        self.assertEqual(self.names(depth=0), ["a.igz", "b.IGZ", "x.bak.igz"])
        self.assertNotIn("sub/deep/d.igz", self.names(depth=1))
        self.assertIn("sub/c.igz", self.names(depth=1))

    def test_exclude_by_name_anywhere(self):
        got = self.names(exclude=["archive*", "*.bak.igz", "old"])
        self.assertEqual(got, ["a.igz", "b.IGZ", "sub/c.igz", "sub/deep/d.igz"])

    def test_exclude_anchored(self):
        got = self.names(exclude=["old/**", "sub/*/**"])
        self.assertIn("keep/old/i.igz", got)
        self.assertNotIn("old/h.igz", got)
        self.assertNotIn("sub/deep/d.igz", got)
        self.assertIn("sub/c.igz", got)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name).resolve()
        touch(data, "rw/m.igz", "ro/n.igz", "loose.igz")
        self.saved = folders.DATA_DIR, folders.FOLDERS
        folders.DATA_DIR = data
        folders.FOLDERS = folders.parse("rw:rw; ro:ro", data_dir=data)
        self.data = data

    def tearDown(self):
        folders.DATA_DIR, folders.FOLDERS = self.saved
        self.tmp.cleanup()

    def test_read_anywhere_in_a_folder(self):
        self.assertEqual(folders.resolve("ro/n.igz", must_exist=True),
                         self.data / "ro" / "n.igz")
        self.assertEqual(folders.resolve(str(self.data / "rw" / "m.igz")),
                         self.data / "rw" / "m.igz")

    def test_write_only_into_rw(self):
        self.assertEqual(folders.resolve("rw/new.igz", write=True),
                         self.data / "rw" / "new.igz")
        with self.assertRaisesRegex(PermissionError, "read-only"):
            folders.resolve("ro/n.igz", write=True)
        with self.assertRaisesRegex(PermissionError, "read-only"):
            folders.resolve("rw/../ro/out.pdf", write=True)

    def test_outside(self):
        with self.assertRaisesRegex(PermissionError, "outside"):
            folders.resolve("../x.igz")
        with self.assertRaisesRegex(PermissionError, "not in a model folder"):
            folders.resolve("loose.igz")
        with self.assertRaises(FileNotFoundError):
            folders.resolve("rw/missing.igz", must_exist=True)

    def test_listing(self):
        got = [(f.name, folders.display_name(p)) for f, p in folders.list_models()]
        self.assertEqual(got, [("rw", "rw/m.igz"), ("ro", "ro/n.igz")])


if __name__ == "__main__":
    unittest.main()
