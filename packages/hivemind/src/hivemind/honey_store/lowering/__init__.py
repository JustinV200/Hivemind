"""Lower a Honey Store label only through a proposal an independent judge or the human decides.

Anything gathered on a Real Cell (a borrowed device, the Hive Stand included) is labelled C2 in
the Honey Store (the Hive's knowledge base) by the provenance floor, whatever it says, so a default
C1 goal never reads it. ADR-0034 lets that label come down, as Capping's discipline without its
Cell vocabulary: the Ripener's own reading of a deposit's text can only start a proposal
(`rules.lowering_target`), an independent clearance judge on the JUDGE model slot or the human
decides it (`judge`, `review.LabelLowering.decide`), the store applies and verifies it in one
transaction, and every edge is on the Pheromone Trail (the Hive's append-only audit log). A
proposal's machine is one transition table (`state`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Driven
    by the House Bee's ripening pass (file, then review) and by `hive honey review` (list, decide,
    judge now); the store's own lowering transactions read `rules` and `state`. Calls into
    `hivemind.honey_store`'s store, models, clearance, identity and ripening decoder,
    `hivemind.llm` (the judge's one structured call) and `hivemind.manifest` only.

Key invariants:
    - Only `HoneyStore.apply_lowering` ever lowers a label, and only after re-running
      `lowering_target` in its own transaction; the Ripener never approves its own reading.
    - The judge sees the deposit's title, text, kind, media type and the target, and never the
      Ripener's reason, an id, or who asked.
    - One proposal per Nectar, ever; LOWERED is terminal.

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the decision.
    - hivemind.honey_store.store.protocol for the store's lowering methods.
    - hivemind.llm.prompts's `judge_clearance.md` for the judge's rubric.

Public API:
    - lowering_target, HUMAN_ONLY_ORIGINS (rules): which Nectar may be proposed, and to what.
    - LoweringState, TRANSITIONS, TERMINAL_STATES, can_transition, assert_transition (state): a
      proposal's one transition table.
    - LoweringProposal, LoweringId, LoweringFiling, LoweringDecision, ClearanceJudgeRequest,
      ClearanceVerdict, ClearanceOutcome and their bounds (models): the stored proposal, what the
      store is handed, and what crosses to and from the judge.
    - ClearanceJudge, ModelClearanceJudge, RUBRIC_ID, CLEARANCE_JUDGE_OUTPUT_TOKENS,
      CLEARANCE_JUDGE_TIMEOUT_S (judge): the review seam and its JUDGE-slot implementation.
    - FakeClearanceJudge, ScriptedAnswer, SCRIPTED_REASON (fake): a scripted judge.
    - proposed_events, applied_events, rejected_events and the event kinds (events): what each
      edge records on the trail.
    - LabelLowering, LoweringDeps, ReviewOutcome and the notes it writes (review): file, review
      and decide.
"""

from hivemind.honey_store.lowering.events import (
    LABEL_LOWERED_KIND,
    LOWERING_PROPOSED_KIND,
    LOWERING_REJECTED_KIND,
    REJECTED_AS_INELIGIBLE,
    REJECTED_BY_APPROVER,
    applied_events,
    proposed_events,
    rejected_events,
)
from hivemind.honey_store.lowering.fake import SCRIPTED_REASON, FakeClearanceJudge, ScriptedAnswer
from hivemind.honey_store.lowering.judge import (
    CLEARANCE_JUDGE_OUTPUT_TOKENS,
    CLEARANCE_JUDGE_TIMEOUT_S,
    RUBRIC_ID,
    ClearanceJudge,
    ModelClearanceJudge,
)
from hivemind.honey_store.lowering.models import (
    LOWERING_ID_PREFIX,
    MAX_HUMAN_REASON_CHARS,
    MAX_NOTE_CHARS,
    MAX_RUBRIC_ID_CHARS,
    MAX_VERDICT_REASON_CHARS,
    MAX_VERDICT_REASONS,
    ClearanceJudgeRequest,
    ClearanceOutcome,
    ClearanceVerdict,
    LoweringDecision,
    LoweringFiling,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.review import (
    NO_TEXT_NOTE,
    TOO_LONG_NOTE,
    UNANSWERED_NOTE,
    LabelLowering,
    LoweringDeps,
    ReviewOutcome,
)
from hivemind.honey_store.lowering.rules import HUMAN_ONLY_ORIGINS, lowering_target
from hivemind.honey_store.lowering.state import (
    TERMINAL_STATES,
    TRANSITIONS,
    LoweringState,
    assert_transition,
    can_transition,
)

__all__ = [
    "CLEARANCE_JUDGE_OUTPUT_TOKENS",
    "CLEARANCE_JUDGE_TIMEOUT_S",
    "HUMAN_ONLY_ORIGINS",
    "LABEL_LOWERED_KIND",
    "LOWERING_ID_PREFIX",
    "LOWERING_PROPOSED_KIND",
    "LOWERING_REJECTED_KIND",
    "MAX_HUMAN_REASON_CHARS",
    "MAX_NOTE_CHARS",
    "MAX_RUBRIC_ID_CHARS",
    "MAX_VERDICT_REASONS",
    "MAX_VERDICT_REASON_CHARS",
    "NO_TEXT_NOTE",
    "REJECTED_AS_INELIGIBLE",
    "REJECTED_BY_APPROVER",
    "RUBRIC_ID",
    "SCRIPTED_REASON",
    "TERMINAL_STATES",
    "TOO_LONG_NOTE",
    "TRANSITIONS",
    "UNANSWERED_NOTE",
    "ClearanceJudge",
    "ClearanceJudgeRequest",
    "ClearanceOutcome",
    "ClearanceVerdict",
    "FakeClearanceJudge",
    "LabelLowering",
    "LoweringDecision",
    "LoweringDeps",
    "LoweringFiling",
    "LoweringId",
    "LoweringProposal",
    "LoweringState",
    "ModelClearanceJudge",
    "ReviewOutcome",
    "ScriptedAnswer",
    "applied_events",
    "assert_transition",
    "can_transition",
    "lowering_target",
    "proposed_events",
    "rejected_events",
]
