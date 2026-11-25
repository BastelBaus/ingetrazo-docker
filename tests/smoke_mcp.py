# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke test of the MCP service: smoke_mcp.py <base-url> <token>"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request

BASE, TOKEN = sys.argv[1].rstrip("/"), sys.argv[2]
PNG = b"\x89PNG\r\n\x1a\n"


def http(path, body=None, token=TOKEN, timeout=300):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data, headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), None


_id = 0


def rpc(method, params=None):
    global _id
    _id += 1
    status, headers, reply = http("/mcp", {"jsonrpc": "2.0", "id": _id,
                                           "method": method, "params": params or {}})
    assert status == 200, (method, status)
    assert "error" not in reply, reply
    return headers, reply["result"]


def document_id():
    """The live document's id, which every tool but list_documents needs."""
    _, result = rpc("tools/call", {"name": "list_documents", "arguments": {}})
    live = json.loads(text(result))["open_documents"]
    assert len(live) == 1, live
    return live[0]["document_id"]


def tool(name, args=None, expect_error=False, with_id=True):
    args = dict(args or {})
    if with_id and name != "list_documents":
        args.setdefault("document_id", document_id())
    _, result = rpc("tools/call", {"name": name, "arguments": args})
    assert bool(result.get("isError")) == expect_error, (name, result)
    return result


def documents_of(listed):
    """Every document of a list_documents reply, folder by folder."""
    return [d for f in listed["folders"] for d in f["documents"]]


def text(result):
    return "".join(c.get("text", "") for c in result["content"] if c["type"] == "text")


def image(result):
    imgs = [c for c in result["content"] if c["type"] == "image"]
    assert imgs, result
    assert base64.b64decode(imgs[0]["data"]).startswith(PNG)


def check(label, fn):
    t0 = time.monotonic()
    fn()
    print(f"  ok  {label} ({time.monotonic() - t0:.1f}s)")


def wait_healthy():
    end = time.monotonic() + 240
    while time.monotonic() < end:
        try:
            status, _, body = http("/healthz", token=None, timeout=15)
            if status == 200 and body and body.get("status") == "ok":
                return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit("MCP service did not become healthy")


def main():
    print("== MCP service")
    check("healthz", wait_healthy)

    def unauthorised():
        status, headers, _ = http("/mcp", {"jsonrpc": "2.0", "id": 0, "method": "ping"},
                                  token=None)
        assert status == 401 and "Bearer" in headers.get("WWW-Authenticate", ""), status
        status, _, _ = http("/mcp", {"jsonrpc": "2.0", "id": 0, "method": "ping"},
                            token="wrong-token-0000000000")
        assert status == 401, status
    check("401 without / with a wrong token", unauthorised)

    def initialize():
        headers, result = rpc("initialize", {"protocolVersion": "2025-06-18",
                                             "capabilities": {},
                                             "clientInfo": {"name": "smoke", "version": "0"}})
        assert result["protocolVersion"] == "2025-06-18", result
        assert headers.get("Mcp-Session-Id"), headers
        status, _, _ = http("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert status == 202, status
    check("initialize + initialized", initialize)

    def tools():
        _, result = rpc("tools/list")
        names = {t["name"] for t in result["tools"]}
        for n in ("run_python", "query_model", "screenshot", "open_document",
                  "save_document", "render_sheet", "list_documents"):
            assert n in names, n
        assert "shutdown" not in names
    check("tools/list", tools)

    def documents():
        listed = json.loads(text(tool("list_documents")))
        assert [(f["name"], f["mode"]) for f in listed["folders"]] == [("data", "rw")], listed
        assert any(d["path"] == "arco.igz" and d["open"] for d in documents_of(listed)), listed
        assert listed["open_documents"][0]["path"] == "/data/arco.igz", listed
    check("list_documents", documents)

    def needs_id():
        r = tool("query_model", expect_error=True, with_id=False)
        assert "document_id" in text(r), text(r)
    check("a tool without document_id is refused", needs_id)

    def info():
        d = json.loads(text(tool("document_info")))
        assert d["path"] == "/data/arco.igz" and d["sheets"], d
    check("document_info", info)
    def sheets():
        listed = json.loads(text(tool("list_sheets")))["sheets"]
        assert listed and listed[0]["index"] == 1, listed
        one = json.loads(text(tool("get_sheet", {"sheet": 1})))
        assert one["name"] == listed[0]["name"], one
        json.loads(text(tool("list_scenes")))
    check("list_sheets, get_sheet 1, list_scenes", sheets)
    check("render_sheet (image)", lambda: image(tool("render_sheet", {"sheet": 1, "dpi": 40})))
    check("screenshot (image)", lambda: image(tool("screenshot", {"width": 320, "height": 200})))

    def build_and_save():
        r = tool("run_python", {"code": "box((50, 50, 0), (1, 1, 1), name='Smoke')"})
        assert "changed: True" in text(r), text(r)
        tool("save_document", {"path": "smoke-saved.igz"})
        docs = documents_of(json.loads(text(tool("list_documents"))))
        assert any(d["path"] == "smoke-saved.igz" and d["open"] for d in docs), docs
    check("run_python + save_document", build_and_save)

    def reopen():
        tool("open_document", {"path": "arco.igz"})
        tool("open_document", {"path": "smoke-saved.igz"})
        assert "Smoke" in text(tool("query_model"))
    check("open_document back and forth", reopen)

    def confined():
        r = tool("open_document", {"path": "/etc/passwd"}, expect_error=True)
        assert "outside the data folder" in text(r), text(r)
        r = tool("save_document", {"path": "../escape.igz"}, expect_error=True)
        assert "outside the data folder" in text(r), text(r)
    check("paths confined to /data", confined)
    print("== MCP service: ok")


if __name__ == "__main__":
    main()
