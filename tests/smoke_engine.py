# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end checks of two running engines (tests/smoke.sh starts them):

    python tests/smoke_engine.py <mcp URL> <mcp token> <render URL> <render token>

The mcp engine has /data read/write with arco.igz open; the render engine
has /data read-only and /cache for its files.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

MCP, MCP_TOKEN = sys.argv[1].rstrip("/"), sys.argv[2]
RENDER, RENDER_TOKEN = sys.argv[3].rstrip("/"), sys.argv[4]
TOKENS = {MCP: MCP_TOKEN, RENDER: RENDER_TOKEN}


def post(base, path, body, token=None, timeout=600):
    token = token or TOKENS[base]
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def call(base, tool, args=None, expect=200):
    status, reply = post(base, "/call", {"tool": tool, "args": args or {}})
    assert status == expect, (tool, status, reply)
    if expect == 200:
        assert reply.get("ok"), (tool, reply)
        return reply.get("result") or {}
    return reply


def check(name, fn):
    t0 = time.monotonic()
    fn()
    print(f"  ok  {name} ({time.monotonic() - t0:.1f}s)")


def wait_healthy(base):
    t0 = time.monotonic()
    while time.monotonic() - t0 < 300:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=10) as r:
                if json.loads(r.read()).get("status") in ("ok", "busy"):
                    return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise SystemExit(f"{base}/healthz not ok after 300 s")


def main():
    print("== engines")
    check("both healthy", lambda: (wait_healthy(MCP), wait_healthy(RENDER)))

    def wrong_token():
        for base in (MCP, RENDER):
            assert post(base, "/call", {"tool": "list_documents"}, token="wrong-" + TOKENS[base])[0] == 401
        assert post(MCP, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    token="wrong-" + MCP_TOKEN)[0] == 401
    check("401 with a wrong token", wrong_token)

    print("== mcp mode")

    def rpc():
        status, reply = post(MCP, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                           "params": {"protocolVersion": "2025-06-18",
                                                      "capabilities": {},
                                                      "clientInfo": {"name": "smoke", "version": "0"}}})
        assert status == 200 and "result" in reply, reply
        status, reply = post(MCP, "/rpc", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in reply["result"]["tools"]}
        assert {"query_model", "run_python", "list_sheets"} <= names, names
        status, reply = post(MCP, "/rpc", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                           "params": {"name": "shutdown", "arguments": {}}})
        assert reply["result"]["isError"], reply
    check("/rpc: initialize, tools/list, shutdown refused", rpc)

    def documents():
        docs = call(MCP, "list_documents").get("documents") or []
        assert docs and docs[0]["path"].endswith("arco.igz"), docs
        call(MCP, "document_info")
    check("arco.igz open (INGETRAZO_MODEL)", documents)

    def save():
        call(MCP, "save_document", {"path": "/data/engine-saved.igz"})
        call(MCP, "export_pdf", {"path": "/cache/x.pdf"}, expect=403)
        call(MCP, "save_document", {"path": "/etc/x.igz"}, expect=403)
    check("saves inside /data only", save)

    print("== render mode")

    def render():
        call(RENDER, "open_document", {"path": "/data/arco.igz", "discard_changes": True})
        sheets = call(RENDER, "list_sheets")
        assert sheets.get("sheets"), sheets
        result = call(RENDER, "export_pdf", {"path": "/cache/smoke/sheet", "format": "png",
                                             "dpi": 30, "sheets": [1]})
        assert result.get("files"), result
        call(RENDER, "export_pdf", {"path": "/cache/smoke/all.pdf", "format": "pdf"})
    check("open, list_sheets, sheet PNG and PDF into /cache", render)

    def refused():
        call(RENDER, "save_document", {}, expect=403)
        call(RENDER, "export_pdf", {"path": "/data/x.pdf"}, expect=403)
        call(RENDER, "open_document", {"path": "/etc/passwd"}, expect=403)
        assert post(RENDER, "/rpc", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 404
    check("never saves, writes only below /cache, no /rpc", refused)


if __name__ == "__main__":
    main()
