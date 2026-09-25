"""Define HouseBeeRipening: the House Bee's own loop that ripens Nectar into Honey beside the Queen.

Ripening turns Nectar (raw deposits waiting in the Honey Store, the Hive's knowledge base) into
Honey (chunked, summarised, embedded rows a query can find), and it calls models: a summary on the
`RIPENER` slot can take tens of seconds on a local model. The Queen's tick must never wait on
that (ADR-0035), so ripening runs here, in the House Bee's (the maintenance role's) own loop beside
her, in the same process: every `[honey.ripening] interval_s` it first drains the notes the
operator proposed from the browser (queued rows in the store, taken in as HUMAN-origin Nectar
attributed to the Hive Stand, the machine the Queen runs on), then runs one `Ripener.run_pass()`,
which ripens pending Nectar and embeds rows still lacking a vector for the current embedder. The
loop is `waggle.loop.TickLoop`'s shape: a failed pass backs off and is retried, and `stop()` wakes
it at once, cancelling a pass still in flight (every store write is its own transaction, so a
cancelled pass leaves each Nectar either ripened or still pending, never half done).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.house_bee`. Built by the
    composition root (`hivemind.cli.compose.build_hive`) when the Hive has a Honey Store, and run
    by `hivemind.cli.compose.run_hive` in the same TaskGroup as the Queen (started after her,
    stopped before her). Calls into `hivemind.cell`, `hivemind.common.tasks`,
    `hivemind.honey_store` (intake, the store's proposal queue, the Ripener), `hivemind.llm` (its
    error root only) and waggle.

Key invariants:
    - Never imports or awaits anything of the Queen's: it shares only the Honey Store with her.
    - A proposal is marked drained only once its Nectar row exists, so a crash between the two
      re-submits it and intake's content dedupe merges it onto the same row.
    - `HoneyStoreError`, `LLMError` and `sqlite3.Error` are recoverable (backed off, retried), so
      a busy database or a down ripener never ends the loop, and with it the Hive's TaskGroup.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for "ripening runs in the House Bee's
      own loop beside the Queen, never inside her tick".
    - hivemind.honey_store.ripening for Ripener, what one pass runs.
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
    NectarOrigin,
    NectarRejectedError,
    NectarSubmission,
    PassOutcome,
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
    """What one House Bee pass did: the proposals it drained, then the Ripener's own counts.

    Attributes:
        drained: Operator-proposed notes turned into Nectar this pass.
        outcome: `Ripener.run_pass()`'s own counts (ripened, failed, rows, re-embedded, ...).
    """

    drained: int
    outcome: PassOutcome


class HouseBeeRipening(TickLoop):
    """Drain proposed notes and run one ripening pass every `[honey.ripening] interval_s`.

    Holds no state of its own beyond `TickLoop`'s stop flag: everything a pass reads and writes
    is in the Honey Store, which is why `run_pass` is also safe to call directly (a test, or
    `hive honey ripen --now` against the same file) while the loop runs.
    """

    # A busy database, a store refusal or an unreachable ripener is worth a backoff and a retry,
    # never the end of the loop (module docstring's "Key invariants").
    _recoverable_errors: ClassVar[tuple[type[Exception], ...]] = (
        HoneyStoreError,
        LLMError,
        sqlite3.Error,
    )

    def __init__(self, access: HoneyAccess, stand_cell_id: CellId, clock: Clock) -> None:
        """Wire the loop to one Hive's Honey Store.

        Args:
            access: The Hive's Honey Store handles; `ripening.interval_s` sets the pace.
            stand_cell_id: The Hive Stand's own Cell, which every drained proposal is attributed
                to: the operator writes from the machine the Queen runs on.
            clock: Injected time source for the pause between passes and the backoff.
        """
        super().__init__(clock)
        self._access = access
        self._stand_cell_id = stand_cell_id

    async def run_pass(self) -> RipeningPass:
        """Drain queued proposals into Nectar, then ripen and embed whatever is pending.

        Returns:
            The proposals drained and the Ripener's own counts for this pass.

        Raises:
            HoneyStoreError: The store failed outside one proposal's own refusal.
            LLMError: A model failure the Ripener does not already absorb itself.
        """
        drained = await self._drain_proposals()
        # External awaits inside: RIPENER summaries and EMBEDDER batches, each bounded by the
        # Ripener's own timeouts and falling back rather than failing the pass.
        outcome = await self._access.ripener.run_pass()
        return RipeningPass(drained=drained, outcome=outcome)

    async def _tick(self) -> None:
        """Run one pass, then pause for `interval_s`; `stop()` cuts either one short."""
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
