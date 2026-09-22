"""Define snapshotter_for: choose a Cell's own Snapshotter by backend capability, never by name.

Roadmap step 5.10: "A snapshotter_for(backend, ...) factory chooses by backend.capabilities.
can_snapshot, never by backend name." A backend that declares `can_snapshot=False` always gets
`hivemind.cell.NoopSnapshotter` -- the documented no-op with a logged warning, whose `rollback`
always raises `SnapshotUnsupportedError` so `hivemind.supervision.capping.gate` falls back to
REVERSE_DIFF (ADR-0018) -- whatever kind of backend it is. A backend that declares
`can_snapshot=True` needs a snapshotter built over that same backend's own client/runner port,
which this module cannot get from `hivemind.hive.backends.base.CellBackend` alone (that Protocol
declares no such accessor, and this dispatch's file list does not include `backends/base.py`); the
`_SNAPSHOTTER_BUILDERS` registry below is a small, explicit `type(backend) -> builder` table
(codingrules section 8.4: "plugins by registry, not by if chain") rather than an `isinstance`
chain hand-written per call, so a future can_snapshot backend this factory does not yet know how
to build degrades to `NoopSnapshotter` -- with a warning already built in, never a crash -- instead
of every caller needing its own fallback.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.snapshot`. Called by the composition root
    (`hivemind.cli.compose.virtual_cells`, a file this dispatch may not edit -- see that module's
    own report for the exact wiring lines needed) once per registered `CellBackend`, to build the
    `Snapshotter` a Warden's `hivemind.supervision.capping.gate.GateDeps.snapshotter` needs. Calls
    into `hivemind.cell` (NoopSnapshotter, Snapshotter), `hivemind.hive.backends.base`
    (CellBackend), `hivemind.hive.backends.docker.backend` (DockerCellBackend),
    `hivemind.hive.backends.qemu.backend` (QemuCellBackend), `hivemind.hive.snapshot.docker`,
    `hivemind.hive.snapshot.ledger`, `hivemind.hive.snapshot.qemu` and waggle only.

Key invariants:
    - `backend.capabilities.can_snapshot is False` always returns `NoopSnapshotter()`, regardless
      of `type(backend)`: the capability flag is checked first and is the only thing that can ever
      route to the no-op path.
    - `_SNAPSHOTTER_BUILDERS` is read-only module data, never mutated at runtime: a new backend
      kind is registered here, in this one table, not by editing `snapshotter_for` itself.

See Also:
    - .claude/roadmap.md step 5.10 for "chooses by capabilities... never by backend name".
    - .claude/codingrules.md section 8.4 for "Plugins by registry, not by if chain".
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for why SnapshotUnsupportedError
      is what makes the gate fall back to REVERSE_DIFF.
    - hivemind.hive.snapshot.docker and .qemu for the two snapshotters this factory builds.
"""

from __future__ import annotations

from collections.abc import Callable

from hivemind.cell import NoopSnapshotter, Snapshotter
from hivemind.hive.backends.base import CellBackend
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.qemu.backend import QemuCellBackend
from hivemind.hive.snapshot.docker import DEFAULT_RETENTION_S, DockerSnapshotter
from hivemind.hive.snapshot.ledger import SnapshotLedgerPort
from hivemind.hive.snapshot.qemu import QemuSnapshotter
from waggle.clock import Clock

__all__ = ["snapshotter_for"]

# A builder takes (backend, ledger, clock, retention_s, disk_budget_bytes) and returns a
# Snapshotter over that backend's own client/runner port; see the module docstring for why this
# is keyed by type rather than branched with isinstance.
_Builder = Callable[[CellBackend, SnapshotLedgerPort, Clock, float, "int | None"], Snapshotter]


def snapshotter_for(
    backend: CellBackend,
    ledger: SnapshotLedgerPort,
    clock: Clock,
    *,
    retention_s: float = DEFAULT_RETENTION_S,
    disk_budget_bytes: int | None = None,
) -> Snapshotter:
    """Choose `backend`'s own Snapshotter, by its declared `capabilities.can_snapshot` alone.

    Args:
        backend: A registered `CellBackend`; only its `.capabilities` and, when that says yes,
            its own type are read (module docstring's own "never by name").
        ledger: The Hive's shared SnapshotLedger, passed straight through to whichever
            Snapshotter is built.
        clock: Injected time source, passed straight through.
        retention_s: Seconds a snapshot survives before expiry; manifest `[virtual_cells]
            snapshot_retention_s`.
        disk_budget_bytes: The most bytes one Cell's own live snapshots may hold; manifest
            `[virtual_cells] snapshot_disk_budget_mb` converted to bytes, or None for no cap.

    Returns:
        `NoopSnapshotter()` when `backend.capabilities.can_snapshot` is False, or this factory has
        no builder registered for `type(backend)` yet; otherwise a `DockerSnapshotter` or
        `QemuSnapshotter` built over `backend`'s own client/runner port.
    """
    if not backend.capabilities.can_snapshot:
        return NoopSnapshotter()
    builder = _builders().get(type(backend))
    if builder is None:
        # A future can_snapshot backend this factory does not know how to build yet: degrade to
        # the documented no-op rather than raise (module docstring's own key invariant).
        return NoopSnapshotter()
    return builder(backend, ledger, clock, retention_s, disk_budget_bytes)


def _build_docker_snapshotter(
    backend: CellBackend,
    ledger: SnapshotLedgerPort,
    clock: Clock,
    retention_s: float,
    disk_budget_bytes: int | None,
) -> Snapshotter:
    """Build a DockerSnapshotter over `backend`'s own DockerClientPort.

    # SAFETY: only ever called through `_builders()[DockerCellBackend]`, which guarantees `backend`
    # is one; the assert documents that for mypy rather than trusting an unchecked cast.
    """
    assert isinstance(backend, DockerCellBackend)  # noqa: S101
    return DockerSnapshotter(
        backend.client,
        ledger,
        clock,
        retention_s=retention_s,
        disk_budget_bytes=disk_budget_bytes,
    )


def _build_qemu_snapshotter(
    backend: CellBackend,
    ledger: SnapshotLedgerPort,
    clock: Clock,
    retention_s: float,
    disk_budget_bytes: int | None,
) -> Snapshotter:
    """Build a QemuSnapshotter over `backend`'s own QemuRunnerPort.

    # SAFETY: only ever called through `_builders()[QemuCellBackend]`, which guarantees `backend`
    # is one; the assert documents that for mypy rather than trusting an unchecked cast.
    """
    assert isinstance(backend, QemuCellBackend)  # noqa: S101
    return QemuSnapshotter(
        backend.runner,
        ledger,
        clock,
        retention_s=retention_s,
        disk_budget_bytes=disk_budget_bytes,
    )


def _builders() -> dict[type, _Builder]:
    """Return the `type(backend) -> Snapshotter builder` table (module docstring's own registry).

    Built fresh on each call rather than once at import (codingrules section 5.5: no module-level
    mutable state) -- two entries, so the cost is negligible next to an actual snapshot/rollback.
    """
    return {
        DockerCellBackend: _build_docker_snapshotter,
        QemuCellBackend: _build_qemu_snapshotter,
    }
