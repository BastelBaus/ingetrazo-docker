# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests of the viewer's version line (viewer/version.py)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viewer import version  # noqa: E402

SHA = "989c0d2e7f522a646758f052f718b1970deaab05"


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Path(self.tmp.name)
        version.info.cache_clear()

    def tearDown(self):
        version.info.cache_clear()
        self.tmp.cleanup()

    def _env(self, **extra):
        env = {"INGETRAZO_APP": str(self.app)}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=False)

    def test_full_build(self):
        (self.app / "core").mkdir()
        (self.app / "core" / "version.py").write_text('__version__ = "0.6.3"\n')
        (self.app / "BUILD_REF").write_text(f"IngeTrazo {SHA} (Tue Nov 24 2026)\n")
        with self._env(INGETRAZO_DOCKER_VERSION="59269dfb2d0d375cf4df4dc8610a3a00300c4081"):
            self.assertEqual(version.info(), {
                "ingetrazo_docker": "59269dfb2d0d375cf4df4dc8610a3a00300c4081",
                "ingetrazo": "0.6.3", "ingetrazo_commit": SHA})
            self.assertEqual(version.text(),
                             "ingetrazo-docker 59269df · IngeTrazo 0.6.3 (989c0d2)")

    def test_tag_is_kept(self):
        with self._env(INGETRAZO_DOCKER_VERSION="v1.2.0"):
            self.assertEqual(version.text(), "ingetrazo-docker v1.2.0")

    def test_nothing_known(self):
        with self._env(INGETRAZO_DOCKER_VERSION=""):
            self.assertEqual(version.text(), "ingetrazo-docker dev")


class FooterTest(unittest.TestCase):
    def test_page_has_the_footer(self):
        from types import SimpleNamespace

        from viewer import server
        saved = getattr(server, "auth", None)
        server.auth = SimpleNamespace(mode="none")
        version.info.cache_clear()
        try:
            with mock.patch.dict(os.environ, {"INGETRAZO_DOCKER_VERSION": "v9.9",
                                              "INGETRAZO_APP": "/nonexistent"}):
                page = server._page("t", "<h1>t</h1>", "body").decode()
        finally:
            server.auth = saved
            version.info.cache_clear()
        self.assertIn('<footer class="ver">ingetrazo-docker v9.9</footer>', page)


if __name__ == "__main__":
    unittest.main()
