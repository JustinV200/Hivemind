"""Run a Patrol: review what a watching Warden kept, decide one thing, and say how it ended.

A Patrol is the hourly review of a watching Warden (ADR-0054). `Patrol.run` reads the observations
kept since the last reviewing Patrol, lets the fixed pre-filter pick out the facts that should not
be missed (`hivemind.wardens.watch.signals`), and hands the window to a `PatrolPort`, which asks
one stateless awake episode for exactly one decision and carries it out: raise an Alarm, deposit a
Nectar summary, propose Cell Wax, or find nothing notable. What the Patrol itself decides is only
what follows from that. A decision that was carried out advances the window, so the same hour is
never reviewed twice; one that found nothing also discards the observations it covered, since
nothing was worth keeping; and a Patrol that could not reach a model, or could not deliver what it
decided, ends `SKIPPED` with its observations kept and its window not advanced, so the next Patrol
covers them too (retention is what finally lets them go). Every Patrol that ran records one
`warden.patrol` event through the port, with its outcome and counts and never any text. A due
Patrol with nothing in its window runs no episode and records nothing: there was nothing to review.

The port is a Protocol so this class needs no Warden and no model machinery; the Warden-facing
implementation (`hivemind.wardens.watch.bridge.WardenPatrolPort`) holds all of that.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.watch.tick`. Calls into the sibling store,
    signals, digest and decision modules, `hivemind.llm.errors` and waggle only.

Key invariants:
    - The window only advances for a decision that was carried out; SKIPPED keeps everything.
    - Only a `NOTHING` Patrol deletes observations, and only the ones it covered.
    - The model call has a timeout, and only the model's declared failures are caught.
    - No observation text reaches a log line, an event or this module's return value.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the four outcomes.
    - hivemind.wardens.awake.patrol for the episode the port runs.
    - hivemind.wardens.watch.bridge for the port a Warden is given.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.llm.errors import LLMError
from hivemind.memory import MemoryContext
from hivemind.wardens.watch import store
from hivemind.wardens.watch.decision import PatrolChoice, PatrolDecision, PatrolOutcome
from hivemind.wardens.watch.deps import WatchDeps
from hivemind.wardens.watch.digest import PatrolWindow
from hivemind.wardens.watch.signals import find_signals

PATROL_TIMEOUT_S = (
    300.0  # Seconds a Patrol's episode may take: one structured answer, and its retries.
)
_AFTER = timedelta(
    microseconds=1
)  # The smallest step: a window starts just after the last one's end.
# Failures that mean "no model decided": it was unreachable, refused, out of budget or too slow.
_NO_DECISION = (LLMError, TimeoutError)

log = get_logger(__name__)

__all__ = ["PATROL_TIMEOUT_S", "Patrol", "PatrolPort"]


class PatrolPort(Protocol):
    """What a Patrol needs from the Warden around it: a decision, its delivery, and a record."""

    async def decide(self, window: PatrolWindow) -> PatrolDecision:
        """Ask one stateless awake episode what to do about `window`.

        Raises:
            LLMError: No model could be reached or would answer.
        """
        ...

    async def carry_out(self, decision: PatrolDecision, window: PatrolWindow) -> bool:
        """Do what `decision` says (an Alarm, Nectar, Wax; nothing for NOTHING).

        Returns:
            True when it was delivered; False when it could not be, and the Patrol is skipped.
        """
        ...

    async def record(self, outcome: PatrolOutcome, window: PatrolWindow) -> None:
        """Record that a Patrol ran, with its outcome and counts, as a `warden.patrol` event."""
        ...


class Patrol:
    """Reviews a watching Warden's observations; owns no state, the schedule is `WatchDeps`'."""

    def __init__(self, watch: WatchDeps, ctx: MemoryContext, port: PatrolPort, owner: str) -> None:
        """Bind a Patrol to one Warden's watch settings, memory and port.

        Args:
            watch: The settings (the two thresholds, retention) and the schedule this advances.
            ctx: The Warden's store, identity and clock the observations are read with.
            port: The Warden-facing half: the episode, its delivery and the record.
            owner: The id of the Warden whose observations this Patrol reviews and discards.
        """
        self._watch = watch
        self._ctx = ctx
        self._port = port
        self._owner = owner

    async def run(self) -> PatrolOutcome | None:
        """Review everything kept since the last reviewing Patrol.

        Returns:
            How the Patrol ended, or None when there was nothing new to review (no episode ran
            and no event was recorded).
        """
        schedule, settings = self._watch.schedule, self._watch.settings
        until = self._ctx.clock.now()
        since = schedule.patrolled_until or until - timedelta(seconds=settings.retention_s)
        loaded = await store.load_observations(self._ctx, self._owner, since, until)
        # The window is inclusive at both ends, so what was kept at the very instant the last
        # Patrol stopped is read again; the ids it already reviewed are what tell the two apart.
        kept = [item for item in loaded if item.entry_id not in schedule.reviewed_ids]
        if not kept:
            return None
        observations = tuple(item.observation for item in kept)
        signals = find_signals(observations, settings.cpu_share_alarm, settings.disk_fill_alarm)
        window = PatrolWindow(kept[0].kept_at, kept[-1].kept_at, observations, signals)
        outcome = await self._review(window)
        if outcome is not PatrolOutcome.SKIPPED:
            schedule.patrolled_until = window.until
            schedule.reviewed_ids = frozenset(
                item.entry_id for item in loaded if item.kept_at == window.until
            )
        await self._port.record(outcome, window)
        return outcome

    async def _review(self, window: PatrolWindow) -> PatrolOutcome:
        """Ask for a decision and carry it out; a Patrol that finds nothing discards its window."""
        try:
            async with asyncio.timeout(PATROL_TIMEOUT_S):
                decision = await self._port.decide(window)
        except _NO_DECISION as error:
            log.warning("warden.patrol_unanswered", error=type(error).__name__)
            return PatrolOutcome.SKIPPED
        if not await self._port.carry_out(decision, window):
            return PatrolOutcome.SKIPPED
        if decision.choice is PatrolChoice.NOTHING:
            # Everything up to the newest observation this Patrol covered, and no later one.
            await store.discard_before(self._ctx, self._owner, window.until + _AFTER)
        return PatrolOutcome.of(decision.choice)
