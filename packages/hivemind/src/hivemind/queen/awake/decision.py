"""Define QueenDecision: the one structured value the Queen's awake episode ever produces.

Codingrules section 8.8: "Awake episodes are stateless. An episode assembles its prompt through
memory.assemble from durable state plus the triggering event, decides one action, writes the
decision back, and discards the transcript." `QueenDecision` is that one decision, shaped so
`hivemind.llm.complete_structured` can ask a model for it directly: which
`hivemind.queen.autopilot.actions.QueenAction` to take (the same closed set autopilot itself
returns, so the Queen's tick handles an awake decision through the exact same action-dispatch code
as a deterministic one), which task (if any) it concerns, why, and an optional `binding` -- the
`[llm.slots]` key to note for a REBIND, set only when `action` is `REBIND`. Roadmap step 4.7's own
leftover adds `shrink_grant_id`/`shrink_amount`, set only when `action` is `GRANT_BY_SHRINKING`: a
contested `ForageRequest`'s own episode (`hivemind.queen.ticks.forage`) hands the model the live
grants for that request's dimension in its prompt (`hivemind.queen.awake.episode.EpisodeExtras.
system_hint`), and the model names which one to shrink, and by how much, to free the headroom the
requester needs. Roadmap step 10.5 (ADR-0032) adds `message`: the words of a `REPLY`, the Queen
answering the human in the chat as Monarch.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's awake
    sub-package. Produced by `hivemind.queen.awake.episode.decide_awake`; acted on by
    `hivemind.queen.queen.Queen`'s tick the same way a deterministic `QueenAction` is. Calls into
    `hivemind.queen.autopilot` (QueenAction) only.

Key invariants:
    - `QueenDecision` is frozen and forbids extras, like every boundary value in this repository.
    - `binding` is set if and only if `action` is `QueenAction.REBIND`; the caller
      (`decide_awake`) validates the model actually supplied one before acting on a REBIND
      decision -- no cross-field validator covers it, because `complete_structured`'s
      JSON-schema rungs need a plain, permissive shape a weak model can fill in without pydantic's
      own validator vocabulary leaking into the prompt.
    - `message` is the one exception, checked here: required (non-blank) exactly when `action` is
      `REPLY`, and refused on any other action. A REPLY with no words would answer the human with
      silence, and words on another action would be text nobody ever reads; either is a malformed
      decision the ladder's own retry is the right place to correct.

See Also:
    - .claude/codingrules.md section 8.8 for "Awake episodes are stateless... decides one action".
    - hivemind.queen.autopilot.actions for QueenAction, the closed set `action` draws from.
    - hivemind.queen.awake.episode for decide_awake, this model's one producer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.queen.autopilot import QueenAction

MAX_REASON_CHARS = 500  # A sentence or two; never a transcript (codingrules section 12).
MAX_BINDING_CHARS = 64  # A [llm.slots] manifest key; generous for any named binding.
MAX_TASK_ID_CHARS = 64  # A TaskId's own textual length; generous for any prefixed ULID.
MAX_GRANT_ID_CHARS = 64  # A GrantId's own textual length; generous for any prefixed ULID.
# A REPLY's words: a paragraph or two, well inside AWAKE_MAX_OUTPUT_TOKENS alongside the rest of
# the decision (about four characters a token), and far under the chat's own line bound.
MAX_MESSAGE_CHARS = 2_000

__all__ = [
    "MAX_BINDING_CHARS",
    "MAX_GRANT_ID_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_REASON_CHARS",
    "MAX_TASK_ID_CHARS",
    "QueenDecision",
]


class QueenDecision(BaseModel):
    """One awake episode's whole output: an action, the task it concerns, why, and its details."""

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
    shrink_grant_id: str | None = Field(
        default=None,
        max_length=MAX_GRANT_ID_CHARS,
        description="The GrantId to shrink; set only when action is GRANT_BY_SHRINKING.",
    )
    shrink_amount: float | None = Field(
        default=None,
        ge=0,
        description="How much to shrink shrink_grant_id by, in the contested request's own "
        "dimension; set only when action is GRANT_BY_SHRINKING.",
    )
    message: str | None = Field(
        default=None,
        max_length=MAX_MESSAGE_CHARS,
        description="What you say to the human in the chat, as Monarch; required when action is "
        "REPLY and set only then.",
    )

    @model_validator(mode="after")
    def _message_only_on_reply(self) -> QueenDecision:
        """Require words exactly on a REPLY (module docstring's one cross-field exception)."""
        replying = self.action is QueenAction.REPLY
        has_words = self.message is not None and bool(self.message.strip())
        if replying and not has_words:
            raise ValueError("A REPLY decision must carry the words to send in `message`.")
        if not replying and self.message is not None:
            raise ValueError(
                f"`message` is only for a REPLY; a {self.action.value} decision must leave it null."
            )
        return self
