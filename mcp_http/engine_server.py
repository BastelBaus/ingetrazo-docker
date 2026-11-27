# SPDX-License-Identifier: AGPL-3.0-or-later
"""An engine: one headless IngeTrazo behind a small internal HTTP API, for
the services of the viewer image (which has no IngeTrazo).

``ENGINE_MODE`` says whom it serves:

``render`` (default)  the viewer: only the tools it renders with, files
  written only below ``INGETRAZO_CACHE``, never ``--allow-save``.
``mcp``  the MCP gateway (``mcp`` in the viewer image, which holds the users,
  OAuth and folder rights): every tool but ``shutdown``, files read and
  written only below the data folder; ``INGETRAZO_MODEL``,
  ``INGETRAZO_ALLOW_SAVE`` and ``INGETRAZO_SAVE_FORMAT`` as for ``mcp``.

``POST /call``  ``{"tool": "...", "args": {...}, "timeout": seconds?}``
  → the bridge's own reply, ``{"ok": true, "result": ...}`` or
  ``{"ok": false, "error": "..."}``.
``POST /rpc``  (``mcp`` mode) one MCP JSON-RPC message through IngeTrazo's
  own handler → its reply (``null`` for a notification).
``GET /healthz``  the same probe as the MCP service (no token).

``/call`` and ``/rpc`` need ``Authorization: Bearer <ENGINE_TOKEN>``.

This is not an MCP endpoint and not for people or AI clients: it has one
shared secret (``ENGINE_TOKEN``), no user list and no folder rights, and
belongs on an internal network that only the viewer image's services reach. Without
``ENGINE_TOKEN`` the engine makes a random one in ``ENGINE_TOKEN_FILE`` (a
volume it shares with the viewer). Whoever holds
the token may run Python in the engine (the viewer reads scene folders and
cameras that way), so the token is as good as a shell in this container.

Guard rails all the same (``check``): in ``render`` mode only the tools the
viewer uses (``ENGINE_TOOLS``), documents opened only from the model
folders, files written only below ``INGETRAZO_CACHE`` (the volume the engine
shares with the viewer), and never ``--allow-save``. In ``mcp`` mode the
gateway checks each caller's folders; the engine keeps every path inside
the data folder.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .appbridge import DATA_DIR, App, env_flag, initial_model, log

#: What the viewer calls; everything else is refused.
ENGINE_TOOLS = frozenset({
    "list_documents", "open_document", "document_info", "list_sheets",
    "list_scenes", "list_layers", "get_scene_info", "run_python",
    "export_pdf", "export_view", "export_file", "screenshot", "render_sheet",
})
#: Tools whose ``path`` is written: below the cache (render) or the data
#: folder (mcp).
WRITE_TOOLS = frozenset({"export_pdf", "export_view", "export_file",
                         "screenshot", "render_sheet", "save_document"})
#: Tools whose ``path`` is read, always below the data folder.
READ_TOOLS = frozenset({"open_document", "import_file"})
#: Never, in either mode: the container's lifetime is Docker's.
HIDDEN_TOOLS = frozenset({"shutdown"})
MODES = ("render", "mcp")
MIN_TOKEN = 16
MAX_BODY = 1_000_000


def _cache_dir() -> Path:
    return Path(os.environ.get("INGETRAZO_CACHE", "/cache"))


def engine_token(create: bool = False) -> str:
    """The shared secret: ``ENGINE_TOKEN``, or else the file
    ``ENGINE_TOKEN_FILE`` names (a volume both containers mount). With
    ``create`` (the engine) a missing file is made with a random token, so
    a compose stack needs no secret in its .env. "" when there is none yet."""
    token = os.environ.get("ENGINE_TOKEN", "").strip()
    if token:
        return token
    name = os.environ.get("ENGINE_TOKEN_FILE", "").strip()
    if not name:
        return ""
    path = Path(name)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if not create:
            return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secrets.token_hex(32))
    try:
        os.link(tmp, path)          # first one wins if two engines race
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text(encoding="utf-8").strip()


def _token() -> str:
    token = engine_token(create=True)
    if len(token) < MIN_TOKEN:
        raise SystemExit(
            f"ENGINE_TOKEN (or the file ENGINE_TOKEN_FILE names) must hold at "
            f"least {MIN_TOKEN} characters; the viewer uses the same one. "
            "E.g.: openssl rand -hex 32")
    return token


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _mode() -> str:
    mode = os.environ.get("ENGINE_MODE", "").strip().lower() or "render"
    if mode not in MODES:
        raise SystemExit(f"ENGINE_MODE must be one of {', '.join(MODES)}, not {mode!r}")
    return mode


def check(tool: str, args: dict, data_dir: Path, cache_dir: Path,
          mode: str = "render") -> str | None:
    """Why a call is refused, or None. Pure, for the unit tests."""
    if tool in HIDDEN_TOOLS or (mode == "render" and tool not in ENGINE_TOOLS):
        return f"tool {tool!r} is not available in the {mode} engine"
    if not isinstance(args, dict):
        return "args must be an object"
    raw = args.get("path")
    if tool in READ_TOOLS:
        if not isinstance(raw, str) or not _inside(Path(raw), data_dir):
            return f"{tool}: path must lie below {data_dir}"
    elif tool in WRITE_TOOLS:
        root = cache_dir if mode == "render" else data_dir
        if tool == "save_document" and mode == "mcp" and raw is None:
            return None                          # saved to its own path
        if not isinstance(raw, str) or not _inside(Path(raw), root):
            return f"{tool}: path must lie below {root}"
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "ingetrazo-engine"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):     # quiet: the viewer logs its renders
        return None

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlsplit(self.path).path == "/healthz":
            status, body = app.probe(MAX_BUSY)
            return self._json(status, body)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        route = urlsplit(self.path).path
        if route not in ("/call", "/rpc") or (route == "/rpc" and MODE != "mcp"):
            return self._json(404, {"error": "not found"})
        got = self.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode()):
            return self._json(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._json(400, {"error": "body missing or too large"})
        try:
            msg = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json(400, {"error": "body is not JSON"})
        if route == "/rpc":
            return self._rpc(msg)
        try:
            tool = str(msg["tool"])
            args = msg.get("args") or {}
            timeout = msg.get("timeout")
            timeout = float(timeout) if timeout is not None else None
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "expected {tool, args, timeout?}"})
        refused = check(tool, args, DATA_DIR, _cache_dir(), MODE)
        if refused:
            return self._json(403, {"ok": False, "error": refused})
        if not app.alive():
            return self._json(503, {"ok": False, "error": "IngeTrazo is not running"})
        try:
            reply = app.call(tool, args, timeout)
        except OSError as exc:
            reply = {"ok": False, "error": f"bridge: {exc}"}
        return self._json(200, reply)

    def _rpc(self, msg) -> None:
        """One MCP message through IngeTrazo's handler (the gateway sends
        them one at a time and keeps the batching, auth and folders)."""
        if not isinstance(msg, dict):
            return self._json(400, {"error": "one JSON-RPC message per request"})
        if msg.get("method") == "tools/call":
            params = msg.get("params") or {}
            refused = check(str(params.get("name", "")), params.get("arguments") or {},
                            DATA_DIR, _cache_dir(), MODE)
            if refused:
                return self._json(200, {"jsonrpc": "2.0", "id": msg.get("id"), "result": {
                    "content": [{"type": "text", "text": refused}], "isError": True}})
        if not app.alive():
            return self._json(503, {"error": "IngeTrazo is not running"})
        return self._json(200, app.handle(msg))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A client that hangs up early (a health probe's timeout) is no
        error worth a traceback."""
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


app: App
TOKEN = ""
MODE = "render"
MAX_BUSY = float(os.environ.get("HEALTH_MAX_BUSY_SECONDS") or 900)


def main() -> None:
    global app, TOKEN, MODE
    MODE = _mode()
    TOKEN = _token()
    app = App()
    port = int(os.environ.get("ENGINE_PORT", 8766))
    server = Server(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    if MODE == "mcp":
        log(f"MCP engine on :{port}/rpc and /call; data {DATA_DIR}")
        app.start(initial_model(), allow_save=env_flag("INGETRAZO_ALLOW_SAVE", True),
                  save_format=os.environ.get("INGETRAZO_SAVE_FORMAT") or None)
    else:
        log(f"render engine on :{port}/call; cache {_cache_dir()}; data {DATA_DIR}")
        app.start(None, allow_save=False)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
