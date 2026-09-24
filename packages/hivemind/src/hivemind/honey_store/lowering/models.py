"""Define the label lowering value models: the stored proposal, and the judge's request and verdict.

A lowering proposal asks to lower one Nectar's label (a raw deposit in the Honey Store, the Hive's
knowledge base, together with the Honey rows ripened from it) when only the Real Cell floor holds
it up and the Ripener read the text as less sensitive (ADR-0034). `LoweringProposal` is the stored
proposal as the store returns it. `ClearanceJudgeRequest` and `ClearanceVerdict` cross the model
boundary to and from the independent clearance judge (the JUDGE model slot): the request carries
only what the judge must see to clear the text, never the Ripener's reason, an id, or who asked.
`LoweringFiling` and `LoweringDecision` are the two small values the lowering service hands the
store to file a proposal and to decide one.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.lowering`.
    Built by the lowering service (`review`) and the store (`store.sqlite.lowering`), read by the
    judges (`judge`, `fake`) and by `hive honey review`. Calls into `hivemind.cell`,
    `hivemind.honey_store.clearance`, `.models`, this package's `state`, the manifest's honey
    schema (the judge's text ceiling) and waggle only.

Key invariants:
    - Every pydantic model here is frozen and forbids unknown fields (codingrules 8.5).
    - `ClearanceJudgeRequest` has no field for an id, the Ripener's reason or the proposer.
    - A verdict's `rubric_id` is always stamped by the judge implementation, never read from a
      model's reply (see `hivemind.honey_store.lowering.judge`).

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the decision.
    - hivemind.honey_store.lowering.state for LoweringState, a proposal's own machine.
    - hivemind.honey_store.lowering.judge for the ClearanceJudge seam these models cross.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated, NewType

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.honey_store.models.nectar import MAX_RIPENER_REASON_CHARS
from hivemind.manifest.schema.honey import MAX_JUDGE_CHARS_CEILING
from waggle.ids import NectarId
from waggle.messages.base import NectarIdField, UtcDatetime
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import (
    MAX_MEDIA_TYPE_CHARS,
    MAX_TITLE_CHARS,
    MIN_MEDIA_TYPE_CHARS,
)

LOWERING_ID_PREFIX = "lowering_"  # No waggle IdKind names a proposal; minted as a prefixed ULID.
MAX_VERDICT_REASONS = 8  # A handful of findings; a longer list is a judge rambling.
MAX_VERDICT_REASON_CHARS = 300  # One line each, as the prompt asks.
MAX_RUBRIC_ID_CHARS = 64  # "honey-clearance/1" and its successors; an id, never a rubric's text.
MAX_NOTE_CHARS = 500  # A sentence or two for the human on why a proposal waits for them.
MAX_HUMAN_REASON_CHARS = 500  # The same bound `hive honey relabel` puts on the human's reason.

# One short reason from a verdict, bounded the way the prompt asks the judge to keep it.
_ReasonField = Annotated[str, Field(max_length=MAX_VERDICT_REASON_CHARS)]

# codingrules 8.5: frozen, extra-forbidding config every pydantic model in this module shares.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

# A lowering proposal's own id: `lowering_<ULID>`, minted by the store that files it.
LoweringId = NewType("LoweringId", str)

__all__ = [
    "LOWERING_ID_PREFIX",
    "MAX_HUMAN_REASON_CHARS",
    "MAX_NOTE_CHARS",
    "MAX_RUBRIC_ID_CHARS",
    "MAX_VERDICT_REASONS",
    "MAX_VERDICT_REASON_CHARS",
    "ClearanceJudgeRequest",
    "ClearanceOutcome",
    "ClearanceVerdict",
    "LoweringDecision",
    "LoweringFiling",
    "LoweringId",
    "LoweringProposal",
]


class ClearanceOutcome(Enum):
    """What the clearance judge decided about one proposal's text."""

    APPROVE = "APPROVE"  # Nothing in the text is more sensitive than the target: lower it.
    REJECT = "REJECT"  # Something in it is more sensitive: the label stays where it is.


class LoweringProposal(BaseModel):
    """One stored proposal to lower one Nectar's label, and how it was decided (ADR-0034).

    Returned by every lowering read and write of `HoneyStore`; shown to the operator by `hive
    honey review`. A PROPOSED proposal with a non-empty `note` waits for the human.
    """

    model_config = _MODEL_CONFIG

    id: LoweringId = Field(description="This proposal's own id, `lowering_<ULID>`.")
    nectar_id: NectarIdField = Field(description="The Nectar whose label it would lower.")
    from_label: HoneyClearance = Field(description="The Nectar's label when it was filed.")
    to_label: HoneyClearance = Field(
        description="The label it would carry: the higher of its declared label and the "
        "Ripener's reading, never below what any depositor declared."
    )
    state: LoweringState = Field(description="Where it is in its own transition table.")
    approver: LabelApprover | None = Field(
        default=None, description="Who decided it (JUDGE or HUMAN); None while PROPOSED."
    )
    ripener_reason: str = Field(
        default="",
        max_length=MAX_RIPENER_REASON_CHARS,
        description="The Ripener's one-line reason for its reading, for the human; never shown "
        "to the judge.",
    )
    verdict_reasons: tuple[_ReasonField, ...] = Field(
        default=(),
        max_length=MAX_VERDICT_REASONS,
        description="The judge's reasons, once a judge answered; empty otherwise.",
    )
    rubric_id: str | None = Field(
        default=None,
        max_length=MAX_RUBRIC_ID_CHARS,
        description="The rubric the judge's verdict was given under; None when no judge answered.",
    )
    human_reason: str | None = Field(
        default=None,
        max_length=MAX_HUMAN_REASON_CHARS,
        description="The operator's own reason, once the human decided; never on the trail.",
    )
    attempts: int = Field(
        default=0, ge=0, description="How many times the judge failed to answer it."
    )
    note: str = Field(
        default="",
        max_length=MAX_NOTE_CHARS,
        description="Why it waits for the human, or why it was rejected unasked; empty otherwise.",
    )
    proposed_at: UtcDatetime = Field(description="When it was filed.")
    decided_at: UtcDatetime | None = Field(
        default=None, description="When it was lowered or rejected; None while PROPOSED."
    )


class ClearanceJudgeRequest(BaseModel):
    """What the clearance judge is shown for one proposal: the deposit's text and the target.

    Crosses into the JUDGE model's prompt as untrusted, delimited data. Deliberately holds no id,
    no Ripener reason and nothing about who proposed the lowering (ADR-0034): the judge reviews
    the text, never the proposer, and shares no context with it (codingrules 8.12).
    """

    model_config = _MODEL_CONFIG

    text: str = Field(
        min_length=1,
        max_length=MAX_JUDGE_CHARS_CEILING,
        description="The deposit's whole text, decoded exactly as ripening decodes it; a judge "
        "must see everything it clears, so it is never cut.",
    )
    title: str = Field(
        max_length=MAX_TITLE_CHARS,
        description="The deposit's own title: it is lowered with the text, so it is judged too.",
    )
    kind: NectarKind = Field(description="What sort of finding the deposit is.")
    media_type: str = Field(
        min_length=MIN_MEDIA_TYPE_CHARS,
        max_length=MAX_MEDIA_TYPE_CHARS,
        description="The deposit's MIME type.",
    )
    target: HoneyClearance = Field(description="The label the text would carry if approved.")
    rubric_id: str = Field(
        min_length=1,
        max_length=MAX_RUBRIC_ID_CHARS,
        description="The rubric the judge is asked to apply; the service always names the "
        "shipped judge's own (`hivemind.honey_store.lowering.judge.RUBRIC_ID`).",
    )


class ClearanceVerdict(BaseModel):
    """The clearance judge's answer for one proposal: approve or reject, with short reasons."""

    model_config = _MODEL_CONFIG

    outcome: ClearanceOutcome = Field(description="Whether the text may carry the target label.")
    reasons: tuple[_ReasonField, ...] = Field(
        default=(),
        max_length=MAX_VERDICT_REASONS,
        description="Why, one short line each; for a rejection, the kinds of detail found, never "
        "the detail itself.",
    )
    rubric_id: str = Field(
        min_length=1,
        max_length=MAX_RUBRIC_ID_CHARS,
        description="The rubric this verdict was given under, stamped by the judge implementation.",
    )


@dataclass(frozen=True, slots=True)
class LoweringFiling:
    """What the lowering service asks the store to file: one Nectar, from which label to which."""

    nectar_id: NectarId  # The Nectar `lowering_target` found eligible.
    from_label: HoneyClearance  # Its label as the service read it.
    to_label: HoneyClearance  # `lowering_target`'s own answer for it.


@dataclass(frozen=True, slots=True)
class LoweringDecision:
    """One decision on a proposal, as the store records it: who, when, and on what grounds."""

    proposal_id: LoweringId  # The proposal being decided.
    approver: LabelApprover  # JUDGE for a verdict, HUMAN for the operator's own decision.
    decided_at: datetime  # The deciding caller's own clock reading.
    verdict: ClearanceVerdict | None = None  # The judge's verdict; None for the human.
    human_reason: str | None = None  # The operator's reason; None for the judge.
