"""Define LedgerRecorder: feed the Forage ledger from the Fanner's own occurrences.

Roadmap step 4.8: "Feed seats-in-flight and spend from the Fanner without a Layer 1 -> Layer 6
import: implement `LedgerRecorder` in the ledger package satisfying
`hivemind.llm.fanner.LlmEventRecorder`." The Fanner (the seat meter every model call passes
through, `hivemind.llm.fanner`) never imports `hivemind.queen` -- codingrules section 4 fixes
`llm` below `queen` in the layer table -- so the ledger cannot be *pushed to* from inside `llm`.
Instead this module sits in `queen` (which may import `llm`, the reverse never true) and
implements `llm`'s own `LlmEventRecorder` Protocol structurally: a `Fanner` built with a
`LedgerRecorder` as its `FannerDeps.recorder` calls straight into
`hivemind.queen.forage.ledger.book.ForageLedger` on every occurrence, with `llm` never knowing
`queen` exists. `record` reads a completed `llm.call`'s own `usage.cost_usd`, `grant_id` and
`goal_id` (when the calling lane carried them -- `hivemind.llm.fanner.lane.FannerLane`'s own
docstring explains the "per lane, not per call" grain this attribution has today) and forwards
them to `ForageLedger.record_spend`; `call_started`/`call_finished` forward straight to
`ForageLedger.seats` (`hivemind.queen.forage.ledger.seats.SeatBook`) so a shared source's live
in-flight count reflects exactly the calls actually running right now.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Built by a composition root (`hivemind.cli.compose.deps.build_fanner`, a later
    dispatch's own wiring -- see this module's own report) and installed as `FannerDeps.recorder`.
    Calls into `hivemind.queen.forage.ledger.book` (ForageLedger) and `hivemind.llm.fanner` (for
    the `LlmEventRecorder` Protocol it implements) only.

Key invariants:
    - `record` only ever reacts to `kind == "llm.call"`; every other kind (`llm.spill`,
      `llm.throttled`) is silently ignored, since neither carries a cost or a seat this ledger
      tracks (a spill has not held a seat on the source it spilled from; codingrules section 12's
      own payload rules keep `record`'s own signature the same regardless).
    - A payload with no `usage`, no `grant_id` or no `goal_id` records nothing for that call
      (partial attribution is not spend attributed to the wrong grant or goal -- see the module
      docstring's own note on the current per-lane attribution grain).

See Also:
    - .claude/roadmap.md step 4.8 for the exact wording this module implements.
    - .claude/codingrules.md section 4 for the `llm`-below-`queen` layer direction this module's
      own placement (inside `queen`, not `llm`) exists to respect.
    - hivemind.llm.fanner.recorder for LlmEventRecorder, the Protocol this class satisfies.
    - hivemind.llm.fanner.lane for FannerLane, the one caller, and its own docstring on why
      `grant_id`/`goal_id` are attributed per lane rather than per call.
    - hivemind.queen.forage.ledger.book for ForageLedger.record_spend and `.seats`, this class's
      two write targets.
"""

from __future__ import annotations

from hivemind.llm.models import JsonObject
from hivemind.queen.forage.ledger.book import ForageLedger
from waggle.ids import GrantId, TaskId

__all__ = ["LedgerRecorder"]


class LedgerRecorder:
    """Feed a ForageLedger from the Fanner's occurrences; implements `LlmEventRecorder`."""

    def __init__(self, ledger: ForageLedger) -> None:
        """Bind this recorder to the one ledger it feeds.

        Args:
            ledger: The Queen's live book of Forage.
        """
        self._ledger = ledger

    async def record(self, kind: str, subject_id: str, payload: JsonObject) -> None:
        """React to `llm.call`: record its cost against the calling lane's grant and goal, if any.

        See `hivemind.llm.fanner.LlmEventRecorder.record` for the full Protocol contract; every
        kind other than `"llm.call"` is a no-op here (module docstring's own "Key invariants").
        """
        if kind != "llm.call":
            return
        grant_id, goal_id, cost_usd = _attribution(payload)
        if grant_id is None or goal_id is None or cost_usd is None:
            # No lane-level attribution, or an unpriced call: nothing to attribute (module
            # docstring's own "Key invariants" -- never guess a grant or a goal).
            return
        await self._ledger.record_spend(grant_id, goal_id, cost_usd)

    async def call_started(self, source_id: str | None, provider: str) -> None:
        """Count one more call in flight on `source_id`, if the map resolved one.

        See `hivemind.llm.fanner.LlmEventRecorder.call_started` for the full Protocol contract.
        """
        if source_id is not None:
            await self._ledger.seats.mark_started(source_id)

    async def call_finished(self, source_id: str | None, provider: str) -> None:
        """Count one fewer call in flight on `source_id`, matching an earlier `call_started`.

        See `hivemind.llm.fanner.LlmEventRecorder.call_finished` for the full Protocol contract.
        """
        if source_id is not None:
            await self._ledger.seats.mark_finished(source_id)


def _attribution(
    payload: JsonObject,
) -> tuple[GrantId | None, TaskId | None, float | None]:
    """Pull `grant_id`, `goal_id` and `usage.cost_usd` out of an `llm.call` payload.

    Each is None when the payload does not carry it: an ordinary lane with no attribution
    (`hivemind.llm.fanner.lane.FannerLane._call_payload` omits the keys entirely rather than
    writing an explicit null -- see that function's own docstring), or a usage dict this call's
    own payload shape never omits in practice but this reader treats defensively all the same.
    """
    grant_id = payload.get("grant_id")
    goal_id = payload.get("goal_id")
    usage = payload.get("usage")
    cost = usage.get("cost_usd") if isinstance(usage, dict) else None
    return (
        GrantId(grant_id) if isinstance(grant_id, str) else None,
        TaskId(goal_id) if isinstance(goal_id, str) else None,
        float(cost) if isinstance(cost, (int, float)) else None,
    )
