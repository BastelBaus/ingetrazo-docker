# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which folders a user may see and write: their shares.

A share is a path in the data folder – a model folder (``projects``) or
a folder inside one (``projects/2026``) – and an access: ``r`` (see the
models), ``rw`` (also save and export into it, MCP only) or ``none``
(carve a folder out of a wider share). ``*`` stands for every folder.
The most specific share that covers a path decides; without one, there
is no access. A read-only model folder (``MODEL_FOLDERS``) allows ``r``
at most, whatever the share says.

Admins have ``rw`` everywhere. Every other user has the shares an admin
gave them in the admin panel; until then, ``DEFAULT_SHARES`` (default
``*:r``: every folder, read). The guest is a user like the others.

``DEFAULT_SHARES`` is a list like ``MODEL_FOLDERS``: ``path:access``,
separated by ``;``, e.g. ``*:none; projects/public:r``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import folders

ACCESS = ("none", "r", "rw")
ALL = "*"
_RANK = {a: n for n, a in enumerate(ACCESS)}


def clean_path(path: str) -> str:
    """A share's path as stored: /-separated from the data folder, or ``*``."""
    path = (path or "").strip().replace("\\", "/").strip("/")
    if path in ("", ALL):
        return ALL
    parts = path.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        raise ValueError(f"not a folder path: {path!r}")
    return "/".join(parts)


def parse(spec: str) -> list[dict]:
    """``path:access; …`` as a list of shares."""
    out: list[dict] = []
    for entry in re.split(r"[;\n]", spec or ""):
        entry = entry.strip()
        if not entry:
            continue
        path, _, access = entry.rpartition(":")
        if not path:
            path, access = entry, "r"
        access = access.strip().lower()
        if access not in ACCESS:
            raise ValueError(f"{entry!r}: the access is one of {', '.join(ACCESS)}")
        out.append({"path": clean_path(path), "access": access})
    return out


def defaults() -> list[dict]:
    try:
        return parse(os.environ.get("DEFAULT_SHARES", f"{ALL}:r"))
    except ValueError as exc:
        raise SystemExit(f"[ingetrazo-docker] DEFAULT_SHARES: {exc}") from None


def validate(shares: list) -> list[dict]:
    """Shares as an admin sent them: cleaned, one per path (the last wins)."""
    seen: dict[str, str] = {}
    for s in shares or []:
        if not isinstance(s, dict) or s.get("access") not in ACCESS:
            raise ValueError("a share is a path and one of " + ", ".join(ACCESS))
        seen[clean_path(str(s.get("path", "")))] = s["access"]
    return [{"path": p, "access": a} for p, a in sorted(seen.items())]


def _covers(share: str, rel: str) -> bool:
    return share == ALL or rel == share or rel.startswith(share + "/")


def access(shares: list[dict], rel: str) -> str:
    """What ``shares`` allow on ``rel`` (a path from the data folder):
    the most specific share decides."""
    best, depth = "none", -1
    for s in shares:
        if _covers(s["path"], rel):
            d = 0 if s["path"] == ALL else s["path"].count("/") + 1
            if d >= depth:
                best, depth = s["access"], d
    return best


def effective(account, rel: str) -> str:
    """What a user (``mcp_http.users.Account``, None: nobody) may do with
    ``rel``, capped by the model folder's mode."""
    if account is None or not account.active:
        return "none"
    got = "rw" if account.admin else access(
        account.shares if account.shares is not None else defaults(), rel)
    p = (folders.DATA_DIR / rel).resolve()
    folder = folders.folder_of(p)
    if folder is None:
        return "none"
    if got == "rw" and not folder.writable:
        got = "r"
    return got


def can_read(account, rel: str) -> bool:
    return _RANK[effective(account, rel)] >= _RANK["r"]


def can_write(account, rel: str) -> bool:
    return effective(account, rel) == "rw"


def tree(max_depth: int = 3, limit: int = 400) -> list[str]:
    """The folders an admin may share: each model folder and the folders
    inside it, ``max_depth`` levels deep, as paths from the data folder
    (``""`` is the data folder itself when it is the one model folder)."""
    out: list[str] = []
    for f in folders.FOLDERS:
        base = folders.display_name(f.root) if f.root != folders.DATA_DIR else ""
        if base:
            out.append(base)
        if not f.root.is_dir():
            continue
        for here, dirs, _ in os.walk(f.root):
            rel_dir = Path(here).relative_to(f.root).as_posix()
            level = 0 if rel_dir == "." else rel_dir.count("/") + 1
            prefix = "" if rel_dir == "." else rel_dir + "/"
            dirs[:] = sorted(d for d in dirs if not f.excluded(prefix + d))
            if level + 1 >= max_depth:
                for d in dirs:
                    out.append(folders.display_name(Path(here) / d))
                dirs[:] = []
            else:
                out.extend(folders.display_name(Path(here) / d) for d in dirs)
            if len(out) >= limit:
                return out[:limit]
    return out
