# syntax=docker/dockerfile:1
#
# desktop-ubuntu: the Exoskeleton-ready Virtual Cell image (roadmap step 6.1, ADR-0031).
#
# base-ubuntu plus everything the Exoskeleton (the optional display, input, audio and browser
# attachment of a Cell) needs to start on demand: Xvfb, a light window manager, xdotool,
# ImageMagick's screen capture, xauth, PulseAudio with its clients, a Chromium build, and fonts.
# Nothing is started at boot: a task that needs a desktop gets a fresh display, sound server and
# browser from `hivemind.exoskeleton.attach`, owned by its lease and stopped on detach, so an
# Overwintered Cell never carries one task's windows into the next (ADR-0031). What every layer is
# and why lives in this image's README.md ("Layers, in order"); this file stays light on prose so
# the two stay in sync, like images/base-ubuntu/Dockerfile.
#
# Build it after base-ubuntu, from the repository root:
#   docker build -t hivemind/base-ubuntu:dev -f images/base-ubuntu/Dockerfile .
#   docker build -t hivemind/desktop-ubuntu:dev -f images/desktop-ubuntu/Dockerfile .

ARG BASE_IMAGE=hivemind/base-ubuntu:dev

# ══════════════════════════════════════════════════════════════════════════════════════════════
# Stage 1: builder -- the same frozen, dev-free sync as base-ubuntu's builder, plus the `browser`
# extra (Playwright, the fast path's driver), into the venv path the runtime stage replaces.
# ══════════════════════════════════════════════════════════════════════════════════════════════
FROM ubuntu:24.04 AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# uv bootstrapped from PyPI, exactly as images/base-ubuntu/Dockerfile does and for the same reason.
RUN python3 -m venv /opt/uv-bootstrap \
    && /opt/uv-bootstrap/bin/pip install --no-cache-dir "uv>=0.8.17" \
    && ln -s /opt/uv-bootstrap/bin/uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/waggle/pyproject.toml packages/waggle/pyproject.toml
COPY packages/hivemind/pyproject.toml packages/hivemind/pyproject.toml
COPY packages/pollen/pyproject.toml packages/pollen/pyproject.toml
COPY packages/waggle/src packages/waggle/src
COPY packages/hivemind/src packages/hivemind/src

ENV UV_PROJECT_ENVIRONMENT=/opt/hivemind/venv
RUN uv sync --frozen --no-dev --no-editable --package hivemind --extra browser

# ══════════════════════════════════════════════════════════════════════════════════════════════
# Stage 2: runtime -- base-ubuntu, the desktop toolchain, the browser-capable venv, Chromium.
# ══════════════════════════════════════════════════════════════════════════════════════════════
FROM ${BASE_IMAGE} AS runtime

# Root only to install; the image drops back to `hive` below, as base-ubuntu runs.
USER root

# The display, input, capture, audio and font packages; README.md explains each one. The X11
# socket directory is created world-writable and sticky, as a desktop system has it, so the
# unprivileged Xvfb a lease starts can create its own socket there.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb \
        openbox \
        xdotool \
        xauth \
        x11-utils \
        imagemagick \
        pulseaudio \
        pulseaudio-utils \
        fonts-dejavu-core \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

# The browser-capable venv replaces base-ubuntu's own at the same path, so every console script's
# absolute shebang still resolves (images/base-ubuntu/Dockerfile's own lesson).
COPY --from=builder --chown=hive:hive /opt/hivemind/venv /opt/hivemind/venv

# Chromium, from Playwright's own build of it, with the system libraries it needs; a fixed path
# every user can read, which the Exoskeleton's browser locator scans (ADR-0031). Ubuntu 24.04's
# own chromium package is only a shim that installs a snap, which cannot run in a container.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN /opt/hivemind/venv/bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright-browsers \
    && rm -rf /var/lib/apt/lists/*

USER hive
WORKDIR /home/hive

# The ENTRYPOINT is base-ubuntu's own `hivemind-in-cell`, unchanged: the in-Cell Warden dials the
# Queen, and its probe reports can_start_display, has_audio and has_browser from what is installed
# above (hivemind.cell.local.probe), so placement sends Exoskeleton work here.
