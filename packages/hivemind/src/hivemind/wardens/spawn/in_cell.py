"""Define InCellSpawnSource: the `in_cell` Warden spawn strategy (roadmap step 5.5).

`hivemind.wardens.deps.WardenDeps.source` is a `hivemind.cell.source.RealCellSource`: whichever
one a composition root injects is where a Warden's `start()` leases its Cell and opens its session
(`hivemind.wardens.warden.Warden.start`, unmodified by this dispatch). `hivemind.cell.local.
HiveStandSource` fills that seam for the Hive Stand; `InCellSpawnSource` fills it for a Warden that
is already running *inside* the one Virtual Cell it owns (a container or VM the Queen provisioned
and will destroy), because the roadmap's own invariant holds here too: "sub-bees run where their
Warden runs, never the other way round" (codingrules section 8.7), and for a Virtual Cell that is
always true from the moment the Cell exists -- there is no Level-0/Level-1 ladder to climb, unlike
a Real Cell. `InCellSpawnSource.cells()` reports exactly the one Cell this process is running
inside, probed once at construction; `lease()` never really contends with anything (only this one
Warden ever calls it) but still refuses a second overlapping lease, the same "no two tenancies at
once" rule every `RealCellSource` keeps; `open_session()` hands back a
`hivemind.cell.in_cell.InCellSession`. This is the whole "in_cell strategy": a composition root
(`hivemind.cli.in_cell`, roadmap step 5.5's own entry point) picks which `RealCellSource` it builds
`WardenDeps.source` from, and nothing above that -- `Warden`, its ticks, or any role code -- ever
learns which one it got, let alone branches on `cell.kind` to find out.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside `wardens.spawn`. Implements
    `hivemind.cell.source.RealCellSource`. Calls into `hivemind.cell` (Cell, CellKind, CellSession,
    RealCellLease and friends), `hivemind.cell.in_cell` (InCellSession, InCellLeaseReleaser),
    `hivemind.forage` (ForageCapacity), `hivemind.pheromone` and waggle only. Selected by
    `hivemind.cli.in_cell`, never by role code.

Key invariants:
    - `cells()` always returns exactly one Cell of kind VIRTUAL, whose id never changes across
      calls (given once at construction: the id the Queen minted when it provisioned this Cell).
    - `lease()` either returns an OPEN RealCellLease or raises LeaseRefusedError; like
      `HiveStandSource`, at most one lease is OPEN at a time, so a lease refused for any other
      reason never leaves a half-opened lease or a stale `_active_lease` behind.
    - `open_session()` always returns an `InCellSession`, never a `LocalProcessSession`: this is
      the one place that choice is made for the in_cell strategy.

See Also:
    - .claude/roadmap.md step 5.5 for "wardens/spawn/ gains the in_cell strategy... Strategy
      selection must not branch on cell.kind; choose by injected configuration."
    - .claude/codingrules.md section 8.7 for "Where the runtime runs is a Warden spawn decision in
      wardens/spawn/, never visible to role code."
    - hivemind.cell.in_cell for InCellSession and InCellLeaseReleaser, this source's own session.
    - hivemind.cell.local.source for HiveStandSource, the sibling strategy this one mirrors.
    - hivemind.cli.in_cell for the composition root that builds and injects this source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.in_cell import InCellLeaseReleaser, InCellSession
from hivemind.cell.lease import LeaseFacts, LeaseRequest, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.models import Cell, CellCapabilities, CellKind
from hivemind.cell.session import SCRATCH_DIR_MODE, CellSession
from hivemind.cell.source import CellIdentity
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.forage import ForageCapacity
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, new_lease_id

_SOURCE_NAME = "in_cell"  # The `source` field the one Cell this source hands out carries.

__all__ = ["InCellSpawnConfig", "InCellSpawnSource"]


@dataclass(frozen=True, slots=True)
class InCellSpawnConfig:
    """What describes this Cell to `InCellSpawnSource`, grouped to stay within codingrules 5.1.

    Attributes:
        cell_id: The id the Queen minted for this Cell when it provisioned it
            (`hivemind.manifest.env`'s `HIVEMIND_CELL_ID` reader, read by the composition root).
        capabilities: This container's own platform facts and capability flags, probed the same
            way `hivemind.cell.local.probe.probe_host` probes the Hive Stand (a Virtual Cell image
            is Ubuntu Linux too, so the same probe applies unchanged).
        capacity: This container's own Forage report.
        comb_shield: This Cell's security tier; MEADOW unless the Queen provisioned it higher
            (codingrules section 8.7: "New Virtual Cells default to MEADOW").
        scratch_root: The directory inside this Cell every lease's own scratch subdirectory is
            created under.
    """

    cell_id: CellId
    capabilities: CellCapabilities
    capacity: ForageCapacity
    comb_shield: CombShieldLevel
    scratch_root: Path


class InCellSpawnSource:
    """The Virtual Cell this Warden already runs inside, as a RealCellSource of exactly one Cell."""

    def __init__(
        self,
        config: InCellSpawnConfig,
        identity: CellIdentity,
        trail: PheromoneTrail,
        clock: Clock,
    ) -> None:
        """Build an InCellSpawnSource describing this process's own Cell.

        Args:
            config: What describes this Cell; see `InCellSpawnConfig`.
            identity: The Hive, node and actor this source stamps on every trail event.
            trail: Where `cell.leased`/`cell.released` events land.
            clock: Source of every minted id and timestamp.
        """
        self._cell = Cell(
            id=config.cell_id,
            kind=CellKind.VIRTUAL,
            name=str(config.cell_id),
            source=_SOURCE_NAME,
            capabilities=config.capabilities,
            capacity=config.capacity,
            # Cell's own validator (codingrules section 6.1): a VIRTUAL Cell is always FULL.
            access_level=AccessLevel.FULL,
            comb_shield=config.comb_shield,
        )
        self._scratch_root = config.scratch_root
        self._identity = identity
        self._trail = trail
        self._clock = clock
        self._active_lease: RealCellLease | None = None

    @property
    def name(self) -> str:
        """This source's name, `"in_cell"`, the value its one Cell carries as `source`."""
        return _SOURCE_NAME

    async def cells(self) -> tuple[Cell, ...]:
        """Return the one Cell this process is running inside.

        Returns:
            A one-element tuple holding this Cell; unlike the Hive Stand's own source, capacity
            here is not refreshed live per call -- a Virtual Cell's resources are fixed for its
            whole disposable lifetime by its `VirtualCellSpec` (a `hive` concern), never contended
            by a second tenant the way the Hive Stand's free memory or disk can be.
        """
        return (self._cell,)

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease on this Cell, refusing a second one while the first is still open.

        Args:
            request: Which Cell, held by which Warden, at which access level.

        Returns:
            An OPEN RealCellLease with its own scratch subdirectory.

        Raises:
            LeaseRefusedError: `request.cell_id` names a different Cell, or a lease on this one
                is already OPEN, RELEASING or ORPHANED.
        """
        self._check_refusal(request)
        lease_id = new_lease_id(self._clock)
        scratch_root = self._scratch_root / lease_id
        # Private to the Cell's own user, exactly as the Hive Stand's lease scratch is.
        scratch_root.mkdir(mode=SCRATCH_DIR_MODE, parents=True, exist_ok=True)
        facts = LeaseFacts(
            id=lease_id,
            cell_id=self._cell.id,
            holder=request.holder,
            task_id=request.task_id,
            scratch_root=scratch_root,
            access_level=self._cell.access_level,
            comb_shield=self._cell.comb_shield,
            allowed_paths=request.allowed_paths,
        )
        lease = RealCellLease(
            facts,
            trail=self._trail,
            clock=self._clock,
            identity=self._identity,
            releaser=InCellLeaseReleaser(self._clock),
        )
        await lease.open()
        self._active_lease = lease
        return lease

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open an InCellSession scoped to `lease`.

        Args:
            lease: An OPEN lease this source itself issued.

        Returns:
            A new InCellSession over `lease`'s scratch directory and allowed paths.
        """
        return InCellSession(lease, self._clock)

    def _check_refusal(self, request: LeaseRequest) -> None:
        """Raise LeaseRefusedError for a wrong Cell id or an already-open lease."""
        if request.cell_id != self._cell.id:
            raise LeaseRefusedError(request.cell_id, "no such Cell for this in_cell source")
        if self._active_lease is not None and self._active_lease.state is not LeaseState.RELEASED:
            raise LeaseRefusedError(request.cell_id, "this Cell is already leased")
