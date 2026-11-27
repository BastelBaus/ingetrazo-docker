# SPDX-License-Identifier: AGPL-3.0-or-later
"""The viewer's IngeTrazos in other containers (render engines).

``ENGINE_URLS`` (comma-separated, e.g. ``http://viewer-engine:8766``) names
one engine per render worker; ``ENGINE_TOKEN`` is the shared secret they
check (mcp_http/engine_server.py), or ``ENGINE_TOKEN_FILE`` where the
engine wrote it. With engines the viewer needs no
IngeTrazo, no X server and no OpenGL of its own: it runs from the slim
viewer image. Without ``ENGINE_URLS`` it starts its own IngeTrazos as
before (``VIEWER_WORKERS``), which only works in the full image.

``RemoteApp`` has the part of ``mcp_http.appbridge.App`` the renderer
uses: ``start``, ``alive`` and ``call``. The files an engine writes land in
the cache volume both containers mount at the same path.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from mcp_http.appbridge import ai_timeout, log
from mcp_http.engine_server import MIN_TOKEN, engine_token

#: How long the viewer waits for an engine to come up at start.
START_WAIT = float(os.environ.get("ENGINE_START_SECONDS") or 600)


def engine_urls() -> list[str]:
    raw = os.environ.get("ENGINE_URLS", "")
    urls = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    for u in urls:
        if not u.startswith(("http://", "https://")):
            raise SystemExit(f"ENGINE_URLS: {u!r} is not an http(s) URL")
    if len(urls) > 16:
        raise SystemExit("ENGINE_URLS: at most 16 engines")
    if urls and not (os.environ.get("ENGINE_TOKEN", "").strip()
                     or os.environ.get("ENGINE_TOKEN_FILE", "").strip()):
        raise SystemExit("ENGINE_URLS needs ENGINE_TOKEN or ENGINE_TOKEN_FILE "
                         "(the engines' shared secret)")
    return urls


class RemoteApp:
    """One render engine, reached over HTTP. One call at a time per engine
    (the renderer gives each worker its own engine), like a local App."""

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token if token is not None else engine_token()
        self.ready = False
        self._alive_at = 0.0
        self._alive = False
        # The engine's bridge answers "timed out" after its own limit; wait
        # a minute longer so that answer, not a socket timeout, arrives.
        self.timeout = max(ai_timeout() or 180.0, 180.0) + 60.0

    def __repr__(self) -> str:
        return f"RemoteApp({self.url})"

    def _health(self, timeout: float = 5.0) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.url + "/healthz", timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}
        except (OSError, ValueError) as exc:
            return 0, {"status": str(exc)}

    def start(self, open_file=None, allow_save: bool = False, save_format=None) -> None:
        """Waits until the engine answers its health probe; the viewer is
        no use without it, so a missing engine ends the container."""
        if allow_save or open_file is not None:
            raise ValueError("a render engine opens models per call and never saves")
        t0 = time.monotonic()
        last = None
        while time.monotonic() - t0 < START_WAIT:
            if len(self.token) < MIN_TOKEN:          # the engine writes it on start
                self.token = engine_token()
            status, body = self._health()
            if status == 200 and len(self.token) >= MIN_TOKEN:
                self.ready = True
                log(f"render engine {self.url} ready after {time.monotonic() - t0:.1f}s")
                return
            if body.get("status") != last:
                last = body.get("status")
                log(f"waiting for render engine {self.url}: {last or status}")
            time.sleep(2.0)
        raise SystemExit(f"render engine {self.url} not ready after {START_WAIT:.0f}s")

    def alive(self) -> bool:
        """Health of the engine, asked at most every 10 s (the viewer's own
        /healthz asks often). ``busy`` counts as alive."""
        if not self.ready:
            return False
        now = time.monotonic()
        if now - self._alive_at > 10.0:
            status, _ = self._health(timeout=5.0)
            self._alive, self._alive_at = status == 200, now
        return self._alive

    def call(self, tool: str, args: dict | None = None,
             timeout: float | None = None) -> dict:
        body = {"tool": tool, "args": args or {}}
        if timeout is not None:
            body["timeout"] = timeout
        req = urllib.request.Request(
            self.url + "/call", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.token}"})
        wait = (timeout + 60.0) if timeout is not None else self.timeout
        try:
            with urllib.request.urlopen(req, timeout=wait) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                got = json.loads(exc.read() or b"{}")
            except ValueError:
                got = {}
            return {"ok": False, "error": got.get("error") or f"engine HTTP {exc.code}"}
        except (OSError, ValueError) as exc:
            self._alive_at = 0.0                 # ask again at the next probe
            return {"ok": False, "error": f"render engine {self.url}: {exc}"}
