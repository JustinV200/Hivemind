# syntax=docker/dockerfile:1
#
# base-ubuntu: terminal-first Virtual Cell image (roadmap step 5.3).
#
# Every layer, and why it is there, is explained in this image's own README.md -- read that
# first; this file is deliberately light on prose so the two stay in sync (the README is the
# narrative, this is the mechanism). No model id or provider URL is ever pinned here
# (codingrules section 8.6; scripts/check_no_model_ids.py's CI gate covers images/ too): model
# access is entirely the Queen's own `[llm]` manifest, never anything baked into a Cell image.
#
# NOTE: Docker was not available in the environment that wrote this file. It has been reviewed
# carefully by reading, not by building; the integration workflow (a later roadmap step, once a
# Docker-enabled runner exists) is what proves it builds and runs. See the README's own "Building
# and testing this image" section for exactly what that workflow must check.

# ══════════════════════════════════════════════════════════════════════════════════════════════
# Stage 1: builder -- uv, the workspace lockfile, and exactly the "hivemind" package's own
# dependency closure (which pulls in "waggle" transitively; never "pollen", a different device's
# own connector with no reason to be inside a Virtual Cell, and never the dev dependency group:
# ruff, mypy, pytest, import-linter never belong inside a shipped Cell image).
# ══════════════════════════════════════════════════════════════════════════════════════════════
FROM ubuntu:24.04 AS builder

# ca-certificates: TLS for the uv installer and for PyPI; curl: fetches the installer.
# python3/python3-venv: the interpreter uv's own venv wraps (codingrules section 2: Python 3.12+;
# Ubuntu 24.04's default python3 is 3.12).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# uv itself (codingrules section 2: "never pip install by hand"), from astral's own installer,
# into a fixed location so PATH does not depend on which shell profile ran it.
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /build

# The uv workspace's own manifest, its one lockfile, and every member's own pyproject.toml (uv
# needs every `[tool.uv.workspace] members` entry on disk to resolve the workspace graph, even
# though the sync step below installs only "hivemind"'s own closure) -- see the README's "Build
# context" section for exactly what a real build must send.
COPY pyproject.toml uv.lock ./
COPY packages/waggle/pyproject.toml packages/waggle/pyproject.toml
COPY packages/hivemind/pyproject.toml packages/hivemind/pyproject.toml
COPY packages/pollen/pyproject.toml packages/pollen/pyproject.toml

# The two source trees this image actually runs; nothing from packages/pollen/src or
# packages/observation-web ever reaches this image.
COPY packages/waggle/src packages/waggle/src
COPY packages/hivemind/src packages/hivemind/src

# --frozen: fail rather than silently re-resolve a different dependency set than uv.lock records
# (codingrules section 2). --no-dev: never the dev tool group. --package hivemind: only hivemind
# and its own closure (waggle transitively), never pollen. --no-editable: install a built
# distribution, not an editable link back to /build/packages/*/src -- the runtime stage below
# copies only the resulting virtual environment, not this stage's source trees, so an editable
# install (a path-reference shim) would break at import time without this flag.
# The venv is created at the exact path the runtime stage mounts it at: every console script
# uv writes carries an absolute shebang (`#!/opt/hivemind/venv/bin/python`), so a venv built at
# /build/.venv and copied elsewhere would leave `hivemind-in-cell` pointing at a path that does
# not exist in the shipped image (the first real build found exactly that).
ENV UV_PROJECT_ENVIRONMENT=/opt/hivemind/venv
RUN uv sync --frozen --no-dev --no-editable --package hivemind

# ══════════════════════════════════════════════════════════════════════════════════════════════
# Stage 2: runtime -- the slim image every Virtual Cell actually boots.
# ══════════════════════════════════════════════════════════════════════════════════════════════
FROM ubuntu:24.04 AS runtime

# Only what the installed venv needs at run time: no compiler, no uv, no curl.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
    && rm -rf /var/lib/apt/lists/* \
    # A non-root user and its own data directory (codingrules section 15: least privilege; nothing
    # in this image runs as root once installed). /var/lib/hivemind/scratch is the same path
    # hivemind.cli.in_cell.config.DEFAULT_SCRATCH_ROOT names, where InCellSpawnSource creates each
    # lease's own scratch subdirectory.
    && useradd --create-home --shell /bin/bash hive \
    && mkdir -p /var/lib/hivemind/scratch \
    && chown -R hive:hive /var/lib/hivemind

COPY --from=builder --chown=hive:hive /opt/hivemind/venv /opt/hivemind/venv
ENV PATH="/opt/hivemind/venv/bin:${PATH}"

USER hive
WORKDIR /home/hive

# No EXPOSE: a Virtual Cell opens no inbound port (codingrules section 15). Every Waggle link
# this image makes is dialled OUT by hivemind-in-cell, toward HIVEMIND_QUEEN_WAGGLE_URL -- see
# the README's "Runtime configuration" table for every HIVEMIND_* variable it reads.

# The in-Cell Warden entry point (roadmap step 5.5): the console script
# packages/hivemind/pyproject.toml registers for hivemind.cli.in_cell.main:main. It runs until a
# signed Shutdown or CellTeardownRequest arrives from the Queen, or the link ends.
ENTRYPOINT ["hivemind-in-cell"]
