# SPDX-License-Identifier: GPL-3.0-or-later
"""The users of the container, in one SQLite file (``USERS_DB``, default
``/state/users.db``). compose.yaml puts it on a volume both services
mount, so the viewer and the MCP server share one list.

A user has a name, a role (``admin`` or ``user``), where they come from
(``local``: a password kept here; ``oidc`` / ``proxy``: logged in
elsewhere, recorded on first login), whether they are active, and the
settings they work with (``SETTINGS``, kept as they change them). Admins
manage the list in the viewer's admin panel (``/admin``).

Passwords are stored as scrypt hashes. The schema carries its version in
``PRAGMA user_version``; newer versions add columns (``MIGRATIONS``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("admin", "user")
SOURCES = ("local", "oidc", "proxy")
#: A name: letters, digits and . _ - @ (e-mail addresses from a provider).
NAME = re.compile(r"^[\w.@+-]{1,64}$", re.UNICODE)
MIN_PASSWORD = 8

#: Each entry upgrades the schema by one version.
MIGRATIONS: list[str] = [
    """CREATE TABLE users (
         name TEXT PRIMARY KEY COLLATE NOCASE,
         role TEXT NOT NULL DEFAULT 'user',
         source TEXT NOT NULL DEFAULT 'local',
         password TEXT,
         active INTEGER NOT NULL DEFAULT 1,
         groups TEXT NOT NULL DEFAULT '[]',
         created TEXT NOT NULL,
         last_login TEXT
       )""",
    # 2: the settings a user works with (SETTINGS), as JSON.
    "ALTER TABLE users ADD COLUMN config TEXT NOT NULL DEFAULT '{}'",
]

#: The settings a user keeps (the viewer's and later the MCP server's):
#: name → (allowed values, default). The default ``None`` means "as the
#: container is set up" (an environment variable).
SETTINGS: dict[str, tuple[tuple, object]] = {
    # The View / 3D / Sheet dropdowns: every folder level, or one at a time.
    "nav_mode": (("list", "tree"), None),
    # The 3D view draws the edges as lines.
    "edges_3d": ((True, False), True),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n, r, p, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def check_password(password: str, stored: str | None) -> bool:
    try:
        kind, n, r, p, salt, digest = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


@dataclass
class Account:
    name: str
    role: str = "user"
    source: str = "local"
    active: bool = True
    has_password: bool = False
    groups: list[str] = field(default_factory=list)
    created: str = ""
    last_login: str | None = None
    config: dict = field(default_factory=dict)

    @property
    def admin(self) -> bool:
        return self.role == "admin"


class UserError(ValueError):
    """A change the list does not allow; the message says why."""


class Users:
    def __init__(self, path: Path | str | None = None) -> None:
        state = Path(os.environ.get("INGETRAZO_STATE", "/state"))
        self.path = Path(path or os.environ.get("USERS_DB", "").strip()
                         or state / "users.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False,
                                   isolation_level=None, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            for n, sql in enumerate(MIGRATIONS[version:], start=version + 1):
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    # Another container may have migrated meanwhile.
                    if self._db.execute("PRAGMA user_version").fetchone()[0] >= n:
                        self._db.execute("ROLLBACK")
                        continue
                    self._db.execute(sql)
                    self._db.execute(f"PRAGMA user_version={n}")
                    self._db.execute("COMMIT")
                except sqlite3.Error:
                    self._db.execute("ROLLBACK")
                    raise

    # ---- reading ---------------------------------------------------------------
    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        try:
            groups = json.loads(row["groups"] or "[]")
        except ValueError:
            groups = []
        try:
            config = json.loads(row["config"] or "{}")
        except ValueError:
            config = {}
        return Account(row["name"], row["role"], row["source"], bool(row["active"]),
                       bool(row["password"]), groups, row["created"], row["last_login"],
                       config if isinstance(config, dict) else {})

    def get(self, name: str) -> Account | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        return self._account(row) if row else None

    def all(self) -> list[Account]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM users ORDER BY name").fetchall()
        return [self._account(r) for r in rows]

    def active_admins(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' "
                                    "AND active = 1").fetchone()[0]

    def authenticate(self, name: str, password: str) -> Account | None:
        """A local user whose password matches, if active."""
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        stored = row["password"] if row else None
        # Hash anyway, so an unknown name takes as long as a wrong password.
        ok = check_password(password, stored or "scrypt$16384$8$1$AAAA$AAAA")
        if not (row and ok and row["active"] and row["source"] == "local"):
            return None
        self._touch(row["name"])
        return self._account(row)

    # ---- changing --------------------------------------------------------------
    def _touch(self, name: str) -> None:
        with self._lock:
            self._db.execute("UPDATE users SET last_login = ? WHERE name = ?", (_now(), name))

    def create(self, name: str, password: str | None = None, role: str = "user",
               source: str = "local") -> Account:
        name = name.strip()
        if not NAME.match(name):
            raise UserError("a user name is 1–64 letters, digits or . _ - @ +")
        if role not in ROLES:
            raise UserError(f"the role is one of {', '.join(ROLES)}")
        if source not in SOURCES:
            raise UserError(f"the source is one of {', '.join(SOURCES)}")
        if source == "local":
            self._check_password(password or "")
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO users (name, role, source, password, created) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, role, source,
                     hash_password(password) if source == "local" else None, _now()))
            except sqlite3.IntegrityError:
                raise UserError(f"there is already a user {name!r}") from None
        return self.get(name)

    @staticmethod
    def _check_password(password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise UserError(f"a password has at least {MIN_PASSWORD} characters")

    def set_password(self, name: str, password: str) -> None:
        self._check_password(password)
        self._require(name, local=True)
        with self._lock:
            self._db.execute("UPDATE users SET password = ? WHERE name = ?",
                             (hash_password(password), name))

    def _require(self, name: str, local: bool = False) -> Account:
        acc = self.get(name)
        if acc is None:
            raise UserError(f"there is no user {name!r}")
        if local and acc.source != "local":
            raise UserError(f"{acc.name} logs in through {acc.source}; the password is kept there")
        return acc

    def _keeps_an_admin(self, acc: Account) -> None:
        if acc.admin and acc.active and self.active_admins() <= 1:
            raise UserError("that would leave no active admin")

    def set_role(self, name: str, role: str) -> None:
        if role not in ROLES:
            raise UserError(f"the role is one of {', '.join(ROLES)}")
        acc = self._require(name)
        if role != "admin":
            self._keeps_an_admin(acc)
        with self._lock:
            self._db.execute("UPDATE users SET role = ? WHERE name = ?", (role, acc.name))

    def set_active(self, name: str, active: bool) -> None:
        acc = self._require(name)
        if not active:
            self._keeps_an_admin(acc)
        with self._lock:
            self._db.execute("UPDATE users SET active = ? WHERE name = ?",
                             (int(active), acc.name))

    def delete(self, name: str) -> None:
        acc = self._require(name)
        self._keeps_an_admin(acc)
        with self._lock:
            self._db.execute("DELETE FROM users WHERE name = ?", (acc.name,))

    def seen(self, name: str, source: str, groups: list[str],
             admin_by_group: bool | None) -> Account:
        """A user logged in through ``oidc`` or ``proxy``: recorded on first
        login (role ``user``), then kept up to date. ``admin_by_group``:
        True / False when the provider's admin group decides the role,
        None when the admin panel does."""
        acc = self.get(name)
        if acc is None:
            role = "admin" if admin_by_group else "user"
            with self._lock:
                self._db.execute(
                    "INSERT OR IGNORE INTO users (name, role, source, groups, created, "
                    "last_login) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, role, source, json.dumps(groups), _now(), _now()))
            return self.get(name)
        updates = {"groups": json.dumps(groups), "last_login": _now()}
        if admin_by_group is not None:
            updates["role"] = "admin" if admin_by_group else "user"
        with self._lock:
            self._db.execute(
                "UPDATE users SET " + ", ".join(f"{k} = ?" for k in updates)
                + " WHERE name = ?", (*updates.values(), acc.name))
        return self.get(name)

    # ---- settings ----------------------------------------------------------------
    @staticmethod
    def settings_of(acc: Account | None) -> dict:
        """Every setting: the user's, else its default (None: the container's)."""
        conf = acc.config if acc else {}
        return {k: conf[k] if conf.get(k) in allowed else default
                for k, (allowed, default) in SETTINGS.items()}

    def set_settings(self, name: str, changes: dict) -> dict:
        """Changes some settings (unknown names and values are refused, None
        goes back to the default). Returns all of them."""
        acc = self._require(name)
        conf = dict(acc.config)
        for key, value in changes.items():
            if key not in SETTINGS:
                raise UserError(f"there is no setting {key!r}")
            allowed, _ = SETTINGS[key]
            if value is None:
                conf.pop(key, None)
            elif value in allowed and type(value) is type(allowed[0]):
                conf[key] = value
            else:
                raise UserError(f"{key} is one of {', '.join(map(str, allowed))}")
        with self._lock:
            self._db.execute("UPDATE users SET config = ? WHERE name = ?",
                             (json.dumps(conf), acc.name))
        return self.settings_of(self.get(acc.name))

    def ensure_admin(self, name: str, password: str) -> bool:
        """The first admin, from the environment: created when there is no
        active admin yet. True when it was."""
        if self.active_admins():
            return False
        acc = self.get(name)
        if acc is None:
            self.create(name, password, "admin")
        else:
            with self._lock:
                self._db.execute(
                    "UPDATE users SET role = 'admin', active = 1, source = 'local', "
                    "password = ? WHERE name = ?", (hash_password(password), acc.name))
        return True

    def import_json(self, path: Path) -> int:
        """The list of an older viewer (``/state/users.json``): the users
        not known yet. Returns how many."""
        try:
            old = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return 0
        added = 0
        for name, rec in (old or {}).items():
            if not isinstance(rec, dict) or self.get(name) is not None or not NAME.match(name):
                continue
            source = rec.get("source") if rec.get("source") in ("oidc", "proxy") else "oidc"
            with self._lock:
                self._db.execute(
                    "INSERT OR IGNORE INTO users (name, role, source, groups, created, "
                    "last_login) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, "admin" if rec.get("role") == "admin" else "user", source,
                     json.dumps(rec.get("groups") or []),
                     rec.get("first_login") or _now(), rec.get("last_login")))
            added += 1
        return added
