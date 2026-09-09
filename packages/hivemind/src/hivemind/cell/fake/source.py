"""Provide FakeCellSource and FakeLeaseReleaser: an in-memory RealCellSource for tests and demos.

`FakeCellSource` implements `hivemind.cell.source.RealCellSource` over a fixed, in-memory Cell
inventory: `lease()` refuses an unknown `cell_id` or one another still-open lease already holds,
and otherwise opens a `RealCellLease` whose scratch root is a synthetic path (nothing is ever
written to a real filesystem). `FakeLeaseReleaser` is the `LeaseReleaser` every lease this source
opens is given: since the fake never actually starts a process or touches a real path, there is
nothing to kill or restore, so it always reports a clean release. Shipped code, not test-only
(codingrules section 14.4), because `pollen`, `hive doctor` and demo paths use it too.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implements
    `hivemind.cell.source.RealCellSource`; constructed directly by tests and demo scripts.
    Calls into hivemind.cell.errors, hivemind.cell.fake.session, hivemind.cell.lease,
    hivemind.cell.lease_state, hivemind.cell.models, hivemind.cell.source and
    hivemind.pheromone only.

Key invariants:
    - "Already leased" is decided by looking at the tracked lease's own `state`, not by a
      separate set kept in sync by hand: once `release()` moves a lease to RELEASED, the same
      `cell_id` is leasable again on the very next call, with no extra bookkeeping in this class.
    - `lease()` either returns an OPEN RealCellLease or raises LeaseRefusedError; it never leaves
      a lease in this source's tracking table without also returning it.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.cell.source for RealCellSource, the Protocol this class implements.
    - hivemind.cell.lease for RealCellLease, LeaseFacts, LeaseRequest and LeaseReleaseReport.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.fake.session import FakeSession
from hivemind.cell.lease import LeaseFacts, LeaseReleaseReport, LeaseRequest, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession
from hivemind.cell.source import CellIdentity
from hivemind.pheromone import PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, new_lease_id

__all__ = ["FakeCellSource", "FakeLeaseReleaser"]

_FAKE_SOURCE_NAME = "fake"  # The `source` field every Cell this source hands out carries.


class FakeLeaseReleaser:
    """Report a clean release, unconditionally: the fake never leaves anything behind.

    Injected into every RealCellLease `FakeCellSource.lease` opens, the same seam
    `hivemind.cell.local.HiveStandLeaseReleaser` fills for the Hive Stand.
    """

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Return a report claiming full restoration, always.

        Args:
            lease: The lease being released; unused, since nothing was ever really started or
                written outside this fake's own in-memory files dict.

        Returns:
            A LeaseReleaseReport with killed_processes=0, residual_paths=(), is_restored=True.
        """
        return LeaseReleaseReport(killed_processes=0, residual_paths=(), is_restored=True)


class FakeCellSource:
    """An in-memory RealCellSource: a fixed Cell inventory, leased and released in memory."""

    def __init__(
        self, cells: Iterable[Cell], identity: CellIdentity, trail: PheromoneTrail, clock: Clock
    ) -> None:
        """Build a source over a fixed Cell inventory.

        Args:
            cells: Every Cell this source hands out; looked up by id in `lease`.
            identity: The Hive, node and actor this source stamps on every trail event.
            trail: Where cell.leased/cell.released events land.
            clock: Source of every minted id and timestamp.
        """
        self._cells: dict[CellId, Cell] = {cell.id: cell for cell in cells}
        self._identity = identity
        self._trail = trail
        self._clock = clock
        self._active_leases: dict[CellId, RealCellLease] = {}

    @property
    def name(self) -> str:
        """This source's name, `"fake"`, the value every one of its Cells carries as `source`."""
        return _FAKE_SOURCE_NAME

    async def cells(self) -> tuple[Cell, ...]:
        """Return every Cell this source was built with, in construction order."""
        return tuple(self._cells.values())

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease on `request.cell_id`, refusing an unknown or already-open one.

        Args:
            request: Which Cell, held by which Warden, at which access level.

        Returns:
            An OPEN RealCellLease with a synthetic scratch root.

        Raises:
            LeaseRefusedError: `request.cell_id` names no Cell this source holds, or a lease on
                it is already OPEN, RELEASING or ORPHANED.
        """
        cell = self._cells.get(request.cell_id)
        if cell is None:
            raise LeaseRefusedError(request.cell_id, "no such cell on this fake source")
        existing = self._active_leases.get(request.cell_id)
        if existing is not None and existing.state is not LeaseState.RELEASED:
            raise LeaseRefusedError(request.cell_id, "already leased")
        lease_id = new_lease_id(self._clock)
        facts = LeaseFacts(
            id=lease_id,
            cell_id=request.cell_id,
            holder=request.holder,
            task_id=request.task_id,
            scratch_root=Path(f"/fake-scratch/{lease_id}"),
            access_level=request.access_level,
            comb_shield=cell.comb_shield,
            allowed_paths=request.allowed_paths,
        )
        lease = RealCellLease(
            facts,
            trail=self._trail,
            clock=self._clock,
            identity=self._identity,
            releaser=FakeLeaseReleaser(),
        )
        await lease.open()
        self._active_leases[request.cell_id] = lease
        return lease

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Return a fresh FakeSession bound to `lease`'s scratch root and allowed paths.

        Args:
            lease: An OPEN lease this source itself issued.

        Returns:
            A new FakeSession over an empty in-memory files dict.
        """
        return FakeSession(lease.scratch_root, self._clock, allowed_paths=lease.allowed_paths)
