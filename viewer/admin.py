# SPDX-License-Identifier: GPL-3.0-or-later
"""The viewer's admin panel (``/admin``) and a user's own page
(``/account``, with their settings): plain HTML forms, each with the
session's CSRF token.

Admins add local users (``VIEWER_AUTH_MODE=local``), set a user's role
(unless the provider's admin group decides it), switch users off and on,
set a local user's password, and remove users. The list always keeps one
active admin, and an admin cannot switch themself off or remove themself.
"""
from __future__ import annotations

from html import escape

from mcp_http.users import ROLES, SETTINGS, UserError

CSS = """
.panel{max-width:960px;margin:0 auto}
.panel h2{font-size:15px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:24px 0 10px}
.panel table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.panel th,.panel td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:middle}
.panel th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.panel tr:last-child td{border-bottom:0}
.panel .acts>div{display:flex;gap:6px;flex-wrap:wrap}
.panel form.inline{display:inline-flex;gap:6px;align-items:center;margin:0}
.panel input,.panel select{border:1px solid var(--line);background:var(--bg);color:var(--fg);padding:5px 8px;border-radius:6px;font:inherit;font-size:13px}
.panel button{border:1px solid var(--line);background:var(--card);color:var(--fg);padding:5px 10px;border-radius:6px;font:inherit;font-size:13px;cursor:pointer}
.panel button:hover{border-color:var(--accent)}
.panel button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.panel button.danger:hover{border-color:#c0392b;color:#c0392b}
.panel .add{display:flex;gap:8px;flex-wrap:wrap;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.panel .note{background:color-mix(in srgb,var(--ok) 12%,var(--card));border:1px solid var(--ok);border-radius:8px;padding:8px 12px;margin:12px 0;font-size:14px}
.panel .err{background:color-mix(in srgb,#c0392b 10%,var(--card));border:1px solid #c0392b;border-radius:8px;padding:8px 12px;margin:12px 0;font-size:14px;color:inherit}
.panel .off td:not(.acts){color:var(--muted)}
.panel .stack{display:flex;flex-direction:column;gap:10px;max-width:360px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.panel .stack label{font-size:13px;color:var(--muted)}
@media (max-width:820px){.panel table,.panel tbody,.panel tr,.panel td{display:block;width:100%}
.panel thead{display:none}.panel tr{border-bottom:1px solid var(--line)}.panel td{border:0;padding:4px 10px}}
"""


def _wrap(title: str, body: str, user, site: str) -> bytes:
    from . import server              # the page frame and the top bar
    header = (f'<a class="btn" href="/" title="All models">←</a>'
              f'<h1>{escape(title)}</h1>')
    return server._page(f"{title} – {site}", header,
                        f'<style>{CSS}</style><div class="panel">{body}</div>', user=user)


def _messages(note: str, error: str) -> str:
    return ((f'<div class="note">{escape(note)}</div>' if note else "")
            + (f'<div class="err">{escape(error)}</div>' if error else ""))


def _form(auth, user, action: str, name: str, inner: str, cls: str = "",
          confirm: str = "") -> str:
    onsubmit = f' onsubmit="return confirm({escape(repr(confirm))})"' if confirm else ""
    return (f'<form class="inline" method="post" action="/admin"{onsubmit}>'
            f'<input type="hidden" name="csrf" value="{auth.sessions.csrf(user.name)}">'
            f'<input type="hidden" name="action" value="{action}">'
            f'<input type="hidden" name="name" value="{escape(name)}">{inner}</form>')


def page(auth, user, note: str = "", error: str = "", title: str = "") -> bytes:
    """The user list, for an admin."""
    by_group = bool(auth.admin_group)
    rows = []
    for acc in auth.users.all():
        me = acc.name.lower() == user.name.lower()
        acts = []
        if by_group and acc.source != "local":
            role = f'{acc.role} <span class="muted">(group)</span>'
        else:
            opts = "".join(f'<option{" selected" if r == acc.role else ""}>{r}</option>'
                           for r in ROLES)
            role = _form(auth, user, "role", acc.name,
                         f'<select name="role" aria-label="Role" onchange="this.form.submit()">'
                         f'{opts}</select><noscript><button>Set</button></noscript>')
        if acc.source == "local" and auth.mode == "local":
            acts.append(_form(auth, user, "password", acc.name,
                              '<input name="password" type="password" placeholder="New password" '
                              'autocomplete="new-password" aria-label="New password" required>'
                              '<button>Set password</button>'))
        if not me:
            acts.append(_form(auth, user, "active", acc.name,
                              f'<input type="hidden" name="on" value="{0 if acc.active else 1}">'
                              f'<button>{"Switch off" if acc.active else "Switch on"}</button>'))
            acts.append(_form(auth, user, "delete", acc.name, '<button class="danger">Remove</button>',
                              confirm=f"Remove {acc.name}?"))
        last = escape((acc.last_login or "–").replace("T", " ").replace("+00:00", " UTC"))
        rows.append(
            f'<tr class="{"" if acc.active else "off"}"><td><strong>{escape(acc.name)}</strong>'
            + (' <span class="muted">(you)</span>' if me else "")
            + ("" if acc.active else ' <span class="tag">off</span>')
            + f'</td><td>{role}</td><td>{escape(acc.source)}</td><td class="muted">{last}</td>'
            f'<td class="acts"><div>{"".join(acts)}</div></td></tr>')
    add = ""
    if auth.mode == "local":
        opts = "".join(f"<option>{r}</option>" for r in reversed(ROLES))
        add = ('<h2>Add a user</h2><form class="add" method="post" action="/admin">'
               f'<input type="hidden" name="csrf" value="{auth.sessions.csrf(user.name)}">'
               '<input type="hidden" name="action" value="create">'
               '<input name="name" placeholder="User name" autocomplete="off" required aria-label="User name">'
               '<input name="password" type="password" placeholder="Password (8+ characters)" '
               'autocomplete="new-password" required aria-label="Password">'
               f'<select name="role" aria-label="Role">{opts}</select>'
               '<button class="primary">Add</button></form>')
    else:
        add = ('<p class="muted">Users log in through '
               + escape("the identity provider" if auth.mode == "oidc" else "the login proxy")
               + " and appear here after their first login."
               + (f" Members of the group {escape(auth.admin_group)!s} are admins." if by_group else "")
               + "</p>")
    body = (_messages(note, error)
            + '<h2>Users</h2><table><thead><tr><th>User</th><th>Role</th><th>Login</th>'
            '<th>Last login</th><th></th></tr></thead><tbody>' + "".join(rows)
            + "</tbody></table>" + add)
    return _wrap("Users", body, user, title)


def act(auth, user, form: dict) -> tuple[str, str]:
    """One change from the admin panel: (what was done, or what went wrong)."""
    def get(key: str) -> str:
        return (form.get(key) or [""])[0]

    action, name = get("action"), get("name").strip()
    me = name.lower() == user.name.lower()
    users = auth.users
    try:
        if action == "create":
            if auth.mode != "local":
                raise UserError("users of a provider or proxy appear after their first login")
            acc = users.create(name, get("password"), get("role") or "user")
            return f"Added {acc.name}.", ""
        if action == "role":
            acc = users.get(name)
            if acc and auth.admin_group and acc.source != "local":
                raise UserError(f"the group {auth.admin_group} decides {acc.name}'s role")
            users.set_role(name, get("role"))
            return f"{name} is now {get('role')}.", ""
        if action == "password":
            users.set_password(name, get("password"))
            return f"New password set for {name}.", ""
        if action == "active":
            if me:
                raise UserError("you cannot switch yourself off")
            on = get("on") == "1"
            users.set_active(name, on)
            return f"{name} is switched {'on' if on else 'off'}.", ""
        if action == "delete":
            if me:
                raise UserError("you cannot remove yourself")
            users.delete(name)
            return f"Removed {name}.", ""
        raise UserError("unknown action")
    except UserError as exc:
        return "", str(exc)[:1].upper() + str(exc)[1:] + "."


#: How the account page names the settings and their values.
LABELS = {
    "nav_mode": ("Folders in the View / 3D / Sheet dropdowns",
                 {"list": "List (every level)", "tree": "Tree (one level at a time)"}),
    "edges_3d": ("Edges in the 3D view", {True: "On", False: "Off"}),
}


def setting_value(key: str, raw: str):
    """A setting's value as a form sends it: "" is the default (None)."""
    if raw == "":
        return None
    allowed = SETTINGS.get(key, ((),))[0]
    if allowed and isinstance(allowed[0], bool):
        return raw.lower() in ("1", "true", "on", "yes")
    return raw


def _settings_form(auth, user) -> str:
    from .server import NAV_MODE
    conf = auth.settings(user)
    acc = auth.users.get(user.name)
    own = acc.config if acc else {}
    fields = []
    for key, (label, names) in LABELS.items():
        allowed, default = SETTINGS[key]
        if default is None:                    # as the container is set up
            default = NAV_MODE if key == "nav_mode" else allowed[0]
        opts = [f'<option value="">Default ({escape(names[default])})</option>']
        for v in allowed:
            sel = " selected" if key in own and conf[key] == v else ""
            value = ("1" if v else "0") if isinstance(v, bool) else v
            opts.append(f'<option value="{value}"{sel}>{escape(names[v])}</option>')
        fields.append(f'<label for="s-{key}">{escape(label)}</label>'
                      f'<select id="s-{key}" name="{key}">{"".join(opts)}</select>')
    return ('<h2>Settings</h2><form class="stack" method="post" action="/account">'
            f'<input type="hidden" name="csrf" value="{auth.sessions.csrf(user.name)}">'
            '<input type="hidden" name="action" value="settings">' + "".join(fields)
            + '<button class="primary">Save</button></form>')


def account_page(auth, user, note: str = "", error: str = "", title: str = "") -> bytes:
    """The user's own page: who they are, their settings, and (local
    users) a new password."""
    acc = auth.users.get(user.name)
    info = (f'<p>Logged in as <strong>{escape(user.name)}</strong>, {escape(user.role)}'
            f'{", through " + escape(acc.source) if acc and acc.source != "local" else ""}.</p>')
    pw = ""
    if acc and acc.source == "local" and auth.mode == "local":
        pw = ('<h2>Password</h2><form class="stack" method="post" action="/account">'
              f'<input type="hidden" name="csrf" value="{auth.sessions.csrf(user.name)}">'
              '<input type="hidden" name="action" value="password">'
              '<label for="o">Current password</label><input id="o" name="old" type="password" '
              'autocomplete="current-password" required>'
              '<label for="n">New password</label><input id="n" name="password" type="password" '
              'autocomplete="new-password" required>'
              '<button class="primary">Change password</button></form>')
    settings = _settings_form(auth, user) if acc else ""
    return _wrap("Account", _messages(note, error) + info + settings + pw, user, title)


def act_account(auth, user, form: dict) -> tuple[str, str]:
    def get(key: str) -> str:
        return (form.get(key) or [""])[0]

    try:
        if get("action") == "settings":
            auth.users.set_settings(user.name, {k: setting_value(k, get(k)) for k in LABELS
                                                if k in form})
            return "Your settings are saved.", ""
        if get("action") == "password":
            if auth.users.authenticate(user.name, get("old")) is None:
                raise UserError("the current password is wrong")
            auth.users.set_password(user.name, get("password"))
            return "Your password is changed.", ""
        raise UserError("unknown action")
    except UserError as exc:
        return "", str(exc)[:1].upper() + str(exc)[1:] + "."
