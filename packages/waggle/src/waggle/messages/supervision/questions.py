"""Define the supervision family's blocking questions: raise one up the chain, answer it back down.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and its
supervision family is the one ``Supervisor`` protocol at every level of the tree (human, Queen,
Wardens, sub-bees). A ``Question`` is raised by a Worker (a sub-bee spawned for one task) or a
Warden (the always-on supervisor of one Cell, a unit of compute) and blocks its task until an
``Answer`` with the same question id comes back; only the Queen (the central orchestrator)
decides whether the question goes to the human, and ``AnswerSource`` says who answered, because
the human is not a bee address. The question id is minted by the asker and is not any envelope's
id, so it survives re-wrapping at each hop. The family's other messages are split out by
responsibility so each file stays under the codingrules 5.1 size limit. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built and read by every Worker, every Warden and the Queen; calls
    into waggle.messages.base and waggle.messages.labels only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A human's answer is always labelled C2 (validator): a program cannot launder it lower.

See Also:
    - docs/waggle/spec.md section 8.3 for the normative fields, bounds and validators.
    - waggle.messages.supervision.oversight, waggle.messages.supervision.alarms and
      waggle.messages.supervision.telemetry for the rest of the family.
    - waggle.messages.labels for HoneyClearance.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MessageIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
    id_validator,
)
from waggle.messages.labels import HoneyClearance

MIN_TEXT_CHARS = 1  # A question, or its answer, always says something.
MAX_TEXT_CHARS = 4_000  # A few paragraphs: enough to ask or answer properly, never a document.
MAX_OPTIONS = 16  # Closed choices the asker offers; more than this is a form, not a question.
MAX_OPTION_CHARS = 200  # One choice is a sentence.
MIN_OPTION_INDEX = 0  # Options are indexed from 0, as the tuple on the wire is.
MAX_OPTION_INDEX = MAX_OPTIONS - 1  # 15: the last index a full options tuple can have.

__all__ = [
    "MAX_OPTIONS",
    "MAX_OPTION_CHARS",
    "MAX_OPTION_INDEX",
    "MAX_TEXT_CHARS",
    "MIN_OPTION_INDEX",
    "MIN_TEXT_CHARS",
    "Answer",
    "AnswerSource",
    "Question",
]


class AnswerSource(Enum):
    """Who answered a question; the human is not a bee address, so this says when it was them."""

    HUMAN = "HUMAN"  # Relayed by the Queen; the answer is C2 by provenance.
    QUEEN = "QUEEN"
    WARDEN = "WARDEN"


# A bee that can ask: a Worker or a Warden, never the Queen or a device.
_BeeId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


class Question(WaggleMessage):
    """Raise a question up the chain that blocks the task (supervision.question, a request).

    Blocks until an Answer with the same question id comes back; only the Queen decides whether
    it goes to the human.
    """

    question_id: MessageIdField = Field(
        description="The id the question is known by everywhere, minted by the asker with "
        "new_message_id so it survives re-wrapping at each hop. It is not any envelope's id."
    )
    task_id: TaskIdField = Field(description="The task that blocks on the answer.")
    asked_by: _BeeId = Field(description="The bee that asked; survives forwarding.")
    text: str = Field(
        min_length=MIN_TEXT_CHARS, max_length=MAX_TEXT_CHARS, description="The question."
    )
    options: tuple[Annotated[str, Field(max_length=MAX_OPTION_CHARS)], ...] = Field(
        max_length=MAX_OPTIONS,
        description="Closed choices, when the asker can offer them; may be empty.",
    )
    clearance: HoneyClearance = Field(
        description="The label of the question text, which may quote Real Cell or personal data."
    )
    asked_at: UtcDatetime = Field(description="When it was first asked; survives forwarding.")


class Answer(WaggleMessage):
    """Deliver the answer to a blocked question back down the chain (supervision.answer, a reply).

    The task resumes on receipt.
    """

    question_id: MessageIdField = Field(description="Matches Question.question_id at every hop.")
    task_id: TaskIdField = Field(description="The task that resumes.")
    text: str = Field(
        min_length=MIN_TEXT_CHARS, max_length=MAX_TEXT_CHARS, description="The answer."
    )
    chosen_option: Annotated[int, Field(ge=MIN_OPTION_INDEX, le=MAX_OPTION_INDEX)] | None = Field(
        description="Index into Question.options when one was chosen; the receiver checks it "
        "is a valid index for that question."
    )
    source: AnswerSource = Field(description="Who answered.")
    clearance: HoneyClearance = Field(
        description="The label of the answer; a human's answer is C2 by provenance."
    )

    @model_validator(mode="after")
    def _human_answer_is_c2(self) -> Answer:
        """Require C2 on an answer the human gave."""
        # A human's words are personal by provenance, whatever they say; a lower label would let
        # a program launder them into a tier that must never hold them.
        if self.source is AnswerSource.HUMAN and self.clearance is not HoneyClearance.C2:
            raise ValueError(
                f"Answer from source HUMAN requires clearance C2, got {self.clearance.value}."
            )
        return self
