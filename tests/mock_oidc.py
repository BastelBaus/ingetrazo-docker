# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tiny OpenID Connect provider for the smoke test – never for real use.

    mock_oidc.py <issuer-url> <port> <client-id> <client-secret>

``/authorize`` logs in "alice" (groups: viewer-admins) at once and sends the
browser back with a code. ``/token`` checks the client secret, the code,
the redirect URI and the PKCE verifier (S256), and returns an ID token
without groups, so the viewer has to ask ``/userinfo`` for them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

ISSUER, PORT, CLIENT, SECRET = sys.argv[1].rstrip("/"), int(sys.argv[2]), sys.argv[3], sys.argv[4]
CODES: dict[str, dict] = {}
TOKENS: dict[str, str] = {}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[oidc] " + fmt % args + "\n")

    def _json(self, status: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/.well-known/openid-configuration":
            return self._json(200, {
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "userinfo_endpoint": ISSUER + "/userinfo",
                "end_session_endpoint": ISSUER + "/end-session"})
        if url.path == "/authorize":
            if (q.get("client_id") != CLIENT or q.get("response_type") != "code"
                    or q.get("code_challenge_method") != "S256"
                    or "openid" not in q.get("scope", "").split()):
                return self._json(400, {"error": "invalid_request"})
            code = secrets.token_urlsafe(16)
            CODES[code] = q
            self.send_response(302)
            self.send_header("Location", q["redirect_uri"] + "?" + urlencode(
                {"code": code, "state": q["state"]}))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if url.path == "/userinfo":
            token = self.headers.get("Authorization", "")[7:]
            if token not in TOKENS:
                return self._json(401, {"error": "invalid_token"})
            return self._json(200, {"sub": TOKENS[token], "preferred_username": "alice",
                                    "groups": ["staff", "viewer-admins"]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlsplit(self.path).path != "/token":
            return self._json(404, {"error": "not found"})
        auth = self.headers.get("Authorization", "")
        if auth != "Basic " + base64.b64encode(f"{CLIENT}:{SECRET}".encode()).decode():
            return self._json(401, {"error": "invalid_client"})
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(
            int(self.headers.get("Content-Length") or 0)).decode()).items()}
        asked = CODES.pop(form.get("code", ""), None)
        if (asked is None or form.get("grant_type") != "authorization_code"
                or form.get("redirect_uri") != asked["redirect_uri"]
                or b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
                != asked["code_challenge"]):
            return self._json(400, {"error": "invalid_grant"})
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT, "sub": "u-alice", "iat": now,
                  "exp": now + 300, "nonce": asked["nonce"]}
        head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        id_token = f"{head}.{b64(json.dumps(claims).encode())}.{b64(b'not-checked')}"
        access = secrets.token_urlsafe(16)
        TOKENS[access] = "u-alice"
        return self._json(200, {"access_token": access, "token_type": "Bearer",
                                "expires_in": 300, "id_token": id_token})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
