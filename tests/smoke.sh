#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# End-to-end smoke test of an image: the MCP service and the viewer, on a
# data folder with one of IngeTrazo's example models.
#
#   IMAGE=ingetrazo-docker:dev tests/smoke.sh
set -euo pipefail
# Git Bash on Windows would rewrite container paths such as /opt/… .
export MSYS_NO_PATHCONV=1

IMAGE="${IMAGE:-ingetrazo-docker:dev}"
MCP_PORT="${SMOKE_MCP_PORT:-18765}"
VIEWER_PORT="${SMOKE_VIEWER_PORT:-18080}"
TOKEN="smoke-$(date +%s)-token-0123456789"
PASSWORD="smoke-password"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${SMOKE_WORKDIR:-$(mktemp -d)}"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
mcp="smoke-mcp-$$"
viewer="smoke-viewer-$$"

# Docker Desktop on Windows (Git Bash) wants a Windows path for -v.
host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }

cleanup() {
  status=$?
  if [ $status -ne 0 ]; then
    echo "---- logs: mcp ----";    docker logs "$mcp" 2>&1 | tail -60 || true
    echo "---- logs: viewer ----"; docker logs "$viewer" 2>&1 | tail -60 || true
  fi
  docker rm -f "$mcp" "$viewer" >/dev/null 2>&1 || true
  exit $status
}
trap cleanup EXIT

mkdir -p "$WORK/data" "$WORK/cache"
chmod 777 "$WORK/data" "$WORK/cache"
docker run --rm --entrypoint cat "$IMAGE" /opt/ingetrazo/examples/arco-yanque.igz \
  > "$WORK/data/arco.igz"
DATA="$(host_path "$WORK/data")"
CACHE="$(host_path "$WORK/cache")"

echo "== image: $IMAGE ($(docker run --rm --entrypoint cat "$IMAGE" /opt/ingetrazo/BUILD_REF))"

docker run -d --name "$mcp" -p "127.0.0.1:$MCP_PORT:8765" \
  -e MCP_AUTH=token -e MCP_TOKEN="$TOKEN" -e INGETRAZO_MODEL=arco.igz \
  -v "$DATA:/data" "$IMAGE" mcp >/dev/null
docker run -d --name "$viewer" -p "127.0.0.1:$VIEWER_PORT:8080" \
  -e HEALTH_PORT=8080 -e VIEWER_PASSWORD="$PASSWORD" -e VIEWER_POLL_SECONDS=1 \
  -v "$DATA:/data:ro" -v "$CACHE:/cache" "$IMAGE" viewer >/dev/null

"$PY" "$(host_path "$HERE")/smoke_mcp.py" "http://127.0.0.1:$MCP_PORT" "$TOKEN"
"$PY" "$(host_path "$HERE")/smoke_viewer.py" "http://127.0.0.1:$VIEWER_PORT" viewer "$PASSWORD"

for c in "$mcp" "$viewer"; do
  for _ in $(seq 1 60); do
    h="$(docker inspect -f '{{.State.Health.Status}}' "$c")"
    [ "$h" = healthy ] && break
    sleep 3
  done
  echo "== docker health of $c: $h"
  [ "$h" = healthy ]
done
echo "== smoke test passed"
