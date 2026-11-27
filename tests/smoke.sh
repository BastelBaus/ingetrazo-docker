#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# End-to-end smoke test of an image: the MCP service and the viewer, on a
# data folder with one of IngeTrazo's example models. With VIEWER_IMAGE also
# the split setup: the viewer image (no IngeTrazo) rendering through an
# engine container.
#
#   IMAGE=ingetrazo-docker:dev [VIEWER_IMAGE=ingetrazo-viewer:dev] tests/smoke.sh
set -euo pipefail
# Git Bash on Windows would rewrite container paths such as /opt/… .
export MSYS_NO_PATHCONV=1

IMAGE="${IMAGE:-ingetrazo-docker:dev}"
VIEWER_IMAGE="${VIEWER_IMAGE:-}"
MCP_PORT="${SMOKE_MCP_PORT:-18765}"
VIEWER_PORT="${SMOKE_VIEWER_PORT:-18080}"
TOKEN="smoke-$(date +%s)-token-0123456789"
PASSWORD="smoke-password"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${SMOKE_WORKDIR:-$(mktemp -d)}"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
mcp="smoke-mcp-$$"
mcp_oauth="smoke-mcp-oauth-$$"
OAUTH_PORT="${SMOKE_OAUTH_PORT:-18766}"
OAUTH_PASSWORD="smoke-oauth-password"
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
# Several model folders (MODEL_FOLDERS), MCP and viewer.
mcp_folders="smoke-mcp-folders-$$"
v_folders="smoke-viewer-folders-$$"
FOLDERS_MCP_PORT="${SMOKE_FOLDERS_MCP_PORT:-18767}"
FOLDERS_VIEWER_PORT="${SMOKE_FOLDERS_VIEWER_PORT:-18085}"
# Split: viewer image + render engine (engine image), on their own network.
s_engine="smoke-engine-$$"
s_viewer="smoke-viewer-split-$$"
s_net="smoke-split-net-$$"
SPLIT_PORT="${SMOKE_SPLIT_PORT:-18086}"
# Split MCP: the gateway (viewer image) + an engine in mcp mode.
s_mcp_engine="smoke-mcp-engine-$$"
s_gateway="smoke-mcp-gateway-$$"
GATEWAY_PORT="${SMOKE_GATEWAY_PORT:-18767}"

# Docker Desktop on Windows (Git Bash) wants a Windows path for -v.
host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }

cleanup() {
  status=$?
  if [ $status -ne 0 ]; then
    echo "---- logs: mcp ----";    docker logs "$mcp" 2>&1 | tail -60 || true
    echo "---- logs: viewer ----"; docker logs "$viewer" 2>&1 | tail -60 || true
    echo "---- logs: mcp (oauth) ----"; docker logs "$mcp_oauth" 2>&1 | tail -40 || true
    for c in "$v_none" "$v_proxy" "$v_oidc" "$oidc" "$mcp_folders" "$v_folders" \
             "$s_engine" "$s_viewer" "$s_mcp_engine" "$s_gateway"; do
      echo "---- logs: $c ----"; docker logs "$c" 2>&1 | tail -30 || true
    done
  fi
  docker rm -f -v "$mcp" "$viewer" "$mcp_oauth" "$v_none" "$v_proxy" "$v_oidc" "$oidc" \
    "$mcp_folders" "$v_folders" "$s_engine" "$s_viewer" \
    "$s_mcp_engine" "$s_gateway" >/dev/null 2>&1 || true
  docker network rm "$net" "$s_net" >/dev/null 2>&1 || true
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
  -e HEALTH_PORT=8080 -e ADMIN_PASSWORD="$PASSWORD" -e VIEWER_POLL_SECONDS=1 \
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

docker run -d --name "$mcp_oauth" -p "127.0.0.1:$OAUTH_PORT:8765" \
  -e MCP_AUTH=oauth -e PUBLIC_URL="http://127.0.0.1:$OAUTH_PORT" -e OAUTH_ALLOW_HTTP=true \
  -e OAUTH_USER=admin -e OAUTH_PASSWORD="$OAUTH_PASSWORD" "$IMAGE" mcp >/dev/null

"$PY" "$TESTS/smoke_mcp.py" "http://127.0.0.1:$MCP_PORT" "$TOKEN"
"$PY" "$TESTS/smoke_oauth.py" "http://127.0.0.1:$OAUTH_PORT" admin "$OAUTH_PASSWORD"
echo "== MCP_AUTH=oauth refuses to start without an https PUBLIC_URL"
if out="$(docker run --rm -e MCP_AUTH=oauth -e PUBLIC_URL=http://example.org \
            -e OAUTH_PASSWORD="$OAUTH_PASSWORD" "$IMAGE" mcp 2>&1)"; then
  echo "it started"; exit 1
fi
echo "$out" | grep -q "PUBLIC_URL must be https"
echo "  ok"
"$PY" "$TESTS/smoke_viewer.py" "http://127.0.0.1:$VIEWER_PORT" admin "$PASSWORD"

# Two model folders: work (rw) and library (ro, *.bak.igz not listed).
mkdir -p "$WORK/folders/work" "$WORK/folders/library"
chmod 777 "$WORK/folders/work" "$WORK/folders/library"
cp "$WORK/data/arco.igz" "$WORK/folders/library/arco.igz"
cp "$WORK/data/arco.igz" "$WORK/folders/library/arco.bak.igz"
F_WORK="$(host_path "$WORK/folders/work")"
F_LIB="$(host_path "$WORK/folders/library")"
FOLDERS="work:rw; library:ro:exclude=*.bak.igz"
docker run -d --name "$mcp_folders" -p "127.0.0.1:$FOLDERS_MCP_PORT:8765" \
  -e MCP_AUTH=token -e MCP_TOKEN="$TOKEN" -e INGETRAZO_MODEL=library/arco.igz \
  -e MODEL_FOLDERS="$FOLDERS" \
  -v "$F_WORK:/data/work" -v "$F_LIB:/data/library:ro" "$IMAGE" mcp >/dev/null
docker run -d --name "$v_folders" -p "127.0.0.1:$FOLDERS_VIEWER_PORT:8080" \
  -e VIEWER_AUTH_MODE=none -e VIEWER_PRERENDER=none -e VIEWER_POLL_SECONDS=1 \
  -e MODEL_FOLDERS="$FOLDERS" \
  -v "$F_WORK:/data/work:ro" -v "$F_LIB:/data/library:ro" "$IMAGE" viewer >/dev/null
"$PY" "$TESTS/smoke_folders.py" "http://127.0.0.1:$FOLDERS_MCP_PORT" "$TOKEN" \
  "http://127.0.0.1:$FOLDERS_VIEWER_PORT"
echo "== MODEL_FOLDERS with a mistake refuses to start"
if out="$(docker run --rm -e MODEL_FOLDERS="a:rx" -e MCP_TOKEN="$TOKEN" "$IMAGE" mcp 2>&1)"; then
  echo "it started"; exit 1
fi
echo "$out" | grep -q "MODEL_FOLDERS"
echo "  ok"

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
users_in() {
  docker exec "$1" python -c 'import sqlite3; print([r[0] for r in sqlite3.connect("/state/users.db").execute("select name from users")])'
}
users_in "$v_oidc" | grep -q "'alice'"
users_in "$v_proxy" | grep -q "'bob'"
echo "  ok  oidc and proxy users recorded in the user list (users.db)"

health_of=("$mcp" "$viewer")
if [ -n "$VIEWER_IMAGE" ]; then
  echo "== split: viewer image ($VIEWER_IMAGE) + render engine"
  echo "-- the viewer image has no IngeTrazo and no X server"
  docker run --rm --entrypoint sh "$VIEWER_IMAGE" -c \
    'test ! -e /opt/ingetrazo && ! command -v xvfb-run >/dev/null'
  echo "  ok"
  echo "-- the viewer image refuses to start without ENGINE_URLS"
  if out="$(docker run --rm -e ADMIN_PASSWORD="$PASSWORD" "$VIEWER_IMAGE" viewer 2>&1)"; then
    echo "it started"; exit 1
  fi
  echo "$out" | grep -q "ENGINE_URLS"
  echo "  ok"
  echo "-- the engine refuses to start without a secret"
  if out="$(docker run --rm "$IMAGE" engine 2>&1)"; then
    echo "it started"; exit 1
  fi
  echo "$out" | grep -q "ENGINE_TOKEN"
  echo "  ok"

  mkdir -p "$WORK/split-cache" "$WORK/engine-secret"
  chmod 777 "$WORK/split-cache" "$WORK/engine-secret"
  S_CACHE="$(host_path "$WORK/split-cache")"
  S_SECRET="$(host_path "$WORK/engine-secret")"
  docker network create "$s_net" >/dev/null
  # The engine makes the secret in the shared folder; the viewer reads it.
  docker run -d --name "$s_engine" --network "$s_net" --network-alias viewer-engine \
    -e HEALTH_PORT=8766 -e ENGINE_TOKEN_FILE=/engine/token \
    -v "$DATA:/data:ro" -v "$S_CACHE:/cache" -v "$S_SECRET:/engine" \
    "$IMAGE" engine >/dev/null
  docker run -d --name "$s_viewer" --network "$s_net" -p "127.0.0.1:$SPLIT_PORT:8080" \
    -e ADMIN_PASSWORD="$PASSWORD" -e VIEWER_POLL_SECONDS=1 \
    -e ENGINE_URLS=http://viewer-engine:8766 -e ENGINE_TOKEN_FILE=/engine/token \
    -v "$DATA:/data:ro" -v "$S_CACHE:/cache" -v "$S_SECRET:/engine:ro" \
    "$VIEWER_IMAGE" viewer >/dev/null
  "$PY" "$TESTS/smoke_viewer.py" "http://127.0.0.1:$SPLIT_PORT" admin "$PASSWORD"
  echo "-- the engine turns away a wrong secret and tools the viewer does not use"
  docker exec "$s_viewer" python - <<'PY'
import json, urllib.error, urllib.request
token = open("/engine/token").read().strip()
def call(tool, args, tok):
    req = urllib.request.Request("http://viewer-engine:8766/call",
        json.dumps({"tool": tool, "args": args}).encode(),
        {"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
assert call("list_documents", {}, "wrong-" + token) == 401
assert call("save_document", {"path": "/data/x.igz"}, token) == 403
assert call("export_pdf", {"path": "/data/x.pdf"}, token) == 403
assert call("list_documents", {}, token) == 200
PY
  echo "  ok"
  health_of+=("$s_engine" "$s_viewer")

  echo "== split MCP: gateway ($VIEWER_IMAGE) + engine in mcp mode"
  echo "-- the gateway refuses to start without MCP_ENGINE_URL"
  if out="$(docker run --rm -e MCP_TOKEN="$TOKEN" "$VIEWER_IMAGE" mcp 2>&1)"; then
    echo "it started"; exit 1
  fi
  echo "$out" | grep -q "MCP_ENGINE_URL"
  echo "  ok"
  mkdir -p "$WORK/mcp-engine-secret"
  chmod 777 "$WORK/mcp-engine-secret"
  S_MSECRET="$(host_path "$WORK/mcp-engine-secret")"
  docker run -d --name "$s_mcp_engine" --network "$s_net" --network-alias mcp-engine \
    -e ENGINE_MODE=mcp -e HEALTH_PORT=8766 -e ENGINE_TOKEN_FILE=/engine/token \
    -e INGETRAZO_MODEL=arco.igz -v "$DATA:/data" -v "$S_MSECRET:/engine" \
    "$IMAGE" engine >/dev/null
  docker run -d --name "$s_gateway" --network "$s_net" -p "127.0.0.1:$GATEWAY_PORT:8765" \
    -e HEALTH_PORT=8765 -e MCP_AUTH=token -e MCP_TOKEN="$TOKEN" \
    -e MCP_ENGINE_URL=http://mcp-engine:8766 -e ENGINE_TOKEN_FILE=/engine/token \
    -v "$DATA:/data:ro" -v "$S_MSECRET:/engine:ro" "$VIEWER_IMAGE" mcp >/dev/null
  "$PY" "$TESTS/smoke_mcp.py" "http://127.0.0.1:$GATEWAY_PORT" "$TOKEN"
  health_of+=("$s_mcp_engine" "$s_gateway")
fi

for c in "${health_of[@]}"; do
  for _ in $(seq 1 60); do
    h="$(docker inspect -f '{{.State.Health.Status}}' "$c")"
    [ "$h" = healthy ] && break
    sleep 3
  done
  echo "== docker health of $c: $h"
  [ "$h" = healthy ]
done
echo "== smoke test passed"
