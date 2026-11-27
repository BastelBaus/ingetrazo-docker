#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Start one of the container's services. Those that run IngeTrazo do so
# inside an in-memory X server (it needs an OpenGL context even with no
# window on screen).
#   mcp     MCP over HTTP (default in the engine image)
#   engine  a render engine for a viewer in another container (internal)
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

# The viewer with render engines, or in the viewer image (no IngeTrazo, no
# X server): plain Python.
if [ "$service" = viewer ] && { [ -n "${ENGINE_URLS:-}" ] || ! command -v xvfb-run >/dev/null 2>&1; }; then
  if [ -z "${ENGINE_URLS:-}" ]; then
    echo "[ingetrazo-docker] this image has no IngeTrazo: set ENGINE_URLS (and ENGINE_TOKEN) to the render engines" >&2
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
