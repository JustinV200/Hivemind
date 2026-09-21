# ADR-0026: Virtual Cell backends sit behind one CellBackend protocol, Docker first and QEMU second

- Status: Accepted
- Date: 2026-09-21

## Context

Phase 5 makes the Hive able to provision its own Cells. A Virtual Cell has to exist on more than
one kind of infrastructure over the life of the project: containers on a developer's machine, real
VMs where container isolation is not enough, and cloud instances later. The development host is
Windows 11 Home, where Hyper-V is not available, so the first backend has to run on Docker Desktop
over WSL2, and the first real-VM backend has to be portable across Windows, macOS and Linux. The
Queen, placement, the Undertaker and the Overwintering pool must not learn any of this: codingrules
8.4 forbids an `if backend == ...` chain, and 8.7 forbids branching on anything but capabilities.

## Decision

**One protocol, `hive/backends/base.py: CellBackend`, is the only door to infrastructure.** It
offers `provision(spec) -> Cell`, idempotent `destroy(cell_id)`, `list_cells(hive_id)`, and
`pause` / `resume`, and it declares a frozen `BackendCapabilities` (can it snapshot, can it pause,
how many Cells it has headroom for). Callers read those capabilities and never the backend's name.
Backends are registered by name in `hive/registry.py` and constructed only in the composition root.

**Docker is the first backend and QEMU the second.** Docker (through the Docker SDK under
`asyncio.to_thread`) gives the shortest path to a working vertical slice on all three supported
hosts and in CI. QEMU follows with the same contract suite, a prebuilt qcow2 made from the same
Ubuntu 24.04 base, cloud-init for bootstrap and the serial console for readiness. Cloud backends
come after both, behind `hive/backends/cloud/base.py`, and are optional for Brood 1.0.

**Everything a backend creates carries the Hive's id as a label.** `list_cells(hive_id)` reads
those labels back from the infrastructure itself, so the Undertaker's startup sweep and
`hive cells abscond` can find and destroy every Cell this Hive made even when the Hive's own
SQLite file is lost. The trail is the second source, never the only one.

**`provision` is all-or-nothing and `destroy` is idempotent.** A failed provision cleans up what
it made before raising `CellProvisionError`; destroying an unknown or already-destroyed Cell
returns silently, because the Undertaker retries.

## Consequences

Positive: one contract suite (`tests/contracts/test_cell_backend_contract.py`) proves every
backend, and `FakeCellBackend` makes the lifecycle, placement and the Undertaker testable with no
infrastructure. A new backend is one module and one registry line. Orphans are recoverable from
labels alone.

Negative: the protocol is the lowest common denominator, so a backend-specific feature (Docker
checkpointing, a cloud provider's spot pricing) needs a capability flag before anyone may use it.
A container is weaker isolation than a VM: `isolation = "required"` is satisfied by Docker in
development, and an operator who needs a hypervisor boundary selects the QEMU backend in the
manifest. `subprocess` and the Docker SDK are confined to `hive/backends/` by `import-linter`.

## Alternatives considered

Hyper-V or WSL2 distributions directly: not available on Windows Home, and not portable.
Firecracker: Linux-only, so it fails the three-host requirement. Kubernetes as the first backend:
provisions a control plane before the first task can run, the opposite of a vertical slice. A
backend enum with branches in the lifecycle: rejected by codingrules 8.4; every new backend would
edit the lifecycle. Relying on the Hive's own database to find orphans: fails in exactly the case
that matters, when the database is what was lost.
