"""Define InboxKind and InboxItem: the one shape every supervisor's inbox holds.

The Attendant (codingrules section 8.8, README "Core concepts" 1) scores one inbox, whatever kind
of thing landed in it: a Waggle message from a bee, an Alarm climbing the chain, a Question waiting
on an answer, a message from the human, a timer firing, or a watch observation. `InboxKind` is that
closed set; `InboxItem` is the one shape every one of those is wrapped in before scoring, carrying
just enough about it (who it is from or about, how urgent, whether it names a task) for
`hivemind.supervision.attendant.scoring.score_item` to work without knowing the underlying
message's own type. `payload` is deliberately `object`: an InboxItem may wrap a waggle message, an
Alarm, a Question, or a plain human string, and scoring never needs to open it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by whichever inbox module feeds a
    Queen's or Warden's Attendant (`queen/inbox/`, `wardens/inbox/`, later roadmap steps); read by
    `hivemind.supervision.attendant.scoring`. Calls into `hivemind.supervision.alarm` (for the
    already-mirrored `AlarmSeverity`, the same type `Alarm.severity` carries) and waggle only.

Key invariants:
    - InboxItem is frozen and forbids extras, like every boundary value here, but sets
      `arbitrary_types_allowed=True` so `payload: object` (any Python value) is accepted without
      pydantic trying and failing to validate its internal shape.
    - latency_budget_s is either None (no budget) or strictly greater than zero, mirroring
      hivemind.forage.tempo.Tempo's own latency_budget_s field.
    - severity is set only for an ALARM item; every other kind carries None (a Supervisor
      implementation builds it that way, not a validator here, because scoring only ever reads
      the field and never depends on it matching kind).

See Also:
    - .claude/codingrules.md section 8.8 for the Attendant and the inbox kinds it scores.
    - .claude/codingrules.md section 6.1, "Attendant" row, for InboxItem's place in the naming
      table.
    - hivemind.supervision.attendant.weights for WeightTable, the per-kind/severity/principal
      weights InboxItem's fields are scored against.
    - hivemind.supervision.attendant.scoring for score_item, the pure function over this type.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.supervision.alarm import AlarmSeverity
from waggle.ids import TaskId
from waggle.messages.base import UtcDatetime

__all__ = ["InboxItem", "InboxKind"]


class InboxKind(Enum):
    """What kind of thing landed in a supervisor's inbox."""

    WAGGLE_MESSAGE = "WAGGLE_MESSAGE"  # A heartbeat, a task result, a request from a bee.
    ALARM = "ALARM"  # An AlarmRaised climbing the chain.
    QUESTION = "QUESTION"  # A Question waiting on an Answer.
    HUMAN_MESSAGE = "HUMAN_MESSAGE"  # A message from the operator, only ever in the Queen's inbox.
    TIMER = "TIMER"  # A scheduled wake-up (a Patrol tick, a heartbeat watchdog).
    WATCH_OBSERVATION = "WATCH_OBSERVATION"  # Something watch mode saw worth a look.


class InboxItem(BaseModel):
    """One thing waiting for a supervisor's attention, in the shape score_item scores.

    Wraps whatever the underlying message, Alarm or Question actually is (`payload`) with just
    the fields scoring needs, so the Attendant never has to know one kind's type from another's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    id: str = Field(
        description="A stable id for this item: an alarm id, a message id, or a "
        "synthesised one for a timer."
    )
    kind: InboxKind = Field(description="What kind of thing this item is.")
    received_at: UtcDatetime = Field(description="When this supervisor's inbox received it.")
    principal: str = Field(
        description="Who or what this item is from or about, looked up in "
        "WeightTable.principal_weights."
    )
    severity: AlarmSeverity | None = Field(
        description="Set when kind is ALARM; None for every other kind."
    )
    task_id: TaskId | None = Field(description="The task this item concerns, if any.")
    latency_budget_s: Annotated[float, Field(gt=0)] | None = Field(
        default=None,
        description="How urgent a reply or action is; shorter is more urgent. None means no "
        "budget.",
    )
    payload_kind: str = Field(
        description="A label for the wrapped payload's own type, e.g. the waggle kind "
        '"task.result", or a local label for a non-Waggle item.'
    )
    payload: object = Field(description="The underlying message, Alarm, Question or string.")
