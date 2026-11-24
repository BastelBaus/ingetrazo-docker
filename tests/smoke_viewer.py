# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke test of the viewer: smoke_viewer.py <base-url> <user> <password>

Runs after smoke_mcp.py, which saved smoke-saved.igz into the shared data
folder: the viewer must pick that file up by itself (live update)."""
from __future__ import annotations

import base64
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE, USER, PASSWORD = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)


def get(path, headers=None, data=None, timeout=60):
    req = urllib.request.Request(BASE + path, data, headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


BASIC = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()}


def check(label, fn):
    t0 = time.monotonic()
    fn()
    print(f"  ok  {label} ({time.monotonic() - t0:.1f}s)")


def wait_for(pred, what, timeout=240):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            got = pred()
            if got:
                return got
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"viewer: timed out waiting for {what}")


def model_page(name):
    status, _, body = get("/", BASIC)
    if status != 200:
        return None
    html = body.decode()
    m = re.search(r'href="(/m/[^"]+)"><div class="thumb">.*?class="name">'
                  + re.escape(name) + "<", html, re.S)
    if not m:
        return None
    status, _, page = get(m.group(1), BASIC)
    page = page.decode()
    return page if status == 200 and "/files/" in page else None


def main():
    print("== viewer")
    check("healthz", lambda: wait_for(
        lambda: get("/healthz")[0] == 200, "healthz"))

    def auth():
        status, headers, _ = get("/", {"Accept": "text/html"})
        assert status == 303 and headers["Location"].startswith("/login"), status
        status, _, _ = get("/")
        assert status == 401, status
        status, _, _ = get("/login", data=urllib.parse.urlencode(
            {"user": USER, "password": "wrong", "next": "/"}).encode())
        assert status == 401, status
        status, headers, _ = get("/login", data=urllib.parse.urlencode(
            {"user": USER, "password": PASSWORD, "next": "//evil.example"}).encode())
        assert status == 303 and headers["Location"] == "/", (status, headers.get("Location"))
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, _ = get("/", {"Cookie": cookie})
        assert status == 200, status
    check("login page, cookie, basic auth, safe redirect", auth)

    def rendered():
        page = wait_for(lambda: model_page("arco.igz"), "arco.igz to render")
        files = re.findall(r'/files/[^"]+\.(?:png|pdf)', page)
        assert any(f.endswith(".pdf") for f in files), files
        png = next(f for f in files if f.endswith(".png"))
        status, headers, body = get(png, BASIC)
        assert status == 200 and headers["Content-Type"] == "image/png" \
            and body.startswith(b"\x89PNG"), status
    check("arco.igz: sheets, views and PDF rendered", rendered)

    def one_at_a_time():
        page = model_page("arco.igz")
        assert page.count("<figure") == 1, page.count("<figure")
        links = re.findall(r'<a href="\?(sheet|view)=(\d+)"', page)
        assert links, "no sheet/view links in the top bar"
        slug = re.search(r'data-slug="([^"]+)"', page).group(1)
        for kind, n in links:
            status, _, body = get(f"/m/{slug}?{kind}={n}", BASIC)
            body = body.decode()
            assert status == 200 and body.count("<figure") == 1, (kind, n, status)
            assert f'href="?{kind}={n}" class="on"' in body, (kind, n)
    check("model page shows one sheet or view at a time", one_at_a_time)

    check("picks up the model saved over MCP", lambda: wait_for(
        lambda: model_page("smoke-saved.igz"), "smoke-saved.igz to render"))
    print("== viewer: ok")


if __name__ == "__main__":
    main()
