#!/bin/sh
# night-veil-env.sh (roadmap step 5.3a): bridge the backend's own environment to systemd units.
#
# A container/VM backend (hivemind.hive.backends.docker/.qemu) sets every HIVEMIND_* variable
# (hivemind.hive.backends.bootstrap.CellBootstrap.environment()) on PID 1 alone -- Docker's own
# `docker run -e` and QEMU's cloud-init both work that way. Ordinary process environment
# inheritance would carry that straight into a plain ENTRYPOINT (base-ubuntu's own design), but
# systemd, running as PID 1 here instead, does not automatically forward its own environment to
# the units it starts. This script reads PID 1's environment once, at boot, and writes every
# HIVEMIND_* entry to /run/hivemind/env in systemd's own EnvironmentFile format, so
# hivemind-warden.service's `EnvironmentFile=-/run/hivemind/env` can pick it up. Only HIVEMIND_*
# is copied -- nothing else PID 1 happens to have set is this Cell's business to forward.
set -eu

mkdir -p /run/hivemind
: > /run/hivemind/env
# /proc/1/environ is NUL-separated, not newline-separated; xargs -0 turns each entry into its own
# line so the grep/redirect below can filter and write it in systemd's own KEY=value form.
xargs -0 -n1 < /proc/1/environ | grep '^HIVEMIND_' > /run/hivemind/env || true
chmod 0600 /run/hivemind/env
