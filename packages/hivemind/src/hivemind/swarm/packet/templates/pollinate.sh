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
WAS_ON_TAILNET=0                   # Set when Tailscale was already up before this packet ran.
MAC_TAILSCALE=/Applications/Tailscale.app/Contents/MacOS/Tailscale   # The Mac app's own CLI.

say() { printf 'pollinate: %s\n' "$*"; }
fail() { printf 'pollinate: FAILED: %s\n' "$*" >&2; exit 1; }

# Whatever happens, the secrets go: a failed run needs a new packet, never a reused key.
remove_secrets() {
  if [ -d "$SECRETS" ]; then rm -rf -- "$SECRETS"; say "Deleted this packet's secrets."; fi
}
trap remove_secrets EXIT

as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

# The Mac app does not put `tailscale` on PATH; its CLI lives inside the app bundle. A path, not a
# function, so `sudo` can run it too.
tailscale_bin() { command -v tailscale 2>/dev/null || printf '%s\n' "$MAC_TAILSCALE"; }

install_tailscale() {
  if command -v tailscale >/dev/null 2>&1 || [ -x "$MAC_TAILSCALE" ]; then
    say 'Tailscale is already installed.'; return
  fi
  # Linux: Tailscale's own installer, which adds its signed package repository. macOS has no
  # unattended install; the standalone app must be installed by hand first.
  case "$(uname -s)" in
    Linux) say 'Installing Tailscale...'; curl -fsSL https://tailscale.com/install.sh | sh ;;
    *) fail 'Install Tailscale from https://tailscale.com/download first; this packet is now spent.' ;;
  esac
}

tailnet_running() {
  "$(tailscale_bin)" status --json 2>/dev/null | grep -q '"BackendState": *"Running"'
}

tagged_hive_device() {
  "$(tailscale_bin)" status --json 2>/dev/null | grep -q '"tag:hive-device"'
}

# Join with the single-use key read from its file, so it is never on a command line. The node is
# not ephemeral: it stays in the tailnet while offline and reconnects by itself on any network.
join_tailnet() {
  if tailnet_running; then
    # Already up: as this packet's tagged node (a re-run), or on some tailnet of its own.
    if tagged_hive_device; then say 'This machine is already on the tailnet.'; return; fi
    WAS_ON_TAILNET=1
    say 'Tailscale was already signed in here; using that connection, not the packet key.'
    return
  fi
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
      [ "$WAS_ON_TAILNET" -eq 0 ] || fail "The Hive Stand $HIVE_HOST:$HIVE_PORT does not answer, and this machine was already signed in to Tailscale, perhaps to a different tailnet. Run 'tailscale logout' and use a new packet."
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

install_tailscale
join_tailnet
wait_hive_stand
install_pollen
enrol_pollen
"$POLLEN" install
"$POLLEN" status
say 'Done: this machine is on the tailnet, enrolled, and pollen starts at login.'
say 'On a headless Linux machine, also run: sudo loginctl enable-linger "$USER"'
