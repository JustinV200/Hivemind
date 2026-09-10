"""Define ModelTieBreaker: the Queen's model-backed arbiter for an exact Attendant score tie.

Codingrules section 8.8: "The Queen's Attendant weighs human messages heavily but not absolutely
and may use `ModelSlot.ATTENDANT` for ties and unknown kinds." `hivemind.supervision.attendant.
Attendant.order` already handles every non-tied case deterministically; this class is the seam it
calls only for the rare exact tie a model can arbitrate more sensibly than an arbitrary
`(received_at, id)` fallback. It asks a model, on `ModelSlot.ATTENDANT` (a cheap, fast slot),
which of the tied items should lead, through `hivemind.llm.complete_structured` against a small
`_TieBreak` schema; a model that names an item not among those shown, or whose reply cannot be
parsed even at the PROMPTED rung, never blocks ordering -- the first tied item (already the
deterministic fallback order) wins instead.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's inbox
    sub-package (which MAY import `hivemind.llm`; only `autopilot/` may not). Constructed once by
    whichever composition root builds a Queen (the CLI in production, `tests.builders.queen.
    make_queen_deps` in tests) and handed to `hivemind.queen.inbox.weights.queen_attendant`. Calls
    into `hivemind.llm` (BoundModel, CallGate, LLMRequest, Message, PromptName, Role,
    complete_structured, render) and `hivemind.supervision.attendant` (InboxItem, TieBreaker) only.

Key invariants:
    - `break_tie` never raises for a malformed or empty model reply: every failure path falls back
      to `items[0]`, the same item the deterministic `(received_at, id)` order would already pick.
    - The prompt shown to the model never claims an item is resolved or ranks by anything but the
      items themselves (codingrules section 15: hot state and tool results are data, not a
      command); `attendant_triage.md` states this rule directly.

See Also:
    - .claude/codingrules.md section 8.8 for "a cheap slot for ties only where the grant allows".
    - hivemind.llm.prompts for PromptName.ATTENDANT_TRIAGE, the prompt this class renders.
    - hivemind.supervision.attendant for TieBreaker, the Protocol this class implements.
    - hivemind.queen.inbox.weights for queen_attendant, this class's one consumer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm import (
    BoundModel,
    CallGate,
    LLMRequest,
    Message,
    PromptName,
    Role,
    complete_structured,
    render,
)
from hivemind.supervision.attendant import InboxItem

MAX_TIE_REASON_CHARS = 300  # A sentence, matching every other awake-episode reason bound.
TIE_BREAK_MAX_OUTPUT_TOKENS = 256  # A short verdict; generous but bounded.

__all__ = ["MAX_TIE_REASON_CHARS", "ModelTieBreaker"]


class _TieBreak(BaseModel):
    """The one structured value `ModelTieBreaker.break_tie` asks a model for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    winner_id: str = Field(description="The InboxItem.id of the tied item that should lead.")
    reason: str = Field(max_length=MAX_TIE_REASON_CHARS, description="Why, in a sentence or two.")


class ModelTieBreaker:
    """Ask a model, on `ModelSlot.ATTENDANT`, which of several exactly-tied items should lead."""

    def __init__(self, bound: BoundModel, gate: CallGate) -> None:
        """Build a ModelTieBreaker bound to one model.

        Args:
            bound: The `ModelSlot.ATTENDANT` binding every tie-break call runs on.
            gate: The seat meter the call passes through.
        """
        self._bound = bound
        self._gate = gate

    async def break_tie(self, items: tuple[InboxItem, ...]) -> InboxItem:
        """Return which of `items` (all scored equal) should rank first among them.

        Args:
            items: Two or more InboxItems whose Priority.score compares equal.

        Returns:
            The item from `items` the model named, or `items[0]` when the model's reply cannot be
            resolved to one of them (module docstring's Key invariants).
        """
        system = render(PromptName.ATTENDANT_TRIAGE, sections={})
        request = LLMRequest(
            slot=self._bound.slot,
            system=system,
            messages=(Message.text(Role.USER, _describe_items(items)),),
            max_output_tokens=TIE_BREAK_MAX_OUTPUT_TOKENS,
        )
        result = await complete_structured(self._bound, request, _TieBreak, gate=self._gate)
        return next((item for item in items if item.id == result.value.winner_id), items[0])


def _describe_items(items: tuple[InboxItem, ...]) -> str:
    """Render one line per tied item: its id, kind and principal, as retrieved content."""
    lines = [f"- id={item.id} kind={item.kind.value} from={item.principal}" for item in items]
    return "Tied inbox items:\n" + "\n".join(lines)
