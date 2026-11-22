# SPDX-License-Identifier: GPL-3.0-or-later
"""IngeTrazo's own stdio MCP server, used as a library.

Nothing of the MCP protocol or the tool list is re-implemented here: the
app ships ``scripts/ingetrazo_mcp.py`` with the tool definitions, the
instructions, the JSON-RPC ``handle()`` and the code that starts a
window-less IngeTrazo (``--headless``) and waits for its bridge. This module
loads that file from the app checkout, starts the child once, and serialises
every request to it — the bridge serves one client at a time, and the HTTP
server and the health probe both share this one connection.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("INGETRAZO_APP", "/opt/ingetrazo"))
DATA_DIR = Path(os.environ.get("INGETRAZO_DATA", "/data")).resolve()


def log(msg: str) -> None:
    print(f"[ingetrazo-docker] {msg}", file=sys.stderr, flush=True)


def _load_stdio_server():
    script = APP_DIR / "scripts" / "ingetrazo_mcp.py"
    spec = importlib.util.spec_from_file_location("ingetrazo_mcp", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingetrazo_mcp"] = module
    spec.loader.exec_module(module)
    for name in ("handle", "_bridge", "_start_child", "TOOLS", "INSTRUCTIONS"):
        if not hasattr(module, name):
            raise RuntimeError(
                f"{script} has no {name}: this IngeTrazo is too old for the "
                "container (needs ingelibre/ingetrazo#1523)")
    return module


class App:
    """One headless IngeTrazo and the one connection to its bridge."""

    def __init__(self) -> None:
        self.mcp = _load_stdio_server()
        self.lock = threading.Lock()
        self.busy_since: float | None = None
        self.ready = False

    # ---- lifecycle -----------------------------------------------------------
    def start(self, open_file: Path | None, allow_save: bool,
              save_format: str | None = None) -> None:
        argv = ["--headless", "--ai-bridge"]
        if open_file is not None:
            argv += ["--open", str(open_file)]
        if allow_save:
            argv.append("--allow-save")
        if save_format:
            argv += ["--save-format", save_format]
        log("starting IngeTrazo: " + " ".join(argv))
        t0 = time.monotonic()
        # Starts `main.py --headless …` on a free port and waits for the
        # ready file; exits this process with IngeTrazo's code if it fails.
        self.mcp.PORT = self.mcp._start_child(argv)
        self.ready = True
        log(f"IngeTrazo ready on 127.0.0.1:{self.mcp.PORT} "
            f"after {time.monotonic() - t0:.1f}s")
        threading.Thread(target=self._watch_child, name="child-watch",
                         daemon=True).start()

    def _watch_child(self) -> None:
        """IngeTrazo gone means nothing left to serve: end the container
        with its exit code, so the restart policy brings it back."""
        child = self.mcp._child
        code = child.wait()
        log(f"IngeTrazo ended (exit code {code}); stopping")
        os._exit(code or 1)

    def alive(self) -> bool:
        child = getattr(self.mcp, "_child", None)
        return self.ready and child is not None and child.poll() is None

    # ---- requests ------------------------------------------------------------
    def handle(self, msg: dict) -> dict | None:
        """One JSON-RPC message through the app's own handler."""
        with self.lock:
            self.busy_since = time.monotonic()
            try:
                return self.mcp.handle(msg)
            finally:
                self.busy_since = None

    def call(self, tool: str, args: dict | None = None,
             timeout: float | None = None) -> dict:
        """A raw bridge request → ``{"ok", "result" | "error"}``."""
        with self.lock:
            self.busy_since = time.monotonic()
            try:
                return self._raw(tool, args or {}, timeout)
            finally:
                self.busy_since = None

    def _raw(self, tool: str, args: dict, timeout: float | None) -> dict:
        sock = getattr(self.mcp, "_sock", None)
        if timeout is not None and sock is not None:
            sock.settimeout(timeout)
        try:
            return self.mcp._bridge(tool, args)
        finally:
            sock = getattr(self.mcp, "_sock", None)
            if timeout is not None and sock is not None:
                sock.settimeout(180.0)

    def probe(self, max_busy: float) -> tuple[int, dict]:
        """``/healthz``: (HTTP status, body). Goes through the shared
        connection — a second one would take the bridge's only slot."""
        if not self.ready:
            return 503, {"status": "starting"}
        if not self.alive():
            return 503, {"status": "ingetrazo not running"}
        if not self.lock.acquire(timeout=2.0):
            since = self.busy_since
            busy_for = 0.0 if since is None else time.monotonic() - since
            if busy_for < max_busy:
                return 200, {"status": "busy", "busy_seconds": round(busy_for)}
            return 503, {"status": "blocked", "busy_seconds": round(busy_for)}
        try:
            reply = self._raw("document_info", {}, 10.0)
        except OSError as exc:
            return 503, {"status": "bridge not answering", "error": str(exc)}
        finally:
            self.lock.release()
        if not reply.get("ok"):
            return 503, {"status": "bridge error"}
        return 200, {"status": "ok"}


def resolve_data_path(value: str, must_exist: bool = False) -> Path:
    """A path the agent gave, relative to the data folder or absolute
    inside it. Anything outside is refused."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = DATA_DIR / p
    p = p.resolve()
    if p != DATA_DIR and DATA_DIR not in p.parents:
        raise PermissionError(f"outside the data folder {DATA_DIR}: {value}")
    if must_exist and not p.is_file():
        raise FileNotFoundError(f"no such file in {DATA_DIR}: {value}")
    return p


def list_models() -> list[Path]:
    return sorted(p for p in DATA_DIR.rglob("*.igz") if p.is_file()
                  and not any(part.startswith(".") for part in
                              p.relative_to(DATA_DIR).parts))


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def initial_model() -> Path | None:
    name = os.environ.get("INGETRAZO_MODEL", "").strip()
    if name:
        return resolve_data_path(name, must_exist=True)
    return None
