# hivemind.hive.backends

The backends package holds one CellBackend implementation per kind of infrastructure a Virtual
Cell can be provisioned on: local containers (`docker/`), a local hypervisor, and cloud providers
under `backends/cloud/`.

## Public API (roadmap steps 5.2, 5.4)

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
  `NetworkPolicy` really enforces at the Docker level.

## What the Docker backend's NetworkPolicy really enforces

See `hivemind.hive.backends.docker.network`'s own module docstring for the full explanation;
summary:

| Policy | What Docker actually does | What it does not do |
|---|---|---|
| `NONE` | An `internal` bridge network: no outbound NAT rule, so no route to the internet through it; every container still gets a `host.docker.internal` -> host-gateway `/etc/hosts` entry, so the control link (ADR-0027) keeps working. | Verified isolation on Docker Desktop: Desktop's own VM/NAT layer has a documented history of not fully honouring `internal` the way native Linux Engine does. Treat as best-effort there. |
| `EGRESS_ONLY` | A plain bridge network: full outbound reach, no inbound (no Virtual Cell backend ever publishes a port). | Nothing beyond "no inbound"; outbound is unrestricted by design. |
| `ALLOWLIST` | The same plain bridge network as `EGRESS_ONLY`, plus the allowlist stamped on a container label for audit. | Enforcement: Docker's own SDK has no hostname-based outbound allowlist. `TODO(5.7a)` marks exactly what the in-Cell firewall (Night Veil's kill-switch work) still has to close. |
| `VPN_TOR` | Refused with `CellProvisionError` before anything is created. | Everything -- Night Veil needs its own image (5.3a) and routing (5.7a), neither built yet. |

## How to test this

- `packages/hivemind/tests/unit/hive/backends/`: unit tests for `base.py`, `fake.py`,
  `bootstrap.py` and every module under `docker/`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: the shared contract suite,
  parametrised by a fixture; `FakeCellBackend` and `DockerCellBackend` (over `FakeDockerClient`
  and `FakeReadinessGate`) both plug in today, `backends/qemu.py` (5.11) once it lands.
- `packages/hivemind/tests/integration/test_docker_backend.py`: `@pytest.mark.integration`, needs
  a real Docker daemon; skips cleanly when none answers.

## Not yet built

- `qemu.py` (roadmap step 5.11), `cloud/` (5.12).
