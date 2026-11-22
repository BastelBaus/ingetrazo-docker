# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only live viewer: every sheet and saved view of every model in the
data folder, in the browser, refreshed when a model file changes.

Routes: ``/`` overview, ``/m/<slug>`` one model, ``/files/<slug>/<rev>/<f>``
rendered images and PDF, ``/events`` server-sent events (a render finished),
``/healthz``. Optional password: ``VIEWER_PASSWORD`` (user ``VIEWER_USER``,
default ``viewer``) — a login page with a session cookie for browsers
(``/login``, ``/logout``), Basic auth for scripts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

from mcp_http.appbridge import App, log

from .render import CACHE_DIR, POLL, Renderer

renderer: Renderer
USER = os.environ.get("VIEWER_USER", "viewer")
PASSWORD = os.environ.get("VIEWER_PASSWORD", "")
TITLE = os.environ.get("VIEWER_TITLE", "IngeTrazo viewer")
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;
--accent:#2f6fb2;--ok:#2e8b57;--warn:#b7791f;--sheet:#fff;--shadow:0 1px 3px rgba(0,0,0,.08)}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#20201e;--fg:#ecebe6;
--muted:#9b9a94;--line:#33322f;--accent:#6aa5e6;--sheet:#fafaf8;--shadow:none}}
*{box-sizing:border-box}html{scroll-padding-top:64px}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
padding:10px 16px;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;font-weight:600;overflow-wrap:anywhere}
header .meta{color:var(--muted);font-size:13px}
.spacer{flex:1}
.live{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.live i{width:8px;height:8px;border-radius:50%;background:var(--muted);display:inline-block}
.live.on i{background:var(--ok)}.live.busy i{background:var(--warn);animation:pulse 1s infinite}
@keyframes pulse{50%{opacity:.3}}
.btn{border:1px solid var(--line);background:var(--card);padding:5px 10px;border-radius:6px;font-size:13px;color:var(--fg)}
main{padding:16px;max-width:1500px;margin:0 auto}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow);color:var(--fg)}
.card:hover{text-decoration:none;border-color:var(--accent)}
.card .thumb{aspect-ratio:16/10;background:var(--sheet);display:flex;align-items:center;justify-content:center;border-bottom:1px solid var(--line)}
.card .thumb img{max-width:100%;max-height:100%;object-fit:contain}
.card .body{padding:10px 12px}.card .name{font-weight:600;overflow-wrap:anywhere}
.card .sub{color:var(--muted);font-size:13px}
.layout{display:grid;grid-template-columns:220px 1fr;gap:24px}
nav.toc{position:sticky;top:64px;align-self:start;max-height:calc(100vh - 80px);overflow:auto;font-size:14px}
nav.toc h3{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:14px 0 6px}
nav.toc a{display:block;padding:3px 8px;border-radius:5px;color:var(--fg)}nav.toc a:hover{background:var(--card);text-decoration:none}
nav.toc .folder{color:var(--muted);font-size:12px;padding:6px 8px 2px}
section h2{font-size:15px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:8px 0 12px}
figure{margin:0 0 28px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow)}
figure a.img{display:block;background:var(--sheet)}
figure img{display:block;width:100%;height:auto}
figcaption{display:flex;gap:10px;align-items:baseline;padding:8px 12px;font-size:14px;border-top:1px solid var(--line)}
figcaption .muted,.muted{color:var(--muted);font-size:13px}
.views{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(420px,1fr))}
.views figure{margin:0}
.errors{background:color-mix(in srgb,var(--warn) 12%,var(--card));border:1px solid var(--warn);border-radius:8px;padding:8px 12px;margin-bottom:16px;font-size:13px}
.empty{color:var(--muted);padding:40px 0;text-align:center}
@media (max-width:820px){.layout{grid-template-columns:1fr}nav.toc{position:static;max-height:none}
.views{grid-template-columns:1fr}}
"""

JS = """
(function(){
  const slug = document.body.dataset.slug || null;
  const dot = document.getElementById('live');
  const key = 'scroll:' + location.pathname;
  try { const y = sessionStorage.getItem(key); if (y !== null) { sessionStorage.removeItem(key);
        window.scrollTo(0, parseInt(y, 10)); } } catch (e) {}
  function set(state, text){ if (!dot) return; dot.className = 'live ' + state;
                             dot.querySelector('span').textContent = text; }
  function connect(){
    const es = new EventSource('/events');
    es.onopen = () => set('on', 'live');
    es.onerror = () => set('', 'reconnecting…');
    es.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (slug && ev.slug !== slug) return;
      if (ev.rendering) { set('busy', 'updating…'); return; }
      try { sessionStorage.setItem(key, String(window.scrollY)); } catch (e) {}
      location.reload();
    };
  }
  if (window.EventSource) connect();
})();
"""


LOGOUT = '<a class="btn" href="/logout">Log out</a>'


def _page(title: str, header: str, body: str, slug: str | None = None) -> bytes:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body data-slug="{escape(slug or '')}"><header>{header}<span class="spacer"></span>
<span id="live" class="live"><i></i><span>connecting…</span></span>{LOGOUT if PASSWORD else ""}</header>
<main>{body}</main><script>{JS}</script></body></html>""".encode("utf-8")


def _when(index: dict) -> str:
    return escape((index.get("rendered_at") or "").replace("T", " ").replace("+00:00", " UTC"))


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _file_url(index: dict, file: str) -> str:
    return f"/files/{quote(index['slug'])}/{quote(index['rev'])}/{quote(file)}"


def overview() -> bytes:
    cards = []
    for m in renderer.models():
        idx = m["index"]
        thumb = ""
        if idx.get("sheets") and idx["sheets"][0].get("file"):
            thumb = _file_url(idx, idx["sheets"][0]["file"])
        elif idx.get("views"):
            thumb = _file_url(idx, idx["views"][0]["file"])
        img = (f'<img src="{thumb}" alt="" loading="lazy">' if thumb
               else '<span class="muted">not rendered yet</span>')
        if m["rendering"]:
            status = "rendering…"
        elif idx:
            status = (f'{_count(len(idx.get("sheets", [])), "sheet")} · '
                      f'{_count(len(idx.get("views", [])), "view")} · {_when(idx)}')
        else:
            status = "waiting"
        cards.append(f'<a class="card" href="/m/{quote(m["slug"])}"><div class="thumb">{img}</div>'
                     f'<div class="body"><div class="name">{escape(m["name"])}</div>'
                     f'<div class="sub">{status}</div></div></a>')
    body = (f'<div class="grid">{"".join(cards)}</div>' if cards else
            '<p class="empty">No .igz files in the data folder yet.</p>')
    return _page(TITLE, f"<h1>{escape(TITLE)}</h1>", body)


def model_page(slug: str) -> bytes | None:
    idx = renderer.index(slug)
    if idx is None:
        if any(m["slug"] == slug for m in renderer.models()):
            return _page(TITLE, f'<a class="btn" href="/">← All models</a>',
                         '<p class="empty">Rendering… this page refreshes when it is done.</p>', slug)
        return None
    toc, sheets_html = [], []
    folder = None
    for i, s in enumerate(idx.get("sheets", [])):
        anchor = f"sheet-{i}"
        if s.get("folder") != folder:
            folder = s.get("folder")
            if folder:
                toc.append(f'<div class="folder">{escape(folder)}</div>')
        toc.append(f'<a href="#{anchor}">{escape(s["name"])}</a>')
        extra = "" if s.get("printed", True) else ' <span class="muted">(not printed)</span>'
        where = f'<span class="muted">{escape(s["folder"])}</span>' if s.get("folder") else ""
        if s.get("file"):
            url = _file_url(idx, s["file"])
            img = f'<a class="img" href="{url}" target="_blank"><img src="{url}" alt="{escape(s["name"])}" loading="lazy"></a>'
        else:
            img = '<p class="empty">not rendered</p>'
        sheets_html.append(f'<figure id="{anchor}">{img}<figcaption><strong>{escape(s["name"])}</strong>'
                           f'{extra}<span class="spacer"></span>{where}</figcaption></figure>')
    views_html = []
    if idx.get("views"):
        toc.append('<h3>Views</h3>')
    for i, v in enumerate(idx.get("views", [])):
        anchor = f"view-{i}"
        toc.append(f'<a href="#{anchor}">{escape(v["name"])}</a>')
        url = _file_url(idx, v["file"])
        views_html.append(f'<figure id="{anchor}"><a class="img" href="{url}" target="_blank">'
                          f'<img src="{url}" alt="{escape(v["name"])}" loading="lazy"></a>'
                          f'<figcaption><strong>{escape(v["name"])}</strong></figcaption></figure>')
    if sheets_html:
        toc.insert(0, '<h3>Sheets</h3>')
    errors = ""
    if idx.get("errors"):
        errors = ('<div class="errors"><strong>Some parts did not render:</strong><br>'
                  + "<br>".join(escape(e) for e in idx["errors"]) + "</div>")
    content = errors
    if sheets_html:
        content += f'<section><h2>Sheets</h2>{"".join(sheets_html)}</section>'
    if views_html:
        content += f'<section><h2>Views</h2><div class="views">{"".join(views_html)}</div></section>'
    if not sheets_html and not views_html:
        content += '<p class="empty">This model has no sheets and no saved views.</p>'
    pdf = (f'<a class="btn" href="{_file_url(idx, idx["pdf"])}" target="_blank">PDF</a>'
           if idx.get("pdf") else "")
    header = (f'<a class="btn" href="/">← All</a><h1>{escape(idx["name"])}</h1>'
              f'<span class="meta">rendered {_when(idx)}</span>{pdf}')
    body = f'<div class="layout"><nav class="toc">{"".join(toc)}</nav><div>{content}</div></div>'
    return _page(f'{idx["name"]} – {TITLE}', header, body, slug)


COOKIE = "itz_viewer"
SESSION_DAYS = int(os.environ.get("VIEWER_SESSION_DAYS", 30))
#: Signs the session cookies; new on every start, so a restart logs out.
_SECRET = secrets.token_bytes(32)


def _credentials_ok(user: str, pw: str) -> bool:
    return (hmac.compare_digest(user.encode(), USER.encode())
            & hmac.compare_digest(pw.encode(), PASSWORD.encode()))


def _sign(expiry: str) -> str:
    return hmac.new(_SECRET, f"{USER}|{expiry}".encode(), hashlib.sha256).hexdigest()


def _new_session() -> str:
    expiry = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{expiry}.{_sign(expiry)}"


def _session_valid(value: str) -> bool:
    expiry, _, sig = value.partition(".")
    if not expiry.isdigit() or int(expiry) < time.time():
        return False
    return hmac.compare_digest(sig, _sign(expiry))


def _safe_next(value: str) -> str:
    """Only a path on this server (no //host, no scheme)."""
    return value if value.startswith("/") and not value.startswith("//") else "/"


def login_page(error: str, nxt: str) -> bytes:
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Log in – {escape(TITLE)}</title><style>{CSS}
.login{{max-width:340px;margin:12vh auto;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:24px;box-shadow:var(--shadow)}}
.login h1{{font-size:18px;margin:0 0 16px}}.login label{{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}}
.login input{{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg);font:inherit}}
.login button{{margin-top:18px;width:100%;padding:9px;border:0;border-radius:6px;background:var(--accent);
color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.err{{color:#c0392b;font-size:14px;margin:0 0 8px}}</style></head>
<body><main><form class="login" method="post" action="/login">
<h1>{escape(TITLE)}</h1>{err}
<input type="hidden" name="next" value="{escape(nxt)}">
<label for="u">User</label><input id="u" name="user" autocomplete="username" value="{escape(USER)}">
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" autofocus>
<button type="submit">Log in</button></form></main></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ingetrazo-viewer"

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/healthz", "/files/")):
            sys.stderr.write("[viewer] %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---- authentication ------------------------------------------------------
    # Browsers get a login page and a session cookie: embedded browsers
    # (VS Code's, many WebViews) never show the Basic-auth dialog and only
    # print the 401. Scripts and curl keep using Basic auth.
    def _cookie_ok(self) -> bool:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        morsel = jar.get(COOKIE)
        return morsel is not None and _session_valid(morsel.value)

    def _basic_ok(self) -> bool:
        value = self.headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(value[6:]).decode().partition(":")
        except ValueError:
            return False
        return _credentials_ok(user, pw)

    def _authorised(self, path: str) -> bool:
        if not PASSWORD or self._cookie_ok() or self._basic_ok():
            return True
        wants_html = "text/html" in self.headers.get("Accept", "")
        if wants_html and not path.startswith(("/files/", "/events")):
            target = self.path if self.path.startswith("/") else "/"
            self._send(303, b"", "text/plain",
                       {"Location": "/login?next=" + quote(target, safe="")})
        else:
            self._send(401, b"authentication required\n", "text/plain",
                       {"WWW-Authenticate": f'Basic realm="{TITLE}", charset="UTF-8"'})
        return False

    def _secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                or os.environ.get("VIEWER_COOKIE_SECURE", "").lower() in ("1", "true", "yes"))

    def _login_page(self, status: int = 200, error: str = "", nxt: str = "/"):
        self._send(status, login_page(error, nxt), "text/html; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/login":
            return self._send(404, b"not found\n", "text/plain")
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        user = (form.get("user") or [""])[0]
        pw = (form.get("password") or [""])[0]
        nxt = _safe_next((form.get("next") or ["/"])[0])
        if not PASSWORD or _credentials_ok(user, pw):
            cookie = (f"{COOKIE}={_new_session()}; Path=/; HttpOnly; SameSite=Lax; "
                      f"Max-Age={SESSION_DAYS * 86400}" + ("; Secure" if self._secure() else ""))
            return self._send(303, b"", "text/plain", {"Location": nxt, "Set-Cookie": cookie})
        time.sleep(0.7)                          # slows down guessing
        return self._login_page(401, "Wrong user name or password.", nxt)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            return self._health()
        if path == "/login":
            nxt = _safe_next((parse_qs(parts.query).get("next") or ["/"])[0])
            return self._login_page(nxt=nxt)
        if path == "/logout":
            return self._send(303, b"", "text/plain", {
                "Location": "/login",
                "Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"})
        if not self._authorised(path):
            return None
        html = "text/html; charset=utf-8"
        nocache = {"Cache-Control": "no-store"}
        if path == "/":
            return self._send(200, overview(), html, nocache)
        if path.startswith("/m/"):
            slug = path[3:]
            page = model_page(slug) if _SAFE.match(slug) else None
            if page is None:
                return self._send(404, b"no such model\n", "text/plain")
            return self._send(200, page, html, nocache)
        if path.startswith("/files/"):
            return self._file(path[7:])
        if path == "/events":
            return self._events()
        return self._send(404, b"not found\n", "text/plain")

    def _file(self, rest: str):
        parts = rest.split("/")
        if len(parts) != 3 or not all(_SAFE.match(p) for p in parts):
            return self._send(404, b"not found\n", "text/plain")
        f = CACHE_DIR.joinpath(*parts)
        if not f.is_file():
            return self._send(404, b"not found\n", "text/plain")
        ctype = {"png": "image/png", "pdf": "application/pdf",
                 "svg": "image/svg+xml"}.get(f.suffix[1:].lower(), "application/octet-stream")
        # A render's folder never changes: cache it for good.
        return self._send(200, f.read_bytes(), ctype,
                          {"Cache-Control": "private, max-age=31536000, immutable"})

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        cond = renderer.changed
        with cond:
            seen = renderer.generation
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                with cond:
                    cond.wait_for(lambda: renderer.generation != seen, timeout=15)
                    gen, event = renderer.generation, dict(renderer.last_event)
                if gen != seen:
                    seen = gen
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return None

    def _health(self):
        stale = time.monotonic() - renderer.heartbeat
        if not renderer.app.alive():
            status, body = 503, {"status": "ingetrazo not running"}
        elif renderer.rendering is None and stale > max(60.0, POLL * 20):
            status, body = 503, {"status": "render loop stalled"}
        else:
            status, body = 200, {"status": "rendering" if renderer.rendering else "ok"}
        self._send(status, json.dumps(body).encode(), "application/json",
                   {"Cache-Control": "no-store"})


def main() -> None:
    global renderer
    app = App()
    renderer = Renderer(app)
    port = int(os.environ.get("VIEWER_PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    log(f"viewer on :{port} (auth: {'basic' if PASSWORD else 'none'}); cache {CACHE_DIR}")
    app.start(None, allow_save=False)
    renderer.run()


if __name__ == "__main__":
    main()
