# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which build is running: this repo's version (``INGETRAZO_DOCKER_VERSION``,
baked in from the build arg ``INGETRAZO_DOCKER_REF``) and the IngeTrazo
inside (``core/version.py`` and the commit the Dockerfile wrote to
``BUILD_REF``). Read once; shown in the viewer's footer and at ``/version``.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{12,40}$")


def _short(ref: str) -> str:
    """A commit hash as its first 7 characters; tags and names as they are."""
    return ref[:7] if _SHA.match(ref) else ref


def _app_version(app: Path) -> str:
    try:
        text = (app / "core" / "version.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", text, re.M)
    return m.group(1) if m else ""


def _app_commit(app: Path) -> str:
    try:
        line = (app / "BUILD_REF").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # "IngeTrazo <sha> (<date>)"
    parts = line.split()
    return parts[1] if len(parts) > 1 else ""


@functools.lru_cache(maxsize=1)
def info() -> dict:
    app = Path(os.environ.get("INGETRAZO_APP", "/opt/ingetrazo"))
    docker = os.environ.get("INGETRAZO_DOCKER_VERSION", "").strip() or "dev"
    return {
        "ingetrazo_docker": docker,
        "ingetrazo": _app_version(app),
        "ingetrazo_commit": _app_commit(app),
    }


def text() -> str:
    """One line, e.g. ``ingetrazo-docker 59269df · IngeTrazo 0.6.3 (989c0d2)``."""
    i = info()
    out = f"ingetrazo-docker {_short(i['ingetrazo_docker'])}"
    app = i["ingetrazo"]
    commit = _short(i["ingetrazo_commit"])
    if app or commit:
        out += " · IngeTrazo " + (f"{app} ({commit})" if app and commit else app or commit)
    return out
