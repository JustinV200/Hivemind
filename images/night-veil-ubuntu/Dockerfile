# syntax=docker/dockerfile:1
#
# night-veil-ubuntu: the highest Comb Shield tier's Virtual Cell image (roadmap step 5.3a).
#
# Built on desktop-ubuntu (roadmap step 6.1): Tor Browser needs a display to be useful, and that
# image carries the Exoskeleton's on-demand display, input, audio and browser toolchain. The rebase
# was the one-line FROM change the phase 5 note predicted; every package below is independent of
# the Exoskeleton bundle.
#
# What every layer is and why: images/night-veil-ubuntu/README.md's own "Layers, in order" table
# is the narrative; this file is deliberately light on prose so the two stay in sync, mirroring
# images/base-ubuntu/Dockerfile's own convention.
#
# Build it after base-ubuntu and desktop-ubuntu, from the repository root; BASE_IMAGE names a
# different desktop-ubuntu tag. hive.night_veil's own attestation (roadmap step 5.7b) is developed
# against FakeNightVeilProbe until a real Cell from this image can be attested for real
# (docs/adr/0030's own Consequences).
ARG BASE_IMAGE=hivemind/desktop-ubuntu:dev

# ══════════════════════════════════════════════════════════════════════════════════════════════
# Stage 1: tor-browser -- Tor Browser's own signed release, verified, unpacked into /opt.
# ══════════════════════════════════════════════════════════════════════════════════════════════
# The image must carry Tor Browser itself (codingrules 8.7: "attestation checks an image rather
# than configuring a Cell at runtime"). Ubuntu's torbrowser-launcher, which this image used first,
# carries only a launcher that downloads Tor Browser at its first GUI run, so a real build had no
# `tor-browser` at all and the tor_browser_launchable check could never pass (found building this
# image for real in phase 6). This stage fetches the release and its detached signature from
# the Tor Project's permanent archive and verifies them with gpgv against the Tor Browser
# Developers signing key, located over WKD and pinned by fingerprint; curl, gnupg and the
# tarball never reach the runtime stage.
FROM ubuntu:24.04 AS tor-browser
ARG TOR_BROWSER_VERSION=14.5.3
ARG TOR_BROWSER_KEY_FINGERPRINT=EF6E286DDA85EA2A4BA7DE684E2C6E8793298290
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl dirmngr gnupg xz-utils \
    && rm -rf /var/lib/apt/lists/*
# `set -e` semantics per command: every `&&` stops at the first failure, and curl's -f turns an
# HTTP error into one, so a missing release or a bad signature fails the build right here.
RUN gpg --batch --auto-key-locate nodefault,wkd --locate-keys torbrowser@torproject.org \
    && gpg --batch --output /tmp/tor-browser.keyring --export "${TOR_BROWSER_KEY_FINGERPRINT}" \
    && test -s /tmp/tor-browser.keyring \
    && base="https://archive.torproject.org/tor-package-archive/torbrowser/${TOR_BROWSER_VERSION}" \
    && tarball="tor-browser-linux-x86_64-${TOR_BROWSER_VERSION}.tar.xz" \
    && curl -fsSLo "/tmp/${tarball}" "${base}/${tarball}" \
    && curl -fsSLo "/tmp/${tarball}.asc" "${base}/${tarball}.asc" \
    && gpgv --keyring /tmp/tor-browser.keyring "/tmp/${tarball}.asc" "/tmp/${tarball}" \
    && tar -xJf "/tmp/${tarball}" -C /opt \
    && test -x /opt/tor-browser/Browser/start-tor-browser

# ══════════════════════════════════════════════════════════════════════════════════════════════
# Stage 2: runtime -- desktop-ubuntu plus the VPN, Tor, the kill-switch and Tor Browser.
# ══════════════════════════════════════════════════════════════════════════════════════════════
FROM ${BASE_IMAGE}

# Root for the rest of this file: unlike base-ubuntu (a plain ENTRYPOINT, never root at runtime),
# this image runs systemd as PID 1 (see the ENTRYPOINT at the bottom and the README's "Boot
# ordering" section for why) and needs root to install packages, load nftables and manage OpenVPN.
# The Warden itself still runs as the unprivileged `hive` user -- see hivemind-warden.service's
# own `User=hive` -- so the one process that actually executes task code keeps the same
# least-privilege posture as every other image; only Cell-wide boot machinery runs as root.
USER root

# openvpn: the VPN client every tier above MEADOW needs (codingrules section 8.7's own PROPOLIS
#   tier is OpenVPN-only; NIGHT_VEIL layers Tor on top). Ships its own openvpn-client@.service
#   systemd template unit, which openvpn-client-override.conf (copied below) re-orders after the
#   kill-switch.
# tor: the Tor daemon. Ships its own tor.service unit, similarly re-ordered by tor-override.conf.
# nftables: the kill-switch (nftables.conf, copied below).
# systemd, systemd-sysv: base-ubuntu's own runtime stage installs neither (a plain ENTRYPOINT
#   needs no init); this image does, to run PID 1 as systemd instead (README's own "Boot
#   ordering"). systemd-sysv provides /sbin/init as the systemd binary, which the ENTRYPOINT below
#   execs directly.
# tzdata: UTC pinning below needs zoneinfo data; locales: the C.UTF-8 pin.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openvpn \
        tor \
        nftables \
        systemd \
        systemd-sysv \
        tzdata \
        locales \
    && rm -rf /var/lib/apt/lists/*

# Location-blind defaults (codingrules section 8.7): UTC timezone and a fixed locale, baked into
# the image so attestation (timezone_utc, locale_pinned) checks a fact that was true before the
# Cell ever booted, never a runtime setting a compromised process could have changed back.
RUN ln -sf /usr/share/zoneinfo/UTC /etc/localtime \
    && echo "UTC" > /etc/timezone \
    && locale-gen C.UTF-8
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Tor Browser, verified in its own stage above, owned by `hive` because it keeps its profile inside
# its own directory, and linked onto PATH as `tor-browser`, the name the tor_browser_launchable
# attestation check (hivemind.hive.night_veil.session_commands.CMD_TOR_BROWSER) looks for.
COPY --from=tor-browser --chown=hive:hive /opt/tor-browser /opt/tor-browser
RUN ln -s /opt/tor-browser/Browser/start-tor-browser /usr/local/bin/tor-browser

# The kill-switch ruleset, Tor's own config, the boot-ordering drop-ins, and this image's own two
# scripts (hostname randomisation, the PID-1-environment bridge) -- see each file's own header for
# what it does and why; this Dockerfile only places them.
COPY images/night-veil-ubuntu/nftables.conf /etc/nftables.conf
COPY images/night-veil-ubuntu/torrc /etc/tor/torrc
COPY images/night-veil-ubuntu/night-veil-kill-switch.service /etc/systemd/system/night-veil-kill-switch.service
COPY images/night-veil-ubuntu/night-veil-hostname.service /etc/systemd/system/night-veil-hostname.service
COPY images/night-veil-ubuntu/night-veil-hostname.sh /usr/local/bin/night-veil-hostname.sh
COPY images/night-veil-ubuntu/night-veil-env.service /etc/systemd/system/night-veil-env.service
COPY images/night-veil-ubuntu/night-veil-env.sh /usr/local/bin/night-veil-env.sh
COPY images/night-veil-ubuntu/hivemind-warden.service /etc/systemd/system/hivemind-warden.service
COPY images/night-veil-ubuntu/openvpn-client-override.conf /etc/systemd/system/openvpn-client@.service.d/override.conf
COPY images/night-veil-ubuntu/tor-override.conf /etc/systemd/system/tor.service.d/override.conf
RUN chmod 0755 /usr/local/bin/night-veil-hostname.sh /usr/local/bin/night-veil-env.sh

# Metadata endpoints null-routed at build time too (redundant with the nftables rule and the boot-
# time blackhole route night-veil-kill-switch.service adds -- codingrules section 8.7: "blocked
# metadata endpoints" is a location-blind default, checked three independent ways so no single
# mechanism failing open is enough to leak it).
RUN ip route add blackhole 169.254.169.254/32 2>/dev/null || true

# base-ubuntu's own ENTRYPOINT (inherited through desktop-ubuntu) starts hivemind-in-cell directly;
# this image never uses that
# ENTRYPOINT (systemd's own hivemind-warden.service starts the same console script instead, once
# nftables -> openvpn -> tor have all come up). base-ubuntu's own ENTRYPOINT is still present in
# the inherited image but is overridden below, and is otherwise inert (nothing here execs it).
RUN systemctl enable \
        night-veil-kill-switch.service \
        night-veil-hostname.service \
        night-veil-env.service \
        hivemind-warden.service \
        tor.service \
    || true
# openvpn-client@night-veil requires the operator's own profile (mounted at
# /etc/openvpn/client/night-veil.conf at runtime, README's own "The OpenVPN profile" section) to
# exist before systemd can even resolve the instance; enabling it here, ahead of that mount,
# fails harmlessly (`|| true`) and the unit is still enabled by name for the next boot that does
# have the profile.
RUN systemctl enable openvpn-client@night-veil.service || true

# No EXPOSE: a Virtual Cell opens no inbound port (codingrules section 15), unchanged from
# base-ubuntu. Every outbound connection this image ever makes -- OpenVPN's handshake, Tor's own
# relay connections, the Warden's Waggle link -- is still dialled OUT.
ENTRYPOINT ["/sbin/init"]
