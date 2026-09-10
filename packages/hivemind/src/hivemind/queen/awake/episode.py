"""Define decide_awake and QueenSources: the Queen's whole awake episode, state in, decision out.

Codingrules section 8.8: "An issue a bee cannot resolve becomes an Alarm... only [NEEDS_JUDGEMENT]
runs an awake episode. Awake episodes are stateless: [an episode] assembles its prompt through
memory.assemble from durable state plus the triggering event, decides one action, writes the
decision back, and discards the transcript." `QueenSources` adapts the Queen's own collaborators
(the Brood Chamber, her memory store, her `hivemind.queen.human_inbox.HumanInbox`) into
`hivemind.memory.HotStateSources`, without `hivemind.memory` ever importing
`hivemind.brood_chamber` or `hivemind.supervision` directly (same layer, no ADR lists that edge --
`hivemind.memory.hot_state` defines its own flat summary models instead, exactly as
`hivemind.wardens.ticks.heartbeat._WardenHotState` does for a Warden). `decide_awake` packs that
view into a token-budgeted `Prompt`
at `effort`, renders it under `queen_system.md`, walks the degradation ladder to get a
`hivemind.queen.awake.decision.QueenDecision` back -- a plain-text model at
`ProviderCapabilities.none()` still owes a decision, on the PROMPTED rung -- and records it as an
`EpisodeRecord` before the transcript itself is discarded.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's awake
    sub-package (which MAY import `hivemind.llm`; only `autopilot/` may not). Called by
    `hivemind.queen.queen.Queen`'s tick whenever `hivemind.queen.autopilot.table.decide` returns
    `NEEDS_JUDGEMENT`. Calls into `hivemind.brood_chamber` (BroodChamber, TERMINAL_STATUSES,
    TaskFilter), `hivemind.cell` (HoneyClearance), `hivemind.forage.slots` (Effort, ModelSlot),
    `hivemind.llm` (LLMRequest, Message, PromptName, Role, complete_structured, render),
    `hivemind.memory` (assemble, AssembleRequest, EstimateCounter, HotStateSources, MemoryContext,
    Principal, TokenBudget, TriggerEvent, record_episode, EpisodeRecord),
    `hivemind.memory.hot_state` (the summary models and their char caps) and
    `hivemind.queen.human_inbox` (HumanInbox) only.

Key invariants:
    - `decide_awake` reads `sources` and `event` only for what goes into the prompt; it holds no
      state of its own across calls, matching codingrules 8.8's "stateless" requirement.
    - `effort` overrides `bound.effort` for this one call only (`dataclasses.replace`); the
      Queen's own standing `ModelSlot.QUEEN` binding is never mutated.
    - `record_episode` is called with the model's own decision and reason, never a raw transcript
      (codingrules section 12: "Thoughts are memory, not audit").

See Also:
    - .claude/codingrules.md section 8.8 for the stateless-awake-episode shape this function is.
    - hivemind.queen.awake.decision for QueenDecision, the schema complete_structured targets.
    - hivemind.llm.prompts for PromptName.QUEEN_SYSTEM and render, the prompt this assembles into.
    - hivemind.memory for assemble and record_episode, the two memory-tier calls this makes.
    - hivemind.queen.deps for QueenDeps, whose `bound_for` and `call_gate` this function calls
      through.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from hivemind.brood_chamber import TERMINAL_STATUSES, BroodChamber
from hivemind.brood_chamber.store.protocol import TaskFilter
from hivemind.cell import HoneyClearance
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm import (
    BoundModel,
    LLMRequest,
    Message,
    PromptName,
    Role,
    complete_structured,
    render,
)
from hivemind.memory import (
    AssembleRequest,
    EpisodeRecord,
    EstimateCounter,
    HotStateSources,
    MemoryContext,
    MemoryStore,
    Note,
    Pin,
    Principal,
    Prompt,
    TokenBudget,
    TriggerEvent,
    assemble,
    record_episode,
)
from hivemind.memory.hot_state import (
    SUMMARY_TEXT_CAP_CHARS,
    SUMMARY_TITLE_CAP_CHARS,
    AlarmSummary,
    DecisionSummary,
    QuestionSummary,
    TaskSummary,
)
from hivemind.queen.awake.decision import QueenDecision
from hivemind.queen.deps import QueenDeps
from hivemind.queen.human_inbox import HumanInbox
from waggle.ids import new_event_id

AWAKE_OUTPUT_RESERVE_TOKENS = 512  # Room for the model's own reply within the packed budget.
AWAKE_MAX_OUTPUT_TOKENS = 1_024  # A QueenDecision is short; generous but bounded.
ACTIVE_TASKS_LIMIT = 100  # A generous slice; packing itself still drops whatever does not fit.
RECENT_DECISIONS_LIMIT = 20  # Matches hivemind.memory.hot_state.packing's own default.
NOTES_LIMIT = 50  # Generous: notes are already bounded per author on write.
_QUEEN_ROLE = "queen"

__all__ = [
    "ACTIVE_TASKS_LIMIT",
    "AWAKE_MAX_OUTPUT_TOKENS",
    "AWAKE_OUTPUT_RESERVE_TOKENS",
    "NOTES_LIMIT",
    "RECENT_DECISIONS_LIMIT",
    "QueenSources",
    "decide_awake",
]


@dataclass(frozen=True, slots=True)
class QueenSources:
    """Adapt the Queen's own chamber, memory store and human inbox into HotStateSources."""

    chamber: BroodChamber
    memory: MemoryStore
    human_inbox: HumanInbox

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """Return every non-terminal task, as flat summaries, newest slice first."""
        candidates = await self.chamber.list(TaskFilter(limit=ACTIVE_TASKS_LIMIT * 4))
        active = [task for task in candidates if task.status not in TERMINAL_STATUSES]
        return tuple(
            TaskSummary(
                id=task.id,
                title=task.spec.title[:SUMMARY_TITLE_CAP_CHARS],
                status=task.status.value,
                objective=task.spec.objective[:SUMMARY_TEXT_CAP_CHARS],
                updated_at=task.updated_at,
                clearance=task.spec.clearance,
            )
            for task in active[:ACTIVE_TASKS_LIMIT]
        )

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """Return every Alarm currently escalated to the human, as flat summaries."""
        return tuple(
            AlarmSummary(
                id=alarm.id,
                kind=alarm.kind.value,
                severity=alarm.severity.value,
                detail=alarm.detail[:SUMMARY_TEXT_CAP_CHARS],
                attempts=alarm.attempts,
                task_id=alarm.context.task_id,
                clearance=alarm.clearance,
                raised_at=alarm.raised_at,
            )
            for alarm in self.human_inbox.alarms
        )

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """Return every question still awaiting an answer, as flat summaries."""
        questions = await self.human_inbox.pending_questions(self.chamber)
        return tuple(
            QuestionSummary(
                id=question.id,
                task_id=question.task_id,
                text=question.text[:SUMMARY_TEXT_CAP_CHARS],
                options=question.options,
                clearance=question.clearance,
                asked_at=question.asked_at,
            )
            for question in questions
        )

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """Return up to `limit` recent episodes, from the Queen's own memory store."""
        episodes = await self.memory.list_episodes(None, HoneyClearance.C2, limit)
        return tuple(
            DecisionSummary(
                episode_id=episode.id,
                at=episode.at,
                decision=episode.decision[:SUMMARY_TEXT_CAP_CHARS],
                action=episode.action[:SUMMARY_TEXT_CAP_CHARS],
                clearance=episode.clearance,
            )
            for episode in episodes
        )

    async def pins(self) -> tuple[Pin, ...]:
        """Return every pin visible at the Queen's own clearance."""
        return await self.memory.list_pins(HoneyClearance.C2)

    async def notes(self) -> tuple[Note, ...]:
        """Return the Queen's own recent notes."""
        return await self.memory.list_notes(None, HoneyClearance.C2, NOTES_LIMIT)


async def decide_awake(
    deps: QueenDeps, event: TriggerEvent, sources: HotStateSources, effort: Effort
) -> QueenDecision:
    """Run one stateless awake episode at `effort` and return its one QueenDecision.

    Args:
        deps: The Queen's collaborators; `bound_for`/`call_gate` are what the model call runs
            through, `memory`/`identity`/`clock` are what the episode is recorded with.
        event: What triggered this episode (a NEEDS_JUDGEMENT inbox item, summarised).
        sources: A view of the Queen's own hot state, typically a `QueenSources`.
        effort: The Effort `hivemind.queen.autopilot.effort.effort_for` chose for this event
            class; overrides the Queen's own standing binding's effort for this call only.

    Returns:
        The one QueenDecision the model produced.

    Raises:
        hivemind.llm.errors.MalformedOutputError: Every rung of the structured-output ladder, on
            every binding in the Queen's own fallback chain, was exhausted.
    """
    bound = deps.bound_for(ModelSlot.QUEEN)
    effort_bound = dataclasses.replace(bound, effort=effort)
    prompt = await _assemble_prompt(deps, event, sources, effort_bound)

    system = render(PromptName.QUEEN_SYSTEM, sections=prompt.sections)
    llm_request = LLMRequest(
        slot=ModelSlot.QUEEN,
        system=system,
        messages=(Message.text(Role.USER, prompt.event_text),),
        max_output_tokens=AWAKE_MAX_OUTPUT_TOKENS,
        effort=effort,
    )
    # External await: one model call, latency class seconds; the ladder itself retries and steps
    # down rungs on a malformed reply, and falls back along the binding's own chain on an outage.
    result = await complete_structured(
        effort_bound, llm_request, QueenDecision, gate=deps.call_gate
    )
    await _record(deps, event, result.value)
    return result.value


async def _assemble_prompt(
    deps: QueenDeps, event: TriggerEvent, sources: HotStateSources, effort_bound: BoundModel
) -> Prompt:
    """Pack the Queen's own hot state into a token-budgeted Prompt, sized for `effort_bound`."""
    principal = Principal(
        id=deps.identity.hive_id,
        slot=ModelSlot.QUEEN,
        clearance=HoneyClearance.C2,
        role=_QUEEN_ROLE,
    )
    budget = TokenBudget(
        max_input_tokens=max(
            1, int(effort_bound.context_window * deps.memory_budget.budget_fraction)
        ),
        output_reserve=deps.memory_budget.output_reserve_tokens,
    )
    request = AssembleRequest(principal=principal, event=event, budget=budget)
    return await assemble(request, sources, EstimateCounter())


async def _record(deps: QueenDeps, event: TriggerEvent, decision: QueenDecision) -> None:
    """Write `decision` back as an EpisodeRecord; the transcript itself is discarded here."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    record = EpisodeRecord(
        id=new_event_id(deps.clock),
        principal=deps.identity.hive_id,
        slot=ModelSlot.QUEEN,
        trigger=event,
        prompt_ref=None,
        reasoning_summary=None,
        decision=decision.action.value,
        action=decision.reason,
        at=deps.clock.now(),
        clearance=HoneyClearance.C2,
        usage=None,
        is_autopilot=False,
    )
    await record_episode(record, ctx)
