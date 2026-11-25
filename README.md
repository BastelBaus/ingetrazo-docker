# ingetrazo-docker

Run [IngeTrazo](https://github.com/ingelibre/ingetrazo) — the free push/pull 3D and BIM modeller — **headless in a
container**:

- **MCP server over HTTP**: lets AI agents (Claude Code, Claude Desktop, claude.ai, Cursor, VS Code, …) model,
  query, render and save documents. The tools are IngeTrazo's own (`run_python`, `query_model`, `screenshot`,
  sheets, views, export, …), plus `list_documents` for the model folders.
- **Live viewer (optional)**: a read-only web page with the **sheets** and **saved views** of every model in the
  model folders. The overview shows a small preview per model, folder by folder. A sheet or view is rendered when
  someone opens it, and kept until its file changes; the page refreshes by itself. A model page shows one
  sheet or view at a time, fitted to the window. A slim top bar has three dropdowns, **View**, **3D** and
  **Sheet**, with the model's folders (nested too) and a filter, plus ‹ › buttons (or the ← → keys) to step
  through the sheets and views. Each dropdown shows its folders either as a **list** (every level at once) or as a
  **tree** (one level at a time: tap a folder to step in, ‹ to step out); the switch is in the dropdown, kept in the
  user's settings, and `VIEWER_NAV_MODE` sets the default. Each sheet and view has its own link (`/m/<model>?sheet=2`, `?view=1`). The
  **3D** dropdown shows the model in 3D in the browser (three.js): drag to turn it, right-drag to move, wheel to
  zoom, the edges as lines; its entries are the whole model and the saved views as camera presets
  (`?3d=1&cam=2`). Models whose 3D file is bigger than `VIEWER_GLB_MAX_MB` get no 3D view.
- **Your models stay in folders you mount**: `./data` on the host, or several folders, each read-only or
  read/write for the MCP service (see [Model folders](#model-folders)).

IngeTrazo itself is not modified: the image builds it from a git ref on GitHub.

| Viewer: every model in the data folder | Viewer: one model's sheets and views |
|---|---|
| ![Overview](docs/viewer-overview.png) | ![Model page](docs/viewer-model.png) |

> **Status:** early. Headless mode is in IngeTrazo 0.6.3. The sheet and scene tools come with
> [#1543](https://github.com/ingelibre/ingetrazo/pull/1543),
> [#1544](https://github.com/ingelibre/ingetrazo/pull/1544) and
> [#1545](https://github.com/ingelibre/ingetrazo/pull/1545). Until those are merged, the image
> builds from their branch on the fork, pinned to a commit.

## Quick start

```sh
git clone https://github.com/BastelBaus/ingetrazo-docker
cd ingetrazo-docker
cp .env.example .env
# set MCP_TOKEN in .env, e.g. to the output of: openssl rand -hex 32
# and VIEWER_PASSWORD (or VIEWER_AUTH_MODE=none for a viewer without login)
mkdir -p data && cp /path/to/*.igz data/

docker compose --profile viewer up -d
```

This pulls the prebuilt image `ghcr.io/bastelbaus/ingetrazo:latest` (amd64 and arm64).
Add `--build` to build it locally instead, e.g. against another `INGETRAZO_REF`.

- MCP: `http://localhost:8765/mcp` (bearer token)
- Viewer: `http://localhost:8080` (user `viewer`, your `VIEWER_PASSWORD`)

Both ports are published on `127.0.0.1` only by default (`MCP_BIND`, `VIEWER_BIND`).

### Connect an MCP client

Claude Code:

```sh
claude mcp add --transport http ingetrazo http://localhost:8765/mcp \
  --header "Authorization: Bearer $MCP_TOKEN"
```

Any client that speaks Streamable HTTP works the same way: URL plus `Authorization` header.

### Connect claude.ai (OAuth)

Remote connectors such as claude.ai's custom connectors log in with OAuth instead of a static token. Set
`MCP_AUTH=oauth`, and put the server behind TLS at a public URL, e.g. with [Caddy](https://caddyserver.com):

```caddyfile
mcp.example.org {
	reverse_proxy 127.0.0.1:8765
}
```

```sh
MCP_AUTH=oauth
PUBLIC_URL=https://mcp.example.org
OAUTH_USER=admin
OAUTH_PASSWORD=<at least 12 characters>
```

In claude.ai, add a custom connector with the URL `https://mcp.example.org/mcp`. claude.ai registers itself, and
you approve it once on the server's consent page with `OAUTH_USER` / `OAUTH_PASSWORD`.

The container is its own small OAuth 2.1 authorization server:
- metadata: RFC 9728 at `/.well-known/oauth-protected-resource`, RFC 8414 at `/.well-known/oauth-authorization-server`;
- dynamic client registration (RFC 7591) at `/register`;
- the authorization code flow with PKCE (S256 only);
- access tokens that expire (`OAUTH_ACCESS_TOKEN_SECONDS`, 3600) and refresh tokens that rotate on every use
  (`OAUTH_REFRESH_TOKEN_DAYS`, 30).

Clients and tokens are kept in `/state/oauth.json`, tokens and client secrets only as SHA-256 hashes. Only redirect
URIs on `OAUTH_REDIRECT_URIS` may register. The default allows claude.ai's and claude.com's callbacks, and
`http://localhost` / `127.0.0.1` on any port for the MCP Inspector and Claude Code on the same machine. Entries
match by scheme and host, and the path by prefix. A wrong password on the consent page is slowed down, and after
20 wrong passwords in 15 minutes the consent page refuses for a while.

## Configuration

All settings are environment variables. See [`.env.example`](.env.example) for the full list.

| Variable | Default | |
|---|---|---|
| `INGETRAZO_DATA_DIR` | `./data` | Host folder mounted at `/data` |
| `MODEL_FOLDERS` | – | Several model folders under `/data`, each `rw` or `ro`, with scan options (see [Model folders](#model-folders)) |
| `MODEL_SCAN_DEPTH` / `MODEL_EXCLUDE` | – | Defaults for every folder: how deep to look for models; globs to leave out |
| `INGETRAZO_MODEL` | – | Document to open at start (relative to `/data`) |
| `INGETRAZO_ALLOW_SAVE` | `true` | Allow `save_document` |
| `INGETRAZO_SAVE_FORMAT` | – | `json` = plain-JSON `.igz`, diffable in git |
| `MCP_AUTH` | `token` | `none`, `token` or `oauth` |
| `MCP_TOKEN` | – | Bearer token with every right, at least 16 characters (`token` mode); optional when users use personal tokens (see [Users](#users)) |
| `PUBLIC_URL` | – | `oauth`: the https URL clients reach the server at |
| `OAUTH_USER` / `OAUTH_PASSWORD` | `admin` / – | `oauth`: who approves clients with every right (password at least 12 characters); optional: users of the user list with *MCP* approve with their own rights |
| `OAUTH_REDIRECT_URIS` | claude.ai, claude.com, loopback | `oauth`: redirect URIs clients may register (comma-separated) |
| `VIEWER_AUTH_MODE` | `local` | Viewer login: `local`, `none`, `oidc` or `proxy` (see [Viewer login](#viewer-login)) |
| `DEFAULT_SHARES` | `*:r` | The folders of a user an admin has not set any for: `path:access; …` with `r`, `rw` or `none`, `*` = every folder (see [Users](#users)) |
| `VIEWER_GUEST` | `false` | `local` / `oidc`: a *Log in as guest* button on the login page (see [Users](#users)) |
| `VIEWER_PASSWORD` | – | `local`: the password (user `VIEWER_USER`); login page for browsers, Basic auth for scripts |
| `VIEWER_PRERENDER` | `previews` | What the viewer renders before anyone looks: `previews`, `none` or `all` (see [Rendering on demand](#rendering-on-demand)) |
| `VIEWER_TITLE` | `IngeTrazo-docker` | The headline (overview, login page, browser tab); admins can change it at `/admin` → *Title* |
| `VIEWER_SHEET_DPI` | `120` | Sheet image resolution |
| `VIEWER_NAV_MODE` | `list` | How the View / 3D / Sheet dropdowns show folders by default: `list` (all levels) or `tree` (one level at a time); each user can switch (kept in their settings) |
| `VIEWER_GLB_MAX_MB` | `50` | 3D view: a model whose GLB is bigger gets no 3D view (it is still rendered as sheets and views); `0` turns the 3D view off |
| `INGETRAZO_IMAGE` | `ghcr.io/bastelbaus/ingetrazo:latest` | Image to pull (or tag to build) |
| `INGETRAZO_REPO` / `INGETRAZO_REF` | fork / pinned commit | Which IngeTrazo to build |
| `INGETRAZO_DOCKER_REF` | `dev` | This repo's tag or commit for a local build; shown in the viewer's footer and at `/version` |

## How it works

![Architecture](docs/architecture.drawio.png)

<sub>The diagram is editable: open `docs/architecture.drawio.png` in [draw.io / diagrams.net](https://app.diagrams.net)
(the diagram is embedded in the PNG) and save it back as PNG. `docs/architecture.drawio` is the same diagram as
XML; `python docs/render_drawio_png.py` re-renders the PNG from it.</sub>

Each container runs one IngeTrazo **without a window** (`--headless --ai-bridge`). It renders with OpenGL in
software (Mesa llvmpipe) inside an in-memory X server (Xvfb).

- **`mcp`**: an HTTP front end. It forwards every MCP request to IngeTrazo's own handler
  (`scripts/ingetrazo_mcp.py` of the cloned app) and talks to the app's AI bridge on the container's loopback.
  The container adds three things: `list_documents`, a check that keeps file paths inside the model folders
  (and writes inside the read/write ones), and hiding `shutdown`. The container's lifetime belongs to Docker. If IngeTrazo exits, the container exits too and the
  restart policy brings it back.
- **`viewer`**: a second, read-only IngeTrazo (data mounted read-only). It watches the model folders and renders
  what is looked at: a sheet as PNG, a saved view as PNG, the sheets as one PDF, the model as GLB (glTF binary,
  for the 3D view), into the cache. It serves them as web pages, and server-sent events reload an open page when
  what it waits for is ready.

Health: `GET /healthz` on both ports, used by the image's `HEALTHCHECK`. For the MCP service it checks that the
IngeTrazo process is alive and that its bridge answers (`list_documents`). The check queues behind the MCP
requests, because the bridge works one request at a time; during a long tool call it reports `busy`. `/healthz` of
the viewer needs no login in any mode.

## Model folders

By default there is one model folder, `/data` (`INGETRAZO_DATA_DIR` on the host), read/write for the MCP service.
For more, list them in `MODEL_FOLDERS`, separated by `;`:

```sh
MODEL_FOLDERS=projects:rw; archive:ro:depth=1:exclude=old*,*.bak.igz
```

Each entry is `name[:rw|ro][:depth=N][:exclude=glob,glob…]`:

| Part | |
|---|---|
| `name` | The folder is mounted at `/data/<name>`. MCP clients use it as the path prefix (`projects/house.igz`); the viewer shows it as a heading. |
| `rw` / `ro` | `rw` (default): the MCP service may save and export into it. `ro`: `save_document`, `export_*`, `render_sheet` and `screenshot` with a path there fail with a clear error, and so does `save_document` without a path while a document from there is open. The viewer is read-only everywhere. |
| `depth=N` | How deep to look for models: `0` the folder itself only, `1` one level of subfolders, … Default: no limit, or `MODEL_SCAN_DEPTH`. |
| `exclude=…` | Globs left out of the listing (plus `MODEL_EXCLUDE`). Without `/` a glob matches a file or folder name at any depth (`*.bak.igz`, `archive*`); with `/` it matches the path from the folder (`old/**`). Folders left out are not walked at all. An excluded file can still be opened by its path. |

Files and folders starting with `.` are never listed. A mistake in `MODEL_FOLDERS` stops the service with a
message that names it.

The host folders are bind mounts, in a `compose.override.yaml` next to `compose.yaml`;
[`compose.folders.example.yaml`](compose.folders.example.yaml) is one to copy. It mounts an empty volume at
`/data` and each folder inside it, read-only for the viewer (and for `ro` folders in the MCP service too).

`list_documents` lists the models folder by folder, with each folder's mode:

```json
{"open_documents": [...], "data_folder": "/data",
 "folders": [{"name": "projects", "path": "/data/projects", "mode": "rw",
              "documents": [{"path": "projects/house.igz", "size_bytes": 81234,
                             "modified": "2026-11-24T10:12:00+00:00", "open": true}]},
             {"name": "archive", "path": "/data/archive", "mode": "ro", "documents": [...]}]}
```

## Rendering on demand

A large model can have dozens of sheets and well over a hundred saved views, and rendering in software (no GPU)
takes a while. So the viewer renders what someone looks at, keeps it, and does the rest later or never:

- **Overview**: one small preview per model, the first sheet or else the first saved view (`VIEWER_PREVIEW_DPI`,
  `VIEWER_PREVIEW_WIDTH` × `VIEWER_PREVIEW_HEIGHT`).
- **Model page**: the sheets and saved views are listed by name (this opens the model but renders nothing). The
  one on screen is rendered when it is opened ("Rendering…"), its neighbours (‹ ›) right after.
- **PDF** and **3D**: made when someone asks for them (the PDF button, the 3D button).
- **A model file changes**: nothing is rendered again at once. The next look at the model lists it again and
  renders what is opened; until then the page shows the previous picture, marked "updating…". The sheets and
  views opened in the last `VIEWER_RECENT_MINUTES` (30) are rendered again first.
- **One queue** does it all, most urgent first: what a user waits for, then the neighbours and recently viewed,
  then previews, then (`all`) everything else.

`VIEWER_PRERENDER` sets what is done before anyone looks:

| Value | |
|---|---|
| `previews` (default) | List each model and render its preview in the background, so the overview is ready. |
| `none` | Nothing in the background: previews when the overview is opened, the rest when it is looked at. |
| `all` | Everything (sheets, views, PDF, 3D model) in the background, as the viewer did before; fine for a few small models. |

Each picture is cached by the file's modification time and size, the item, and the render settings in its file
name (`sheet-003-120dpi.png`), so changing `VIEWER_SHEET_DPI` renders anew instead of showing an old picture.

## Viewer login

`VIEWER_AUTH_MODE` (`AUTH_MODE` works too) says who may see the viewer. `/healthz` is always open.

| Mode | Who gets in | Settings |
|---|---|---|
| `local` (default) | The users of the [user list](#users) with a password: a login page and a session cookie for browsers, Basic auth for scripts. `VIEWER_USER` / `VIEWER_PASSWORD` make the first admin. Without any admin and without a password the viewer does **not** start. | `VIEWER_USER`, `VIEWER_PASSWORD` |
| `none` | Anyone who reaches the port, on purpose (a trusted network, a public showcase). A warning in the log; no Log out button. | – |
| `oidc` | Users of an OpenID Connect provider (Authentik, Keycloak, …): authorization code flow with PKCE. | `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`; optional `OIDC_SCOPES`, `OIDC_ADMIN_GROUP`, `OIDC_USERNAME_CLAIM`, `OIDC_GROUPS_CLAIM` |
| `proxy` | Users a reverse proxy with forward auth (Traefik or nginx with Authentik) has logged in; the proxy names them in a header. The header is believed only from `PROXY_TRUSTED_IPS`. | `PROXY_TRUSTED_IPS`; optional `PROXY_USER_HEADER` (default `X-authentik-username`), `PROXY_GROUPS_HEADER`, `PROXY_ADMIN_GROUP`, `PROXY_LOGOUT_URL` |

With `oidc` and `proxy` a user is added to the [user list](#users) on first login. There is no LDAP client in the
container: user sources such as LDAP are configured in the identity provider.

Sessions (`local`, `oidc`) last `VIEWER_SESSION_DAYS` (30) and end when the container restarts.

### Users

The users live in one SQLite file, `users.db`, on the volume `users` that both services mount (`USERS_DB`, default
`/state/users.db`). Each user has a role, `admin` or `user`, and is switched on or off.

- **Admins** manage the list at **`/admin`** (the *Users* button in the top bar): add local users (`local` mode), set
  roles and passwords, switch users off and on, remove them. The list always keeps one active admin, and an admin
  cannot switch themself off or remove themself.
- **The first admin** comes from `VIEWER_USER` / `VIEWER_PASSWORD` while there is no active admin. After that the
  list decides: changing `VIEWER_PASSWORD` later does nothing; change the password at `/account` or `/admin`.
- **`oidc` and `proxy`**: a user is added on first login, as `user`. With `OIDC_ADMIN_GROUP` / `PROXY_ADMIN_GROUP`
  set, that group decides who is admin; without it, the admin panel does. Passwords stay with the provider.
- **Everyone** sees their account at `/account` (their name in the top bar); local users change their password there.
- **Avatar**: at `/account` each user makes their own: pick a style (`pattern`, `initials` or `shapes`) and press
  *New* until one fits. It is drawn in the container as SVG from a few values; nothing is uploaded. It shows in the
  top bar and in the admin panel; without one, the initials of the name.
- **Folders per user**: at `/admin` → *Folders* an admin gives each user model folders, or folders inside them,
  with *Read*, *Read & write* or *No access* (to carve a folder out of a wider share); the most specific setting
  wins, and a read-only model folder stays read-only. The viewer shows a user only what they may read; *write*
  is for saving and exporting through MCP. Admins have everything. A user nobody has set folders for gets
  `DEFAULT_SHARES` (default `*:r`: every folder, read), e.g. `DEFAULT_SHARES=*:none; projects/public:r`.
- **Web and MCP separately**: each user has two switches in the panel, *Web* (the viewer) and *MCP* (the MCP
  server). New users get *Web* only; admins keep *Web*. A user with *MCP* makes personal tokens at `/account`
  (`Authorization: Bearer itz_…`, for `MCP_AUTH=token`), or logs in on the OAuth consent page (`MCP_AUTH=oauth`)
  with their name and password. Over MCP they list, open and use only what their folders allow, and save and
  export only where they have *Read & write*; `run_python` is for admins only, since Python in the container
  reaches every file. There is one IngeTrazo and one open document for all callers: a tool on a document the
  caller may not read is refused. `MCP_TOKEN` and `OAUTH_USER` / `OAUTH_PASSWORD` still have every right; with the
  user list both are optional.
- **Guest** (`VIEWER_GUEST=true`, for the `local` and `oidc` logins): the login page gets a **Log in as guest**
  button that lets anyone in without a password, as the one user `guest`. The guest has settings like everyone
  else (shared by all guests), is never an admin, and an admin can switch it off in the panel without restarting.
  `VIEWER_AUTH_MODE=none` stays the viewer without any login.
- **Settings per user**: what a user switches in the viewer is kept with them, in the user list, and follows them
  to any browser: list or tree in the dropdowns (default `VIEWER_NAV_MODE`), edges in the 3D view. `/account` shows
  them, with *Default* to go back to the container's. Without an account (`VIEWER_AUTH_MODE=none`) the browser
  keeps them.

Switching a user off or removing them ends their session at once. A viewer that kept users in `/state/users.json`
takes them over on its first start.

### Authentik with OIDC

1. In Authentik, create an **OAuth2/OpenID Provider** (confidential client) with the redirect URI
   `https://viewer.example.org/auth/callback`, and an **Application** that uses it, e.g. with slug `ingetrazo-viewer`.
2. In `.env`:

   ```sh
   VIEWER_AUTH_MODE=oidc
   OIDC_ISSUER_URL=https://auth.example.org/application/o/ingetrazo-viewer/
   OIDC_CLIENT_ID=<client id from Authentik>
   OIDC_CLIENT_SECRET=<client secret from Authentik>
   OIDC_REDIRECT_URI=https://viewer.example.org/auth/callback
   OIDC_ADMIN_GROUP=ingetrazo-admins      # optional
   ```

Authentik's default `profile` scope includes the user's `groups`. For Keycloak, add a "Group Membership" mapper
(claim `groups`, without the full path) to the client. The issuer and the redirect URI must be `https://`: the ID
token comes straight from the provider's token endpoint over TLS and with the client secret, so its signature is
not checked (OpenID Connect Core 3.1.3.7), while issuer, audience, expiry and nonce are. `OIDC_ALLOW_HTTP=true`
lifts that for a lab.

### Authentik as forward auth (Traefik)

Authentik's proxy outpost logs the user in; Traefik asks it on every request and passes the user on.

```yaml
services:
  viewer:
    environment:
      VIEWER_AUTH_MODE: proxy
      PROXY_TRUSTED_IPS: 172.30.0.0/24        # the network Traefik reaches the viewer on
      PROXY_GROUPS_HEADER: X-authentik-groups
      PROXY_ADMIN_GROUP: ingetrazo-admins
      PROXY_LOGOUT_URL: /outpost.goauthentik.io/sign_out
    labels:
      traefik.http.routers.itz-viewer.rule: Host(`viewer.example.org`)
      traefik.http.routers.itz-viewer.middlewares: authentik@docker
      traefik.http.middlewares.authentik.forwardauth.address: http://authentik-server:9000/outpost.goauthentik.io/auth/traefik
      traefik.http.middlewares.authentik.forwardauth.trustForwardHeader: "true"
      traefik.http.middlewares.authentik.forwardauth.authResponseHeaders: X-authentik-username,X-authentik-groups
```

Set `PROXY_TRUSTED_IPS` to the proxy's address or network only, and don't publish the viewer's port elsewhere:
anyone else who can reach it could send the header themselves. With nginx, use `auth_request` to the outpost and
`proxy_set_header X-authentik-username $authentik_username;` (see Authentik's nginx docs).

## Security

`run_python` executes arbitrary Python inside the container. Treat MCP access like shell access to the container:

- keep `MCP_AUTH=token` (or `oauth`), never `none` on a reachable port;
- put TLS in front when you publish beyond localhost (a reverse proxy such as Caddy or Traefik);
- the container runs as an unprivileged user, and only `/data`, `/cache` and `/state` are writable.

The path check on `open_document`/`save_document`/imports/exports keeps well-behaved agents inside the model
folders, and out of writing to the `ro` ones. It is not a sandbox against `run_python`: mount `ro` folders
read-only (`:ro`) for the MCP service too, as the example does, so the file system enforces it as well.

## License

GPL-3.0-or-later, like IngeTrazo, which the image contains.
