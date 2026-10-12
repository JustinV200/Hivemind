"""Describe a promoted tool to the Honey Store, so a bee can find it by what it does (step 9.8).

Honey describes, the Comb permits, the Basket stores (ADR-0048): a promotion deposits one Nectar
naming the tool, its platforms, the capabilities it acts under and the proof that passed, and the
registry stays the only authority on whether it may run. `QueenHoneyDeposit` is the promotion
flow's `HoneyDeposit` for the Queen: it builds the submission with `promotion_submission`, as
recorded on the Hive Stand (the Queen's own Cell, borrowed, MEADOW), and hands it to the Honey
intake. With no Honey Store wired it does nothing, and says so once.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by `PromotionFlow` after a promotion is durable, and by the remote-promotion handler; calls into
    `hivemind.royal_jelly.honey_link` (the submission) and `QueenDeps.honey` (the intake).

Key invariants:
    - Runs after the registry promoted; a failure here is the flow's to log and never undoes it.
    - The deposit's site is the Queen's own record (the Hive Stand's Cell id, its tier), never a
      field of any message.

See Also:
    - hivemind.royal_jelly.honey_link for the Nectar text and the scope rule it applies.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel, hive_stand_cell_id
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps
from hivemind.royal_jelly.honey_link import PromotionSite, promotion_submission
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec

log = get_logger(__name__)

__all__ = ["QueenHoneyDeposit"]


class QueenHoneyDeposit:
    """Deposit the Nectar describing a promoted tool through the Queen's Honey intake."""

    def __init__(self, deps: QueenDeps) -> None:
        """Bind the deposit to the Queen's collaborators.

        Args:
            deps: The Queen's collaborators; `honey`, `identity` and `clock` are used.
        """
        self._deps = deps
        self._warned = False  # Logged once per runner, not once per promotion.

    async def deposit_promotion(
        self, spec: ToolSpec, report: QuarantineReport, package_sha256: str
    ) -> None:
        """Submit the promotion's Nectar as recorded on the Hive Stand (the flow's `HoneyDeposit`).

        Args:
            spec: The promoted spec.
            report: The passing report that proved it.
            package_sha256: The promoted package's hash; it must be the report's.
        """
        site = PromotionSite(
            cell_id=hive_stand_cell_id(self._deps.identity.node_id),
            tier=CombShieldLevel.MEADOW,
            from_borrowed_cell=True,  # The Hive Stand is the operator's own machine.
        )
        await self.deposit_at(spec, report, package_sha256, site)

    async def deposit_at(
        self, spec: ToolSpec, report: QuarantineReport, package_sha256: str, site: PromotionSite
    ) -> None:
        """Submit the Nectar for a promotion recorded at `site`; a no-op without a Honey Store.

        Args:
            spec: The promoted spec.
            report: The passing report that proved it.
            package_sha256: The promoted package's hash; it must be the report's.
            site: The Cell the promotion was recorded on, from the Queen's own link record: a
                cell-scope tool is findable only by that Cell's bees.
        """
        honey = self._deps.honey
        if honey is None:
            if not self._warned:
                self._warned = True
                log.warning("queen.tools.honey_unavailable", tool_id=spec.tool_id)
            return
        submission = promotion_submission(spec, report, package_sha256, site, self._deps.clock)
        # Local SQLite writes behind the intake's own lock; no model is called.
        await honey.intake.submit(submission)
