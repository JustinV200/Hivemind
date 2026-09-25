"""Roadmap step 5.10: DockerSnapshotter and QemuSnapshotter, and the ledger/factory around them.

`hivemind.cell.snapshot.Snapshotter` is the protocol the Capping gate calls
(`hivemind.supervision.capping.gate.GateDeps.snapshotter`) before an `irreversible` or
`device_command` proposal, and rolls back to on a failed postcondition (ADR-0018). This package is
where that protocol gets real implementations for Virtual Cells: `DockerSnapshotter` (a `docker
commit`, rolled back by recreating the container from the committed image) and `QemuSnapshotter`
(a QMP `savevm`/`loadvm` internal qcow2 snapshot). A Real Cell keeps using `hivemind.cell.
NoopSnapshotter` -- this package never touches Real Cells. `snapshotter_for` is how a composition
root picks the right one for a given `hivemind.hive.backends.base.CellBackend`, by capability, not
by name. Every snapshot either implementation takes is recorded in a shared `SnapshotLedger`
(disk-Forage accounting, retention expiry, budget eviction), so both backends' snapshots are
accounted the same way regardless of which one took them.

Fits into the Hive:
    Layer 3 (sources of Cells). Called by the composition root (`hivemind.cli.compose.
    virtual_cells`, not edited by this dispatch -- see its own module's report for the exact
    wiring lines) and by `hivemind.hive.lifecycle.CellLifecycle.teardown` (deletes a destroyed
    Cell's own snapshots). Calls into `hivemind.cell`, `hivemind.hive.backends.base`,
    `hivemind.hive.backends.docker`, `hivemind.hive.backends.qemu` and waggle only.

Key invariants:
    - Capping (`hivemind.supervision.capping`) never imports this package, or anything from
      `hive` at all: the Warden is the one composition-root actor that builds a Snapshotter and
      hands it to `GateDeps.snapshotter` (roadmap step 5.10's own wording).
    - Every Snapshotter built here shares one `SnapshotLedger` instance per Hive, so
      `SnapshotLedger.disk_used_bytes(cell_id)` is a single number regardless of which backend a
      Cell happens to run on.

See Also:
    - .claude/roadmap.md step 5.10 for this package's own build instructions.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the Snapshotter contract and
      the REVERSE_DIFF fallback this package's implementations never themselves trigger.
    - hivemind.cell.snapshot for Snapshotter, SnapshotId and NoopSnapshotter, the protocol and the
      Real-Cell implementation this package's two classes stand beside.

Public API:
    - SnapshotRecord, SnapshotLedgerPort, SnapshotLedger, SnapshotNotFoundError: the shared book
      of every live snapshot, its Protocol and the in-memory implementation
      (hivemind.hive.snapshot.ledger).
    - SqliteSnapshotLedger (hivemind.hive.snapshot.sqlite_ledger): the durable SnapshotLedgerPort
      implementation, so a rollback in a separate CLI process can see an earlier snapshot.
    - DockerSnapshotter (hivemind.hive.snapshot.docker), QemuSnapshotter
      (hivemind.hive.snapshot.qemu): the two backend-specific Snapshotter implementations.
    - DockerSnapshotImages (hivemind.hive.snapshot.docker): removes a Night Veil Cell's snapshot
      images at its teardown (codingrules section 12).
    - snapshotter_for, snapshot_images_for (hivemind.hive.snapshot.factory): choose a Cell's own
      Snapshotter, and what removes its snapshot images, by backend capability, never by name.
"""

from hivemind.hive.snapshot.docker import DockerSnapshotImages, DockerSnapshotter
from hivemind.hive.snapshot.factory import snapshot_images_for, snapshotter_for
from hivemind.hive.snapshot.ledger import (
    SnapshotLedger,
    SnapshotLedgerPort,
    SnapshotNotFoundError,
    SnapshotRecord,
)
from hivemind.hive.snapshot.qemu import QemuSnapshotter
from hivemind.hive.snapshot.sqlite_ledger import SqliteSnapshotLedger

__all__ = [
    "DockerSnapshotImages",
    "DockerSnapshotter",
    "QemuSnapshotter",
    "SnapshotLedger",
    "SnapshotLedgerPort",
    "SnapshotNotFoundError",
    "SnapshotRecord",
    "SqliteSnapshotLedger",
    "snapshot_images_for",
    "snapshotter_for",
]
