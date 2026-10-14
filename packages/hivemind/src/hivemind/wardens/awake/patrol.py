"""Define decide_patrol: a Patrol's whole awake episode, from the observations to one decision.

A Patrol (a watching Warden's hourly review of what it saw on an idle Cell, ADR-0054) is the one
place a Warden thinks about a Cell it is not working on. Codingrules 8.8 makes every awake episode
stateless: `decide_patrol` assembles its prompt through `hivemind.memory.assemble` from durable
state plus the triggering event, asks the model for one `PatrolDecision`, writes the decision back
as an `EpisodeRecord` and discards the transcript. What differs from `decide_awake` is what the
episode is about. The event is the Patrol itself: a trusted framing with the pre-filter's counts,
and the signals and observations as one fenced block of untrusted facts, scanned like any outside
text (`hivemind.memory.TriggerEvent.untrusted`), so a process name or a log line can never speak to
the model as the Hive. The episode is `C2`, the label the observations carry: its principal may see
`C2` hot state (its own earlier decisions, which quote the machine), its record is `C2`, and the
record keeps the framing but not the fenced facts, which stay only in the observations themselves,
for as long as their retention lasts. It runs on `ModelSlot.WARDEN` through the Warden's own
Fanner lane, and its prompt is `PromptName.PATROL`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's awake
    sub-package (which is allowed to import `hivemind.llm`; only `autopilot/` may not). Called by
    `hivemind.wardens.watch.bridge.WardenPatrolPort`. Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.forage.slots` (ModelSlot), `hivemind.llm`, `hivemind.memory`,
    `hivemind.wardens.awake.episode` (its two budget constants) and
    `hivemind.wardens.watch.decision` only.

Key invariants:
    - `decide_patrol` reads its event and the Warden's own memory store only for what goes into the
      prompt; it holds no state across calls (codingrules section 8.8).
    - Hot state holds only this Warden's own earlier Patrol decisions: no tasks, Alarms,
      questions, notes or pins, so nothing about other work reaches a prompt that carries the
      operator's machine.
    - The episode record is labelled `C2` and holds no observation text beyond the model's own
      reason; the trail records that an episode happened and what it cost, never its words
      (codingrules section 12).

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the Patrol.
    - hivemind.wardens.awake.episode for decide_awake, whose shape this follows.
    - hivemind.wardens.watch.digest for the event this is handed.
    - hivemind.llm.prompts for PromptName.PATROL, the prompt it renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, Message, PromptName, Role, complete_structured, render
from hivemind.memory import (
    AlarmSummary,
    AssembleRequest,
    CellWaxSummary,
    DecisionSummary,
    EpisodeRecord,
    EstimateCounter,
    Handoff,
    MemoryContext,
    MemoryStore,
    Note,
    Pin,
    Principal,
    Prompt,
    QuestionSummary,
    Scorable,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
    assemble,
    deposit_dropped_items,
    record_episode,
    run_with_overflow_retry,
)
from hivemind.wardens.awake.episode import AWAKE_OUTPUT_RESERVE_TOKENS, WARDEN_BUDGET_FRACTION
from hivemind.wardens.watch.decision import PatrolDecision
from waggle.ids import CellId, WardenId, new_event_id

if TYPE_CHECKING:
    # Type-checking only: hivemind.wardens.deps imports hivemind.wardens.watch, whose package face
    # this module's own import of watch.decision runs, so a runtime import back would cycle.
    from hivemind.wardens.deps import WardenDeps

PATROL_TRIGGER_KIND = "watch.patrol"  # The TriggerEvent.kind of every Patrol; its thought keeps it.
PATROL_MAX_OUTPUT_TOKENS = 1_024  # A PatrolDecision is short; generous but bounded.
_PATROL_PRINCIPAL = "warden-patrol"  # The Principal.id this episode is assembled for.
_PATROL_ROLE = "warden"  # The Principal.role it is assembled for.
_DECISION_CHARS = 500  # A DecisionSummary's own cap on its two text fields.

__all__ = ["PATROL_MAX_OUTPUT_TOKENS", "PATROL_TRIGGER_KIND", "PatrolSources", "decide_patrol"]


class PatrolSources:
    """A HotStateSources holding only this Warden's own earlier Patrol decisions.

    A Patrol is told what it decided in earlier hours (and why) so it does not raise the same
    thing again; nothing else about the Warden's work belongs in a prompt that carries the
    operator's machine, so every other source is empty.
    """

    def __init__(self, memory: MemoryStore, warden_id: WardenId) -> None:
        """Bind the sources to one Warden's memory.

        Args:
            memory: The store this Warden records its episodes in.
            warden_id: The Warden whose Patrol decisions are read back (its episodes' principal).
        """
        self._memory = memory
        self._principal = str(warden_id)

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """Return up to `limit` of this Warden's own recent Patrol decisions, newest first."""
        episodes = await self._memory.list_episodes(self._principal, HoneyClearance.C2, limit)
        return tuple(
            DecisionSummary(
                episode_id=episode.id,
                at=episode.at,
                decision=episode.decision[:_DECISION_CHARS],
                action=episode.action[:_DECISION_CHARS],
                clearance=episode.clearance,
                tainted=episode.tainted,  # assemble refuses a TAINTED decision outright.
            )
            for episode in episodes
            if episode.trigger.kind == PATROL_TRIGGER_KIND
        )

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """Return none: a Patrol runs while no bee works."""
        return ()

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """Return none: this prompt carries only the Cell's own observations."""
        return ()

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """Return none: a Patrol asks nothing of anyone."""
        return ()

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        """Return none: a Patrol proposes Cell Wax, it does not read it."""
        return ()

    async def pins(self) -> tuple[Pin, ...]:
        """Return none: standing facts about other work stay out of this prompt."""
        return ()

    async def notes(self) -> tuple[Note, ...]:
        """Return none, for the same reason as `pins`."""
        return ()

    async def handoff(self) -> Handoff | None:
        """Return None: a Patrol never resumes from a Handoff."""
        return None


async def decide_patrol(
    deps: WardenDeps, event: TriggerEvent, warden_id: WardenId
) -> PatrolDecision:
    """Run one stateless Patrol episode and return its one PatrolDecision.

    Args:
        deps: This Warden's collaborators; `bound` and `call_gate` are what the model call runs
            through, `memory`, `identity` and `clock` are what the episode is recorded with.
        event: The Patrol (`hivemind.wardens.watch.digest.patrol_event`): its framing, and its
            facts as outside text with their scan verdict.
        warden_id: This Warden's own id: the principal its episodes are recorded under.

    Returns:
        The one PatrolDecision the model produced.

    Raises:
        hivemind.llm.errors.LLMError: Every rung of the structured-output ladder, on every binding
            in `deps.bound`'s fallback chain, was exhausted, or no provider was reachable.
        hivemind.memory.overflow.ContextOverflowError: The assembled prompt overflowed the bound
            model's context `MAX_OVERFLOWS` times in a row, even after shrinking the budget.
    """
    budget = TokenBudget(
        max_input_tokens=max(1, int(deps.bound.context_window * WARDEN_BUDGET_FRACTION)),
        output_reserve=AWAKE_OUTPUT_RESERVE_TOKENS,
    )
    sources = PatrolSources(deps.memory, warden_id)

    async def _episode(budget: TokenBudget) -> PatrolDecision:
        """One assemble-and-call attempt at `budget`; the unit run_with_overflow_retry retries."""
        prompt = await _assemble_prompt(deps, event, sources, budget)
        request = LLMRequest(
            slot=ModelSlot.WARDEN,
            system=render(PromptName.PATROL, sections=prompt.sections),
            messages=(Message.text(Role.USER, prompt.event_text),),
            max_output_tokens=PATROL_MAX_OUTPUT_TOKENS,
        )
        # External await: one model call, latency class seconds (a local model, tens of seconds);
        # the ladder retries and steps down rungs itself, and falls back along deps.bound's chain
        # on an outage. A ContextTooLongError propagates to run_with_overflow_retry; any other
        # LLMError reaches the Patrol, which records the whole review as skipped and keeps its
        # observations.
        result = await complete_structured(deps.bound, request, PatrolDecision, gate=deps.call_gate)
        await _record(deps, event, warden_id, result.value)
        return result.value

    return await run_with_overflow_retry(_episode, budget, deps.trail, deps.identity, deps.clock)


async def _assemble_prompt(
    deps: WardenDeps, event: TriggerEvent, sources: PatrolSources, budget: TokenBudget
) -> Prompt:
    """Pack the Patrol's own hot state into a Prompt at `budget`, archiving every dropped item."""
    # C2: the label the observations carry, so this one episode may read its own C2 decisions.
    principal = Principal(
        id=_PATROL_PRINCIPAL, slot=ModelSlot.WARDEN, clearance=HoneyClearance.C2, role=_PATROL_ROLE
    )
    request = AssembleRequest(principal=principal, event=event, budget=budget)
    dropped: list[Scorable] = []
    prompt = await assemble(request, sources, EstimateCounter(), on_drop=dropped.append)
    if dropped:
        # Roadmap step 4.4: "every dropped item is findable in Bee Bread by id."
        ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
        await deposit_dropped_items(dropped, ctx)
    return prompt


async def _record(
    deps: WardenDeps, event: TriggerEvent, warden_id: WardenId, decision: PatrolDecision
) -> None:
    """Write `decision` back as a C2 EpisodeRecord; the transcript itself is discarded here."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    record = EpisodeRecord(
        id=new_event_id(deps.clock),
        principal=str(warden_id),
        slot=ModelSlot.WARDEN,
        # The framing stays; the fenced facts do not, so the machine's words live only in the
        # observations themselves, for as long as their retention lasts.
        trigger=event.model_copy(update={"untrusted": None}),
        prompt_ref=None,
        reasoning_summary=None,
        decision=decision.choice.value,
        action=decision.reason,
        at=deps.clock.now(),
        clearance=HoneyClearance.C2,
        usage=None,
        is_autopilot=False,
    )
    await record_episode(record, ctx)
