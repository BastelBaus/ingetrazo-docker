# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke test of several model folders:
smoke_folders.py <mcp-url> <token> <viewer-url>

The containers run with ``MODEL_FOLDERS="work:rw; library:ro:exclude=*.bak.igz"``;
library holds arco.igz (open at start) and arco.bak.igz, work is empty.
The viewer runs with ``VIEWER_AUTH_MODE=none`` and ``VIEWER_PRERENDER=none``."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

import smoke_mcp as m

VIEWER = sys.argv[3].rstrip("/")


def get(path):
    with urllib.request.urlopen(VIEWER + path, timeout=60) as r:
        return r.read().decode()


def wait_for(pred, what, timeout=300):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            got = pred()
            if got:
                return got
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


def main():
    print("== model folders (MCP)")
    m.check("healthz", m.wait_healthy)

    def listed():
        got = json.loads(m.text(m.tool("list_documents")))
        assert [(f["name"], f["mode"]) for f in got["folders"]] == [
            ("work", "rw"), ("library", "ro")], got["folders"]
        paths = [d["path"] for d in m.documents_of(got)]
        assert paths == ["library/arco.igz"], paths        # arco.bak.igz excluded
    m.check("list_documents groups by folder, excludes *.bak.igz", listed)

    def read_only():
        for name, args in (("save_document", {}),
                           ("save_document", {"path": "library/copy.igz"}),
                           ("export_pdf", {"path": "library/sheets.pdf"})):
            r = m.tool(name, args, expect_error=True)
            assert "read-only" in m.text(r), (name, m.text(r))
        r = m.tool("open_document", {"path": "elsewhere.igz"}, expect_error=True)
        assert "not in a model folder" in m.text(r), m.text(r)
    m.check("no saving or exporting into the read-only folder", read_only)

    def into_rw():
        m.tool("save_document", {"path": "work/copy.igz"})
        m.tool("export_pdf", {"path": "work/sheets.pdf", "sheets": [1]})
        got = json.loads(m.text(m.tool("list_documents")))
        work = next(f for f in got["folders"] if f["name"] == "work")
        assert [d["path"] for d in work["documents"]] == ["work/copy.igz"], work
        m.tool("open_document", {"path": "library/arco.bak.igz"})   # excluded, still opens
    m.check("save and export into the read/write folder", into_rw)

    print("== model folders (viewer, render on demand)")

    def overview():
        page = wait_for(lambda: (lambda p: p if 'class="name">copy.igz<' in p else None)(
            get("/")), "the saved copy in the overview")
        for name in ("work", "library"):
            assert f"<h2>{name}" in page, name
        assert 'read-only</span>' in page
        return page
    m.check("overview grouped by folder", overview)

    def on_demand():
        page = get("/")
        slug = re.search(r'href="/m/([^"]+)"><div class="thumb">(?:(?!</a>).)*'
                         r'class="name">arco\.igz<', page, re.S).group(1)
        first = wait_for(lambda: (lambda p: p if "data-wait=\"list\"" not in p else None)(
            get(f"/m/{slug}?sheet=1")), "arco.igz to be listed")
        assert re.search(r'data-wait="[^"]*sheet:1', first) or '<img src="/files/' in first
        wait_for(lambda: '<img src="/files/' in get(f"/m/{slug}?sheet=1"),
                 "sheet 1 to render")
        pdf = wait_for(lambda: (lambda r: r if r.geturl().endswith(".pdf") else None)(
            urllib.request.urlopen(VIEWER + f"/m/{slug}?pdf=1", timeout=60)),
            "the PDF")
        assert pdf.read(4) == b"%PDF"
    m.check("a sheet and the PDF rendered when asked for", on_demand)
    print("== model folders: ok")


if __name__ == "__main__":
    main()
