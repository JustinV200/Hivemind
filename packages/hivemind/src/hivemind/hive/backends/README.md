# hivemind.hive.backends

The backends package holds one CellBackend implementation per kind of infrastructure a Virtual
Cell can be provisioned on: local containers, a local hypervisor, and cloud providers under
backends/cloud/.

## Public API (roadmap step 5.2)

- `CellBackend` (`base.py`): `provision`, `destroy`, `list_cells`, `pause`, `resume`, plus `name`
  and `capabilities` so a caller branches on what a backend can do, never on which one it is.
- `BackendCapabilities` (`base.py`): `can_snapshot`, `can_pause`, `headroom`.
- `VirtualCellRecord` (`base.py`): what `list_cells` returns -- id, status, image, labels,
  created_at, enough for the Undertaker's orphan sweep to reconcile without asking twice.
- `FakeCellBackend` (`fake.py`): the in-memory reference implementation; deterministic via an
  injected `Clock`, with switches to simulate provision failure, slow readiness and destroy
  failure, and every call recorded for assertions.

## How to test this

- `packages/hivemind/tests/unit/hive/backends/`: unit tests for `base.py` and `fake.py`.
- `packages/hivemind/tests/contracts/test_cell_backend_contract.py`: the shared contract suite,
  parametrised by a fixture so `backends/docker.py` (5.4) and `backends/qemu.py` (5.11) plug in
  without changing the suite itself.

## Not yet built

- `docker.py` (roadmap step 5.4), `qemu.py` (5.11), `cloud/` (5.12).
