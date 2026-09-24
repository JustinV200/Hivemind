# night-veil-ubuntu image

Night Veil is the Comb Shield tier for work that must not be linkable to the operator (codingrules
section 8.7): Virtual only, all traffic through OpenVPN plus Tor with direct egress blocked, every
model slot local, and a Waggle control link that reaches the Hive Stand through a Tor hidden
service, never the VPN tunnel and never a clearnet address. This image is what a Night Veil Cell
actually boots: `hivemind/desktop-ubuntu:dev` (roadmap step 6.1) plus the OpenVPN client, Tor, Tor
Browser, an nftables kill-switch, and the location-blind defaults baked in. `hive.night_veil`
(roadmap step 5.7b) attests this image deterministically before `CellReady`; nothing is installed
or configured at runtime -- everything in this document is baked in at build time, which is the
whole point (`docs/adr/0030-night-veil-retention-and-clearance-boundary.md`: "readiness is
attestation of an image, never configuration of a Cell").

Built on `desktop-ubuntu` (roadmap step 6.1) since phase 6: Tor Browser needs a display, and that
image carries the Exoskeleton's on-demand display, input, audio and browser toolchain.

## Packages this image adds over desktop-ubuntu

| Package | Why |
|---|---|
| `openvpn` | The VPN client every tier above MEADOW needs (PROPOLIS is OpenVPN-only; NIGHT_VEIL layers Tor on top). Ships `openvpn-client@.service`, a systemd template unit; `openvpn-client-override.conf` re-orders it after the kill-switch. |
| `tor` | The Tor daemon, configured by `torrc` (below). Ships `tor.service`; `tor-override.conf` re-orders it after OpenVPN. |
| Tor Browser (`/opt/tor-browser`, `tor-browser` on PATH) | The Tor Project's signed release, fetched in its own build stage from the permanent archive and verified with `gpgv` against the Tor Browser Developers signing key, located over WKD and pinned by fingerprint (`TOR_BROWSER_VERSION`, `TOR_BROWSER_KEY_FINGERPRINT` build arguments). Ubuntu's `torbrowser-launcher`, used first, carries only a launcher that downloads Tor Browser at its first GUI run, so a real build had no `tor-browser` and `CMD_TOR_BROWSER` (`which tor-browser`) could never pass attestation -- found building this image for real in phase 6. |
| `nftables` | The kill-switch (`nftables.conf`, below). |
| `systemd`, `systemd-sysv` | base-ubuntu's own runtime stage installs neither (a plain `ENTRYPOINT` needs no init); this image runs systemd as PID 1 instead, so the fixed nftables -> openvpn -> tor -> Warden ordering codingrules section 8.7 requires is expressed as unit dependencies rather than a hand-rolled shell script, and so `hive.night_veil`'s own `tor_healthy`/`timezone_utc` checks (`systemctl`, `timedatectl`) have something to query. `systemd-sysv` provides `/sbin/init`, the `ENTRYPOINT`. |
| `tzdata`, `locales` | UTC and `C.UTF-8` need their own data files; see "Location-blind defaults" below. |

## The nftables kill-switch (`nftables.conf`)

Default drop on every chain (`input`, `forward`, `output`); nothing leaves this Cell except what
is explicitly named. `nftables.conf`'s own header comments explain each rule in full; the short
version:

- Loopback and established/related connections are always allowed (the Warden's own traffic to
  Tor's SOCKS/control ports never leaves the box; a stateful firewall does not re-match every
  packet of an already-permitted flow).
- OpenVPN's own handshake -- matched by the `openvpn` user its systemd unit runs it as, never by
  a hardcoded server address -- may dial out on the physical interface, because the tunnel has to
  come up before rules that depend on `tun0` existing can mean anything.
- Once `tun0` exists, only Tor's own outbound connections (matched by the `debian-tor` user Tor's
  own package runs it as -- relay and directory-server addresses have no fixed list) may use the
  tunnel. Every other process's attempt to reach the network through it is refused.
- Cloud metadata addresses (`169.254.0.0/16`, `168.63.129.16` for Azure's own second address) are
  refused explicitly, redundant with both the default-drop policy and the blackhole route
  `night-veil-kill-switch.service` also adds -- three independent mechanisms, so one failing open
  is never enough to leak the endpoint.

Loaded by `night-veil-kill-switch.service`, before `openvpn-client@night-veil` or `tor` ever
start: the kill-switch is in force before there is anything to leak, never applied after the fact.

## Tor (`torrc`)

`SocksPort 127.0.0.1:9050` (loopback only -- the Warden's Waggle transport is the one thing on
this Cell that dials it) and `ControlPort 127.0.0.1:9051` (cookie-authenticated, no password file
to leak). `Log notice file /dev/null`: no logs retained, matching codingrules section 12's Night
Veil boundary -- Tor's own log is one of the "side channels" `docs/adr/0030` names, and this image
never writes one in the first place rather than relying on teardown to purge it. `ClientOnly 1`:
this Cell is a Tor client, never a relay or bridge.

## Boot ordering

systemd units, not a shell script, express the fixed order codingrules section 8.7 requires:

```
night-veil-kill-switch.service  (nftables + blackhole route)
        |
        v
openvpn-client@night-veil.service   (the operator's own profile, mounted at runtime)
        |
        v
tor.service
        |
        v
night-veil-env.service  (bridges PID 1's own HIVEMIND_* env into /run/hivemind/env)
        |
        v
hivemind-warden.service  (runs as the unprivileged `hive` user)
```

`night-veil-hostname.service` runs independently, before `sysinit.target`, and does not gate
anything else (hostname randomisation has no ordering dependency on the network stack).

**Why systemd as PID 1 needs `night-veil-env.service`:** a container or VM backend
(`hivemind.hive.backends.docker`/`.qemu`) sets every `HIVEMIND_*` variable
(`hivemind.hive.backends.bootstrap.CellBootstrap.environment()`) on PID 1 alone. base-ubuntu's own
plain `ENTRYPOINT` inherits that environment for free (ordinary process inheritance); systemd,
running as PID 1 here instead, does not automatically forward its own environment to the units it
starts. `night-veil-env.sh` reads `/proc/1/environ` once, at boot, and writes every `HIVEMIND_*`
entry to `/run/hivemind/env`, which `hivemind-warden.service`'s own `EnvironmentFile=` picks up.

## The OpenVPN profile is mounted at runtime, never baked in

`openvpn-client@night-veil.service` (the Debian/Ubuntu package's own template unit, instantiated
by the file `/etc/openvpn/client/night-veil.conf`) expects the operator's own OpenVPN profile at
exactly that path. This image never bakes one in: a profile names credentials and a specific VPN
provider, neither of which belongs in a built, shareable image, and codingrules section 8.7
already treats "verify the tunnel" and "never install or reroute one on a borrowed machine" as
separate concerns from provisioning the image itself. The composition root that provisions a Night
Veil Cell is responsible for mounting the operator's own profile at that path before the Cell
boots (a report item for whichever backend change wires the mount -- this Dockerfile only assumes
the file will be there).

## Location-blind defaults (codingrules section 8.7)

- **UTC timezone:** `/etc/localtime` symlinked to `UTC` and `/etc/timezone` set at build time.
  `hive.night_veil`'s own `timezone_utc` check (`timedatectl show --property=Timezone --value`)
  reads exactly this.
- **Fixed locale:** `LANG=C.UTF-8`/`LC_ALL=C.UTF-8`, generated at build time. `locale_pinned`
  checks a Hive's own configured `[security.tiers.NIGHT_VEIL] locale_profile` against this.
- **Randomised hostname:** `night-veil-hostname.service` sets a fresh `night-veil-<8 hex bytes>`
  hostname from `/dev/urandom` on every boot -- never the Cell's own id or anything else the Queen
  already knows, so the hostname itself carries no information back to this Hive either.
- **Metadata endpoints null-routed:** see the kill-switch section above; blocked three independent
  ways (an explicit nftables rule, the default-drop policy underneath it, and a blackhole route).

## What this image does not attempt

- **Verified against a real build end to end.** Every stage but the Tor Browser download was
  built for real in phase 6 (on `desktop-ubuntu`, in a sandbox that cannot reach the Tor Project);
  `.github/workflows/integration.yml` builds the whole image nightly, after the integration tests,
  so a Tor Project outage never hides their results.
- **Running under the Docker backend today.** systemd as PID 1 needs more than the `NET_ADMIN`
  capability the kill-switch alone would need: `hivemind.hive.backends.docker.backend.
  DockerCellBackend._build_container_spec` applies `cap_drop=("ALL",)` and `read_only_rootfs=True`
  to every Virtual Cell spec today, which a systemd-based image cannot boot under at all (systemd
  needs a writable `/run` and `/sys/fs/cgroup` and considerably more than one capability back).
  QEMU (`hivemind.hive.backends.qemu`) gives Night Veil a real hypervisor boundary where systemd
  is simply a normal Ubuntu boot with no container-specific allowance needed -- `docs/adr/0026`'s
  own reasoning for QEMU being the harder isolation boundary applies doubly here. Making the
  Docker backend able to run this image at all (a relaxed, VPN_TOR-only container spec, on top of
  the `cap_add=NET_ADMIN` gap `hive.backends.docker.backend`'s own comment already names) is a
  report item, not something this dispatch's file list can close.
- **Reaching the Tor Project from every build host.** The Tor Browser stage needs
  `archive.torproject.org` and the Tor Project's WKD key server; a build sandbox without them
  (the one phase 6 was built in) cannot build this image at all, and says so at that stage.

## Building and testing this image

```sh
docker build -f images/base-ubuntu/Dockerfile -t hivemind/base-ubuntu:dev .
docker build -f images/desktop-ubuntu/Dockerfile -t hivemind/desktop-ubuntu:dev .
docker build -f images/night-veil-ubuntu/Dockerfile -t hivemind/night-veil-ubuntu:dev .
```

Same repository-root build context as `base-ubuntu` (its own README's "Build context" section);
this Dockerfile's `COPY` lines reference `images/night-veil-ubuntu/*` explicitly for that reason.
Built after `base-ubuntu` and `desktop-ubuntu` in `.github/workflows/integration.yml`, since it
`FROM`s the latter.

A real build-and-boot integration test (a later step, mirroring
`packages/hivemind/tests/integration/test_docker_backend.py`) must confirm, at minimum:

- `nft list ruleset` shows the default-drop policy on every chain.
- A direct (non-proxied) `curl` fails; a `curl --socks5-hostname 127.0.0.1:9050` to a known `.onion`
  succeeds once a real Tor circuit is up.
- `timedatectl show --property=Timezone --value` prints `UTC`; `locale` shows `LANG=C.UTF-8`.
- The hostname differs between two fresh boots of the same image.
- `systemctl is-active tor` and, once a profile is mounted, `openvpn-client@night-veil` are both
  active before `hivemind-warden.service` starts (`systemctl list-dependencies
  hivemind-warden.service` shows the full chain).
