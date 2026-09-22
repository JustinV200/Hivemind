# syntax=docker/dockerfile:1
#
# night-veil-ubuntu: the highest Comb Shield tier's Virtual Cell image (roadmap step 5.3a).
#
# TODO(6.1): rebase onto `hivemind/desktop-ubuntu:dev` once it exists -- images/desktop-ubuntu is
# still only a placeholder README today ("Populated in phase 6"), and Tor Browser genuinely needs
# a display to be useful, which only that image will provide. Built on `hivemind/base-ubuntu:dev`
# for now (images/base-ubuntu/README.md's own build command), so this image already exists and is
# attestable the moment 5.7b needs it; the packages this Dockerfile adds do not depend on the
# Exoskeleton bundle, so the rebase is expected to be a one-line FROM change plus a re-verify of
# the layer list below.
#
# What every layer is and why: images/night-veil-ubuntu/README.md's own "Layers, in order" table
# is the narrative; this file is deliberately light on prose so the two stay in sync, mirroring
# images/base-ubuntu/Dockerfile's own convention.
#
# NOTE: Docker was not available in the environment that wrote this file, exactly as
# images/base-ubuntu/Dockerfile's own note says. Reviewed carefully by reading; the integration
# workflow (.github/workflows/integration.yml) is what proves it builds. hive.night_veil's own
# attestation (roadmap step 5.7b) is developed against FakeNightVeilProbe until a real Cell from
# this image can be attested for real (docs/adr/0030's own Consequences).
FROM hivemind/base-ubuntu:dev

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
# torbrowser-launcher: installs and verifies Tor Browser's own signed release through Ubuntu's
#   universe repository (apt's own signature chain, not a hand-verified tarball -- simpler and no
#   less trustworthy than verifying a GPG signature by hand in this Dockerfile, and it is the
#   same path a human operator would use on a real Ubuntu desktop). `tor_browser_launchable`
#   (hivemind.hive.night_veil.session_commands.CMD_TOR_BROWSER) checks the `tor-browser` binary
#   this package installs is on PATH.
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
        torbrowser-launcher \
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

# base-ubuntu's own ENTRYPOINT starts hivemind-in-cell directly; this image never uses that
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
