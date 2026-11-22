#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Start one of the container's services inside an in-memory X server
# (IngeTrazo needs an OpenGL context even with no window on screen).
#   mcp     MCP over HTTP (default)
#   viewer  read-only live web viewer of sheets and views
#   shell   a shell, for debugging
set -e

service="${1:-mcp}"
shift || true

case "$service" in
  mcp)    module=mcp_http.server ;;
  viewer) module=viewer.server ;;
  shell)  exec /bin/sh "$@" ;;
  *)      exec "$service" "$@" ;;
esac

# Qt keeps its settings and caches in HOME; keep them in the state volume.
export XDG_CONFIG_HOME="${INGETRAZO_STATE:-/state}/config"
export XDG_CACHE_HOME="${INGETRAZO_STATE:-/state}/cache-$service"
uid="$(id -u)"
export XDG_RUNTIME_DIR="/tmp/runtime-$uid"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

exec xvfb-run -a -s "-screen 0 ${XVFB_SCREEN:-2560x1600x24} -nolisten tcp" \
  python -m "$module" "$@"
