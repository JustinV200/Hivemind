#!/usr/bin/env bash
# pollinate.sh: put this Linux or macOS machine on the Hive's private network, enrol it in the
# Swarm and start pollen at every login. Written by `hive swarm packet` for @@HIVE_HOST@@.
#
# Run it from the packet folder, as the account pollen should run under (it asks for sudo only
# to install Tailscale and bring it up):
#     bash ./pollinate.sh
#
# Safe to run again: each step checks whether it is already done. The invite and the Tailscale
# key in ./secrets are never printed, are handed to the programs that use them by file or
# standard input (never on a command line), and are deleted when this script ends, whether it
# worked or not.

set -euo pipefail

HIVE_HOST=@@HIVE_HOST_QUOTED@@        # The Hive Stand's name on the tailnet (MagicDNS).
HIVE_PORT='@@HIVE_PORT@@'          # The swarm listener's port.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS="$HERE/secrets"
AUTH_KEY_FILE="$SECRETS/tailscale-authkey.txt"
INVITE_FILE="$SECRETS/invite.txt"
POLLEN="$HOME/.local/bin/pollen"   # Per user: no root needed to run it.
WAIT_SECONDS=120                   # How long to wait for the tailnet and the Hive Stand.
MAC_TAILSCALE=/Applications/Tailscale.app/Contents/MacOS/Tailscale   # The Mac app's own CLI.

say() { printf 'pollinate: %s\n' "$*"; }
fail() { printf 'pollinate: FAILED: %s\n' "$*" >&2; exit 1; }
# Before the trap is armed: the secrets stay, and the same packet works once the cause is fixed.
stop() { printf 'pollinate: STOPPED: %s Nothing was used up.\n' "$*" >&2; exit 1; }

# Whatever happens once the run starts, the secrets go: a failed run needs a new packet.
remove_secrets() {
  if [ -d "$SECRETS" ]; then rm -rf -- "$SECRETS"; say "Deleted this packet's secrets."; fi
}

as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

# The Mac app does not put `tailscale` on PATH; its CLI lives inside the app bundle. A path, not a
# function, so `sudo` can run it too.
tailscale_bin() { command -v tailscale 2>/dev/null || printf '%s\n' "$MAC_TAILSCALE"; }

tailscale_installed() { command -v tailscale >/dev/null 2>&1 || [ -x "$MAC_TAILSCALE" ]; }

install_tailscale() {
  if tailscale_installed; then say 'Tailscale is already installed.'; return; fi
  # Linux only (preflight stopped a Mac without it): Tailscale's own installer, which adds its
  # signed package repository.
  say 'Installing Tailscale...'; curl -fsSL https://tailscale.com/install.sh | sh
}

tailnet_running() {
  "$(tailscale_bin)" status --json --peers=false 2>/dev/null | grep -q '"BackendState": *"Running"'
}

tagged_hive_device() {
  "$(tailscale_bin)" status --json --peers=false 2>/dev/null | grep -q '"tag:hive-device"'
}

# Join with the single-use key read from its file, so it is never on a command line. The node is
# not ephemeral: it stays in the tailnet while offline and reconnects by itself on any network.
join_tailnet() {
  # Running here means this packet's tagged node (a re-run): preflight stopped any other account.
  if tailnet_running; then say 'This machine is already on the tailnet.'; return; fi
  [ -f "$AUTH_KEY_FILE" ] || fail 'This packet has been used: its Tailscale key is gone. Ask for a new packet.'
  say 'Joining the tailnet...'
  local code=0
  as_root "$(tailscale_bin)" up --auth-key="file:$AUTH_KEY_FILE" --reset --timeout="${WAIT_SECONDS}s" || code=$?
  rm -f -- "$AUTH_KEY_FILE"   # Spent or refused, it is of no further use.
  [ "$code" -eq 0 ] || fail "tailscale up failed (exit $code)."
}

hive_stand_answers() {
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 3 "$HIVE_HOST" "$HIVE_PORT" >/dev/null 2>&1
  else
    timeout 3 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "$HIVE_HOST" "$HIVE_PORT" >/dev/null 2>&1
  fi
}

wait_hive_stand() {
  local waited=0
  until hive_stand_answers; do
    if [ "$waited" -ge "$WAIT_SECONDS" ]; then
      fail "The Hive Stand $HIVE_HOST:$HIVE_PORT does not resolve or answer over the tailnet. Is the Hive running, and does the tailnet policy let tag:hive-device reach it?"
    fi
    sleep 3; waited=$((waited + 3))
  done
  say "The Hive Stand answers at $HIVE_HOST:$HIVE_PORT."
}

install_pollen() {
  if [ -f "$POLLEN" ] && cmp -s "$HERE/pollen" "$POLLEN"; then say 'pollen is already installed.'; return; fi
  mkdir -p "$(dirname "$POLLEN")"
  install -m 0755 "$HERE/pollen" "$POLLEN"
  say "Installed pollen to $POLLEN."
}

# Enrol with the single-use invite on standard input, so it never appears in a process list.
enrol_pollen() {
  if "$POLLEN" status >/dev/null 2>&1; then say 'This machine is already enrolled.'; return; fi
  [ -f "$INVITE_FILE" ] || fail 'This packet has been used: its invite is gone. Ask for a new packet.'
  "$POLLEN" enrol - < "$INVITE_FILE"
}

# What would make this run fail before it starts, checked before anything can spend or delete
# the secrets: a Mac with no Tailscale app, or a machine signed in to another Tailscale account.
preflight() {
  if [ "$(uname -s)" != Linux ] && ! tailscale_installed; then
    stop 'Install the Tailscale app from https://tailscale.com/download, then run this script again.'
  fi
  if tailscale_installed && tailnet_running && ! tagged_hive_device; then
    stop "This machine is signed in to another Tailscale account. Run 'tailscale logout', then run this script again."
  fi
}

preflight
trap remove_secrets EXIT
install_tailscale
join_tailnet
wait_hive_stand
install_pollen
enrol_pollen
"$POLLEN" install
"$POLLEN" status
say 'Done: this machine is on the tailnet, enrolled, and pollen starts at login.'
say 'On a headless Linux machine, also run: sudo loginctl enable-linger "$USER"'
