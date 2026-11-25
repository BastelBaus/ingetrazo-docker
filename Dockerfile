# syntax=docker/dockerfile:1
# SPDX-License-Identifier: GPL-3.0-or-later
#
# IngeTrazo, headless, in a container: the app from a git ref on GitHub
# (unchanged), an X server in memory for its OpenGL context (Mesa llvmpipe),
# and this repo's small services around it (MCP over HTTP, live viewer).

FROM python:3.12-slim-bookworm

# Which IngeTrazo to build. Headless mode is in IngeTrazo 0.6.3; the sheet
# and scene tools come with ingelibre/ingetrazo#1543, #1544 and #1545.
# Until those are merged upstream the default is their branch on the fork
# (feature/sheet-sets), pinned by commit so a build is reproducible.
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

# The viewer's 3D view runs three.js (MIT) in the browser: a pinned release,
# checked against its SHA-256, served by the viewer itself (no CDN at view time).
RUN set -e; \
    base=https://cdn.jsdelivr.net/npm/three@0.170.0; \
    dir=/opt/ingetrazo-docker/viewer/static/three; \
    fetch() { mkdir -p "$(dirname "$dir/$2")"; \
              curl -fsSL --retry 3 -o "$dir/$2" "$base/$1"; \
              echo "$3  $dir/$2" | sha256sum -c --quiet -; }; \
    fetch build/three.module.min.js three.module.min.js 08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7; \
    fetch examples/jsm/loaders/GLTFLoader.js addons/loaders/GLTFLoader.js 45139faddd5aaf48ed2d62203d976e5cbd703db1a592de40527f9f6cf58abd44; \
    fetch examples/jsm/controls/OrbitControls.js addons/controls/OrbitControls.js 80efaadea4f8a636a65fb0bd08bfef62f3d93a0bb94e2e7500f23176c5c07f4e; \
    fetch examples/jsm/utils/BufferGeometryUtils.js addons/utils/BufferGeometryUtils.js c25b7930e570e9ec56173cd3b866ec8d2e10016630db3937efb439daf1cedbf6; \
    fetch LICENSE LICENSE 4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454

# This repo's services.
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY mcp_http/ /opt/ingetrazo-docker/mcp_http/
COPY viewer/ /opt/ingetrazo-docker/viewer/
# A checkout with private file modes (umask 077, e.g. Komodo on TrueNAS) would
# copy unreadable sources for the unprivileged user.
RUN chmod -R a+rX /opt/ingetrazo-docker

RUN useradd --create-home --uid 1000 ingetrazo \
 && mkdir -p /data /cache /state /users \
 && chown ingetrazo:ingetrazo /data /cache /state /users

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
EXPOSE 8765 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["mcp"]

HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
  CMD curl -fs "http://127.0.0.1:${HEALTH_PORT:-8765}/healthz" > /dev/null || exit 1
