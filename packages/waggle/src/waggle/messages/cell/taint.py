"""Define CellTaintOrder: the Queen orders an isolated Cell's Warden to taint its own memory.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). When the
Queen (the central orchestrator) isolates a Cell (a unit of compute) she labels the memory written
there tainted from the isolation's first cited event on, so it never reaches a prompt or a resumed
bee until a judge clears it (ADR-0043). A Virtual Cell's Warden (its always-on supervisor) keeps
its own memory store inside the Cell (ADR-0027), which her label on the Hive's tables cannot reach,
so she sends this order: the Warden runs the same setter over its own store, with the same scope
(the bees and tasks she names, from the instant she names) and the same cause (her `cell.isolated`
event). The order only narrows: a Warden that receives it labels memory and refuses to resume
from what it labelled; it never widens anything. Added in PROTOCOL_MINOR 8.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry under
    ``cell.taint_order``; built by the Queen's isolation path and read by a Cell's Warden; calls
    into waggle.ids and waggle.messages.base only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - The order names at least one bee or one task (validator): an order that names neither
      would label nothing, which is a sender's bug, not an order.

See Also:
    - docs/waggle/spec.md section 8.5 for the normative fields and bounds.
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    EventIdField,
    TaskIdField,
    UtcDatetime,
    WaggleMessage,
    id_validator,
)

MAX_TAINT_AUTHORS = 256  # Every bee one Cell ever ran, with room to spare.
MAX_TAINT_TASKS = 1_024  # Every task one Cell ever held, with room to spare.

__all__ = ["MAX_TAINT_AUTHORS", "MAX_TAINT_TASKS", "CellTaintOrder"]

# A bee whose memory the order reaches: a Worker (a sub-bee) or the Cell's own Warden.
_Author = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


class CellTaintOrder(WaggleMessage):
    """Label this Cell's own memory tainted: these bees' and tasks' items, from this instant on."""

    cell_id: CellIdField = Field(description="The isolated Cell; always the receiver's own.")
    cause_event_id: EventIdField = Field(
        description="The Queen's cell.isolated event, recorded as every label's cause."
    )
    suspect_at: UtcDatetime = Field(
        description="Memory written at or after this instant is suspect: the isolation's first "
        "cited event, or when the isolation began when it cites none."
    )
    authors: tuple[_Author, ...] = Field(
        default=(),
        max_length=MAX_TAINT_AUTHORS,
        description="The bees that ran on the Cell, whose own items are covered.",
    )
    task_ids: tuple[TaskIdField, ...] = Field(
        default=(),
        max_length=MAX_TAINT_TASKS,
        description="The tasks placed on the Cell, whose items are covered.",
    )
    reason: str = Field(
        min_length=1, max_length=MAX_REASON_CHARS, description="Why, naming ids only."
    )

    @model_validator(mode="after")
    def _names_something(self) -> CellTaintOrder:
        """Refuse an order that names neither a bee nor a task: it would label nothing."""
        if not self.authors and not self.task_ids:
            raise ValueError("a CellTaintOrder names at least one author or one task.")
        return self
