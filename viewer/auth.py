# SPDX-License-Identifier: GPL-3.0-or-later
"""Who may see the viewer: ``VIEWER_AUTH_MODE`` (or ``AUTH_MODE``).

- ``local`` (default) – the built-in login: one user, ``VIEWER_USER`` /
  ``VIEWER_PASSWORD``. A login page and a session cookie for browsers,
  Basic auth for scripts. Without a password the viewer does not start.
- ``none``   – no login at all, on purpose; a warning in the log.
- ``oidc``   – OpenID Connect (Authentik, Keycloak, …): authorisation code
  flow with PKCE, then the viewer's own session cookie.
- ``proxy``  – a reverse proxy with forward auth (Traefik / nginx with
  Authentik) has logged the user in and names them in a header; only
  requests from ``PROXY_TRUSTED_IPS`` are believed.

``oidc`` and ``proxy`` keep a list of who logged in (``/state/users.json``):
a user is added on first login, with the role ``admin`` when in the admin
group, else ``viewer``. The viewer is read-only, so today both roles see
the same; the role is shown and kept for what may come.

The ID token comes straight from the provider's token endpoint over TLS,
with the client secret, so its signature is not checked (OpenID Connect
Core 3.1.3.7); issuer, audience, expiry and nonce are.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from mcp_http.appbridge import log

MODES = ("local", "none", "oidc", "proxy")
STATE_DIR = Path(os.environ.get("INGETRAZO_STATE", "/state"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class User:
    name: str
    role: str = "viewer"          # "admin" or "viewer"


class Sessions:
    """Signed session cookies: ``<payload>.<hmac>``. The key is new on every
    start, so a restart logs everyone out."""

    def __init__(self, days: int) -> None:
        self.key = secrets.token_bytes(32)
        self.max_age = days * 86400

    def _sig(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, user: User) -> str:
        payload = _b64(json.dumps({"u": user.name, "r": user.role,
                                   "e": int(time.time()) + self.max_age}).encode())
        return f"{payload}.{self._sig(payload)}"

    def read(self, value: str) -> User | None:
        payload, _, sig = value.partition(".")
        if not payload or not hmac.compare_digest(sig, self._sig(payload)):
            return None
        try:
            data = json.loads(_unb64(payload))
        except ValueError:
            return None
        if not isinstance(data, dict) or int(data.get("e", 0)) < time.time():
            return None
        return User(str(data.get("u", "")), str(data.get("r", "viewer")))


class UserStore:
    """Who logged in through ``oidc`` or ``proxy``, in ``users.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.seen: dict[str, float] = {}
        try:
            self.users = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            self.users = {}

    def record(self, name: str, role: str, groups: list[str], source: str) -> None:
        now = time.time()
        with self.lock:
            known = self.users.get(name)
            # A proxy names the user on every request: write at most every
            # ten minutes per user, and at once when something changed.
            if (known and known.get("role") == role
                    and known.get("groups") == groups
                    and now - self.seen.get(name, 0) < 600):
                return
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if known is None:
                log(f"viewer: new user {name!r} ({role}, via {source})")
                known = {"first_login": stamp}
            known.update(role=role, groups=groups, source=source, last_login=stamp)
            self.users[name] = known
            self.seen[name] = now
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".part")
                tmp.write_text(json.dumps(self.users, indent=1, ensure_ascii=False), "utf-8")
                tmp.replace(self.path)
            except OSError as exc:
                log(f"viewer: cannot write {self.path}: {exc}")


class OIDC:
    """The relying party of the authorisation code flow with PKCE."""

    def __init__(self) -> None:
        self.issuer = _env("OIDC_ISSUER_URL").rstrip("/")
        self.client_id = _env("OIDC_CLIENT_ID")
        self.client_secret = _env("OIDC_CLIENT_SECRET")
        self.redirect_uri = _env("OIDC_REDIRECT_URI")
        self.scopes = _env("OIDC_SCOPES") or "openid profile email"
        self.user_claim = _env("OIDC_USERNAME_CLAIM") or "preferred_username"
        self.groups_claim = _env("OIDC_GROUPS_CLAIM") or "groups"
        self.admin_group = _env("OIDC_ADMIN_GROUP")
        missing = [n for n, v in (("OIDC_ISSUER_URL", self.issuer),
                                  ("OIDC_CLIENT_ID", self.client_id),
                                  ("OIDC_CLIENT_SECRET", self.client_secret),
                                  ("OIDC_REDIRECT_URI", self.redirect_uri)) if not v]
        if missing:
            raise SystemExit("VIEWER_AUTH_MODE=oidc needs " + ", ".join(missing))
        if "openid" not in self.scopes.split():
            raise SystemExit("OIDC_SCOPES must include openid")
        if urlsplit(self.redirect_uri).path != "/auth/callback":
            raise SystemExit("OIDC_REDIRECT_URI must end in /auth/callback "
                             "(e.g. https://viewer.example.org/auth/callback)")
        insecure = [n for n, v in (("OIDC_ISSUER_URL", self.issuer),
                                   ("OIDC_REDIRECT_URI", self.redirect_uri))
                    if not v.startswith("https://")]
        if insecure and not _flag("OIDC_ALLOW_HTTP"):
            raise SystemExit(", ".join(insecure) + " must be https:// (the ID "
                             "token's signature is not checked; TLS is what "
                             "vouches for it). OIDC_ALLOW_HTTP=true for a lab.")
        self._meta: dict | None = None
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---- the provider ----------------------------------------------------------
    @staticmethod
    def _json(req: Request | str, timeout: float = 10.0) -> dict:
        with urlopen(req, timeout=timeout) as r:      # noqa: S310 — configured URL
            data = json.loads(r.read())
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return data

    def meta(self) -> dict:
        """The provider's discovery document, read on first use (the
        provider may start after the viewer)."""
        if self._meta is None:
            meta = self._json(self.issuer + "/.well-known/openid-configuration")
            for key in ("issuer", "authorization_endpoint", "token_endpoint"):
                if not meta.get(key):
                    raise ValueError(f"the discovery document has no {key}")
            if meta["issuer"].rstrip("/") != self.issuer:
                raise ValueError(f"issuer mismatch: {meta['issuer']!r}")
            self._meta = meta
        return self._meta

    # ---- the flow ------------------------------------------------------------------
    def start(self, nxt: str) -> str:
        """Where to send the browser to log in."""
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        now = time.time()
        with self._lock:
            for k in [k for k, v in self._pending.items() if v["t"] < now - 600]:
                del self._pending[k]
            if len(self._pending) > 1000:          # a flood of unfinished logins
                self._pending.clear()
            self._pending[state] = {"nonce": nonce, "verifier": verifier,
                                    "next": nxt, "t": now}
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        return self.meta()["authorization_endpoint"] + "?" + urlencode({
            "response_type": "code", "client_id": self.client_id,
            "redirect_uri": self.redirect_uri, "scope": self.scopes,
            "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256"})

    def finish(self, code: str, state: str) -> tuple[User, list[str], str]:
        """The callback: (user, groups, where to go next). ValueError when
        anything does not add up."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or pending["t"] < time.time() - 600:
            raise ValueError("unknown or expired login; start again")
        meta = self.meta()
        basic = base64.b64encode(
            f"{_quote(self.client_id)}:{_quote(self.client_secret)}".encode()).decode()
        tokens = self._json(Request(
            meta["token_endpoint"],
            data=urlencode({"grant_type": "authorization_code", "code": code,
                            "redirect_uri": self.redirect_uri,
                            "code_verifier": pending["verifier"]}).encode(),
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"}))
        claims = self._id_claims(tokens.get("id_token", ""), pending["nonce"])
        if (self.user_claim not in claims or self.groups_claim not in claims) \
                and tokens.get("access_token") and meta.get("userinfo_endpoint"):
            info = self._json(Request(meta["userinfo_endpoint"], headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Accept": "application/json"}))
            if info.get("sub") == claims.get("sub"):
                claims = {**info, **claims}
        name = str(claims.get(self.user_claim) or claims.get("email")
                   or claims.get("sub") or "")
        if not name:
            raise ValueError("the provider named no user")
        groups = claims.get(self.groups_claim) or []
        groups = [str(g) for g in groups] if isinstance(groups, list) else [str(groups)]
        role = "admin" if self.admin_group and self.admin_group in groups else "viewer"
        return User(name, role), groups, pending["next"]

    def _id_claims(self, token: str, nonce: str) -> dict:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("no ID token")
        claims = json.loads(_unb64(parts[1]))
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if claims.get("iss", "").rstrip("/") != self.issuer:
            raise ValueError("ID token from another issuer")
        if self.client_id not in auds or (len(auds) > 1 and claims.get("azp") != self.client_id):
            raise ValueError("ID token for another client")
        if int(claims.get("exp", 0)) < time.time() - 60:
            raise ValueError("ID token expired")
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError("ID token nonce mismatch")
        return claims

    def end_session(self) -> str | None:
        try:
            return self.meta().get("end_session_endpoint") or None
        except (OSError, ValueError):
            return None


def _quote(text: str) -> str:
    """client_secret_basic form-encodes id and secret (RFC 6749 2.3.1)."""
    from urllib.parse import quote_plus
    return quote_plus(text)


class Proxy:
    """Forward auth: the user (and groups) a trusted proxy puts in headers."""

    def __init__(self) -> None:
        self.user_header = _env("PROXY_USER_HEADER") or "X-authentik-username"
        self.groups_header = _env("PROXY_GROUPS_HEADER")
        self.admin_group = _env("PROXY_ADMIN_GROUP")
        self.logout_url = _env("PROXY_LOGOUT_URL")
        raw = [p.strip() for p in _env("PROXY_TRUSTED_IPS").split(",") if p.strip()]
        if not raw:
            raise SystemExit("VIEWER_AUTH_MODE=proxy needs PROXY_TRUSTED_IPS: the "
                             "proxy's address(es) or networks, e.g. 172.18.0.0/16 — "
                             "a user header from anywhere else is ignored")
        try:
            self.trusted = [ipaddress.ip_network(p, strict=False) for p in raw]
        except ValueError as exc:
            raise SystemExit(f"PROXY_TRUSTED_IPS: {exc}") from None

    def trusts(self, addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in net for net in self.trusted)

    def user(self, addr: str, headers) -> tuple[User, list[str]] | None:
        if not self.trusts(addr):
            return None
        name = (headers.get(self.user_header) or "").strip()
        if not name:
            return None
        groups: list[str] = []
        if self.groups_header:
            raw = headers.get(self.groups_header) or ""
            sep = "|" if "|" in raw else ","        # Authentik joins with "|"
            groups = [g.strip() for g in raw.split(sep) if g.strip()]
        role = "admin" if self.admin_group and self.admin_group in groups else "viewer"
        return User(name, role), groups


class Auth:
    def __init__(self) -> None:
        raw = _env("VIEWER_AUTH_MODE") or _env("AUTH_MODE") or "local"
        self.mode = raw.lower()
        if self.mode not in MODES:
            raise SystemExit(f"VIEWER_AUTH_MODE must be one of {', '.join(MODES)}, "
                             f"not {raw!r}")
        self.sessions = Sessions(int(_env("VIEWER_SESSION_DAYS") or 30))
        self.user_name = _env("VIEWER_USER") or "viewer"
        self.password = os.environ.get("VIEWER_PASSWORD", "")
        self.oidc = OIDC() if self.mode == "oidc" else None
        self.proxy = Proxy() if self.mode == "proxy" else None
        self.users = (UserStore(STATE_DIR / "users.json")
                      if self.mode in ("oidc", "proxy") else None)
        if self.mode == "local" and not self.password:
            raise SystemExit(
                "the viewer's login (VIEWER_AUTH_MODE=local, the default) needs "
                "VIEWER_PASSWORD. For a viewer without any login, say so: "
                "VIEWER_AUTH_MODE=none")
        if self.mode == "none":
            log("WARNING: VIEWER_AUTH_MODE=none — anyone who reaches the viewer "
                "sees every model in the data folder. Use it only on a trusted "
                "network or for a public showcase.")

    def describe(self) -> str:
        if self.mode == "oidc":
            return f"oidc, issuer {self.oidc.issuer}"
        if self.mode == "proxy":
            return (f"proxy, user from {self.proxy.user_header}, trusted "
                    + ", ".join(str(n) for n in self.proxy.trusted))
        return self.mode

    def credentials_ok(self, user: str, pw: str) -> bool:
        return (self.mode == "local"
                and hmac.compare_digest(user.encode(), self.user_name.encode())
                & hmac.compare_digest(pw.encode(), self.password.encode()))

    def from_proxy(self, addr: str, headers) -> User | None:
        found = self.proxy.user(addr, headers)
        if found is None:
            return None
        user, groups = found
        self.users.record(user.name, user.role, groups, "proxy")
        return user

    def logged_in(self, user: User, groups: list[str]) -> None:
        if self.users is not None:
            self.users.record(user.name, user.role, groups, self.mode)
