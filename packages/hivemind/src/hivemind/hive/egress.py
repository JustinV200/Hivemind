"""Cut and restore a running Virtual Cell's egress through its own backend, or say why not.

Isolating a Cell (roadmap step 10.6a, ADR-0035) sets a Virtual Cell's (a container or VM the Hive
provisioned) egress to none except its Waggle control link, which checkpointing, pausing and
forensics need; lifting the isolation gives the Cell its own network policy back. Only a backend
that declares `BackendCapabilities.can_cut_egress` and implements
`hivemind.hive.backends.base.EgressCutter` can do that to a running Cell, and `LifecycleEgress` is
the one adapter that asks: it finds the Cell's backend through the `CellLifecycle` that tracks it,
branches on the declared capability (never on the backend's name), and answers with an
`EgressOutcome` the isolation records on `cell.isolated`. A Cell the lifecycle does not track (a
Real Cell, borrowed and never re-networked) is `UNTRACKED`; a backend that cannot is
`UNSUPPORTED`; a backend call that fails or overruns is `FAILED`. None of these stops the rest of
an isolation: revoking the grant, pausing the bees and the `BLOCK` Cell Wax still apply.

Fits into the Hive:
    Layer 3 (sources of Cells), inside the hive package. Built by the composition root over the
    Hive's one `CellLifecycle` and handed to the Queen as her `CellEgress` seam; called by
    `hivemind.queen.isolation`. Calls into `hivemind.common.logging`, `hivemind.hive.backends.base`
    (EgressCutter), `hivemind.hive.errors`, `hivemind.hive.lifecycle` and waggle only.

Key invariants:
    - A backend is asked to cut or restore only when it declares `can_cut_egress` and implements
      `EgressCutter`; otherwise nothing is called and the outcome says why.
    - Every backend call is bounded by `EGRESS_TIMEOUT_S`; neither method ever raises.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md, "Only the
      Queen isolates a Cell".
    - hivemind.hive.backends.docker.network for why Docker declares no such capability.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.hive.backends.base import EgressCutter
from hivemind.hive.errors import HiveError
from hivemind.hive.lifecycle import CellLifecycle
from waggle.ids import CellId

# One backend call re-plans a Cell's network; seconds at most on a real backend, and past this the
# Queen records the cut as failed rather than hold her tick on an unresponsive backend.
EGRESS_TIMEOUT_S = 30.0

log = get_logger(__name__)

__all__ = ["EGRESS_TIMEOUT_S", "CellEgress", "EgressOutcome", "LifecycleEgress"]


class EgressOutcome(Enum):
    """What one cut or restore did to a Cell's egress."""

    CUT = "cut"  # Only the Cell's control link is reachable now.
    RESTORED = "restored"  # The Cell's own network policy applies again.
    UNSUPPORTED = "unsupported"  # Its backend declares no can_cut_egress: egress is unchanged.
    UNTRACKED = "untracked"  # No Virtual Cell this lifecycle tracks (a Real Cell): nothing to cut.
    FAILED = "failed"  # The backend refused or overran: egress is as it was, as far as is known.


class CellEgress(Protocol):
    """The Queen's seam for a Cell's egress: cut it to the control link alone, or restore it."""

    async def cut(self, cell_id: CellId) -> EgressOutcome:
        """Cut `cell_id`'s egress to its Waggle control link alone, when its backend can.

        Args:
            cell_id: The Cell being isolated.

        Returns:
            CUT, or why nothing was cut (UNSUPPORTED, UNTRACKED, FAILED).
        """
        ...

    async def restore(self, cell_id: CellId) -> EgressOutcome:
        """Give `cell_id` its own network policy's egress back, when its backend can.

        Args:
            cell_id: The Cell whose isolation the human lifted.

        Returns:
            RESTORED, or why nothing was restored (UNSUPPORTED, UNTRACKED, FAILED).
        """
        ...


class LifecycleEgress:
    """The one `CellEgress`: asks a tracked Cell's own backend, by its declared capability."""

    def __init__(self, lifecycle: CellLifecycle) -> None:
        """Wrap the Hive's one `CellLifecycle`, the only place a Cell's backend is known.

        Args:
            lifecycle: Tracks every Virtual Cell and the backend that provisioned it.
        """
        self._lifecycle = lifecycle

    async def cut(self, cell_id: CellId) -> EgressOutcome:
        """See `CellEgress.cut`."""
        return await self._apply(cell_id, cut=True)

    async def restore(self, cell_id: CellId) -> EgressOutcome:
        """See `CellEgress.restore`."""
        return await self._apply(cell_id, cut=False)

    async def _apply(self, cell_id: CellId, *, cut: bool) -> EgressOutcome:
        """Cut or restore through the Cell's backend, or return why that did not happen."""
        target = self._lifecycle.snapshot_target(cell_id)
        if target is None:
            return EgressOutcome.UNTRACKED  # A Real Cell, or one no longer tracked: nothing to do.
        backend = target[0]
        # Capabilities, never the backend's name (codingrules 8.6): a backend that declares the
        # capability but lacks the methods is treated exactly like one that declares nothing.
        if not backend.capabilities.can_cut_egress or not isinstance(backend, EgressCutter):
            return EgressOutcome.UNSUPPORTED
        call = backend.cut_egress if cut else backend.restore_egress
        try:
            # External await: one backend call; bounded, and a refusal is an outcome, not a crash.
            async with asyncio.timeout(EGRESS_TIMEOUT_S):
                await call(cell_id)
        except (TimeoutError, HiveError) as failure:
            log.warning(
                "hive.egress_failed", cell_id=cell_id, cut=cut, error=type(failure).__name__
            )
            return EgressOutcome.FAILED
        return EgressOutcome.CUT if cut else EgressOutcome.RESTORED
