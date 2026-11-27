#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# End-to-end smoke test of the engine image: one engine in each mode on a
# data folder with one of IngeTrazo's example models. (The services that use
# the engines are tested in ingetrazo-web-viewer, against this image.)
#
#   IMAGE=ingetrazo-docker:dev tests/smoke.sh
set -euo pipefail
# Git Bash on Windows would rewrite container paths such as /opt/… .
export MSYS_NO_PATHCONV=1

IMAGE="${IMAGE:-ingetrazo-docker:dev}"
TOKEN="smoke-$(date +%s)-engine-0123456789"
MCP_PORT="${SMOKE_MCP_ENGINE_PORT:-18766}"
RENDER_PORT="${SMOKE_RENDER_ENGINE_PORT:-18767}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${SMOKE_WORKDIR:-$(mktemp -d)}"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
mcp="smoke-mcp-engine-$$"
render="smoke-render-engine-$$"

# Docker Desktop on Windows (Git Bash) wants a Windows path for -v.
host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }

cleanup() {
  status=$?
  if [ $status -ne 0 ]; then
    for c in "$mcp" "$render"; do
      echo "---- logs: $c ----"; docker logs "$c" 2>&1 | tail -60 || true
    done
  fi
  docker rm -f -v "$mcp" "$render" >/dev/null 2>&1 || true
  exit $status
}
trap cleanup EXIT

mkdir -p "$WORK/data" "$WORK/cache/smoke" "$WORK/secret"
chmod 777 "$WORK/data" "$WORK/cache" "$WORK/cache/smoke" "$WORK/secret"
docker run --rm --entrypoint cat "$IMAGE" /opt/ingetrazo/examples/arco-yanque.igz \
  > "$WORK/data/arco.igz"
DATA="$(host_path "$WORK/data")"
CACHE="$(host_path "$WORK/cache")"
SECRET="$(host_path "$WORK/secret")"

echo "== image: $IMAGE ($(docker run --rm --entrypoint cat "$IMAGE" /opt/ingetrazo/BUILD_REF))"

echo "-- refuses to start without a secret, or with an unknown ENGINE_MODE"
if out="$(docker run --rm "$IMAGE" engine 2>&1)"; then
  echo "it started"; exit 1
fi
echo "$out" | grep -q "ENGINE_TOKEN"
if out="$(docker run --rm -e ENGINE_TOKEN="$TOKEN" -e ENGINE_MODE=x "$IMAGE" engine 2>&1)"; then
  echo "it started"; exit 1
fi
echo "$out" | grep -q "ENGINE_MODE"
echo "  ok"

# The mcp engine gets its secret from ENGINE_TOKEN, the render engine makes
# one in the shared folder (ENGINE_TOKEN_FILE), as in compose.
docker run -d --name "$mcp" -p "127.0.0.1:$MCP_PORT:8766" \
  -e ENGINE_MODE=mcp -e ENGINE_TOKEN="$TOKEN" -e INGETRAZO_MODEL=arco.igz \
  -v "$DATA:/data" "$IMAGE" engine >/dev/null
docker run -d --name "$render" -p "127.0.0.1:$RENDER_PORT:8766" \
  -e ENGINE_TOKEN_FILE=/engine/token \
  -v "$DATA:/data:ro" -v "$CACHE:/cache" -v "$SECRET:/engine" "$IMAGE" engine >/dev/null
# The file is the engine user's (0600): read it from inside.
RENDER_TOKEN=""
for _ in $(seq 1 60); do
  RENDER_TOKEN="$(docker exec "$render" cat /engine/token 2>/dev/null || true)"
  [ -n "$RENDER_TOKEN" ] && break
  sleep 1
done
[ "${#RENDER_TOKEN}" -ge 16 ]
echo "  ok  the render engine made its secret"

"$PY" "$HERE/smoke_engine.py" "http://127.0.0.1:$MCP_PORT" "$TOKEN" \
  "http://127.0.0.1:$RENDER_PORT" "$RENDER_TOKEN"
test -s "$WORK/data/engine-saved.igz"
echo "  ok  the mcp engine saved into /data"
ls "$WORK/cache/smoke/"*.png >/dev/null
test -s "$WORK/cache/smoke/all.pdf"
echo "  ok  the render engine wrote its files into /cache"

for c in "$mcp" "$render"; do
  for _ in $(seq 1 60); do
    h="$(docker inspect -f '{{.State.Health.Status}}' "$c")"
    [ "$h" = healthy ] && break
    sleep 3
  done
  echo "== docker health of $c: $h"
  [ "$h" = healthy ]
done
echo "== smoke test passed"
