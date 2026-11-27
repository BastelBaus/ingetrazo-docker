# SPDX-License-Identifier: AGPL-3.0-or-later
"""The render engine: one headless IngeTrazo behind a small internal HTTP
API, for the viewer when the viewer runs in its own container.

The viewer image has no IngeTrazo. It sends each bridge call here:

``POST /call``  ``{"tool": "...", "args": {...}, "timeout": seconds?}``
  → the bridge's own reply, ``{"ok": true, "result": ...}`` or
  ``{"ok": false, "error": "..."}``. Needs ``Authorization: Bearer
  <ENGINE_TOKEN>``.
``GET /healthz``  the same probe as the MCP service (no token).

This is not an MCP endpoint and not for people or AI clients: it has one
shared secret (``ENGINE_TOKEN``), no user list and no folder rights, and
belongs on an internal network that only the viewer reaches. Without
``ENGINE_TOKEN`` the engine makes a random one in ``ENGINE_TOKEN_FILE`` (a
volume it shares with the viewer). Whoever holds
the token may run Python in the engine (the viewer reads scene folders and
cameras that way), so the token is as good as a shell in this container.

Guard rails all the same: only the tools the viewer uses (``ENGINE_TOOLS``),
documents opened only from the model folders, files written only below
``INGETRAZO_CACHE`` (the volume the engine shares with the viewer), and
never with ``--allow-save``: the engine cannot overwrite a model.
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

from .appbridge import DATA_DIR, App, log

#: What the viewer calls; everything else is refused.
ENGINE_TOOLS = frozenset({
    "list_documents", "open_document", "document_info", "list_sheets",
    "list_scenes", "list_layers", "get_scene_info", "run_python",
    "export_pdf", "export_view", "export_file", "screenshot", "render_sheet",
})
#: Tools whose ``path`` is written; it must lie below the cache.
WRITE_TOOLS = frozenset({"export_pdf", "export_view", "export_file",
                         "screenshot", "render_sheet"})
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


def check(tool: str, args: dict, data_dir: Path, cache_dir: Path) -> str | None:
    """Why a call is refused, or None. Pure, for the unit tests."""
    if tool not in ENGINE_TOOLS:
        return f"tool {tool!r} is not available in the render engine"
    if not isinstance(args, dict):
        return "args must be an object"
    raw = args.get("path")
    if tool == "open_document":
        if not isinstance(raw, str) or not _inside(Path(raw), data_dir):
            return f"open_document: path must lie below {data_dir}"
    elif tool in WRITE_TOOLS:
        if not isinstance(raw, str) or not _inside(Path(raw), cache_dir):
            return f"{tool}: path must lie below {cache_dir}"
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
        if urlsplit(self.path).path != "/call":
            return self._json(404, {"error": "not found"})
        got = self.headers.get("Authorization", "")
        if not hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode()):
            return self._json(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._json(400, {"error": "body missing or too large"})
        try:
            msg = json.loads(self.rfile.read(length))
            tool = str(msg["tool"])
            args = msg.get("args") or {}
            timeout = msg.get("timeout")
            timeout = float(timeout) if timeout is not None else None
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "expected {tool, args, timeout?}"})
        refused = check(tool, args, DATA_DIR, _cache_dir())
        if refused:
            return self._json(403, {"ok": False, "error": refused})
        if not app.alive():
            return self._json(503, {"ok": False, "error": "IngeTrazo is not running"})
        try:
            reply = app.call(tool, args, timeout)
        except OSError as exc:
            reply = {"ok": False, "error": f"bridge: {exc}"}
        return self._json(200, reply)


app: App
TOKEN = ""
MAX_BUSY = float(os.environ.get("HEALTH_MAX_BUSY_SECONDS") or 900)


def main() -> None:
    global app, TOKEN
    TOKEN = _token()
    app = App()
    port = int(os.environ.get("ENGINE_PORT", 8766))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    log(f"render engine on :{port}/call; cache {_cache_dir()}; data {DATA_DIR}")
    app.start(None, allow_save=False)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
