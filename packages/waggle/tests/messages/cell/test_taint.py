"""Tests for waggle.messages.cell.taint: CellTaintOrder, the Queen's order to taint a Cell's store.

Fits into the Hive:
    Mirrors packages/waggle/src/waggle/messages/cell/taint.py (codingrules section 3); the
    registry, the codec round trip and the spec drift are covered with the rest of the family.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/waggle/spec.md section 8.5, "CellTaintOrder (PROTOCOL_MINOR 10)".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import PROTOCOL_VERSION, Hop, wrap
from waggle.ids import IdKind, new_id, new_node_id
from waggle.messages.cell.taint import MAX_TAINT_AUTHORS, MAX_TAINT_TASKS, CellTaintOrder

CLOCK = FakeClock()


def _order(**overrides: object) -> CellTaintOrder:
    """A valid order naming the Cell's Warden and one task, with `overrides` applied."""
    fields: dict[str, object] = {
        "cell_id": new_id(IdKind.CELL, CLOCK),
        "cause_event_id": new_id(IdKind.EVENT, CLOCK),
        "suspect_at": CLOCK.now(),
        "authors": (new_id(IdKind.WARDEN, CLOCK),),
        "task_ids": (new_id(IdKind.TASK, CLOCK),),
        "reason": "Cell isolated by the queen on a Guard report.",
    }
    fields.update(overrides)
    return CellTaintOrder.model_validate(fields)


def test_an_order_naming_neither_a_bee_nor_a_task_is_refused() -> None:
    # It would label nothing: a sender's bug, never an order.
    with pytest.raises(ValidationError, match="at least one author or one task"):
        _order(authors=(), task_ids=())


def test_an_author_is_a_worker_or_a_warden_never_anything_else() -> None:
    _order(authors=(new_id(IdKind.WORKER, CLOCK),), task_ids=())  # A sub-bee alone is enough.

    with pytest.raises(ValidationError):
        _order(authors=(new_id(IdKind.TASK, CLOCK),))


@pytest.mark.parametrize(
    ("field", "kind", "bound"),
    [("authors", IdKind.WORKER, MAX_TAINT_AUTHORS), ("task_ids", IdKind.TASK, MAX_TAINT_TASKS)],
)
def test_the_named_bees_and_tasks_are_bounded(field: str, kind: IdKind, bound: int) -> None:
    ids = tuple(new_id(kind, CLOCK) for _ in range(bound + 1))

    with pytest.raises(ValidationError):
        _order(**{field: ids})


def test_an_order_crosses_the_codec_unchanged_at_minor_eight() -> None:
    codec = Codec()
    order = _order()
    hop = Hop(
        sender=new_id(IdKind.HIVE, CLOCK),
        recipient=new_id(IdKind.WARDEN, CLOCK),
        node_id=new_node_id(CLOCK),
    )
    envelope = wrap(order, hop, clock=CLOCK)

    decoded = codec.decode(codec.encode(envelope))

    assert decoded.payload == order
    assert decoded.kind == "cell.taint_order"
    assert decoded.version == PROTOCOL_VERSION
