# ingetrazo-docker

The **IngeTrazo engine**: [IngeTrazo](https://github.com/ingelibre/ingetrazo) — the free push/pull 3D and BIM
modeller — **headless in a container**, behind a small internal HTTP API.

The engine is the back end of [**ingetrazo-web-viewer**](https://github.com/BastelBaus/ingetrazo-web-viewer), which
holds everything that concerns people: the MCP gateway for AI agents (token / OAuth, users, folder rights) and the
live web viewer. Each of its services gets an engine of its own on an internal network; the engine knows only one
service token and has no port to the outside.

**To run IngeTrazo on a server, start with ingetrazo-web-viewer**: its `compose.yaml` runs the services and the
engines together. This repository builds the engine image:

| Image | Command | |
|---|---|---|
| `ghcr.io/bastelbaus/ingetrazo` | `engine` | IngeTrazo headless, Xvfb, Mesa llvmpipe; `ENGINE_MODE=mcp` for the MCP gateway, `render` (default) for the viewer |

IngeTrazo itself is not modified: the image builds it from a git ref on GitHub.

> **Status:** early. Headless mode is in IngeTrazo 0.6.3. What the web viewer still needs from the fork
> ([#1544](https://github.com/ingelibre/ingetrazo/pull/1544) sheets and scenes over MCP,
> [#1586](https://github.com/ingelibre/ingetrazo/pull/1586), [#1683](https://github.com/ingelibre/ingetrazo/pull/1683),
> [#1685](https://github.com/ingelibre/ingetrazo/pull/1685)) is tracked in
> [ingelibre/ingetrazo#1717](https://github.com/ingelibre/ingetrazo/issues/1717). Until it is merged, the image
> builds from the fork, pinned to a commit; CI also builds and checks the newest upstream release (job *upstream*,
> informational) to show when it can switch.

> **Moved:** until late November 2026 this repository also held the MCP server with its users and OAuth and the
> live viewer, all in one container. Those now live in ingetrazo-web-viewer (with their history); the images
> before that change keep working as they were.

## How it works

The container starts one IngeTrazo **without a window** (`--headless --ai-bridge`), rendering with OpenGL in software
(Mesa llvmpipe) inside an in-memory X server (Xvfb), and serves an HTTP API on port 8766
([`engine/server.py`](engine/server.py)):

| Route | | |
|---|---|---|
| `POST /call` | bearer token | `{"tool", "args", "timeout"?}` → IngeTrazo's bridge reply `{"ok", "result" \| "error"}` |
| `POST /rpc` | bearer token | `mcp` mode: one MCP JSON-RPC message through IngeTrazo's own handler (`scripts/ingetrazo_mcp.py`) |
| `GET /healthz` | – | `ok`, `busy` (a call is running) or 503 (`starting`, `blocked`, IngeTrazo gone) |

Guard rails, besides the token:

| | `render` (the viewer's) | `mcp` (the gateway's) |
|---|---|---|
| Tools | only what the viewer renders with | every tool but `shutdown` |
| Opens and imports | only below `/data` | only below `/data` |
| Writes | only below `/cache` (the volume it shares with the viewer); never saves | only below `/data` |
| At start | nothing open | `INGETRAZO_MODEL`, `--allow-save` if `INGETRAZO_ALLOW_SAVE` |

Each user's folders, read-only folders and `run_python` for non-admins are checked by the gateway, not here: the
token allows `run_python`, so **treat it like shell access to the container**. If IngeTrazo exits, the container
exits too and the restart policy brings it back.

## Configuration

| Variable | Default | |
|---|---|---|
| `ENGINE_MODE` | `render` | `render` or `mcp` (see above) |
| `ENGINE_TOKEN` | – | The shared secret, at least 16 characters |
| `ENGINE_TOKEN_FILE` | – | Without `ENGINE_TOKEN`: a file the engine reads, or makes with a random secret on its first start (a volume shared with its service) |
| `ENGINE_PORT` | `8766` | The API's port |
| `INGETRAZO_MODEL` | – | `mcp`: document to open at start (relative to `/data`) |
| `INGETRAZO_ALLOW_SAVE` | `true` | `mcp`: allow `save_document` |
| `INGETRAZO_SAVE_FORMAT` | – | `mcp`: `json` = plain-JSON `.igz`, diffable in git |
| `MODEL_FOLDERS` / `MODEL_SCAN_DEPTH` / `MODEL_EXCLUDE` | – | The model folders, as the services have them (see ingetrazo-web-viewer) |
| `INGETRAZO_AI_TIMEOUT` | app default (180 s) | How long one call may run before IngeTrazo answers `timed out on the UI thread`; raise it for big models in software rendering. Needs an IngeTrazo with [ingelibre/ingetrazo#1586](https://github.com/ingelibre/ingetrazo/pull/1586) |
| `HEALTH_MAX_BUSY_SECONDS` | `900` | `/healthz` turns `blocked` (503) after one call ran this long; keep it above `INGETRAZO_AI_TIMEOUT` |
| `XVFB_SCREEN` | `2560x1600x24` | Virtual screen of the in-memory X server; smaller = less memory |
| `LP_NUM_THREADS` | cores | Mesa's render threads; cap it when several engines share a host |

Volumes: `/data` (models; read-only for `render`), `/cache` (`render`: shared with the viewer at the same path),
`/state` (Qt's settings), and the folder of `ENGINE_TOKEN_FILE`.

## Building

```sh
docker build -t ingetrazo-docker:dev .
docker build -t ingetrazo-docker:upstream \
  --build-arg INGETRAZO_REPO=https://github.com/ingelibre/ingetrazo --build-arg INGETRAZO_REF=v0.6.3.3 .
IMAGE=ingetrazo-docker:dev tests/smoke.sh
```

`INGETRAZO_REPO` / `INGETRAZO_REF` choose the IngeTrazo (default: the fork, pinned to a commit);
`INGETRAZO_DOCKER_REF` is this repo's version, logged at start. `python tests/upstream_ready.py <app dir>` says
whether an IngeTrazo checkout has what the web viewer needs.

## Security

- The engine belongs on an internal network that only its service reaches; publish no port.
- Its token (or the token file's volume) is as sensitive as a shell in the container.
- The container runs as an unprivileged user; only `/data`, `/cache` and `/state` are writable, and a `render`
  engine gets `/data` read-only. Mount read-only folders `:ro` for an `mcp` engine too, so the file system enforces
  them as well.

## Contributing

Contributions are welcome. Commits need a DCO sign-off (`git commit -s`); see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

AGPL-3.0-or-later (see [LICENSE](LICENSE)). The IngeTrazo inside the image stays under its own
GPL-3.0-or-later; section 13 of the AGPL allows the combination.

If you run a modified version as a network service, the AGPL asks you to offer its users the
source of that version.
