"""Define HouseBeeRipening: the House Bee's own loop that ripens Nectar into Honey beside the Queen.

Ripening turns Nectar (raw deposits waiting in the Honey Store, the Hive's knowledge base) into
Honey (chunked, summarised, embedded rows a query can find), and it calls models: a summary on the
`RIPENER` slot can take tens of seconds on a local model. The Queen's tick must never wait on
that (ADR-0031), so ripening runs here, in the House Bee's (the maintenance role's) own loop beside
her, in the same process. Every `[honey.ripening] interval_s` one pass first drains the notes the
operator proposed from the browser (queued rows in the store, taken in as HUMAN-origin Nectar
attributed to the Hive Stand, the machine the Queen runs on), then runs one `Ripener.run_pass()`,
which ripens pending Nectar and embeds rows still lacking a vector for the current embedder, and
then runs judge-reviewed label lowering (ADR-0034): it files one proposal for each newly ripened
Nectar whose C2 label only the Real Cell floor holds up and whose text the Ripener read as less
sensitive, then asks the independent clearance judge (the JUDGE model slot) about waiting
proposals, which the store applies or rejects. Filing runs even with no judge bound; the review
then does nothing and every proposal waits for the human (`hive honey review`). The loop is
`waggle.loop.TickLoop`'s shape: a failed pass backs off and is retried, and `stop()` wakes it at
once, cancelling a pass still in flight (every store write is its own transaction, so a cancelled
pass leaves each Nectar either ripened or still pending, and each proposal filed or decided or not,
never half done).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.house_bee`. Built by the
    composition root (`hivemind.cli.compose.build_hive`) when the Hive has a Honey Store, and run
    by `hivemind.cli.compose.run_hive` in the same TaskGroup as the Queen (started after her,
    stopped before her); `hive honey ripen --now` runs one whole pass on demand. Calls into
    `hivemind.cell`, `hivemind.common.tasks`, `hivemind.honey_store` (intake, the store's proposal
    queue, the Ripener, the lowering service), `hivemind.llm` (its error root only) and waggle.

Key invariants:
    - Never imports or awaits anything of the Queen's: it shares only the Honey Store with her.
    - A proposal is marked drained only once its Nectar row exists, so a crash between the two
      re-submits it and intake's content dedupe merges it onto the same row.
    - Lowering runs only after ripening in the same pass, so a Nectar is proposed on the pass that
      recorded its Ripener reading; this loop never lowers a label itself: only the store's own
      apply transaction does, on the judge's approval (ADR-0034).
    - `HoneyStoreError`, `LLMError` and `sqlite3.Error` are recoverable (backed off, retried), so
      a busy database, a down ripener or a judge outage (`ProviderUnavailableError`,
      `RateLimitedError`, which the lowering service lets through) never ends the loop, and with
      it the Hive's TaskGroup; a judge that merely fails to answer is absorbed by the service.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for "ripening runs in the House Bee's
      own loop beside the Queen, never inside her tick".
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for filing and review.
    - hivemind.honey_store.ripening for Ripener, what one pass ripens with.
    - hivemind.honey_store.lowering for LabelLowering, what one pass files and reviews with.
    - waggle.loop for TickLoop, the loop shape this follows.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, ClassVar

from hivemind.cell import CombShieldLevel
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reaping
from hivemind.honey_store import (
    HoneyAccess,
    HoneyProposal,
    HoneyStoreError,
    InvalidScopeError,
    LabelLowering,
    LoweringDeps,
    NectarOrigin,
    NectarRejectedError,
    NectarSubmission,
    PassOutcome,
    ReviewOutcome,
)
from hivemind.llm import LLMError
from waggle.clock import Clock
from waggle.ids import CellId
from waggle.loop import TickLoop
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import MAX_TITLE_CHARS

MAX_PROPOSALS_PER_PASS = 32  # Operator notes are rare; a pass stays short even after a backlog.
PROPOSAL_MEDIA_TYPE = "text/plain"  # A proposed note is the operator's own words, as typed.
# A proposal whose content intake refuses, or whose folder names no valid scope, would be refused
# again on every pass: it is passed over (and stays queued for the operator to see) instead.
_PROPOSAL_REFUSALS = (NectarRejectedError, InvalidScopeError, ValueError)

__all__ = ["MAX_PROPOSALS_PER_PASS", "PROPOSAL_MEDIA_TYPE", "HouseBeeRipening", "RipeningPass"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RipeningPass:
    """What one House Bee pass did: notes drained, the Ripener's counts, then label lowering's.

    Attributes:
        drained: Operator-proposed notes turned into Nectar this pass.
        outcome: `Ripener.run_pass()`'s own counts (ripened, failed, rows, re-embedded, ...).
        filed: Label lowering proposals filed this pass (ADR-0034), one per newly eligible
            Nectar.
        review: What the clearance judge's review did with waiting proposals: lowered,
            rejected, unanswered, handed to the human; all zero with no judge bound.
    """

    drained: int
    outcome: PassOutcome
    filed: int
    review: ReviewOutcome


class HouseBeeRipening(TickLoop):
    """Drain notes, ripen, then file and review label lowerings every `[honey.ripening] interval_s`.

    Holds no state of its own beyond `TickLoop`'s stop flag and the stateless lowering service:
    everything a pass reads and writes is in the Honey Store, which is why `run_pass` is also safe
    to call directly (a test, or `hive honey ripen --now` against the same file) while the loop
    runs.
    """

    # A busy database, a store refusal, an unreachable ripener or a judge outage is worth a
    # backoff and a retry, never the end of the loop (module docstring's "Key invariants").
    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = (
        HoneyStoreError,
        LLMError,
        sqlite3.Error,
    )

    def __init__(self, access: HoneyAccess, stand_cell_id: CellId, clock: Clock) -> None:
        """Wire the loop to one Hive's Honey Store.

        Args:
            access: The Hive's Honey Store handles; `ripening.interval_s` sets the pace, and
                `lowering` and `judge` how label lowering is filed and reviewed.
            stand_cell_id: The Hive Stand's own Cell, which every drained proposal is attributed
                to: the operator writes from the machine the Queen runs on.
            clock: Injected time source for the pause between passes, the backoff and every
                lowering event and decision.
        """
        super().__init__(clock)
        self._access = access
        self._stand_cell_id = stand_cell_id
        # Built once: the service keeps nothing between calls, and every event it records is the
        # Hive's own ("system"), stamped with the same identity as the rest of this pass.
        self._lowering = LabelLowering(
            LoweringDeps(access.store, access.identity, clock, access.lowering, access.judge)
        )

    async def run_pass(self) -> RipeningPass:
        """Drain queued notes, ripen what is pending, then file and review label lowerings.

        Returns:
            The notes drained, the Ripener's own counts, the lowering proposals filed and what
            the clearance judge's review did with waiting ones.

        Raises:
            HoneyStoreError: The store failed outside one proposal's own refusal.
            LLMError: A model failure the Ripener does not already absorb itself, or a judge
                outage (`ProviderUnavailableError`, `RateLimitedError`); ripening and filing are
                already committed by then, and the next pass reviews again.
        """
        drained = await self._drain_proposals()
        # External awaits inside: RIPENER summaries and EMBEDDER batches, each bounded by the
        # Ripener's own timeouts and falling back rather than failing the pass.
        outcome = await self._access.ripener.run_pass()
        # After ripening, so a Nectar ripened just now is proposed on this same pass; local
        # SQLite only, bounded by the connection's busy timeout.
        filed = await self._lowering.file_proposals()
        # External awaits inside: one JUDGE call per waiting proposal, each under the judge's own
        # timeout; an unanswered one is counted, an outage propagates for the loop to back off.
        review = await self._lowering.review_pending()
        return RipeningPass(drained=drained, outcome=outcome, filed=filed, review=review)

    async def _tick(self) -> None:
        """Run one whole pass, then pause for `interval_s`; `stop()` cuts either one short."""
        await self._until_stopped(self.run_pass())
        await self._until_stopped(self._clock.sleep(self._access.ripening.interval_s))

    async def _on_tick_failed(self, error: Exception) -> None:
        """Log a recovered pass failure before `TickLoop`'s own backoff (ids and codes only)."""
        code = error.code if isinstance(error, HiveMindError) else type(error).__name__
        log.warning("house_bee.ripening_pass_failed", reason=code)

    async def _until_stopped(self, work: Coroutine[Any, Any, object]) -> None:
        """Await `work` unless `stop()` comes first, in which case `work` is cancelled.

        Both waiters are reaped on every exit, so neither is ever left pending; a failure `work`
        raised surfaces here, from its reap, as `TickLoop.run`'s recoverable error to back off.
        """
        work_task = asyncio.ensure_future(work)
        # Throwaway: only wakes this wait early when stop() is called mid-pass or mid-pause.
        stop_task = asyncio.ensure_future(self._stop.wait())
        async with reaping(stop_task), reaping(work_task):
            await asyncio.wait({work_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

    async def _drain_proposals(self) -> int:
        """Take each queued operator note in as HUMAN Nectar, mark it drained; return how many."""
        store = self._access.store
        # Local SQLite on the store's own thread, milliseconds; oldest first.
        proposals = await store.pending_proposals(MAX_PROPOSALS_PER_PASS)
        drained = 0
        # One proposal at a time: a refused one is passed over, the rest still drain.
        for proposal in proposals:
            try:
                result = await self._access.intake.submit(self._submission(proposal))
            except _PROPOSAL_REFUSALS as error:
                log.warning(
                    "house_bee.proposal_refused", proposal_id=proposal.id, reason=_code(error)
                )
                continue
            await store.mark_proposal_drained(proposal.id, result.nectar.id)
            drained += 1
        return drained

    def _submission(self, proposal: HoneyProposal) -> NectarSubmission:
        """Build one proposal's HUMAN submission: C2 by construction, in the folder proposed."""
        return NectarSubmission(
            kind=NectarKind.FINDING,
            origin=NectarOrigin.HUMAN,
            media_type=PROPOSAL_MEDIA_TYPE,
            title=proposal.title[:MAX_TITLE_CHARS],
            content=proposal.text.encode("utf-8"),
            task_id=None,
            cell_id=self._stand_cell_id,
            observed_at=proposal.created_at,
            declared=None,
            # The operator's own words from the Hive Stand: borrowed, so intake's floor is C2
            # whatever the default label, exactly as a HUMAN origin's floor already is.
            from_borrowed_cell=True,
            tier=CombShieldLevel.MEADOW,
            proposed_scope=proposal.scope,
        )


def _code(error: Exception) -> str:
    """Name a refusal by its stable code when it has one, else by its class, for a log line."""
    return error.code if isinstance(error, HiveMindError) else type(error).__name__
