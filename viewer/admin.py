# SPDX-License-Identifier: AGPL-3.0-or-later
"""The viewer's admin panel (``/admin``) and a user's own page
(``/account``, with their settings): plain HTML forms, each with the
session's CSRF token.

Admins add local users (``VIEWER_AUTH_MODE=local``), set a user's role
(unless the provider's admin group decides it), switch users off and on,
set a local user's password, give them folders (``/admin/shares``, see
mcp_http/shares.py), and remove users. The list always keeps one
active admin, and an admin cannot switch themself off or remove themself.
"""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from mcp_http import shares as share_mod
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
.panel .avatar-box{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.panel .rights{display:inline-flex;gap:0}
.panel .right{border:1px solid var(--line);background:var(--card);color:var(--muted);padding:5px 9px;font-size:12px;border-radius:0;text-decoration:line-through}
.panel .rights>:first-child .right,.panel .rights>.right:first-child{border-radius:6px 0 0 6px}
.panel .rights>:last-child .right,.panel .rights>.right:last-child{border-radius:0 6px 6px 0;margin-left:-1px}
.panel .right.on{color:var(--fg);background:color-mix(in srgb,var(--ok) 14%,var(--card));text-decoration:none;font-weight:600}
.panel .token{font-family:ui-monospace,monospace;font-size:13px;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px;overflow-wrap:anywhere;user-select:all}
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
    if note.startswith("TOKEN:"):              # a new token: shown once, easy to copy
        return ('<div class="note">Your new token – copy it now, it is shown only once:'
                f'<div class="token">{escape(note[6:])}</div></div>')
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
        if acc.source == "guest":
            role = f'{acc.role} <span class="muted">(guest)</span>'
        elif by_group and acc.source != "local":
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
        rights = []
        for key, label, on, locked in (
                ("web", "Web", acc.web, acc.admin),
                ("mcp", "MCP", acc.mcp, acc.source == "guest")):
            if locked:
                rights.append(f'<span class="right{" on" if on else ""}" title="fixed">{label}</span>')
            else:
                rights.append(_form(auth, user, "rights", acc.name,
                                    f'<input type="hidden" name="{key}" value="{0 if on else 1}">'
                                    f'<button class="right{" on" if on else ""}" aria-pressed="{str(on).lower()}" '
                                    f'title="{label}: {"on – click to turn off" if on else "off – click to turn on"}">'
                                    f'{label}</button>'))
        acts.append('<span class="rights">' + "".join(rights) + "</span>")
        if not acc.admin:
            n = ("default" if acc.shares is None else
                 f"{sum(1 for x in acc.shares if x['access'] != 'none')} shared")
            acts.append(f'<a class="btn" href="/admin/shares?name={quote(acc.name)}">'
                        f'Folders <span class="muted">({n})</span></a>')
        if not me:
            acts.append(_form(auth, user, "active", acc.name,
                              f'<input type="hidden" name="on" value="{0 if acc.active else 1}">'
                              f'<button>{"Switch off" if acc.active else "Switch on"}</button>'))
            if acc.source != "guest":
                acts.append(_form(auth, user, "delete", acc.name,
                                  '<button class="danger">Remove</button>',
                                  confirm=f"Remove {acc.name}?"))
        last = escape((acc.last_login or "–").replace("T", " ").replace("+00:00", " UTC"))
        from .server import avatar_img
        rows.append(
            f'<tr class="{"" if acc.active else "off"}"><td>{avatar_img(acc.name, 24)} '
            f'<strong>{escape(acc.name)}</strong>'
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
    from . import site
    site_form = ('<h2>Title</h2><form class="add" method="post" action="/admin">'
                 f'<input type="hidden" name="csrf" value="{auth.sessions.csrf(user.name)}">'
                 '<input type="hidden" name="action" value="title">'
                 f'<input name="title" value="{escape(site.title())}" maxlength="{site.MAX_TITLE}" '
                 f'placeholder="{escape(site.DEFAULT_TITLE)}" aria-label="Title" style="min-width:18em">'
                 '<button class="primary">Save</button>'
                 '<button name="reset" value="1">Default</button></form>'
                 '<p class="muted">The headline of the overview and the login page. '
                 f'Default: {escape(site.DEFAULT_TITLE)} (<code>VIEWER_TITLE</code>).</p>')
    body = (_messages(note, error)
            + '<h2>Users</h2><table><thead><tr><th>User</th><th>Role</th><th>Login</th>'
            '<th>Last login</th><th></th></tr></thead><tbody>' + "".join(rows)
            + "</tbody></table>" + add + site_form)
    return _wrap("Users", body, user, title)


def act(auth, user, form: dict) -> tuple[str, str]:
    """One change from the admin panel: (what was done, or what went wrong)."""
    def get(key: str) -> str:
        return (form.get(key) or [""])[0]

    action, name = get("action"), get("name").strip()
    me = name.lower() == user.name.lower()
    users = auth.users
    try:
        if action == "title":
            from . import site
            now = site.set_title("" if get("reset") == "1" else get("title"))
            return f"The title is now {now}.", ""
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
        if action == "rights":
            web, mcp = get("web"), get("mcp")
            users.set_rights(name, web=(web == "1") if web else None,
                             mcp=(mcp == "1") if mcp else None)
            what = "web viewer" if web else "MCP server"
            return f"{name} {'may' if (web or mcp) == '1' else 'may no longer'} use the {what}.", ""
        if action == "shares":
            acc = users.get(name)
            if acc is None:
                raise UserError(f"there is no user {name!r}")
            if get("default") == "1":
                users.set_shares(name, None)
                return f"{name} has the default folders again.", ""
            chosen = []
            for key, vals in form.items():
                if key.startswith("s:") and vals[0] in share_mod.ACCESS:
                    chosen.append({"path": key[2:] or share_mod.ALL, "access": vals[0]})
            extra = get("add_path").strip()
            if extra:
                chosen.append({"path": extra, "access": get("add_access") or "r"})
            users.set_shares(name, chosen)
            return f"Folders of {name} saved.", ""
        if action == "delete":
            if me:
                raise UserError("you cannot remove yourself")
            users.delete(name)
            return f"Removed {name}.", ""
        raise UserError("unknown action")
    except UserError as exc:
        return "", str(exc)[:1].upper() + str(exc)[1:] + "."


ACCESS_NAMES = {"": "–", "none": "No access", "r": "Read", "rw": "Read & write"}


def shares_page(auth, user, name: str, note: str = "", error: str = "",
                title: str = "") -> bytes:
    """One user's folders, for an admin: each model folder and the folders
    in it, with what the user may do there."""
    acc = auth.users.get(name)
    if acc is None:
        return _wrap("Folders", _messages("", f"There is no user {name}."), user, title)
    own = {s["path"]: s["access"] for s in (acc.shares or [])}
    using_default = acc.shares is None
    current = own if not using_default else {
        s["path"]: s["access"] for s in share_mod.defaults()}
    paths = [share_mod.ALL] + share_mod.tree()
    paths += [p for p in current if p not in paths]
    rows = []
    for path in paths:
        depth = 0 if path in (share_mod.ALL, "") else path.count("/") + 1
        label = "All folders" if path == share_mod.ALL else (path.rsplit("/", 1)[-1] or "data")
        here = current.get(path, "")
        opts = "".join(f'<option value="{v}"{" selected" if v == here else ""}>{escape(t)}</option>'
                       for v, t in ACCESS_NAMES.items())
        got = share_mod.effective(acc, "*" if path in (share_mod.ALL, "") else path) \
            if not acc.admin else "rw"
        rows.append(f'<tr><td style="padding-left:{10 + depth * 18}px" title="{escape(path)}">'
                    f'{escape(label)}</td><td><select name="s:{escape(path)}" '
                    f'aria-label="Access to {escape(label)}">{opts}</select></td>'
                    f'<td class="muted">{escape(ACCESS_NAMES[got])}</td></tr>')
    csrf = auth.sessions.csrf(user.name)
    body = (_messages(note, error)
            + f'<p>What <strong>{escape(acc.name)}</strong> may see (and, through MCP, write). '
            '“–” takes what the folder above says; the most specific setting wins. '
            'Read-only model folders stay read-only.</p>'
            + ('<p class="muted">Now: the default for every user (DEFAULT_SHARES). '
               'Saving below gives this user their own.</p>' if using_default else "")
            + '<form method="post" action="/admin">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<input type="hidden" name="action" value="shares">'
            f'<input type="hidden" name="name" value="{escape(acc.name)}">'
            '<table><thead><tr><th>Folder</th><th>Share</th><th>Result</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table>'
            '<div class="add" style="margin-top:12px"><input name="add_path" placeholder="Another folder, e.g. projects/2026/x" '
            'aria-label="Another folder">'
            '<select name="add_access" aria-label="Access"><option value="r">Read</option>'
            '<option value="rw">Read &amp; write</option><option value="none">No access</option></select>'
            '<span class="spacer"></span><button class="primary">Save</button></div></form>'
            + ("" if using_default else
               _form(auth, user, "shares", acc.name,
                     '<input type="hidden" name="default" value="1">'
                     '<button>Back to the default</button>'))
            + '<p><a href="/admin">← All users</a></p>')
    return _wrap(f"Folders of {acc.name}", body, user, title)


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
    if acc and acc.source == "guest":
        info = ('<p>Logged in as <strong>guest</strong>. Every guest shares these '
                'settings.</p>')
    else:
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
    settings = (_avatar_form(auth, user, acc) + _settings_form(auth, user)) if acc else ""
    return _wrap("Account", _messages(note, error) + info + settings + pw
                 + _tokens(auth, user, acc), user, title)


def _avatar_form(auth, user, acc) -> str:
    """The user's avatar: pick a style, press New until one fits."""
    from . import avatar
    from .server import avatar_img
    current = (avatar.valid(acc.avatar) or {}).get("style", "initials")
    csrf = auth.sessions.csrf(user.name)
    opts = "".join(f'<option{" selected" if st == current else ""}>{st}</option>'
                   for st in avatar.STYLES)
    shared = ' <span class="muted">(every guest sees the same)</span>' if acc.source == "guest" else ""
    return ('<h2>Avatar</h2><div class="avatar-box">' + avatar_img(acc.name, 72)
            + '<form class="inline" method="post" action="/account">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<input type="hidden" name="action" value="avatar">'
            f'<select name="style" aria-label="Style">{opts}</select>'
            '<button class="primary">New</button></form>'
            + ("" if acc.avatar is None else
               '<form class="inline" method="post" action="/account">'
               f'<input type="hidden" name="csrf" value="{csrf}">'
               '<input type="hidden" name="action" value="avatar_reset">'
               '<button>Default</button></form>')
            + shared + '</div>')


def _tokens(auth, user, acc) -> str:
    """Personal MCP access tokens, for users who may use the MCP server."""
    if acc is None or not acc.mcp:
        return ""
    csrf = auth.sessions.csrf(user.name)
    rows = "".join(
        f'<tr><td>{escape(t["label"] or "–")}</td>'
        f'<td class="muted">{escape(t["created"][:10])}</td>'
        f'<td class="muted">{escape((t["last_used"] or "never")[:16].replace("T", " "))}</td>'
        '<td class="acts"><div><form class="inline" method="post" action="/account">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<input type="hidden" name="action" value="revoke">'
        f'<input type="hidden" name="id" value="{t["id"]}">'
        '<button class="danger">Revoke</button></form></div></td></tr>'
        for t in auth.users.tokens(acc.name))
    table = ('<table><thead><tr><th>Name</th><th>Made</th><th>Last used</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table>' if rows else "")
    return ('<h2>MCP access</h2><p class="muted">A personal token for an MCP client '
            '(<code>Authorization: Bearer &lt;token&gt;</code> with MCP_AUTH=token). The client '
            'sees the folders you see here; with read &amp; write it may save and export.</p>'
            + table
            + '<form class="add" method="post" action="/account" style="margin-top:12px">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<input type="hidden" name="action" value="token">'
            '<input name="label" placeholder="Name, e.g. Claude Desktop" aria-label="Token name">'
            '<button class="primary">New token</button></form>')


def act_account(auth, user, form: dict) -> tuple[str, str]:
    def get(key: str) -> str:
        return (form.get(key) or [""])[0]

    try:
        if get("action") == "settings":
            auth.users.set_settings(user.name, {k: setting_value(k, get(k)) for k in LABELS
                                                if k in form})
            return "Your settings are saved.", ""
        if get("action") == "avatar":
            from . import avatar
            auth.users.set_avatar(user.name, avatar.fresh(get("style")))
            return "A new avatar. Press New again for another.", ""
        if get("action") == "avatar_reset":
            auth.users.set_avatar(user.name, None)
            return "Back to the default avatar.", ""
        if get("action") == "token":
            token = auth.users.create_token(user.name, get("label"))
            return "TOKEN:" + token, ""
        if get("action") == "revoke":
            if not auth.users.revoke_token(user.name, get("id")):
                raise UserError("no such token")
            return "The token is revoked.", ""
        if get("action") == "password":
            if auth.users.authenticate(user.name, get("old")) is None:
                raise UserError("the current password is wrong")
            auth.users.set_password(user.name, get("password"))
            return "Your password is changed.", ""
        raise UserError("unknown action")
    except UserError as exc:
        return "", str(exc)[:1].upper() + str(exc)[1:] + "."
