# syntax=docker/dockerfile:1
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Two images from one Dockerfile:
#
#   engine (default target)  IngeTrazo, headless: the app from a git ref on
#       GitHub (unchanged), an X server in memory for its OpenGL context
#       (Mesa llvmpipe) and this repo's services around it: MCP over HTTP
#       (`mcp`), the render engine for a separate viewer (`engine`), and,
#       as before, the viewer with its own IngeTrazos (`viewer`).
#   viewer  (--target viewer)  the live web viewer alone, without IngeTrazo
#       or X server: it renders through engines (ENGINE_URLS).

# ---- three.js for the viewer's 3D view ------------------------------------------
# A pinned release (MIT), checked against its SHA-256, served by the viewer
# itself (no CDN at view time).
FROM python:3.12-slim-bookworm AS three
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN set -e; \
    base=https://cdn.jsdelivr.net/npm/three@0.170.0; \
    dir=/three; \
    fetch() { mkdir -p "$(dirname "$dir/$2")"; \
              curl -fsSL --retry 3 -o "$dir/$2" "$base/$1"; \
              echo "$3  $dir/$2" | sha256sum -c --quiet -; }; \
    fetch build/three.module.min.js three.module.min.js 08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7; \
    fetch examples/jsm/loaders/GLTFLoader.js addons/loaders/GLTFLoader.js 45139faddd5aaf48ed2d62203d976e5cbd703db1a592de40527f9f6cf58abd44; \
    fetch examples/jsm/controls/OrbitControls.js addons/controls/OrbitControls.js 80efaadea4f8a636a65fb0bd08bfef62f3d93a0bb94e2e7500f23176c5c07f4e; \
    fetch examples/jsm/utils/BufferGeometryUtils.js addons/utils/BufferGeometryUtils.js c25b7930e570e9ec56173cd3b866ec8d2e10016630db3937efb439daf1cedbf6; \
    fetch LICENSE LICENSE 4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454

# ---- viewer: the web viewer alone ------------------------------------------------
FROM python:3.12-slim-bookworm AS viewer

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini curl \
 && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY mcp_http/ /opt/ingetrazo-docker/mcp_http/
COPY viewer/ /opt/ingetrazo-docker/viewer/
COPY --from=three /three /opt/ingetrazo-docker/viewer/static/three
RUN chmod -R a+rX /opt/ingetrazo-docker

RUN useradd --create-home --uid 1000 ingetrazo \
 && mkdir -p /data /cache /state /users /engine \
 && chown ingetrazo:ingetrazo /data /cache /state /users /engine

ARG INGETRAZO_DOCKER_REF=dev
ENV INGETRAZO_DOCKER_VERSION=$INGETRAZO_DOCKER_REF \
    INGETRAZO_DATA=/data \
    INGETRAZO_CACHE=/cache \
    INGETRAZO_STATE=/state \
    PYTHONPATH=/opt/ingetrazo-docker

# Every module imports (stdlib only, no IngeTrazo needed).
RUN python -c "import viewer.server, viewer.engine, mcp_http.users"

USER ingetrazo
WORKDIR /home/ingetrazo
VOLUME ["/cache", "/state", "/users"]
EXPOSE 8080
ENV HEALTH_PORT=8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["viewer"]

HEALTHCHECK --interval=30s --timeout=15s --start-period=60s --retries=3 \
  CMD curl -fs "http://127.0.0.1:${HEALTH_PORT:-8080}/healthz" > /dev/null || exit 1

# ---- engine: IngeTrazo headless (the default target) -----------------------------
FROM python:3.12-slim-bookworm AS engine

# Which IngeTrazo to build. Headless mode is in IngeTrazo 0.6.3; the sheet
# and scene tools the viewer needs come with ingelibre/ingetrazo#1544 (and
# #1586, #1683, #1685; tracked in ingelibre/ingetrazo#1717). Until those
# are merged upstream the default is their branch on the fork, pinned by
# commit so a build is reproducible. Then: INGETRAZO_REPO=
# https://github.com/ingelibre/ingetrazo and INGETRAZO_REF=<release tag>
# (CI already builds an upstream release, see .github/workflows/ci.yml).
ARG INGETRAZO_REPO=https://github.com/BastelBaus/ingetrazo
ARG INGETRAZO_REF=187e43f9bc85a28b513f1d0b3f9c68a55dbf47a4

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Qt's xcb platform + OpenGL in software (llvmpipe), an in-memory X server,
# fonts for sheet texts, git for the clone (and openskp's pip install).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ca-certificates curl tini \
      xvfb xauth \
      libgl1 libegl1 libgl1-mesa-dri libglx-mesa0 \
      libxkbcommon0 libxkbcommon-x11-0 \
      libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
      libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
      libxcb-xfixes0 libxcb-xkb1 libxcb-sync1 libxcb-shm0 \
      libxrender1 libxi6 libxext6 libsm6 libice6 \
      libfontconfig1 libfreetype6 libdbus-1-3 libglib2.0-0 \
      libasound2 libpulse0 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6 \
      fonts-dejavu-core fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

# The app, exactly as published at INGETRAZO_REF.
WORKDIR /opt/ingetrazo
RUN git init -q . \
 && git remote add origin "$INGETRAZO_REPO" \
 && git fetch -q --depth 1 origin "$INGETRAZO_REF" \
 && git checkout -q FETCH_HEAD \
 && git log -1 --format='IngeTrazo %H (%cd)' > /opt/ingetrazo/BUILD_REF \
 && rm -rf .git \
 && grep -q -- '--headless' main.py \
 || { echo "ERROR: $INGETRAZO_REPO@$INGETRAZO_REF has no --headless mode (needs IngeTrazo 0.6.3 or later)" >&2; exit 1; }

RUN grep -viE '^\s*(pytest|truststore)' requirements.txt > /tmp/req.txt \
 && pip install -r /tmp/req.txt \
 && rm /tmp/req.txt

# The viewer's 3D view (three.js, see the first stage).
COPY --from=three /three /opt/ingetrazo-docker/viewer/static/three

# This repo's services.
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY mcp_http/ /opt/ingetrazo-docker/mcp_http/
COPY viewer/ /opt/ingetrazo-docker/viewer/
# A checkout with private file modes (umask 077, e.g. Komodo on TrueNAS) would
# copy unreadable sources for the unprivileged user.
RUN chmod -R a+rX /opt/ingetrazo-docker

RUN useradd --create-home --uid 1000 ingetrazo \
 && mkdir -p /data /cache /state /users /engine \
 && chown ingetrazo:ingetrazo /data /cache /state /users /engine

# This repo's version (tag or commit), shown in the viewer's footer and at
# /version. release.yml and compose.yaml pass it; a plain build says "dev".
ARG INGETRAZO_DOCKER_REF=dev
ENV INGETRAZO_DOCKER_VERSION=$INGETRAZO_DOCKER_REF

ENV QT_QPA_PLATFORM=xcb \
    LIBGL_ALWAYS_SOFTWARE=1 \
    INGETRAZO_APP=/opt/ingetrazo \
    INGETRAZO_DATA=/data \
    INGETRAZO_CACHE=/cache \
    INGETRAZO_STATE=/state \
    PYTHONPATH=/opt/ingetrazo-docker

# The install is complete (shaders, translations, textures…).
RUN python /opt/ingetrazo/main.py --check

USER ingetrazo
WORKDIR /home/ingetrazo
VOLUME ["/data", "/cache", "/state", "/users"]
EXPOSE 8765 8766 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["mcp"]

HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
  CMD curl -fs "http://127.0.0.1:${HEALTH_PORT:-8765}/healthz" > /dev/null || exit 1
