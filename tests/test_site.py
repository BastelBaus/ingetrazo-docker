# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests of the viewer's title an admin can change (viewer/site.py)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viewer import site  # noqa: E402


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"INGETRAZO_STATE": self.tmp.name})
        self.env.start()
        site.reset()

    def tearDown(self):
        self.env.stop()
        site.reset()
        self.tmp.cleanup()

    def test_default_then_custom_then_default(self):
        self.assertEqual(site.title(), site.DEFAULT_TITLE)
        self.assertEqual(site.set_title("  Haus   Stauffenberg "), "Haus Stauffenberg")
        site.reset()                                   # read back from the file
        self.assertEqual(site.title(), "Haus Stauffenberg")
        data = json.loads((Path(self.tmp.name) / "site.json").read_text())
        self.assertEqual(data, {"title": "Haus Stauffenberg"})
        self.assertEqual(site.set_title(""), site.DEFAULT_TITLE)
        self.assertNotIn("title", json.loads((Path(self.tmp.name) / "site.json").read_text()))

    def test_long_title_is_cut(self):
        self.assertEqual(len(site.set_title("x" * 200)), site.MAX_TITLE)

    def test_broken_file_falls_back(self):
        (Path(self.tmp.name) / "site.json").write_text("{nope")
        self.assertEqual(site.title(), site.DEFAULT_TITLE)


if __name__ == "__main__":
    unittest.main()
