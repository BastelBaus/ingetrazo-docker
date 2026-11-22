# SPDX-License-Identifier: GPL-3.0-or-later
"""Who may call the MCP endpoint.

``MCP_AUTH``:

- ``none``  – anyone who reaches the port. Only for a trusted machine or LAN:
  ``run_python`` runs arbitrary Python inside the container.
- ``token`` – ``Authorization: Bearer <MCP_TOKEN>`` (Claude Code, Claude
  Desktop via mcp-remote, Cursor, VS Code, …).
- ``oauth`` – a minimal OAuth 2.1 authorisation server for remote
  connectors such as claude.ai (see oauth.py).
"""
from __future__ import annotations

import hmac
import os

from .appbridge import log

MODES = ("none", "token", "oauth")


class Auth:
    def __init__(self) -> None:
        self.mode = os.environ.get("MCP_AUTH", "token").strip().lower() or "token"
        if self.mode not in MODES:
            raise SystemExit(f"MCP_AUTH must be one of {', '.join(MODES)}, "
                             f"not {self.mode!r}")
        self.token = os.environ.get("MCP_TOKEN", "").strip()
        self.oauth = None
        if self.mode == "token" and len(self.token) < 16:
            raise SystemExit("MCP_AUTH=token needs MCP_TOKEN with at least 16 "
                             "characters (e.g. `openssl rand -hex 32`)")
        if self.mode == "oauth":
            from .oauth import OAuthServer
            self.oauth = OAuthServer()
        if self.mode == "none":
            log("WARNING: MCP_AUTH=none — anyone who reaches this port can run "
                "arbitrary Python in the container (run_python). Use it only "
                "on a trusted machine or network.")

    def check(self, headers) -> bool:
        if self.mode == "none":
            return True
        value = headers.get("Authorization", "")
        if not value.lower().startswith("bearer "):
            return False
        presented = value[7:].strip()
        if self.mode == "token":
            return hmac.compare_digest(presented.encode(), self.token.encode())
        return self.oauth.valid_access_token(presented)

    def challenge(self, base_url: str) -> str:
        """The WWW-Authenticate header of a 401."""
        if self.mode == "oauth":
            return ('Bearer resource_metadata="'
                    f'{base_url}/.well-known/oauth-protected-resource"')
        return 'Bearer realm="ingetrazo"'
