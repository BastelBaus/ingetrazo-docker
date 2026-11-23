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
# The other VIEWER_AUTH_MODEs: none, proxy, oidc (with a mock provider).
v_none="smoke-viewer-none-$$"
v_proxy="smoke-viewer-proxy-$$"
v_oidc="smoke-viewer-oidc-$$"
oidc="smoke-oidc-$$"
net="smoke-net-$$"
SUB="10.231.$((RANDOM % 200 + 20))"
NONE_PORT="${SMOKE_NONE_PORT:-18081}"
PROXY_PORT="${SMOKE_PROXY_PORT:-18082}"
OIDC_VIEWER_PORT="${SMOKE_OIDC_VIEWER_PORT:-18083}"
OIDC_PORT="${SMOKE_OIDC_PORT:-18084}"

# Docker Desktop on Windows (Git Bash) wants a Windows path for -v.
host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }

cleanup() {
  status=$?
  if [ $status -ne 0 ]; then
    echo "---- logs: mcp ----";    docker logs "$mcp" 2>&1 | tail -60 || true
    echo "---- logs: viewer ----"; docker logs "$viewer" 2>&1 | tail -60 || true
    for c in "$v_none" "$v_proxy" "$v_oidc" "$oidc"; do
      echo "---- logs: $c ----"; docker logs "$c" 2>&1 | tail -30 || true
    done
  fi
  docker rm -f -v "$mcp" "$viewer" "$v_none" "$v_proxy" "$v_oidc" "$oidc" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
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

TESTS="$(host_path "$HERE")"
docker network create --subnet "$SUB.0/24" "$net" >/dev/null
docker run -d --name "$v_none" -p "127.0.0.1:$NONE_PORT:8080" \
  -e VIEWER_AUTH_MODE=none -v "$DATA:/data:ro" "$IMAGE" viewer >/dev/null
docker run -d --name "$v_proxy" --network "$net" --ip "$SUB.5" \
  -p "127.0.0.1:$PROXY_PORT:8080" -e VIEWER_AUTH_MODE=proxy \
  -e PROXY_TRUSTED_IPS="$SUB.10/32" -e PROXY_GROUPS_HEADER=X-authentik-groups \
  -e PROXY_ADMIN_GROUP=viewer-admins -v "$DATA:/data:ro" "$IMAGE" viewer >/dev/null
docker run -d --name "$oidc" --network "$net" --network-alias oidc \
  -p "127.0.0.1:$OIDC_PORT:9000" -v "$TESTS:/tests:ro" --entrypoint python \
  "$IMAGE" /tests/mock_oidc.py http://oidc:9000 9000 viewer-smoke smoke-secret >/dev/null
docker run -d --name "$v_oidc" --network "$net" -p "127.0.0.1:$OIDC_VIEWER_PORT:8080" \
  -e VIEWER_AUTH_MODE=oidc -e OIDC_ISSUER_URL=http://oidc:9000 \
  -e OIDC_CLIENT_ID=viewer-smoke -e OIDC_CLIENT_SECRET=smoke-secret \
  -e OIDC_REDIRECT_URI="http://127.0.0.1:$OIDC_VIEWER_PORT/auth/callback" \
  -e OIDC_ALLOW_HTTP=true -e OIDC_ADMIN_GROUP=viewer-admins \
  -v "$DATA:/data:ro" "$IMAGE" viewer >/dev/null

"$PY" "$TESTS/smoke_mcp.py" "http://127.0.0.1:$MCP_PORT" "$TOKEN"
"$PY" "$TESTS/smoke_viewer.py" "http://127.0.0.1:$VIEWER_PORT" viewer "$PASSWORD"

echo "== viewer, VIEWER_AUTH_MODE=local without a password"
if out="$(docker run --rm "$IMAGE" viewer 2>&1)"; then
  echo "the viewer started without a password"; exit 1
fi
echo "$out" | grep -q "VIEWER_AUTH_MODE=none"
echo "  ok  refuses to start, and says how to open it on purpose"
"$PY" "$TESTS/smoke_auth.py" none "http://127.0.0.1:$NONE_PORT"
"$PY" "$TESTS/smoke_auth.py" proxy-untrusted "http://127.0.0.1:$PROXY_PORT"
docker run --rm --network "$net" --ip "$SUB.10" -v "$TESTS:/tests:ro" \
  --entrypoint python "$IMAGE" /tests/smoke_auth.py proxy-trusted "http://$SUB.5:8080"
"$PY" "$TESTS/smoke_auth.py" oidc "http://127.0.0.1:$OIDC_VIEWER_PORT" \
  "http://127.0.0.1:$OIDC_PORT"
docker exec "$v_oidc" cat /state/users.json | grep -q '"alice"'
docker exec "$v_proxy" cat /state/users.json | grep -q '"bob"'
echo "  ok  oidc and proxy users recorded in /state/users.json"

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
