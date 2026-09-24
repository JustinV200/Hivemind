"""Tests for hivemind.queen.inbox.weights: queen_attendant and to_inbox_item.

Fits into the Hive:
    Mirrors src/hivemind/queen/inbox/weights.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.inbox.weights for the module under test.
"""

from __future__ import annotations

from hivemind.queen.inbox.weights import HUMAN_PRINCIPAL, human_inbox_item, to_inbox_item
from hivemind.supervision.attendant import InboxKind
from waggle.clock import FakeClock
from waggle.envelope import Hop, wrap
from waggle.ids import (
    new_cell_id,
    new_device_id,
    new_hive_id,
    new_node_id,
    new_task_id,
    new_warden_id,
)
from waggle.messages.cell import CellWaxProposed
from waggle.messages.cell.wax import WaxOrigin, WaxSeverity
from waggle.messages.control import HumanMessage
from waggle.messages.labels import HoneyClearance


def test_cell_wax_proposed_classifies_as_a_low_weight_waggle_message() -> None:
    """Roadmap step 4.2a: "a proposal is an inbox item the Attendant scores low"."""
    clock = FakeClock()
    hive_id, node_id, warden_id = new_hive_id(clock), new_node_id(clock), new_warden_id(clock)
    task_id = new_task_id(clock)
    hop = Hop(sender=warden_id, recipient=hive_id, node_id=node_id)
    proposed = CellWaxProposed(
        cell_id=new_cell_id(clock),
        severity=WaxSeverity.CAUTION,
        text="This Cell's disk fills up under heavy load.",
        reason="Saw two ENOSPC failures in a row.",
        clearance=HoneyClearance.C1,
        expires_at=None,
        origin=WaxOrigin.BEE,
        proposer=warden_id,
        task_id=task_id,
    )
    envelope = wrap(proposed, hop, clock=clock)

    item = to_inbox_item(envelope, str(warden_id))

    # WAGGLE_MESSAGE, not its own InboxKind: scored the same low base weight as routine traffic
    # (queen_default's own WAGGLE_MESSAGE weight sits well below ALARM/HUMAN_MESSAGE/QUESTION).
    assert item.kind is InboxKind.WAGGLE_MESSAGE
    assert item.severity is None
    assert item.task_id == task_id
    assert item.payload is proposed


def test_a_human_message_is_its_own_kind_under_the_human_principal() -> None:
    clock = FakeClock()
    device_id = new_device_id(clock)
    message = HumanMessage(text="Is it done?", task_id=new_task_id(clock), device_id=device_id)

    item = human_inbox_item(message, "chat_1", clock.now())

    assert item.kind is InboxKind.HUMAN_MESSAGE
    assert (item.id, item.principal, item.task_id) == ("chat_1", HUMAN_PRINCIPAL, message.task_id)
    assert item.payload is message
