"""Tests for hivemind.wardens.offline.policy: what a cut-off Warden's link does with each message.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/policy.py (codingrules section 3). The table is pure
    data, so every row of it is tested on its own: the messages that queue, the one that is held,
    the one that is refused, those that are dropped, and the rule that a kind nobody named is
    refused as it is on a dead link.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.policy for the module under test.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the rows' reasons.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from builders.offline import Wire, make_wire

from hivemind.wardens.offline import OfflineAction, OfflineRefusedError, decide_offline, owes_reply
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError
from waggle.ids import new_cell_id, new_device_id
from waggle.messages.control import Ping
from waggle.messages.swarm import NucAction, NucOutcome, NucPromoted, RuntimeLevel
from waggle.messages.task import TaskStage

_Build = Callable[[Wire], Envelope]

_QUEUE = OfflineAction.QUEUE
_ROWS: list[tuple[str, _Build, OfflineAction]] = [
    ("result", lambda w: w.result(), _QUEUE),
    ("checkpoint", lambda w: w.progress(TaskStage.CHECKPOINTED), _QUEUE),
    ("paused", lambda w: w.progress(TaskStage.PAUSED), _QUEUE),
    ("started", lambda w: w.progress(TaskStage.STARTED), OfflineAction.DROP),
    ("working", lambda w: w.progress(TaskStage.WORKING), OfflineAction.DROP),
    ("resumed", lambda w: w.progress(TaskStage.RESUMED), OfflineAction.DROP),
    ("alarm", lambda w: w.alarm(), _QUEUE),
    ("forage request", lambda w: w.forage_request(), _QUEUE),
    ("lease released", lambda w: w.lease_released(), _QUEUE),
    ("question", lambda w: w.question(), OfflineAction.HOLD),
    ("cell request", lambda w: w.cell_request(), OfflineAction.REFUSE),
    ("heartbeat", lambda w: w.heartbeat(), OfflineAction.DROP),
    ("cell heartbeat", lambda w: w.cell_heartbeat(), OfflineAction.DROP),
]


@pytest.mark.parametrize(
    ("build", "action"), [(r[1], r[2]) for r in _ROWS], ids=[r[0] for r in _ROWS]
)
def test_each_row_of_the_table_gives_its_action(build: _Build, action: OfflineAction) -> None:
    assert decide_offline(build(make_wire()).payload) is action


def test_a_kind_the_table_does_not_name_is_refused_as_on_a_dead_link() -> None:
    envelope = make_wire().wrap(Ping())

    assert decide_offline(envelope.payload) is OfflineAction.REFUSE


def test_the_decision_depends_on_the_message_and_nothing_else() -> None:
    wire = make_wire()

    first = decide_offline(wire.result().payload)
    second = decide_offline(wire.result().payload)

    assert first is second is OfflineAction.QUEUE


@pytest.mark.parametrize(
    ("build", "owed"),
    [
        (lambda w: w.forage_request(), True),
        (lambda w: w.question(), True),
        (lambda w: w.cell_request(), True),
        (lambda w: w.result(), False),
        (lambda w: w.alarm(), False),
        (lambda w: w.progress(), False),
    ],
    ids=["forage", "question", "cell-request", "result", "alarm", "progress"],
)
def test_only_a_request_is_owed_a_reply(build: _Build, owed: bool) -> None:
    assert owes_reply(build(make_wire()).kind) is owed


def test_a_refusal_reads_as_a_lost_connection_to_every_guarded_send() -> None:
    refusal = OfflineRefusedError("A cell.request may not wait for the Queen.")

    # Every send already guarded against a dead link (`wardens.links.send_guarded`) catches it.
    assert isinstance(refusal, ConnectionLostError)
    assert OfflineRefusedError.code == "hive.wardens.offline_refused"


def test_the_answer_to_a_promotion_order_is_dropped_not_kept_for_the_return() -> None:
    # The Queen waits a bounded time for it; one sent after the link came back describes a move
    # she has already undone, so it is forgotten, not replayed.
    clock = FakeClock()
    reply = NucPromoted(
        cell_id=new_cell_id(clock),
        device_id=new_device_id(clock),
        action=NucAction.DEMOTE,
        outcome=NucOutcome.DEMOTED,
        level=RuntimeLevel.COLONIZED,
        server_id=None,
        loaded_models=(),
        reason="No model server runs on this Cell.",
    )

    assert decide_offline(reply) is OfflineAction.DROP
