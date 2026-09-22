"""Offline helpers shared by `hivemind.cli.readback.virtual`'s six Virtual/Real Cell commands.

Roadmap step 5.13 (`cli/cells.py` grows `inspect`, `destroy`, `release`, `snapshot`, `rollback`,
`abscond`): every one of those commands needs the same handful of reads and writes -- find a
Virtual Cell across every registered backend by id (`virtual_cell_lookup`, `list_all_virtual`),
find every Real Cell lease the trail shows opened but never closed (`open_real_leases`), guess
whether a Queen process might still be running from the trail alone (`queen_likely_running`, since
v0 has no live link into one -- `hivemind.cli.readback.wardens`'s own module docstring), build an
`Undertaker` over a ledger-backed `GrantRevoker` (`build_undertaker`, `LedgerGrantRevoker`),
reconstruct an already-open `RealCellLease` from nothing but its own `cell.leased` trail event and
a still-present scratch directory (`reconstruct_lease`), and stand in a minimal, schema-valid `Cell`
for a Virtual Cell `list_cells` only reported as a `VirtualCellRecord` (`placeholder_cell`, the
snapshot/rollback seam's own documented gap: a `VirtualCellRecord` carries no `capabilities`/
`capacity`, so a future per-backend `Snapshotter` that needs real Cell fidelity has to re-derive it
itself). Kept separate from `virtual.py` (output formatting and the six thin command bodies) so
neither file grows past codingrules section 5.1's 300-line budget.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.readback`. Called only by
    `hivemind.cli.readback.virtual`. Calls into `hivemind.cell` (RealCellLease, LeaseFacts,
    AccessLevel, CombShieldLevel, Cell, CellCapabilities, Snapshotter), `hivemind.cell.local`
    (HiveStandLeaseReleaser), `hivemind.forage` (ForageCapacity, HostCapacity), `hivemind.hive`
    (BackendRegistry, VirtualCellRecord), `hivemind.hive.snapshot` (SnapshotLedger,
    snapshotter_for, a lazy in-function import -- see `build_snapshotter`'s own docstring),
    `hivemind.pheromone` (PheromoneTrail, TrailQuery, MAX_QUERY_LIMIT, ForageEvent),
    `hivemind.queen.forage.ledger` (ForageLedger), `hivemind.workers.roles.undertaker` and waggle
    only.

Key invariants:
    - `queen_likely_running` is a heuristic, not a fact (module docstring): a Warden that crashed
      without reaching `stop()` leaves its latest `warden.*` event stuck at `warden.started`/
      `.active`/`.watch` forever, which this reads as "still running" even though nothing is. It
      only ever widens toward "assume it's running and use the safe, order-writing path", never
      the other way, so it cannot cause `hive cells release` to bypass a live Queen.
    - `reconstruct_lease` sets `RealCellLease.state` to `OPEN` directly, bypassing `open()`: this
      is a reconstruction of state the trail says already happened, not a fresh open, so calling
      `open()` again would both write a duplicate `cell.leased` event and violate its own
      REQUESTED -> OPEN precondition (`RealCellLease.open`'s own docstring).
    - `placeholder_cell`'s `capabilities`/`capacity` are documented stand-ins, not a real read of
      the Cell (module docstring); only `id` (and, loosely, `name`) are trustworthy.

See Also:
    - .claude/roadmap.md step 5.13 for the six commands this module supports.
    - hivemind.workers.roles.undertaker.sweep for orphan_virtual_cells/orphan_real_leases, the
      Queen-startup sweep's own pure decisions this module's effectful reads parallel offline.
    - hivemind.cell.lease for RealCellLease, LeaseFacts and the trail event shape `cell.leased`
      records, which `open_real_leases`/`reconstruct_lease` read back.

Public API:
    - LedgerGrantRevoker: a `GrantRevoker` backed by an offline-restored `ForageLedger`.
    - LeaseOrphan: one still-open Real Cell lease, as reconstructed from the trail alone.
    - build_undertaker, list_all_virtual, virtual_cell_lookup, open_real_leases,
      queen_likely_running, reconstruct_lease, placeholder_cell, build_snapshotter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import (
    AccessLevel,
    Cell,
    CellCapabilities,
    CellIdentity,
    CellKind,
    CombShieldLevel,
    LeaseFacts,
    LeaseState,
    OsFamily,
    RealCellLease,
    Snapshotter,
)
from hivemind.cell.local import HiveStandLeaseReleaser
from hivemind.forage import ForageCapacity, HostCapacity
from hivemind.hive import BackendRegistry, CellBackend, VirtualCellRecord
from hivemind.pheromone import MAX_QUERY_LIMIT, ForageEvent, PheromoneTrail, TrailQuery
from hivemind.queen.forage.ledger import ForageLedger
from hivemind.workers.roles.undertaker import (
    NullLeavingsRemover,
    NullWaxRetirer,
    Undertaker,
    UndertakerDeps,
)
from waggle.clock import Clock
from waggle.ids import CellId, HiveId, LeaseId, TaskId, WardenId, new_event_id
from waggle.messages import OsFamily as WireOsFamily

# A placeholder_cell's own stand-in facts (module docstring's "Key invariants"): a terminal-only
# Cell, matching hivemind.hive.backends.fake.FakeCellBackend's own defaults for a Cell nothing
# else describes better offline.
_PLACEHOLDER_ARCH = "x86_64"
_PLACEHOLDER_DISTRIBUTION = "unknown"
_PLACEHOLDER_SHELL = "/bin/sh"
_PLACEHOLDER_PACKAGE_MANAGER = "unknown"
_PLACEHOLDER_PYTHON_VERSION = "unknown"
_PLACEHOLDER_CORES = 1
_PLACEHOLDER_MEMORY_BYTES = 512 * 1024 * 1024
_PLACEHOLDER_DISK_BYTES = 1024 * 1024 * 1024
_PLACEHOLDER_MAX_SUB_BEES = 1

__all__ = [
    "LeaseOrphan",
    "LedgerGrantRevoker",
    "build_snapshotter",
    "build_undertaker",
    "list_all_virtual",
    "open_real_leases",
    "placeholder_cell",
    "queen_likely_running",
    "reconstruct_lease",
    "virtual_cell_lookup",
]


class LedgerGrantRevoker:
    """A `GrantRevoker` over an offline `ForageLedger`.

    Wraps `hivemind.workers.roles.undertaker.role.GrantRevoker`: revokes every live grant on a
    Cell and writes through to the durable store the ledger was restored from, so the effect
    survives this one CLI process (`hivemind.cli.compose.deps.build_ledger` is the one place a CLI
    command builds that ledger).
    """

    def __init__(
        self, ledger: ForageLedger, trail: PheromoneTrail, clock: Clock, identity: CellIdentity
    ) -> None:
        """Build a revoker over an already-restored `ledger` and the trail it writes receipts to."""
        self._ledger = ledger
        self._trail = trail
        self._clock = clock
        self._identity = identity

    async def revoke_for_cell(self, cell_id: CellId, reason: str) -> int:
        """Revoke every live grant on `cell_id`; see `GrantRevoker.revoke_for_cell`."""
        # Local import: hivemind.forage.grant_state is a plain, legal downward import, but the
        # state machine's own transition check is folded in here rather than calling
        # hivemind.queen.forage.grants.revoke, which takes a full QueenDeps this offline caller
        # has no Queen to build (module docstring).
        from hivemind.forage.grant_state import GrantState, can_transition

        revoked = 0
        for grant in tuple(self._ledger.live_grants()):
            if grant.cell_id != cell_id or not can_transition(grant.state, GrantState.REVOKED):
                continue  # A different Cell, or still ISSUED (never drawn on): nothing to revoke.
            await self._ledger.record_grant(grant.model_copy(update={"state": GrantState.REVOKED}))
            await self._record_revoked(grant.id, reason)
            revoked += 1
        return revoked

    async def _record_revoked(self, grant_id: str, reason: str) -> None:
        """Record one `forage.revoked` event, the same kind `queen.forage.grants.revoke` writes."""
        del reason  # Free text never belongs in a payload (codingrules section 12); id-only here.
        event = ForageEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=self._clock.now(),
            actor=self._identity.actor,
            kind="forage.revoked",
            subject_id=grant_id,
            payload={"cause": "RELEASED"},
        )
        await self._trail.record(event)


def build_undertaker(
    backend: CellBackend,
    trail: PheromoneTrail,
    clock: Clock,
    identity: CellIdentity,
    ledger: ForageLedger,
) -> Undertaker:
    """Build an offline `Undertaker`: a real `LedgerGrantRevoker`, no Wax or Leavings store.

    `hive cells destroy`/`abscond` (roadmap step 5.13) have no live Cell Wax store or Leavings
    ledger to wire in (the latter lives on another branch, not yet merged --
    `hivemind.workers.roles.undertaker.role.LeavingsRemover`'s own docstring), so both use the
    documented no-op defaults already shipped for exactly this gap.
    """
    deps = UndertakerDeps(
        backend=backend,
        grant_revoker=LedgerGrantRevoker(ledger, trail, clock, identity),
        wax_retirer=NullWaxRetirer(),
        leavings_remover=NullLeavingsRemover(),
        trail=trail,
        clock=clock,
        identity=identity,
    )
    return Undertaker(deps)


async def virtual_cell_lookup(
    registry: BackendRegistry, hive_id: HiveId, cell_id: str
) -> tuple[str, VirtualCellRecord] | None:
    """Return `(backend_name, record)` for the first registered backend that lists `cell_id`.

    "Deciding Real/Virtual... is by 'is it in the backend's labelled list', never `cell.kind`"
    (roadmap step 5.13): this is that one check, shared by `inspect`/`destroy`.
    """
    for name, record in await list_all_virtual(registry, hive_id):
        if record.cell_id == cell_id:
            return name, record
    return None


async def list_all_virtual(
    registry: BackendRegistry, hive_id: HiveId
) -> tuple[tuple[str, VirtualCellRecord], ...]:
    """Return every `(backend_name, record)` every registered backend lists for `hive_id`."""
    rows: list[tuple[str, VirtualCellRecord]] = []
    for name in registry.names():
        backend = registry.get(name)
        for record in await backend.list_cells(hive_id):
            rows.append((name, record))
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class LeaseOrphan:
    """One Real Cell lease the trail shows opened but never closed, reconstructed from that event.

    Attributes:
        lease_id: The lease's own id.
        cell_id: The Cell it was opened on.
        holder: The Warden that opened it.
        task_id: The task it served, or None for an internal lease.
        access_level: The access it was granted.
        comb_shield: The Cell's own tier at the time it was opened.
    """

    lease_id: str
    cell_id: CellId
    holder: WardenId
    task_id: TaskId | None
    access_level: AccessLevel
    comb_shield: CombShieldLevel


async def open_real_leases(trail: PheromoneTrail) -> tuple[LeaseOrphan, ...]:
    """Return every `cell.leased` event this trail holds with no matching `cell.released`.

    Unlike `hivemind.workers.roles.undertaker.sweep.orphan_real_leases`, this never filters by
    "the task is still active": `hive cells abscond` tears down every open lease this Hive's own
    database still shows, active task or not (roadmap step 5.13's own wording, "release every
    lease"), so the whole richer fact set (not just the id) is read back in one pass here.
    """
    leased = await trail.query(TrailQuery(kind="cell.leased", limit=MAX_QUERY_LIMIT))
    released_ids = {
        str(event.payload.get("lease_id"))
        for event in await trail.query(TrailQuery(kind="cell.released", limit=MAX_QUERY_LIMIT))
    }
    orphans: list[LeaseOrphan] = []
    for event in leased:
        lease_id = event.payload.get("lease_id")
        if lease_id is None or str(lease_id) in released_ids:
            continue
        orphans.append(
            LeaseOrphan(
                lease_id=str(lease_id),
                cell_id=CellId(event.subject_id),
                holder=WardenId(str(event.payload.get("holder"))),
                task_id=TaskId(str(event.payload["task_id"]))
                if event.payload.get("task_id") is not None
                else None,
                access_level=AccessLevel(str(event.payload.get("access_level"))),
                comb_shield=CombShieldLevel(str(event.payload.get("comb_shield"))),
            )
        )
    return tuple(orphans)


async def queen_likely_running(trail: PheromoneTrail) -> bool:
    """Guess, from the trail alone, whether a Queen process might still be running.

    Heuristic, never a fact (module docstring): True whenever any Warden's own latest `warden.*`
    event is not `warden.stopped` (v0 has exactly one Warden, `hivemind.cli.readback.wardens`'s
    own module docstring, but this reads every one it has ever seen, the same way that command
    does). `hive cells release` treats True as "write the order and let a live Queen's tick find
    it"; only a clean False is trusted enough to report the lease orphaned.
    """
    events = await trail.query(TrailQuery(family="warden", limit=MAX_QUERY_LIMIT))
    latest: dict[str, str] = {}
    for event in events:
        latest[event.subject_id] = event.kind
    return any(kind != "warden.stopped" for kind in latest.values())


def reconstruct_lease(
    orphan: LeaseOrphan,
    scratch_root: Path,
    trail: PheromoneTrail,
    clock: Clock,
    identity: CellIdentity,
) -> RealCellLease:
    """Rebuild an already-OPEN `RealCellLease` from `orphan`'s own trail facts, for release().

    `allowed_paths` is always empty here: the `cell.leased` event never recorded it (module
    docstring's own "Key invariants" list this as a documented, minor gap -- an allowed path
    outside scratch that this reconstruction cannot restore is left in `residual_paths` instead of
    silently dropped, since `HiveStandLeaseReleaser.release()` only ever touches what
    `restore_records`/`started_pids` name, both empty here for the same reason: this process never
    saw the original session that would have recorded them).
    """
    facts = LeaseFacts(
        id=LeaseId(orphan.lease_id),
        cell_id=orphan.cell_id,
        holder=orphan.holder,
        task_id=orphan.task_id,
        scratch_root=scratch_root,
        access_level=orphan.access_level,
        comb_shield=orphan.comb_shield,
        allowed_paths=(),
    )
    lease = RealCellLease(
        facts, trail=trail, clock=clock, identity=identity, releaser=HiveStandLeaseReleaser(clock)
    )
    # Reconstruction of already-open state, not a fresh open (module docstring's own "Key
    # invariants"): set directly rather than through open(), which would re-record cell.leased.
    lease.state = LeaseState.OPEN
    return lease


def placeholder_cell(record: VirtualCellRecord) -> Cell:
    """Build a minimal, schema-valid `Cell` for `record`, for the snapshot/rollback seam alone.

    See the module docstring's own "Key invariants": only `id` is trustworthy here.
    """
    capabilities = CellCapabilities(
        os=OsFamily.LINUX,
        arch=_PLACEHOLDER_ARCH,
        distribution=_PLACEHOLDER_DISTRIBUTION,
        shell=_PLACEHOLDER_SHELL,
        package_manager=_PLACEHOLDER_PACKAGE_MANAGER,
        python_version=_PLACEHOLDER_PYTHON_VERSION,
        has_display=False,
        has_audio=False,
        has_browser=False,
        can_start_display=False,
        can_host_model=True,
        network_scopes=(),
    )
    capacity = ForageCapacity(
        host=HostCapacity(
            cores=_PLACEHOLDER_CORES,
            memory_bytes=_PLACEHOLDER_MEMORY_BYTES,
            memory_free_bytes=_PLACEHOLDER_MEMORY_BYTES,
            disk_bytes=_PLACEHOLDER_DISK_BYTES,
            disk_free_bytes=_PLACEHOLDER_DISK_BYTES,
            cpu_load=0.0,
            gpus=(),
            arch=_PLACEHOLDER_ARCH,
            os=WireOsFamily.LINUX,
        ),
        local_seats=(),
        max_sub_bees=_PLACEHOLDER_MAX_SUB_BEES,
    )
    return Cell(
        id=record.cell_id,
        kind=CellKind.VIRTUAL,
        name=record.image,
        source="hive",
        capabilities=capabilities,
        capacity=capacity,
        access_level=AccessLevel.FULL,
        comb_shield=CombShieldLevel.MEADOW,
    )


def build_snapshotter(
    backend: CellBackend, clock: Clock, *, retention_s: float, disk_budget_bytes: int | None
) -> Snapshotter:
    """Return `backend`'s own `Snapshotter`, via `hivemind.hive.snapshot.snapshotter_for`.

    Lazy, in-command import (roadmap step 5.13's own instruction): `hivemind.hive.snapshot`
    (`DockerSnapshotter`/`QemuSnapshotter`, roadmap step 5.10) is a concurrent dispatch's own
    package; importing it here, inside this function, rather than at this module's own top level
    keeps this module importable even on a gate run from before that package landed.

    A `hive cells snapshot`/`rollback` pair invoked as two separate CLI processes has one
    documented limitation this dispatch cannot close from here: `DockerSnapshotter`/
    `QemuSnapshotter` each keep their own snapshot-id-to-image mapping in memory, in the instance
    `snapshotter_for` builds (its own module docstring: built for a long-lived Warden holding one
    `GateDeps.snapshotter` for its whole life, not a stateless CLI spanning two processes), so a
    `rollback` invocation's own fresh instance never sees what an earlier, separate `snapshot`
    invocation recorded -- only a snapshot backend that declares `capabilities.can_snapshot=False`
    (`NoopSnapshotter`, whose own `rollback` always raises regardless) is unaffected, since there
    is nothing to round-trip in the first place. Flagged in this dispatch's own report as the open
    integration question for whoever gives `hive cells snapshot`/`rollback` a durable ledger to
    share across processes.

    Args:
        backend: The Virtual Cell's own backend; only `.capabilities` and, when it can snapshot,
            its own type are read (`snapshotter_for`'s own "never by name").
        clock: Injected time source, passed straight through.
        retention_s: Manifest `[virtual_cells] snapshot_retention_s`.
        disk_budget_bytes: Manifest `[virtual_cells] snapshot_disk_budget_mb`, converted to bytes.

    Returns:
        `NoopSnapshotter()` when `backend` cannot snapshot; otherwise a fresh, in-memory-only
        `DockerSnapshotter`/`QemuSnapshotter` over a fresh `SnapshotLedger()`.
    """
    from hivemind.hive.snapshot import SnapshotLedger, snapshotter_for

    ledger = SnapshotLedger()
    return snapshotter_for(
        backend, ledger, clock, retention_s=retention_s, disk_budget_bytes=disk_budget_bytes
    )
