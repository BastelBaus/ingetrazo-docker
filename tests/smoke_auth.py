# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke test of the viewer's VIEWER_AUTH_MODE settings.

    smoke_auth.py none            <viewer-url>
    smoke_auth.py proxy-untrusted <viewer-url>
    smoke_auth.py proxy-trusted   <viewer-url>        (from the trusted address)
    smoke_auth.py oidc            <viewer-url> <provider-url-from-here>
"""
from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

HTML = {"Accept": "text/html"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)


def get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, None, headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def check(label, fn):
    t0 = time.monotonic()
    fn()
    print(f"  ok  {label} ({time.monotonic() - t0:.1f}s)")


def wait_healthy(base):
    end = time.monotonic() + 240
    while time.monotonic() < end:
        try:
            if get(base + "/healthz", timeout=10)[0] == 200:
                return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"{base} did not become healthy")


def none(base):
    print("== viewer, VIEWER_AUTH_MODE=none")
    check("healthz", lambda: wait_healthy(base))

    def open_access():
        status, _, page = get(base + "/", HTML)
        assert status == 200, status
        assert "/logout" not in page, "a Log out button without a login"
        status, headers, _ = get(base + "/login?next=/m/x")
        assert status == 303 and headers["Location"] == "/m/x", (status, headers)
    check("open without login, no Log out, /login goes on", open_access)


def proxy_untrusted(base):
    print("== viewer, VIEWER_AUTH_MODE=proxy (from an untrusted address)")
    check("healthz", lambda: wait_healthy(base))

    def ignored():
        status, _, _ = get(base + "/", {"X-authentik-username": "mallory"})
        assert status == 401, status
        status, _, page = get(base + "/", {**HTML, "X-authentik-username": "mallory"})
        assert status == 401 and "proxy" in page, status
    check("the user header from an untrusted address is ignored", ignored)


def proxy_trusted(base):
    def named():
        status, _, page = get(base + "/", {**HTML, "X-authentik-username": "bob",
                                           "X-authentik-groups": "staff|viewer-admins"})
        assert status == 200, status
        assert 'title="bob (admin)"' in page, "the user and role are not shown"
        status, _, _ = get(base + "/", HTML)
        assert status == 401, status
    check("proxy: the user from the trusted proxy's header, role from groups", named)


def oidc(base, provider):
    print("== viewer, VIEWER_AUTH_MODE=oidc (mock provider)")
    check("healthz", lambda: wait_healthy(base))

    def to_here(url):
        """The provider's URL as the viewer knows it → as this script reaches it."""
        p, here = urlsplit(url), urlsplit(provider)
        return urlunsplit((here.scheme, here.netloc, p.path, p.query, ""))

    def redirected():
        status, headers, _ = get(base + "/m/arco", HTML)
        assert status == 303 and headers["Location"].startswith("/auth/login?next="), status
        status, _, _ = get(base + "/files/a/b/c.png")
        assert status == 401, status
    check("a page goes to /auth/login, a file is a 401", redirected)

    def login():
        status, headers, _ = get(base + "/auth/login?next=%2Fm%2Farco")
        assert status == 303, status
        authorize = headers["Location"]
        assert "code_challenge_method=S256" in authorize and "nonce=" in authorize, authorize
        status, headers, _ = get(to_here(authorize))
        assert status == 302, status
        callback = headers["Location"]
        # The same code with a forged state is refused.
        forged = re.sub(r"state=[^&]+", "state=forged", callback)
        assert get(forged)[0] == 400
        status, headers, _ = get(callback)
        assert status == 303 and headers["Location"] == "/m/arco", (status, headers)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, page = get(base + "/", {**HTML, "Cookie": cookie})
        assert status == 200 and 'title="alice (admin)"' in page, status
        status, headers, page = get(base + "/logout", {"Cookie": cookie})
        assert status == 200 and "Max-Age=0" in headers["Set-Cookie"], status
        assert "/end-session" in page, "no link to the provider's logout"
    check("login with PKCE, forged state refused, user and role, logout", login)


if __name__ == "__main__":
    mode, args = sys.argv[1], [a.rstrip("/") for a in sys.argv[2:]]
    {"none": none, "proxy-untrusted": proxy_untrusted,
     "proxy-trusted": proxy_trusted, "oidc": oidc}[mode](*args)
