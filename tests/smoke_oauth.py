# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke test of MCP_AUTH=oauth: smoke_oauth.py <base-url> <user> <password>

The flow a remote connector goes through: the 401 challenge, both metadata
documents, dynamic registration, the consent page, the code with PKCE,
the token, tools/list, and refresh-token rotation."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

BASE, USER, PASSWORD = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
REDIRECT = "http://localhost:6274/oauth/callback"      # the MCP Inspector's


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)


def http(path, data=None, headers=None, timeout=60):
    if isinstance(data, dict):
        data = urlencode(data).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    req = urllib.request.Request(path if path.startswith("http") else BASE + path,
                                 data, headers or {})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jpost(path, obj, headers=None):
    return http(path, json.dumps(obj).encode(),
                {"Content-Type": "application/json", **(headers or {})})


def check(label, fn):
    t0 = time.monotonic()
    fn()
    print(f"  ok  {label} ({time.monotonic() - t0:.1f}s)")


def wait_healthy():
    end = time.monotonic() + 240
    while time.monotonic() < end:
        try:
            status, _, body = http("/healthz", timeout=15)
            if status == 200 and json.loads(body).get("status") == "ok":
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise SystemExit("MCP service (oauth) did not become healthy")


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def code_for(client_id, challenge, state="st-1"):
    """Through the consent page: the code the client gets back."""
    q = urlencode({"response_type": "code", "client_id": client_id,
                   "redirect_uri": REDIRECT, "code_challenge": challenge,
                   "code_challenge_method": "S256", "state": state,
                   "scope": "mcp", "resource": BASE + "/mcp"})
    status, headers, page = http("/authorize?" + q)
    assert status == 200 and headers.get("X-Frame-Options") == "DENY", status
    tx = re.search(rb'name="tx" value="([^"]+)"', page).group(1).decode()
    status, headers, _ = http("/authorize", {"tx": tx, "user": USER,
                                             "password": PASSWORD, "decision": "allow"})
    assert status == 302, status
    back = urlsplit(headers["Location"])
    params = {k: v[0] for k, v in parse_qs(back.query).items()}
    assert back.netloc == "localhost:6274" and params["state"] == state, headers["Location"]
    assert params["iss"] == BASE, params
    return params["code"]


def tools_list(token):
    return jpost("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                 {"Authorization": f"Bearer {token}",
                  "Accept": "application/json, text/event-stream"})


def main():
    print("== MCP service, MCP_AUTH=oauth")
    check("healthz", wait_healthy)
    meta = {}

    def challenge():
        status, headers, _ = jpost("/mcp", {"jsonrpc": "2.0", "id": 0, "method": "ping"})
        assert status == 401, status
        m = re.search(r'resource_metadata="([^"]+)"', headers.get("WWW-Authenticate", ""))
        assert m and m.group(1) == BASE + "/.well-known/oauth-protected-resource", headers
        meta["resource"] = m.group(1)
    check("401 with the resource metadata in WWW-Authenticate", challenge)

    def metadata():
        status, _, body = http(meta["resource"])
        prm = json.loads(body)
        assert status == 200 and prm["resource"] == BASE + "/mcp", prm
        assert prm["authorization_servers"] == [BASE], prm
        status, headers, body = http("/.well-known/oauth-authorization-server")
        asm = json.loads(body)
        assert status == 200 and asm["issuer"] == BASE, asm
        assert asm["code_challenge_methods_supported"] == ["S256"], asm
        assert asm["registration_endpoint"] == BASE + "/register", asm
        assert headers.get("Access-Control-Allow-Origin") == "*", headers
        req = urllib.request.Request(BASE + "/register", method="OPTIONS")
        with opener.open(req, timeout=15) as r:
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "*"
    check("protected resource (RFC 9728) and server (RFC 8414) metadata, CORS", metadata)

    client = {}

    def register():
        for bad in ("https://evil.example/cb", "http://localhost:1@evil.example/cb",
                    "https://claude.ai.evil.example/api/mcp/auth_callback"):
            status, _, body = jpost("/register", {"redirect_uris": [bad]})
            assert status == 400 and json.loads(body)["error"] == "invalid_redirect_uri", (bad, status)
        status, _, body = jpost("/register", {"redirect_uris": [REDIRECT],
                                              "client_name": "Smoke",
                                              "token_endpoint_auth_method": "none"})
        reg = json.loads(body)
        assert status == 201 and reg["client_id"] and "client_secret" not in reg, reg
        client["id"] = reg["client_id"]
    check("dynamic registration (RFC 7591), redirect URIs off the list refused", register)

    def refusals():
        q = urlencode({"response_type": "code", "client_id": client["id"],
                       "redirect_uri": REDIRECT, "state": "x"})
        status, headers, _ = http("/authorize?" + q)
        assert status == 302 and "error=invalid_request" in headers["Location"], status
        status, _, _ = http("/authorize?" + urlencode({"client_id": "nope", "redirect_uri": REDIRECT}))
        assert status == 400, status
        _, challenge = pkce()
        q = urlencode({"response_type": "code", "client_id": client["id"],
                       "redirect_uri": REDIRECT, "code_challenge": challenge,
                       "code_challenge_method": "S256"})
        tx = re.search(rb'name="tx" value="([^"]+)"', http("/authorize?" + q)[2]).group(1).decode()
        status, _, page = http("/authorize", {"tx": tx, "user": USER, "password": "wrong",
                                              "decision": "allow"})
        assert status == 401 and b"Wrong user name or password" in page, status
        status, headers, _ = http("/authorize", {"tx": tx, "decision": "deny"})
        assert status == 302 and "error=access_denied" in headers["Location"], status
    check("no PKCE, unknown client, wrong password, deny", refusals)

    tokens = {}

    def flow():
        verifier, challenge = pkce()
        code = code_for(client["id"], challenge)
        status, _, body = http("/token", {"grant_type": "authorization_code", "code": code,
                                          "client_id": client["id"], "redirect_uri": REDIRECT,
                                          "code_verifier": "wrong-" + verifier})
        assert status == 400 and json.loads(body)["error"] == "invalid_grant", status
        verifier, challenge = pkce()
        code = code_for(client["id"], challenge, "st-2")
        status, headers, body = http("/token", {"grant_type": "authorization_code",
                                                "code": code, "client_id": client["id"],
                                                "redirect_uri": REDIRECT,
                                                "code_verifier": verifier})
        tok = json.loads(body)
        assert status == 200 and tok["token_type"] == "Bearer" and tok["refresh_token"], tok
        assert headers.get("Cache-Control") == "no-store", headers
        again = http("/token", {"grant_type": "authorization_code", "code": code,
                                "client_id": client["id"], "redirect_uri": REDIRECT,
                                "code_verifier": verifier})
        assert again[0] == 400, "a code worked twice"
        tokens.update(tok)
    check("consent, code with PKCE (a wrong verifier refused), token, code once only", flow)

    def use():
        status, _, body = tools_list(tokens["access_token"])
        assert status == 200 and json.loads(body)["result"]["tools"], status
        assert tools_list("not-a-token")[0] == 401
    check("tools/list with the access token; a bad token is a 401", use)

    def refresh():
        old = tokens["refresh_token"]
        status, _, body = http("/token", {"grant_type": "refresh_token", "refresh_token": old,
                                          "client_id": client["id"]})
        new = json.loads(body)
        assert status == 200 and new["refresh_token"] != old, new
        assert tools_list(new["access_token"])[0] == 200
        status, _, _ = http("/token", {"grant_type": "refresh_token", "refresh_token": old,
                                       "client_id": client["id"]})
        assert status == 400, "a refresh token worked twice"
    check("refresh token rotation", refresh)

    def confidential():
        status, _, body = jpost("/register", {"redirect_uris": [REDIRECT],
                                              "client_name": "Smoke (secret)"})
        reg = json.loads(body)
        assert status == 201 and reg["client_secret"], reg
        verifier, challenge = pkce()
        code = code_for(reg["client_id"], challenge, "st-3")
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": REDIRECT, "code_verifier": verifier}
        basic = base64.b64encode(f"{reg['client_id']}:wrong".encode()).decode()
        assert http("/token", form, {"Authorization": f"Basic {basic}"})[0] == 401
        code = code_for(reg["client_id"], challenge, "st-4")
        form["code"] = code
        basic = base64.b64encode(f"{reg['client_id']}:{reg['client_secret']}".encode()).decode()
        status, _, body = http("/token", form, {"Authorization": f"Basic {basic}"})
        assert status == 200 and json.loads(body)["access_token"], status
    check("confidential client: client_secret_basic, a wrong secret refused", confidential)
    print("== MCP service, oauth: ok")


if __name__ == "__main__":
    main()
