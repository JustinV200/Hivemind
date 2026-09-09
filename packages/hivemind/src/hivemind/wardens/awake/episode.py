"""Define decide_awake: one Warden's whole awake episode, from durable state to one WardenDecision.

Codingrules section 8.8: "An issue a bee cannot resolve becomes an Alarm... only [NEEDS_JUDGEMENT]
runs an awake episode. Awake episodes are stateless: [an episode] assembles its prompt through
memory.assemble from durable state plus the triggering event, decides one action, writes the
decision back, and discards the transcript." `decide_awake` is that whole episode for a Warden:
`hivemind.memory.assemble` packs `sources` (a `HotStateSources` view the Warden's own tick builds
fresh from its sub-bee table, pending questions and memory store) into a token-budgeted `Prompt`;
`hivemind.llm.render` lays the stable prefix (`warden_system.md` plus PINS/HOT_STATE) in front of
the triggering event's own text as the final user turn; `hivemind.llm.complete_structured` walks
the degradation ladder to get a `hivemind.wardens.awake.decision.WardenDecision` back, on whichever
rung `deps.bound`'s declared capabilities support -- a plain-text model at `ProviderCapabilities.
none()` still owes a decision, on the PROMPTED rung; `hivemind.memory.record_episode` writes the
decision back as an `EpisodeRecord`
(`is_autopilot=False`) before the transcript itself is discarded, exactly as codingrules 8.8
prescribes.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's awake
    sub-package (which is allowed to import `hivemind.llm`; only `autopilot/` may not). Called by
    `hivemind.wardens.warden.Warden`'s tick whenever `hivemind.wardens.autopilot.table.decide`
    returns `NEEDS_JUDGEMENT`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.forage.slots`
    (ModelSlot), `hivemind.llm` (LLMRequest, Message, Role, PromptName, render, complete_structured)
    and `hivemind.memory` (assemble, AssembleRequest, EstimateCounter, HotStateSources,
    MemoryContext, Principal, TokenBudget, TriggerEvent, record_episode, EpisodeRecord) only.

Key invariants:
    - `decide_awake` reads `sources` and `event` only for what goes into the prompt; it holds no
      state of its own across calls, matching codingrules 8.8's "stateless" requirement.
    - The system prompt carries only PINS and HOT_STATE (`Prompt.sections`, never RETRIEVED in
      memory v0); the triggering event's own text is the final user turn, never folded into the
      system prompt, so provider prompt caching still sees a stable prefix call after call.
    - `record_episode` is called with the model's own decision and reason, never a raw transcript
      (codingrules section 12: "Thoughts are memory, not audit"; the trail's own `memory.episode`
      event, written by `record_episode` itself, carries only counts).

See Also:
    - .claude/codingrules.md section 8.8 for the stateless-awake-episode shape this function is.
    - hivemind.wardens.awake.decision for WardenDecision, the schema complete_structured targets.
    - hivemind.llm.prompts for PromptName.WARDEN_SYSTEM and render, the prompt this assembles into.
    - hivemind.memory for assemble and record_episode, the two memory-tier calls this makes.
    - hivemind.wardens.deps for WardenDeps, whose `bound` and `call_gate` this function calls
      through.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, Message, PromptName, Role, complete_structured, render
from hivemind.memory import (
    AssembleRequest,
    EpisodeRecord,
    EstimateCounter,
    HotStateSources,
    MemoryContext,
    Principal,
    TokenBudget,
    TriggerEvent,
    assemble,
    record_episode,
)
from hivemind.wardens.awake.decision import WardenDecision
from hivemind.wardens.deps import WardenDeps
from waggle.ids import new_event_id

# The fraction of the Warden's own bound model's context window a hot-state prompt may fill; the
# rest is headroom for the system body, the event itself and the response (mirrors the manifest's
# future [memory] budget_fraction default, restated here since WardenDeps carries no such field).
WARDEN_BUDGET_FRACTION = 0.5
AWAKE_OUTPUT_RESERVE_TOKENS = 512  # Room for the model's own reply within the packed budget.
AWAKE_MAX_OUTPUT_TOKENS = 1_024  # A WardenDecision is short; generous but bounded.
_WARDEN_PRINCIPAL_ID = "warden"  # This episode's own principal id, for EpisodeRecord.principal.
_WARDEN_ROLE = "warden"

__all__ = [
    "AWAKE_MAX_OUTPUT_TOKENS",
    "AWAKE_OUTPUT_RESERVE_TOKENS",
    "WARDEN_BUDGET_FRACTION",
    "decide_awake",
]


async def decide_awake(
    deps: WardenDeps, event: TriggerEvent, sources: HotStateSources
) -> WardenDecision:
    """Run one stateless awake episode and return its one WardenDecision.

    Args:
        deps: This Warden's collaborators; `bound` and `call_gate` are what the model call runs
            through, `memory`/`identity`/`clock` are what the episode is recorded with.
        event: What triggered this episode (a NEEDS_JUDGEMENT inbox item, summarised).
        sources: A view of this Warden's own hot state, built fresh by its caller from whatever
            the Warden currently tracks.

    Returns:
        The one WardenDecision the model produced.

    Raises:
        hivemind.llm.errors.MalformedOutputError: Every rung of the structured-output ladder, on
            every binding in `deps.bound`'s fallback chain, was exhausted.
    """
    principal = Principal(
        id=_WARDEN_PRINCIPAL_ID,
        slot=ModelSlot.WARDEN,
        clearance=HoneyClearance.C1,
        role=_WARDEN_ROLE,
    )
    budget = TokenBudget(
        max_input_tokens=max(1, int(deps.bound.context_window * WARDEN_BUDGET_FRACTION)),
        output_reserve=AWAKE_OUTPUT_RESERVE_TOKENS,
    )
    request = AssembleRequest(principal=principal, event=event, budget=budget)
    prompt = await assemble(request, sources, EstimateCounter())

    system = render(PromptName.WARDEN_SYSTEM, sections=prompt.sections)
    llm_request = LLMRequest(
        slot=ModelSlot.WARDEN,
        system=system,
        messages=(Message.text(Role.USER, prompt.event_text),),
        max_output_tokens=AWAKE_MAX_OUTPUT_TOKENS,
    )
    # External await: one model call, latency class seconds; the ladder itself retries and steps
    # down rungs on a malformed reply, and falls back along deps.bound's own chain on an outage.
    result = await complete_structured(deps.bound, llm_request, WardenDecision, gate=deps.call_gate)
    await _record(deps, event, result.value)
    return result.value


async def _record(deps: WardenDeps, event: TriggerEvent, decision: WardenDecision) -> None:
    """Write `decision` back as an EpisodeRecord; the transcript itself is discarded here."""
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    record = EpisodeRecord(
        id=new_event_id(deps.clock),
        principal=_WARDEN_PRINCIPAL_ID,
        slot=ModelSlot.WARDEN,
        trigger=event,
        prompt_ref=None,
        reasoning_summary=None,
        decision=decision.action.value,
        action=decision.reason,
        at=deps.clock.now(),
        clearance=HoneyClearance.C1,
        usage=None,
        is_autopilot=False,
    )
    await record_episode(record, ctx)
