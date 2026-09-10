"""Define QueenDecision: the one structured value the Queen's awake episode ever produces.

Codingrules section 8.8: "Awake episodes are stateless. An episode assembles its prompt through
memory.assemble from durable state plus the triggering event, decides one action, writes the
decision back, and discards the transcript." `QueenDecision` is that one decision, shaped so
`hivemind.llm.complete_structured` can ask a model for it directly: which
`hivemind.queen.autopilot.actions.QueenAction` to take (the same closed set autopilot itself
returns, so the Queen's tick handles an awake decision through the exact same action-dispatch code
as a deterministic one), which task (if any) it concerns, why, and an optional `binding` -- the
`[llm.slots]` key to note for a REBIND, set only when `action` is `REBIND`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's awake
    sub-package. Produced by `hivemind.queen.awake.episode.decide_awake`; acted on by
    `hivemind.queen.queen.Queen`'s tick the same way a deterministic `QueenAction` is. Calls into
    `hivemind.queen.autopilot` (QueenAction) only.

Key invariants:
    - `QueenDecision` is frozen and forbids extras, like every boundary value in this repository.
    - `binding` is set if and only if `action` is `QueenAction.REBIND`; the caller
      (`decide_awake`) validates the model actually supplied one before acting on a REBIND
      decision -- this model itself has no cross-field validator, because
      `complete_structured`'s JSON-schema rungs need a plain, permissive shape a weak model can
      fill in without pydantic's own validator vocabulary leaking into the prompt.

See Also:
    - .claude/codingrules.md section 8.8 for "Awake episodes are stateless... decides one action".
    - hivemind.queen.autopilot.actions for QueenAction, the closed set `action` draws from.
    - hivemind.queen.awake.episode for decide_awake, this model's one producer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.queen.autopilot import QueenAction

MAX_REASON_CHARS = 500  # A sentence or two; never a transcript (codingrules section 12).
MAX_BINDING_CHARS = 64  # A [llm.slots] manifest key; generous for any named binding.
MAX_TASK_ID_CHARS = 64  # A TaskId's own textual length; generous for any prefixed ULID.

__all__ = ["MAX_BINDING_CHARS", "MAX_REASON_CHARS", "MAX_TASK_ID_CHARS", "QueenDecision"]


class QueenDecision(BaseModel):
    """One awake episode's whole output: an action, the task it concerns, why, and a binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: QueenAction = Field(description="The one QueenAction this episode decided on.")
    task_id: str | None = Field(
        default=None,
        max_length=MAX_TASK_ID_CHARS,
        description="The task this decision concerns, when the triggering event named one.",
    )
    reason: str = Field(max_length=MAX_REASON_CHARS, description="Why, in a sentence or two.")
    binding: str | None = Field(
        default=None,
        max_length=MAX_BINDING_CHARS,
        description="The [llm.slots] key to note for a REBIND; set only when action is REBIND.",
    )
