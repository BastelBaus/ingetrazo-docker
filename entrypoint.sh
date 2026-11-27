#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Start the engine: one IngeTrazo inside an in-memory X server (it needs an
# OpenGL context even with no window on screen), behind engine/server.py.
#   engine  the engine (default; ENGINE_MODE render or mcp)
#   shell   a shell, for debugging
set -e

service="${1:-engine}"
shift || true

case "$service" in
  engine) ;;
  shell)  exec /bin/sh "$@" ;;
  *)      exec "$service" "$@" ;;
esac

# Qt keeps its settings and caches in HOME; keep them in the state volume.
export XDG_CONFIG_HOME="${INGETRAZO_STATE:-/state}/config"
export XDG_CACHE_HOME="${INGETRAZO_STATE:-/state}/cache-engine"
uid="$(id -u)"
export XDG_RUNTIME_DIR="/tmp/runtime-$uid"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

exec xvfb-run -a -s "-screen 0 ${XVFB_SCREEN:-2560x1600x24} -nolisten tcp" \
  python -m engine.server "$@"
