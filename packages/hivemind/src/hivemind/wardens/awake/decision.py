"""Define WardenDecision: the one structured value a Warden's awake episode ever produces.

Codingrules section 8.8: "Awake episodes are stateless. An episode assembles its prompt through
memory.assemble from durable state plus the triggering event, decides one action, writes the
decision back, and discards the transcript." `WardenDecision` is that one decision, shaped so
`hivemind.llm.ladders.structured.complete_structured` can ask a model for it directly: which
`hivemind.wardens.autopilot.actions.WardenAction` to take (the same closed set autopilot itself
returns, so a Warden's tick handles an awake decision through the exact same action-dispatch code
as a deterministic one), why, and an optional `binding` -- the `[llm.slots]` key to rebind to, set
only when `action` is `REBIND` and otherwise left `None`, matching `waggle.messages.supervision.
Intervene`'s own `slot`-required-exactly-for-REBIND shape.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's awake
    sub-package. Produced by `hivemind.wardens.awake.episode.decide_awake`; acted on by
    `hivemind.wardens.warden.Warden`'s tick the same way a deterministic `WardenAction` is. Calls
    into `hivemind.wardens.autopilot` (WardenAction) only.

Key invariants:
    - `WardenDecision` is frozen and forbids extras, like every boundary value in this repository.
    - `binding` is set if and only if `action` is `WardenAction.REBIND`; the caller (`decide_awake`)
      is responsible for asking a model for a `binding` and validating the model actually
      supplied one before acting on a REBIND decision -- this model itself has no cross-field
      validator, because `complete_structured`'s JSON-schema rungs need a plain, permissive shape
      a weak model can fill in without pydantic's own validator vocabulary leaking into the prompt.

See Also:
    - .claude/codingrules.md section 8.8 for "Awake episodes are stateless... decides one action".
    - hivemind.wardens.autopilot.actions for WardenAction, the closed set `action` draws from.
    - hivemind.wardens.awake.episode for decide_awake, this model's one producer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.wardens.autopilot import WardenAction

MAX_REASON_CHARS = 500  # A sentence or two; never a transcript (codingrules section 12).
MAX_BINDING_CHARS = 64  # A [llm.slots] manifest key; generous for any named binding.

__all__ = ["MAX_BINDING_CHARS", "MAX_REASON_CHARS", "WardenDecision"]


class WardenDecision(BaseModel):
    """One awake episode's whole output: which action to take, why, and (for REBIND) which slot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: WardenAction = Field(description="The one WardenAction this episode decided on.")
    reason: str = Field(max_length=MAX_REASON_CHARS, description="Why, in a sentence or two.")
    binding: str | None = Field(
        default=None,
        max_length=MAX_BINDING_CHARS,
        description="The [llm.slots] key to rebind to; set only when action is REBIND.",
    )
