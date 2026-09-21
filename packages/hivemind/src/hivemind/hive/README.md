# hivemind.hive

The hive package (lowercase, distinct from the Hive as a whole) provisions and destroys Virtual
Cells: VM or container Cells the Queen owns outright rather than borrows. It covers their
lifecycle, Night Veil attestation (the always-teardown-only security tier) and the Overwintering
pool that keeps a dormant Cell around for fast reuse.

## Public API (roadmap step 5.1/5.2)

- `VirtualCellSpec` / `NetworkPolicy` (`models.py`): a request to provision one Virtual Cell --
  image, resources, lifetime, network policy, Exoskeleton flag, the `ForageCapacity` the image
  promises, its `CombShieldLevel`, a ready timeout, its `hive_id` and free-form labels.
- `VirtualCellStatus` / `TRANSITIONS` / `can_transition` / `assert_transition` /
  `can_enter_dormant` / `assert_dormant_allowed` (`cell_state.py`): the one state machine every
  Virtual Cell moves through, `PROVISIONING -> READY -> GRANTED -> RELEASED -> (DORMANT |
  DESTROYING) -> DESTROYED`, plus the separate Night Veil invariant (never DORMANT).
- `CellBackend` / `BackendCapabilities` / `VirtualCellRecord` (`backends/base.py`): the protocol
  every provisioning backend implements, its declared capabilities, and what `list_cells` returns.
- `FakeCellBackend` (`backends/fake.py`): the in-memory reference implementation, used by tests,
  demos and `hive doctor`.
- `BackendRegistry` / `CellBackendFactory` (`registry.py`): name -> `CellBackend`, for the
  composition root.
- `HiveError` and its subclasses (`errors.py`): this package's own error tree.

## How to test this

- `packages/hivemind/tests/unit/hive/`: unit tests for every module above, mirroring `src/`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: one contract suite run over
  every `CellBackend` implementation (`FakeCellBackend` today; `DockerCellBackend` and
  `QemuCellBackend` plug in through the same fixture once later roadmap steps land).

## Not yet built (later roadmap steps)

- `lifecycle.py` (5.6): the only intended caller of `cell_state.assert_transition` and
  `assert_dormant_allowed`.
- `night_veil.py` (5.7b), `snapshot.py` (5.10), `overwinter/` (5.9), `backends/docker.py` (5.4),
  `backends/qemu.py` (5.11), `backends/cloud/` (5.12).
