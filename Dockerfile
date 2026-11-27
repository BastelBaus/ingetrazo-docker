# syntax=docker/dockerfile:1
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The IngeTrazo engine: IngeTrazo headless, from a git ref on GitHub
# (unchanged), in an in-memory X server for its OpenGL context (Mesa
# llvmpipe), behind a small internal HTTP API (engine/server.py). It serves
# the MCP gateway and the live viewer of ingetrazo-web-viewer
# (https://github.com/BastelBaus/ingetrazo-web-viewer) over an internal
# network: ENGINE_MODE=mcp for the gateway, render (default) for the viewer.

FROM python:3.12-slim-bookworm

# Which IngeTrazo to build. Headless mode is in IngeTrazo 0.6.3; the sheet
# and scene tools the web viewer needs come with ingelibre/ingetrazo#1544 (and
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

# The engine's HTTP API around it.
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY engine/ /opt/ingetrazo-docker/engine/
# A checkout with private file modes (umask 077, e.g. Komodo on TrueNAS) would
# copy unreadable sources for the unprivileged user.
RUN chmod -R a+rX /opt/ingetrazo-docker

RUN useradd --create-home --uid 1000 ingetrazo \
 && mkdir -p /data /cache /state /engine \
 && chown ingetrazo:ingetrazo /data /cache /state /engine

# This repo's version (tag or commit), logged at start. publish.yml passes
# it; a plain build says "dev".
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
VOLUME ["/data", "/cache", "/state"]
EXPOSE 8766
ENV HEALTH_PORT=8766

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["engine"]

HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
  CMD curl -fs "http://127.0.0.1:${ENGINE_PORT:-8766}/healthz" > /dev/null || exit 1
