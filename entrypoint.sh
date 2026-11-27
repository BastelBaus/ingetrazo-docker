#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Start one of the container's services. Those that run IngeTrazo do so
# inside an in-memory X server (it needs an OpenGL context even with no
# window on screen).
#   mcp     MCP over HTTP (default in the engine image); with
#           MCP_ENGINE_URL a gateway to an engine, no X server needed
#   engine  an engine for the viewer image's services (internal;
#           ENGINE_MODE render or mcp)
#   viewer  read-only live web viewer of sheets and views; with
#           ENGINE_URLS it renders through engines and needs no X server
#   shell   a shell, for debugging
set -e

service="${1:-mcp}"
shift || true

case "$service" in
  mcp)    module=mcp_http.server ;;
  engine) module=mcp_http.engine_server ;;
  viewer) module=viewer.server ;;
  shell)  exec /bin/sh "$@" ;;
  *)      exec "$service" "$@" ;;
esac

# The viewer and the MCP service with engines, or in the viewer image (no
# IngeTrazo, no X server): plain Python.
case "$service" in
  viewer) remote="${ENGINE_URLS:-}"; var=ENGINE_URLS ;;
  mcp)    remote="${MCP_ENGINE_URL:-}"; var=MCP_ENGINE_URL ;;
  *)      remote=""; var="" ;;
esac
if [ -n "$var" ] && { [ -n "$remote" ] || ! command -v xvfb-run >/dev/null 2>&1; }; then
  if [ -z "$remote" ]; then
    echo "[ingetrazo-docker] this image has no IngeTrazo: set $var (and ENGINE_TOKEN or ENGINE_TOKEN_FILE) to the engine(s)" >&2
    exit 2
  fi
  exec python -m "$module" "$@"
fi

# Qt keeps its settings and caches in HOME; keep them in the state volume.
export XDG_CONFIG_HOME="${INGETRAZO_STATE:-/state}/config"
export XDG_CACHE_HOME="${INGETRAZO_STATE:-/state}/cache-$service"
uid="$(id -u)"
export XDG_RUNTIME_DIR="/tmp/runtime-$uid"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

exec xvfb-run -a -s "-screen 0 ${XVFB_SCREEN:-2560x1600x24} -nolisten tcp" \
  python -m "$module" "$@"
