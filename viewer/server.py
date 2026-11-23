# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only live viewer: every sheet and saved view of every model in the
data folder, in the browser, refreshed when a model file changes.

Routes: ``/`` overview, ``/m/<slug>`` one model, ``/files/<slug>/<rev>/<f>``
rendered images and PDF, ``/events`` server-sent events (a render finished),
``/healthz`` (always open). Who may see the rest: ``VIEWER_AUTH_MODE``
(see auth.py) — ``local`` (``/login``, ``/logout``, Basic auth for
scripts), ``none``, ``oidc`` (``/auth/login``, ``/auth/callback``) or
``proxy``.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from mcp_http.appbridge import App, log

from .auth import Auth, User
from .render import CACHE_DIR, POLL, Renderer

renderer: Renderer
auth: Auth
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
header.bar{padding:6px 12px;gap:8px;flex-wrap:nowrap}
header.bar h1{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:3em;max-width:22vw}
.navs{display:flex;gap:8px;min-width:0;flex:0 1 auto}
.dd{position:relative;min-width:0}
.dd>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:6px;max-width:38vw;
border:1px solid var(--line);background:var(--card);padding:4px 8px;border-radius:6px;font-size:13px;white-space:nowrap;overflow:hidden}
.dd>summary::-webkit-details-marker{display:none}
.dd>summary::after{content:"";border:4px solid transparent;border-top-color:var(--muted);margin:4px 0 0 2px}
.dd[open]>summary,.dd>summary:hover{border-color:var(--accent)}
.dd .k{flex:none;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.dd .cur{flex:0 1 auto;overflow:hidden;text-overflow:ellipsis;min-width:0}.dd>summary::after{flex:none}.dd .cur .p{color:var(--muted)}
.dd-panel{position:absolute;top:calc(100% + 6px);left:0;z-index:10;width:min(380px,calc(100vw - 24px));
max-height:min(70vh,560px);display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);
border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.18)}
.dd-filter{margin:8px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;font-size:14px}
.dd-list{overflow:auto;padding:4px 0 8px;font-size:14px}
.dd-list a,.dd-list .f{display:block;padding:3px 12px 3px calc(12px + var(--d,0) * 14px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dd-list a{color:var(--fg)}.dd-list a:hover{background:var(--bg);text-decoration:none}
.dd-list a.on{background:color-mix(in srgb,var(--accent) 14%,transparent);font-weight:600}
.dd-list .f{color:var(--muted);font-size:12px;padding-top:7px}.dd-list .f::before{content:"▸ "}
.dd-list .none{font-style:italic}.dd-list .none::before{content:""}
.dd-empty{color:var(--muted);padding:6px 12px;font-size:13px}
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
@media (max-width:820px){.views{grid-template-columns:1fr}
header.bar{flex-wrap:wrap}header.bar h1{max-width:none;flex:1}header.bar .meta{display:none}
.navs{order:9;flex:1 1 100%}.navs .dd{position:static;flex:1 1 0}.dd>summary{max-width:none}
.dd .cur{flex:1 1 auto}
.dd-panel{left:12px;width:auto;right:12px}}
.who{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:14em}
@media (max-width:820px){.who{display:none}}
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

  // ---- the View / Sheet dropdowns of the top bar ----
  const header = document.querySelector('header');
  const dds = Array.from(document.querySelectorAll('details.dd'));
  if (!dds.length) return;
  function pad(){ document.documentElement.style.scrollPaddingTop = (header.offsetHeight + 8) + 'px'; }
  pad(); window.addEventListener('resize', pad);
  function closeAll(except){ dds.forEach(d => { if (d !== except) d.open = false; }); }
  dds.forEach(dd => {
    const filter = dd.querySelector('.dd-filter');
    dd.addEventListener('toggle', () => {
      if (!dd.open) return;
      closeAll(dd);
      const on = dd.querySelector('a.on');
      if (on) on.scrollIntoView({block: 'nearest'});
      if (filter && matchMedia('(pointer:fine)').matches) filter.focus();
    });
    dd.querySelectorAll('.dd-list a').forEach(a => a.addEventListener('click', () => {
      dd.open = false; held = document.getElementById(a.getAttribute('href').slice(1)); show(a); }));
    if (filter) filter.addEventListener('input', () => {
      const q = filter.value.trim().toLowerCase();
      const rows = Array.from(dd.querySelectorAll('.dd-list > *'));
      let any = false;
      rows.forEach(r => { if (r.tagName === 'A') {
        const hit = !q || (r.dataset.folder + ' ' + r.textContent).toLowerCase().includes(q);
        r.hidden = !hit; any = any || hit; } });
      // A folder heading stays while something below it (deeper) is shown.
      rows.forEach((r, i) => { if (r.tagName === 'A') return;
        const d = +r.style.getPropertyValue('--d') || 0; let keep = false;
        for (let j = i + 1; j < rows.length; j++) {
          const rj = rows[j], dj = +rj.style.getPropertyValue('--d') || 0;
          if (rj.tagName !== 'A' && dj <= d) break;
          if (rj.tagName === 'A' && !rj.hidden) { keep = true; break; } }
        r.hidden = !keep; });
      dd.querySelector('.dd-empty').hidden = any;
    });
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('details.dd')) closeAll(null); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(null); });

  // Scroll spy: the sheet or view on screen is the current one of its dropdown.
  function show(a){
    const dd = a.closest('details.dd');
    dd.querySelectorAll('a.on').forEach(x => x.classList.remove('on'));
    a.classList.add('on');
    const cur = dd.querySelector('.cur'); cur.textContent = '';
    if (a.dataset.folder) { const p = document.createElement('span'); p.className = 'p';
      p.textContent = a.dataset.folder + ' \\u203a '; cur.appendChild(p); }
    cur.appendChild(document.createTextNode(a.textContent));
    cur.title = (a.dataset.folder ? a.dataset.folder + ' \\u203a ' : '') + a.textContent;
  }
  const figs = Array.from(document.querySelectorAll('figure[id]'));
  let last = null, queued = false, held = null;
  function spy(){
    queued = false;
    const top = header.offsetHeight + 40;
    // A picked entry stays current while it is on screen, even when the
    // page cannot scroll it up to the top (the last ones).
    if (held) { const r = held.getBoundingClientRect();
      if (r.bottom > top && r.top < innerHeight) { last = held; return; } held = null; }
    let cur = figs[0];
    for (const f of figs) { if (f.getBoundingClientRect().top <= top) cur = f; else break; }
    if (!cur || cur === last) return;
    last = cur;
    const a = document.querySelector('.dd-list a[href="#' + cur.id + '"]');
    if (a) show(a);
  }
  window.addEventListener('scroll', () => { if (!queued) { queued = true; requestAnimationFrame(spy); } },
                          {passive: true});
  spy();
})();
"""


def _account(user: User | None) -> str:
    """The top bar's right end: who is logged in, and the way out."""
    if user is None or auth.mode == "none":
        return ""
    who = ""
    if auth.mode in ("oidc", "proxy"):
        role = " (admin)" if user.role == "admin" else ""
        who = f'<span class="muted who" title="{escape(user.name + role)}">{escape(user.name)}</span>'
    out = ('<a class="btn" href="/logout">Log out</a>'
           if auth.mode != "proxy" or auth.proxy.logout_url else "")
    return who + out


def _page(title: str, header: str, body: str, slug: str | None = None,
          bar: bool = False, user: User | None = None) -> bytes:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body data-slug="{escape(slug or '')}"><header{' class="bar"' if bar else ''}>{header}{'' if bar else '<span class="spacer"></span>'}
<span id="live" class="live"><i></i><span>connecting…</span></span>{_account(user)}</header>
<main>{body}</main><script>{JS}</script></body></html>""".encode("utf-8")


def _when(index: dict) -> str:
    return escape((index.get("rendered_at") or "").replace("T", " ").replace("+00:00", " UTC"))


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _file_url(index: dict, file: str) -> str:
    return f"/files/{quote(index['slug'])}/{quote(index['rev'])}/{quote(file)}"


SEP = " › "
#: More entries than this and a dropdown gets a filter box.
FILTER_FROM = 8


def _folder(item: dict) -> list[str]:
    """An entry's folder path, top first. Older renders stored "A/B"."""
    f = item.get("folder")
    if isinstance(f, list):
        return [str(p) for p in f if str(p).strip()]
    return [p for p in str(f or "").split("/") if p.strip()]


def _dropdown(label: str, entries: list[tuple[str, str, list[str]]]) -> str:
    """One dropdown of the top bar: ``entries`` are (anchor, name, folder
    path) in document order, shown as a folder tree – in each folder its
    subfolders first, then its entries, as IngeTrazo's explorers do; the
    entries outside any folder come last, under "No folder"."""
    if not entries:
        return ""
    tree: dict = {"sub": {}, "items": []}
    for anchor, name, path in entries:
        node = tree
        for part in path:
            node = node["sub"].setdefault(part, {"sub": {}, "items": []})
        node["items"].append((anchor, name, path))
    rows: list[str] = []

    def item(anchor: str, name: str, path: list[str], depth: int) -> None:
        rows.append(f'<a href="#{anchor}" style="--d:{depth}" '
                    f'data-folder="{escape(SEP.join(path))}">{escape(name)}</a>')

    def walk(node: dict, depth: int) -> None:
        for name, sub in node["sub"].items():
            rows.append(f'<div class="f" style="--d:{depth}">{escape(name)}</div>')
            walk(sub, depth + 1)
        if depth:
            for e in node["items"]:
                item(*e, depth)

    walk(tree, 0)
    if tree["items"]:
        if tree["sub"]:
            rows.append('<div class="f none" style="--d:0">No folder</div>')
        for e in tree["items"]:
            item(*e, 1 if tree["sub"] else 0)
    filt = ('<input class="dd-filter" type="search" placeholder="Filter…" '
            f'aria-label="Filter {escape(label.lower())}s">' if len(entries) > FILTER_FROM else "")
    return (f'<details class="dd"><summary><span class="k">{escape(label)}</span>'
            f'<span class="cur muted">{len(entries)}</span></summary>'
            f'<div class="dd-panel">{filt}<div class="dd-list">{"".join(rows)}</div>'
            '<div class="dd-empty" hidden>Nothing matches.</div></div></details>')


def overview(user: User | None = None) -> bytes:
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
    return _page(TITLE, f"<h1>{escape(TITLE)}</h1>", body, user=user)


def model_page(slug: str, user: User | None = None) -> bytes | None:
    idx = renderer.index(slug)
    if idx is None:
        if any(m["slug"] == slug for m in renderer.models()):
            return _page(TITLE, f'<a class="btn" href="/">← All models</a>',
                         '<p class="empty">Rendering… this page refreshes when it is done.</p>',
                         slug, user=user)
        return None
    sheet_nav, view_nav, sheets_html = [], [], []
    for i, s in enumerate(idx.get("sheets", [])):
        anchor = f"sheet-{i}"
        path = _folder(s)
        sheet_nav.append((anchor, s["name"], path))
        extra = "" if s.get("printed", True) else ' <span class="muted">(not printed)</span>'
        where = f'<span class="muted">{escape(SEP.join(path))}</span>' if path else ""
        if s.get("file"):
            url = _file_url(idx, s["file"])
            img = f'<a class="img" href="{url}" target="_blank"><img src="{url}" alt="{escape(s["name"])}" loading="lazy"></a>'
        else:
            img = '<p class="empty">not rendered</p>'
        sheets_html.append(f'<figure id="{anchor}">{img}<figcaption><strong>{escape(s["name"])}</strong>'
                           f'{extra}<span class="spacer"></span>{where}</figcaption></figure>')
    views_html = []
    for i, v in enumerate(idx.get("views", [])):
        anchor = f"view-{i}"
        path = _folder(v)
        view_nav.append((anchor, v["name"], path))
        where = f'<span class="spacer"></span><span class="muted">{escape(SEP.join(path))}</span>' if path else ""
        url = _file_url(idx, v["file"])
        views_html.append(f'<figure id="{anchor}"><a class="img" href="{url}" target="_blank">'
                          f'<img src="{url}" alt="{escape(v["name"])}" loading="lazy"></a>'
                          f'<figcaption><strong>{escape(v["name"])}</strong>{where}</figcaption></figure>')
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
    navs = _dropdown("View", view_nav) + _dropdown("Sheet", sheet_nav)
    header = (f'<a class="btn" href="/" title="All models">←</a>'
              f'<h1 title="{escape(idx["name"])}">{escape(idx["name"])}</h1>'
              + (f'<nav class="navs">{navs}</nav>' if navs else "")
              + f'<span class="spacer"></span><span class="meta">rendered {_when(idx)}</span>{pdf}')
    return _page(f'{idx["name"]} – {TITLE}', header, content, slug, bar=True, user=user)


COOKIE = "itz_viewer"


def _safe_next(value: str) -> str:
    """Only a path on this server (no //host, no /\\host, no scheme)."""
    return (value if value.startswith("/") and not value.startswith(("//", "/\\"))
            else "/")


LOGIN_CSS = """
.login{max-width:340px;margin:12vh auto;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:24px;box-shadow:var(--shadow)}
.login h1{font-size:18px;margin:0 0 16px}.login label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
.login input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg);font:inherit}
.login button,.login .go{display:block;text-align:center;margin-top:18px;width:100%;padding:9px;border:0;border-radius:6px;
background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
.login .go:hover{text-decoration:none}.login p{margin:0 0 8px}
.err{color:#c0392b;font-size:14px;margin:0 0 8px}
"""


def _card(title: str, inner: str) -> bytes:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} – {escape(TITLE)}</title><style>{CSS}{LOGIN_CSS}</style></head>
<body><main><div class="login"><h1>{escape(TITLE)}</h1>{inner}</div></main></body></html>""".encode("utf-8")


def login_page(error: str, nxt: str) -> bytes:
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return _card("Log in", f"""<form method="post" action="/login">{err}
<input type="hidden" name="next" value="{escape(nxt)}">
<label for="u">User</label><input id="u" name="user" autocomplete="username" value="{escape(auth.user_name)}">
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" autofocus>
<button type="submit">Log in</button></form>""")


def message_page(text: str, link: str = "", label: str = "", extra: str = "") -> bytes:
    go = f'<a class="go" href="{escape(link)}">{escape(label)}</a>' if link else ""
    return _card(label or "Viewer", f"<p>{escape(text)}</p>{extra}{go}")


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
    # Browsers get a login page (local) or the provider's (oidc), then a
    # session cookie: embedded browsers (VS Code's, many WebViews) never
    # show the Basic-auth dialog and only print the 401. Scripts and curl
    # use Basic auth (local). A proxy (proxy) names the user on every request.
    def _cookie_user(self) -> User | None:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = jar.get(COOKIE)
        return None if morsel is None else auth.sessions.read(morsel.value)

    def _basic_user(self) -> User | None:
        value = self.headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return None
        try:
            user, _, pw = base64.b64decode(value[6:]).decode().partition(":")
        except ValueError:
            return None
        return User(user, "admin") if auth.credentials_ok(user, pw) else None

    def _user(self) -> User | None:
        if auth.mode == "none":
            return User("anonymous")
        if auth.mode == "proxy":
            return auth.from_proxy(self.client_address[0], self.headers)
        user = self._cookie_user()
        if user is None and auth.mode == "local":
            user = self._basic_user()
        return user

    def _authorised(self, path: str) -> User | None:
        user = self._user()
        if user is not None:
            return user
        wants_html = "text/html" in self.headers.get("Accept", "")
        page = wants_html and not path.startswith(("/files/", "/events"))
        target = self.path if self.path.startswith("/") else "/"
        if auth.mode == "proxy":
            if page:
                self._html(401, message_page(
                    "This viewer is reached through its login proxy, which did "
                    "not name a user."))
            else:
                self._send(401, b"authentication required (through the proxy)\n",
                           "text/plain")
        elif page:
            where = "/login" if auth.mode == "local" else "/auth/login"
            self._send(303, b"", "text/plain",
                       {"Location": where + "?next=" + quote(target, safe="")})
        elif auth.mode == "local":
            self._send(401, b"authentication required\n", "text/plain",
                       {"WWW-Authenticate": f'Basic realm="{TITLE}", charset="UTF-8"'})
        else:
            self._send(401, b"authentication required\n", "text/plain")
        return None

    def _secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                or os.environ.get("VIEWER_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
                or (auth.mode == "oidc" and auth.oidc.redirect_uri.startswith("https://")))

    def _html(self, status: int, body: bytes, extra: dict | None = None):
        self._send(status, body, "text/html; charset=utf-8",
                   {"Cache-Control": "no-store", **(extra or {})})

    def _login_page(self, status: int = 200, error: str = "", nxt: str = "/"):
        self._html(status, login_page(error, nxt))

    def _session_cookie(self, user: User) -> str:
        return (f"{COOKIE}={auth.sessions.issue(user)}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={auth.sessions.max_age}" + ("; Secure" if self._secure() else ""))

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/login" or auth.mode != "local":
            return self._send(404, b"not found\n", "text/plain")
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        user = (form.get("user") or [""])[0]
        pw = (form.get("password") or [""])[0]
        nxt = _safe_next((form.get("next") or ["/"])[0])
        if auth.credentials_ok(user, pw):
            return self._send(303, b"", "text/plain", {
                "Location": nxt, "Set-Cookie": self._session_cookie(User(user, "admin"))})
        time.sleep(0.7)                          # slows down guessing
        return self._login_page(401, "Wrong user name or password.", nxt)

    def do_HEAD(self):
        self.do_GET()

    def _account_routes(self, path: str, query: dict) -> bool:
        """/login, /logout and the OIDC flow; True when one answered."""
        nxt = _safe_next((query.get("next") or ["/"])[0])
        clear = f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        if path == "/login":
            if auth.mode == "local":
                self._login_page(nxt=nxt)
            elif auth.mode == "oidc":
                self._send(303, b"", "text/plain",
                           {"Location": "/auth/login?" + urlencode({"next": nxt})})
            else:
                self._send(303, b"", "text/plain", {"Location": nxt})
            return True
        if path == "/logout":
            if auth.mode == "local":
                self._send(303, b"", "text/plain",
                           {"Location": "/login", "Set-Cookie": clear})
            elif auth.mode == "oidc":
                end = auth.oidc.end_session()
                extra = (f'<p class="muted"><a href="{escape(end)}?'
                         f'{escape(urlencode({"client_id": auth.oidc.client_id}))}">'
                         "Also log out of the identity provider</a></p>" if end else "")
                self._html(200, message_page("You are logged out of the viewer.",
                                             "/auth/login", "Log in again", extra),
                           {"Set-Cookie": clear})
            elif auth.mode == "proxy" and auth.proxy.logout_url:
                self._send(303, b"", "text/plain", {"Location": auth.proxy.logout_url})
            else:
                self._send(303, b"", "text/plain", {"Location": "/"})
            return True
        if auth.mode != "oidc" or path not in ("/auth/login", "/auth/callback"):
            return False
        try:
            if path == "/auth/login":
                self._send(303, b"", "text/plain", {"Location": auth.oidc.start(nxt)})
                return True
            if query.get("error"):
                raise ValueError(f"the provider said: {query['error'][0]} "
                                 + (query.get("error_description") or [""])[0])
            user, groups, nxt = auth.oidc.finish((query.get("code") or [""])[0],
                                                 (query.get("state") or [""])[0])
        except (OSError, ValueError) as exc:
            log(f"viewer: OIDC login failed: {exc}")
            self._html(502 if isinstance(exc, OSError) else 400, message_page(
                f"The login did not work: {exc}", "/auth/login", "Try again"))
            return True
        auth.logged_in(user, groups)
        self._send(303, b"", "text/plain",
                   {"Location": _safe_next(nxt), "Set-Cookie": self._session_cookie(user)})
        return True

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            return self._health()
        if self._account_routes(path, parse_qs(parts.query)):
            return None
        user = self._authorised(path)
        if user is None:
            return None
        html = "text/html; charset=utf-8"
        nocache = {"Cache-Control": "no-store"}
        if path == "/":
            return self._send(200, overview(user), html, nocache)
        if path.startswith("/m/"):
            slug = path[3:]
            page = model_page(slug, user) if _SAFE.match(slug) else None
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
    global renderer, auth
    auth = Auth()                    # a wrong setting stops here, before IngeTrazo
    app = App()
    renderer = Renderer(app)
    port = int(os.environ.get("VIEWER_PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    log(f"viewer on :{port} (auth: {auth.describe()}); cache {CACHE_DIR}")
    app.start(None, allow_save=False)
    renderer.run()


if __name__ == "__main__":
    main()
