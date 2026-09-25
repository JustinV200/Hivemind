# hivemind.hive.backends

The backends package holds one CellBackend implementation per kind of infrastructure a Virtual
Cell can be provisioned on: local containers (`docker/`), a local hypervisor (`qemu/`), and cloud
providers under `backends/cloud/`.

## Public API (roadmap steps 5.2, 5.4, 5.11, 5.12)

- `CellBackend` (`base.py`): `provision`, `destroy`, `list_cells`, `pause`, `resume`, plus `name`
  and `capabilities` so a caller branches on what a backend can do, never on which one it is.
  `can_night_veil` says whether a backend can hold a NIGHT_VEIL Cell and meet its teardown rule
  (codingrules section 12): Docker (and the fake) declare it; QEMU does not, and refuses such a
  spec itself, fail-closed (`hivemind.hive` README, "Night Veil: what a Cell itself keeps").
- `BackendCapabilities` (`base.py`): `can_snapshot`, `can_pause`, `headroom`, `can_cut_egress`,
  `can_night_veil`.
- `VirtualCellRecord` (`base.py`): what `list_cells` returns -- id, status, image, labels,
  created_at, enough for the Undertaker's orphan sweep to reconcile without asking twice.
- `FakeCellBackend` (`fake.py`): the in-memory reference implementation; deterministic via an
  injected `Clock`, with switches to simulate provision failure, slow readiness and destroy
  failure, and every call recorded for assertions.
- `CellBootstrap` / `QueenEndpoint` / `ReadinessGate` / `CellReadyInfo` / `mint_cell_bootstrap`
  (`bootstrap.py`): the backend-independent seam every backend provisions through -- a fresh
  per-Cell identity and keypair, where the Queen is as reachable from inside a Cell, and how a
  backend learns a Cell has become reachable. `FakeReadinessGate` (`fake.py`, beside
  `FakeCellBackend`) is its in-memory implementation, with a `set_never_ready` switch. Roadmap
  step 10.3a: a `QueenEndpoint` may carry a `NightVeilLink` (the Hive Stand's hidden-service
  Waggle URL and the Tor SOCKS proxy, built from `[security.tiers.NIGHT_VEIL]`), and
  `cell_endpoint` is the one per-Cell choice every backend makes before minting: the ordinary
  endpoint for MEADOW and PROPOLIS, the link for NIGHT_VEIL, and a `CellProvisionError` (nothing
  created) for a Night Veil Cell on a Hive with no link, or with one the Cell could never dial
  (not a v3 onion service, or a proxy that is not a loopback `socks5h`/`socks4a` one), so it is
  never handed a clearnet address. The chosen endpoint carries the Cell's tier, rendered as
  `HIVEMIND_COMB_SHIELD`, so the Cell's own floors see it, and its reservation
  (`hivemind.hive.models.CellReservation`, from its spec), rendered as `HIVEMIND_RESERVATION`, so
  the Cell reports that as its capacity rather than the host's figures it would probe.
- `DockerCellBackend` / `build_docker_backend` (`docker/`): the first working `CellBackend`, over
  a Docker daemon. See `docker/`'s own module docstrings for `DockerClientPort`, `SdkDockerClient`
  (the only module that may import the `docker` SDK), `FakeDockerClient` and what each
  `NetworkPolicy` really enforces at the Docker level. `DockerClientPort` also carries
  `commit_container`/`remove_image`/`recreate_from_image` (roadmap step 5.10), the narrow slice
  `hivemind.hive.snapshot.docker.DockerSnapshotter` needs; `DockerCellBackend.client` exposes the
  same port instance that factory builds a snapshotter over.
- `QemuCellBackend` / `build_qemu_backend` (`qemu/`): the second working `CellBackend`, over real
  QEMU VMs. See `qemu/`'s own module docstrings for `QemuRunnerPort`, `ProcessQemuRunner` (the
  only module that may launch a `qemu-*` binary), `FakeQemuRunner`, the cloud-init documents
  `qemu/cloud_init.py` renders, and what each `NetworkPolicy` really enforces under QEMU
  user-mode networking (below). `QemuRunnerPort` also carries `savevm`/`loadvm` (roadmap step
  5.10, over QMP `human-monitor-command`) for `hivemind.hive.snapshot.qemu.QemuSnapshotter`;
  `QemuCellBackend.runner` exposes the same port instance that factory builds a snapshotter over.
- `hivemind.hive.backends.cloud`: `CloudCellBackend`, `CloudBackendConfig`, `CloudCredentials`,
  `CloudRegion`, `PricingTag` and `FakeCloudCellBackend` (the reference implementation; no real
  provider is chosen -- ADR-0026, `cloud/README.md`).

## What the Docker backend's NetworkPolicy really enforces

See `hivemind.hive.backends.docker.network`'s own module docstring for the full explanation;
summary:

| Policy | What Docker actually does | What it does not do |
|---|---|---|
| `NONE` | An `internal` bridge network: no outbound NAT rule, so no route to the internet through it; every container still gets a `host.docker.internal` -> host-gateway `/etc/hosts` entry, so the control link (ADR-0027) keeps working. | Verified isolation on Docker Desktop: Desktop's own VM/NAT layer has a documented history of not fully honouring `internal` the way native Linux Engine does. Treat as best-effort there. |
| `EGRESS_ONLY` | A plain bridge network: full outbound reach, no inbound (no Virtual Cell backend ever publishes a port). | Nothing beyond "no inbound"; outbound is unrestricted by design. |
| `ALLOWLIST` | The same plain bridge network as `EGRESS_ONLY`, plus the allowlist stamped on a container label for audit. | Enforcement: Docker's own SDK has no hostname-based outbound allowlist. `TODO(5.7a)` marks exactly what the in-Cell firewall (Night Veil's kill-switch work) still has to close. |
| `VPN_TOR` | The same plain bridge network as `EGRESS_ONLY`, plus a `hivemind.network_policy=VPN_TOR` label for audit; `provision()` refuses a VPN_TOR spec outright unless `spec.image == "night-veil-ubuntu"`. | Enforcement: Docker's own network API cannot restrict outbound reach to "only the VPN endpoint and Tor's bootstrap"; the `night-veil-ubuntu` image's own in-container nftables kill-switch (5.3a) is the real boundary. `cap_add=("NET_ADMIN",)` (needed for that kill-switch to load its own ruleset) is not yet threaded through `ContainerSpec` -- a report item for whoever next touches `docker/client.py`. |

### The control network, and cutting a running Cell's egress (roadmap step 10.6a)

With `[virtual_cells] control_subnet` set, every Docker Cell whose link does not ride Tor is
dual-homed (`docker/network.py` has the design, `docker/egress.py` the lever):

| Network | What it is | What it carries |
|---|---|---|
| control (one per Hive) | `internal`, inter-container traffic off, on the operator's subnet; its gateway is the host's own address there, where the Queen's listener binds | The Waggle link alone: no default route, so nothing beyond the host is reachable through it, and no other Cell |
| egress (one per Cell) | The per-policy network in the table above, attached before the Cell starts | The default route: everything else, the host's docker0 (`host.docker.internal`) included |

`DockerCellBackend` then declares `can_cut_egress`: `cut_egress` detaches the Cell from its egress
network and `restore_egress` attaches it again, both idempotent, and the link never notices. A
Night Veil (VPN_TOR) Cell is never dual-homed and is refused a cut, as is a Cell provisioned before
the control subnet was set (`BackendCapabilityError`, recorded as `unsupported`). Anything on the
host bound to every interface still answers on the control gateway; bind the model servers Cells
use to docker0. `tests/integration/test_docker_egress.py` proves the cut, the link and the lift
against a real daemon.

## What the QEMU backend's NetworkPolicy really enforces

See `hivemind.hive.backends.qemu.network`'s own module docstring for the full explanation;
summary:

| Policy | What QEMU actually does | What it does not do |
|---|---|---|
| `NONE` | `-netdev user,...,restrict=on` plus one `guestfwd` rule forwarding a fixed guest address to the real Queen endpoint. `restrict=on` blocks the guest from reaching the host at all, including QEMU's own `10.0.2.2` host alias; `guestfwd` is the one documented exception QEMU still services under `restrict=on`, so the control link survives. | Verified against real QEMU: this dev host has none (ADR-0026), so the `guestfwd=...-cmd:...` relay (which shells out to the host's own Python to bridge the connection) has been reviewed by reading, not run. |
| `EGRESS_ONLY` | `-netdev user,id=net0`, no `restrict` -- full outbound reach via SLIRP's own NAT, no inbound (no Virtual Cell backend ever publishes a port). A Queen endpoint on loopback is rewritten to QEMU's `10.0.2.2` host alias so it is actually reachable from inside the guest. | Nothing beyond "no inbound"; outbound is unrestricted by design. |
| `ALLOWLIST` | The same unrestricted user network as `EGRESS_ONLY`, plus the allowlist stamped into `cell.json`'s labels for audit. | Enforcement: QEMU's own SLIRP stack has no hostname-based outbound allowlist. `TODO(5.7a)` marks exactly what the in-Cell firewall (Night Veil's kill-switch work) still has to close, mirroring Docker's own `ALLOWLIST_LABEL`. |
| `VPN_TOR` | Never reached today: VPN_TOR is Night Veil's policy, and this backend refuses every Night Veil spec (`can_night_veil` is False). Were it lifted: the same unrestricted user network as `EGRESS_ONLY`, and `provision()` refuses a VPN_TOR spec outright unless `spec.image == "night-veil-ubuntu"`. | Enforcement: QEMU's own SLIRP stack cannot restrict outbound reach to "only the VPN endpoint and Tor's bootstrap" any more than Docker's network API can. The `night-veil-ubuntu` image's own in-guest nftables kill-switch (5.3a) is the real boundary -- and, unlike Docker, a QEMU guest's own init runs with full kernel privilege inside the VM, so there is no host-side `cap_add` gap to close here. |

QEMU cannot cut a running Cell's egress yet (`can_cut_egress` is undeclared): `restrict=on` is fixed
when a `-netdev` is created, and QMP's `set_link`/`netdev_del` take the VM's one NIC down, the
link's included. A cut needs Docker's split: a control NIC (`restrict=on` plus the one `guestfwd`
to the Queen) and an egress NIC carrying the default route, both brought up by cloud-init, with
the cut and the lift being QMP `set_link` on the egress NIC. Not built: no QEMU on this host to
prove it on (ADR-0026).

## How to test this

- `packages/hivemind/tests/unit/hive/backends/`: unit tests for `base.py`, `fake.py`,
  `bootstrap.py`, every module under `docker/` and `qemu/`, and `cloud/base.py`/`cloud/fake.py`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: the shared contract suite,
  parametrised by a fixture; `FakeCellBackend`, `DockerCellBackend` (over `FakeDockerClient` and
  `FakeReadinessGate`), `QemuCellBackend` (over `FakeQemuRunner` and `FakeReadinessGate`) and
  `FakeCloudCellBackend` all plug in.
- `packages/hivemind/tests/integration/test_docker_backend.py` and
  `test_qemu_backend.py`: `@pytest.mark.integration`, each needs its own real infrastructure;
  both skip cleanly when none is reachable. `test_docker_egress.py` runs a whole Hive on a real
  daemon and isolates a real Cell (it needs a Cell image built from this tree, named by
  `HIVEMIND_TEST_CELL_IMAGE`).

## Not yet built

- A real `hivemind.hive.backends.cloud` provider implementation (post-1.0; ADR-0026,
  `cloud/README.md`).
