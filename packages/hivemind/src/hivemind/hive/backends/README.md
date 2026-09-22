# hivemind.hive.backends

The backends package holds one CellBackend implementation per kind of infrastructure a Virtual
Cell can be provisioned on: local containers (`docker/`), a local hypervisor (`qemu/`), and cloud
providers under `backends/cloud/`.

## Public API (roadmap steps 5.2, 5.4, 5.11, 5.12)

- `CellBackend` (`base.py`): `provision`, `destroy`, `list_cells`, `pause`, `resume`, plus `name`
  and `capabilities` so a caller branches on what a backend can do, never on which one it is.
- `BackendCapabilities` (`base.py`): `can_snapshot`, `can_pause`, `headroom`.
- `VirtualCellRecord` (`base.py`): what `list_cells` returns -- id, status, image, labels,
  created_at, enough for the Undertaker's orphan sweep to reconcile without asking twice.
- `FakeCellBackend` (`fake.py`): the in-memory reference implementation; deterministic via an
  injected `Clock`, with switches to simulate provision failure, slow readiness and destroy
  failure, and every call recorded for assertions.
- `CellBootstrap` / `QueenEndpoint` / `ReadinessGate` / `CellReadyInfo` / `mint_cell_bootstrap`
  (`bootstrap.py`): the backend-independent seam every backend provisions through -- a fresh
  per-Cell identity and keypair, where the Queen is as reachable from inside a Cell, and how a
  backend learns a Cell has become reachable. `FakeReadinessGate` (`fake.py`, beside
  `FakeCellBackend`) is its in-memory implementation, with a `set_never_ready` switch.
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

## What the QEMU backend's NetworkPolicy really enforces

See `hivemind.hive.backends.qemu.network`'s own module docstring for the full explanation;
summary:

| Policy | What QEMU actually does | What it does not do |
|---|---|---|
| `NONE` | `-netdev user,...,restrict=on` plus one `guestfwd` rule forwarding a fixed guest address to the real Queen endpoint. `restrict=on` blocks the guest from reaching the host at all, including QEMU's own `10.0.2.2` host alias; `guestfwd` is the one documented exception QEMU still services under `restrict=on`, so the control link survives. | Verified against real QEMU: this dev host has none (ADR-0026), so the `guestfwd=...-cmd:...` relay (which shells out to the host's own Python to bridge the connection) has been reviewed by reading, not run. |
| `EGRESS_ONLY` | `-netdev user,id=net0`, no `restrict` -- full outbound reach via SLIRP's own NAT, no inbound (no Virtual Cell backend ever publishes a port). A Queen endpoint on loopback is rewritten to QEMU's `10.0.2.2` host alias so it is actually reachable from inside the guest. | Nothing beyond "no inbound"; outbound is unrestricted by design. |
| `ALLOWLIST` | The same unrestricted user network as `EGRESS_ONLY`, plus the allowlist stamped into `cell.json`'s labels for audit. | Enforcement: QEMU's own SLIRP stack has no hostname-based outbound allowlist. `TODO(5.7a)` marks exactly what the in-Cell firewall (Night Veil's kill-switch work) still has to close, mirroring Docker's own `ALLOWLIST_LABEL`. |
| `VPN_TOR` | The same unrestricted user network as `EGRESS_ONLY`; `provision()` refuses a VPN_TOR spec outright unless `spec.image == "night-veil-ubuntu"`. | Enforcement: QEMU's own SLIRP stack cannot restrict outbound reach to "only the VPN endpoint and Tor's bootstrap" any more than Docker's network API can. The `night-veil-ubuntu` image's own in-guest nftables kill-switch (5.3a) is the real boundary -- and, unlike Docker, a QEMU guest's own init runs with full kernel privilege inside the VM, so there is no host-side `cap_add` gap to close here. |

## How to test this

- `packages/hivemind/tests/unit/hive/backends/`: unit tests for `base.py`, `fake.py`,
  `bootstrap.py`, every module under `docker/` and `qemu/`, and `cloud/base.py`/`cloud/fake.py`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: the shared contract suite,
  parametrised by a fixture; `FakeCellBackend`, `DockerCellBackend` (over `FakeDockerClient` and
  `FakeReadinessGate`), `QemuCellBackend` (over `FakeQemuRunner` and `FakeReadinessGate`) and
  `FakeCloudCellBackend` all plug in.
- `packages/hivemind/tests/integration/test_docker_backend.py` and
  `test_qemu_backend.py`: `@pytest.mark.integration`, each needs its own real infrastructure;
  both skip cleanly when none is reachable.

## Not yet built

- A real `hivemind.hive.backends.cloud` provider implementation (post-1.0; ADR-0026,
  `cloud/README.md`).
