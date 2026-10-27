"""Define LifecycleCellCut: the Sting Cut's Virtual side, over the Hive's own Cell lifecycle.

`hivemind.queen.sting_cut.hands.CellCut` names what a Sting Cut (roadmap step 13.4a) needs from
the Virtual side; this is the one implementation. It finds a Cell through the `CellLifecycle`
that provisioned it, so a Real Cell (never tracked there) gets "nothing done" from every method.
Evidence is a snapshot through the Cell's own backend, by its declared `can_snapshot` (never its
name), kept for `EVIDENCE_RETENTION_S` rather than a rollback snapshot's hour, since forensics
take longer than a rollback. The scrub is the Overwintering scrub the composition root already
builds (`hivemind.queen.cell_gate.scrub.make_scrub`: `Intervene(RELEASE_LEASE)` and a bounded
wait for the Warden's confirmation), so a cut and a pause scrub a Cell the same way. Forgetting
the key is the readiness gate's own `forget`; the teardown is the lifecycle's, which purges a
Night Veil Cell's segment and every record its tier retained.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Built by `hivemind.cli.compose.guard.build_guard_deps` over the parts
    `hivemind.cli.compose.virtual_cells` builds. Calls into `hivemind.cell`, `hivemind.common`
    (logging), `hivemind.hive` (lifecycle, errors, snapshot, overwinter), `hivemind.queen.cell_gate`
    (QueenReadinessGate) and the sub-package's own order only.

Key invariants:
    - No method raises for an untracked Cell or a backend's refusal: each answers what it did.
    - Every backend call is bounded by `CUT_TIMEOUT_S`.

See Also:
    - hivemind.queen.sting_cut.path for the order these run in.
"""

from __future__ import annotations

import asyncio

from hivemind.cell import Cell, SnapshotUnsupportedError
from hivemind.common.logging import get_logger
from hivemind.hive import CellScrubError, HiveError
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.overwinter import Scrubber
from hivemind.hive.snapshot import snapshotter_for
from hivemind.queen.cell_gate import QueenReadinessGate
from hivemind.queen.sting_cut.order import ScrubResult
from waggle.clock import Clock
from waggle.ids import CellId

EVIDENCE_RETENTION_S = 30 * 86_400.0  # A month: room to look at a cut Cell, not a rollback's hour.
CUT_TIMEOUT_S = 120.0  # One snapshot (a `docker commit`) or one teardown; past it, nothing kept.

log = get_logger(__name__)

__all__ = ["CUT_TIMEOUT_S", "EVIDENCE_RETENTION_S", "LifecycleCellCut"]


class LifecycleCellCut:
    """The one `CellCut`: the Hive's lifecycle, readiness gate and Overwintering scrub."""

    def __init__(
        self, lifecycle: CellLifecycle, gate: QueenReadinessGate, scrub: Scrubber, clock: Clock
    ) -> None:
        """Wrap the Virtual side's own collaborators.

        Args:
            lifecycle: Tracks every Virtual Cell and the backend that provisioned it.
            gate: The readiness gate whose keys a Cell's handshake is verified against.
            scrub: The Overwintering scrub: release the lease, await the Warden's confirmation.
            clock: Stamps the evidence snapshot's ledger row.
        """
        self._lifecycle = lifecycle
        self._gate = gate
        self._scrub = scrub
        self._clock = clock

    def tracks(self, cell_id: CellId) -> bool:
        """See `CellCut.tracks`."""
        return self._lifecycle.status_of(cell_id) is not None

    async def keep_evidence(self, cell_id: CellId) -> str | None:
        """See `CellCut.keep_evidence`."""
        target = self._lifecycle.snapshot_target(cell_id)
        ledger = self._lifecycle.snapshot_ledger
        if target is None or ledger is None or not target[0].capabilities.can_snapshot:
            return None  # Untracked, or no backend here that can keep a snapshot.
        backend, cell = target
        snapshotter = snapshotter_for(
            backend, ledger, self._clock, retention_s=EVIDENCE_RETENTION_S
        )
        try:
            # External await: one backend snapshot (a `docker commit`, a qcow2 copy), bounded.
            async with asyncio.timeout(CUT_TIMEOUT_S):
                return str(await snapshotter.snapshot(cell))
        except (TimeoutError, SnapshotUnsupportedError, HiveError) as failure:
            log.warning("queen.cut_evidence_failed", cell_id=cell_id, error=type(failure).__name__)
            return None

    async def scrub(self, cell: Cell) -> ScrubResult:
        """See `CellCut.scrub`."""
        try:
            await self._scrub(cell)  # Bounded by the scrub's own SCRUB_TIMEOUT_S.
        except CellScrubError:
            return ScrubResult.UNCONFIRMED  # Already cell.scrub_failed on the trail.
        return ScrubResult.CONFIRMED

    async def forget_key(self, cell_id: CellId) -> bool:
        """See `CellCut.forget_key`."""
        if not self.tracks(cell_id):
            return False
        await self._gate.forget(cell_id)
        return True

    async def teardown(self, cell_id: CellId) -> bool:
        """See `CellCut.teardown`."""
        if not self.tracks(cell_id):
            return False
        try:
            # External await: the backend's destroy, then the Night Veil purge; bounded.
            async with asyncio.timeout(CUT_TIMEOUT_S):
                await self._lifecycle.teardown(cell_id)
        except (TimeoutError, HiveError) as failure:
            log.warning("queen.cut_teardown_failed", cell_id=cell_id, error=type(failure).__name__)
            return False
        return True
