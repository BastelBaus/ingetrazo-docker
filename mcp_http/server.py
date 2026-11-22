# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP over Streamable HTTP for a headless IngeTrazo.

``POST /mcp`` takes JSON-RPC and answers with JSON (the stateless form of
the Streamable HTTP transport); ``GET /mcp`` offers no server-initiated
stream (405). ``GET /healthz`` is the container's health probe.

Every request goes to IngeTrazo's own MCP handler (scripts/ingetrazo_mcp.py),
so the tools and their descriptions are the app's. The container adds:
``list_documents``, the confinement of file paths to the data folder, and
hides ``shutdown`` (the container's lifetime is the orchestrator's).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .appbridge import (DATA_DIR, App, env_flag, initial_model, list_models,
                        log, resolve_data_path)
from .auth import Auth

#: Protocol revisions this server can speak; the tool and content shapes it
#: uses are the same in all of them.
KNOWN_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
HIDDEN_TOOLS = {"shutdown"}
#: Tools whose ``path`` argument names a file: confined to the data folder.
PATH_TOOLS = {"open_document": True, "save_document": False,
              "export_pdf": False, "export_view": False,
              "export_sheets": False}

LIST_DOCUMENTS = {
    "name": "list_documents",
    "description": (
        "List the IngeTrazo documents (.igz) in the container's data folder, "
        "with size, modification time and which one is open. Paths for "
        "open_document, save_document and the export tools are relative to "
        "this folder (absolute paths must lie inside it)."),
    "inputSchema": {"type": "object", "properties": {}},
}

app: App
auth: Auth
MAX_BUSY = float(os.environ.get("HEALTH_MAX_BUSY_SECONDS", 900))


def _text(result: object, is_error: bool = False) -> dict:
    text = result if isinstance(result, str) else json.dumps(
        result, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _list_documents() -> dict:
    reply = app.call("document_info")
    open_path = (reply.get("result") or {}).get("path") if reply.get("ok") else None
    docs = []
    for p in list_models():
        st = p.stat()
        docs.append({
            "path": str(p.relative_to(DATA_DIR)).replace(os.sep, "/"),
            "size_bytes": st.st_size,
            "modified": datetime.fromtimestamp(
                st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "open": open_path is not None and str(p) == str(open_path),
        })
    return _text({"data_folder": str(DATA_DIR), "documents": docs})


def _handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = dict(params.get("arguments") or {})
        if name in HIDDEN_TOOLS:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": _text(f"{name} is not available in the container",
                                    is_error=True)}
        if name == "list_documents":
            return {"jsonrpc": "2.0", "id": msg_id, "result": _list_documents()}
        if name in PATH_TOOLS and args.get("path"):
            try:
                args["path"] = str(resolve_data_path(
                    str(args["path"]), must_exist=PATH_TOOLS[name]))
            except (PermissionError, FileNotFoundError) as exc:
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": _text(str(exc), is_error=True)}
            msg = {**msg, "params": {**params, "arguments": args}}
    reply = app.handle(msg)
    if reply is None:
        return None
    if method == "initialize":
        wanted = (msg.get("params") or {}).get("protocolVersion")
        result = reply.setdefault("result", {})
        if wanted in KNOWN_VERSIONS:
            result["protocolVersion"] = wanted
        result["serverInfo"] = {"name": "ingetrazo-docker", "version": "0.1.0"}
    elif method == "tools/list":
        tools = [t for t in reply["result"]["tools"]
                 if t.get("name") not in HIDDEN_TOOLS]
        reply["result"]["tools"] = tools + [LIST_DOCUMENTS]
    return reply


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ingetrazo-docker"

    # ---- plumbing -------------------------------------------------------------
    def log_message(self, fmt, *args):          # one quiet line per request
        if self.path != "/healthz":
            sys.stderr.write("[http] %s %s\n" % (self.address_string(),
                                                 fmt % args))

    def _send(self, status: int, body: bytes = b"",
              ctype: str = "application/json", headers: dict | None = None):
        self.send_response(status)
        if body or status not in (202, 204):
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: int, obj, headers: dict | None = None):
        self._send(status, json.dumps(obj).encode(), headers=headers)

    def base_url(self) -> str:
        public = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
        if public:
            return public
        host = self.headers.get("Host", f"localhost:{self.server.server_port}")
        return f"http://{host}"

    def _origin_ok(self) -> bool:
        """DNS-rebinding guard of the MCP spec: a browser page from a
        foreign origin must not reach an unauthenticated server."""
        origin = self.headers.get("Origin")
        if not origin or auth.mode != "none":
            return True
        allowed = {o.strip().rstrip("/") for o in
                   os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()}
        if origin.rstrip("/") in allowed:
            return True
        return (urlsplit(origin).hostname or "") in ("localhost", "127.0.0.1", "::1")

    def _authorised(self) -> bool:
        if auth.check(self.headers):
            return True
        self._json(401, {"error": "unauthorized"}, headers={
            "WWW-Authenticate": auth.challenge(self.base_url())})
        return False

    # ---- routes -------------------------------------------------------------
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            status, body = app.probe(MAX_BUSY)
            return self._json(status, body)
        if auth.oauth is not None and auth.oauth.route(self, "GET"):
            return None
        if path == "/mcp":
            if not self._authorised():
                return None
            return self._send(405, headers={"Allow": "POST"})
        if path == "/":
            return self._send(200, b"IngeTrazo MCP server: POST /mcp\n",
                              "text/plain; charset=utf-8")
        return self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if urlsplit(self.path).path == "/mcp":
            return self._send(405, headers={"Allow": "POST"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if auth.oauth is not None and auth.oauth.route(self, "POST"):
            return None
        if path != "/mcp":
            return self._json(404, {"error": "not found"})
        if not self._origin_ok():
            return self._json(403, {"error": "origin not allowed"})
        if not self._authorised():
            return None
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": "parse error"}})
        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        if not messages or not all(isinstance(m, dict) for m in messages):
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "invalid request"}})
        headers = {}
        replies = []
        for m in messages:
            if m.get("method") == "initialize":
                headers["Mcp-Session-Id"] = secrets.token_hex(16)
            if "method" not in m:           # a client's response: nothing to do
                continue
            try:
                r = _handle(m)
            except Exception as exc:        # never drop the connection silently
                log(f"error handling {m.get('method')}: {exc!r}")
                r = None if m.get("id") is None else {
                    "jsonrpc": "2.0", "id": m.get("id"),
                    "error": {"code": -32603, "message": str(exc)}}
            if r is not None:
                replies.append(r)
        if not replies:
            return self._send(202, headers=headers)
        return self._json(200, replies if batch else replies[0], headers)


def main() -> None:
    global app, auth
    auth = Auth()
    app = App()
    port = int(os.environ.get("MCP_PORT", 8765))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    # Listen first, so /healthz says "starting" while IngeTrazo opens.
    import threading
    threading.Thread(target=server.serve_forever, name="http",
                     daemon=True).start()
    log(f"MCP endpoint on :{port}/mcp (auth: {auth.mode}); data folder {DATA_DIR}")
    app.start(initial_model(), allow_save=env_flag("INGETRAZO_ALLOW_SAVE", True),
              save_format=os.environ.get("INGETRAZO_SAVE_FORMAT") or None)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
