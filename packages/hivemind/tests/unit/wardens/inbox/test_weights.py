"""Tests for hivemind.wardens.inbox.weights: the Warden's inbox classification.

Mirrors src/hivemind/wardens/inbox/weights.py (codingrules section 3: tests/unit mirrors src/
one-to-one).
"""

from __future__ import annotations

from hivemind.forage.slots import ModelSlot
from hivemind.supervision import InboxKind, Rebind, to_wire
from hivemind.wardens.inbox import to_inbox_item
from waggle.clock import FakeClock
from waggle.envelope import Hop, wrap
from waggle.ids import (
    new_alarm_id,
    new_hive_id,
    new_message_id,
    new_node_id,
    new_task_id,
    new_warden_id,
)
from waggle.messages.honey import HoneyResponse
from waggle.messages.supervision.oversight import Intervene


def test_an_intervene_classifies_as_a_waggle_message_carrying_its_task_id() -> None:
    # A Queen's lever arrives on the Queen link, which has no sub-bee sender: the task id the
    # classifier reads off the payload is the only way the Warden finds the sub-bee it targets.
    clock = FakeClock()
    task_id = new_task_id(clock)
    reason = "the primary binding keeps failing"
    action, slot = to_wire(Rebind(reason=reason, slot=ModelSlot.WORKER))
    message = Intervene(
        action=action,
        subject=None,
        task_id=task_id,
        slot=slot,
        binding="local_worker",
        alarm_id=new_alarm_id(clock),
        reason=reason,
    )
    hop = Hop(sender=new_hive_id(clock), recipient=new_warden_id(clock), node_id=new_node_id(clock))

    item = to_inbox_item(wrap(message, hop, clock=clock), principal="queen")

    assert item.kind is InboxKind.WAGGLE_MESSAGE
    assert item.task_id == task_id


def test_a_queen_reply_keeps_the_correlation_id_the_honey_relay_matches_on() -> None:
    """Roadmap step 7.8: the Queen's HoneyResponse names the forwarded query only here."""
    clock = FakeClock()
    hop = Hop(sender=new_hive_id(clock), recipient=new_warden_id(clock), node_id=new_node_id(clock))
    forwarded = new_message_id(clock)
    response = HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason="none"
    )

    item = to_inbox_item(wrap(response, hop, clock=clock, correlation_id=forwarded), "queen")

    assert item.correlation_id == forwarded
    assert item.task_id is None
