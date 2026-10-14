"""Unit: how the Nuc scenario reads the trail and listens at the Queen's door.

The claims the phase 11 exit criterion makes about a Nuc are claims of order and count over events:
the ceilings come before the order that uses them, a task ends once, a segment merged after a
reconnection brings nothing twice, no more bees run than the Warden's ceiling allows. The scenario
(`tests/e2e/test_phase11_exit_nuc.py`) proves them against a real container; what its helpers
answer, and that its tap on the Queen's door records and changes nothing, is checked here in
milliseconds over events built by hand.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises `e2e.swarm.colony.trail`
    and `e2e.swarm.colony.orders`; nothing here starts a container or touches a network.

Key invariants:
    - Events are built through their families' own models, so a kind or a payload the audit
      vocabulary would refuse fails here as it would on a real trail.
    - No test sleeps.

See Also:
    - tests/unit/swarm/test_colony_rig.py for the manifests and the container half of the rig.
"""

from __future__ import annotations

from types import SimpleNamespace

from builders.offline import make_wire
from builders.trail import make_trail, make_trail_event
from e2e.swarm.colony.orders import Tap, tap
from e2e.swarm.colony.trail import (
    about,
    after,
    bees_of,
    between,
    ends_of,
    grant_of,
    llm_calls,
    named,
    peak_bees,
    position,
    recorded_twice,
    repeated,
)

from hivemind.pheromone import LlmUsage
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import new_cell_id, new_device_id, new_grant_id, new_task_id, new_worker_id
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote, NucPromoted, RuntimeLevel

_CLOCK = FakeClock()
TASK, OTHER = new_task_id(_CLOCK), new_task_id(_CLOCK)
BEE_1, BEE_2, BEE_3 = new_worker_id(_CLOCK), new_worker_id(_CLOCK), new_worker_id(_CLOCK)
GRANT_A, GRANT_B = new_grant_id(_CLOCK), new_grant_id(_CLOCK)


def _bee(kind: str, bee: str, task: str = TASK) -> tuple[str, dict[str, object]]:
    """A `worker.*` event for `bee`, which names its task in its payload as a Warden's does."""
    return kind, {"subject_id": bee, "payload": {"task_id": task}}


def test_events_are_found_by_kind_by_task_and_by_time() -> None:
    trail = make_trail(
        [
            ("task.assigned", {"subject_id": TASK}),
            _bee("worker.started", BEE_1),
            ("task.assigned", {"subject_id": OTHER}),
            _bee("worker.done", BEE_1, OTHER),
        ]
    )

    assert [e.kind for e in named(trail, "worker.started", "worker.done")] == [
        "worker.started",
        "worker.done",
    ]
    assert [e.id for e in about(trail, TASK)] == [trail[0].id, trail[1].id]
    assert [e.id for e in after(trail, trail[2].at)] == [trail[2].id, trail[3].id]
    assert [e.id for e in between(trail, trail[1].at, trail[3].at)] == [trail[1].id, trail[2].id]


def test_the_position_of_an_event_orders_it_against_another_by_kind_and_payload() -> None:
    trail = make_trail(
        [
            ("forage.ceilings_set", {"payload": {"revision": 1}}),
            ("forage.source_added", {"payload": {"source_id": "small"}}),
            ("forage.source_added", {"payload": {"source_id": "extra"}}),
        ]
    )

    assert position(trail, "forage.ceilings_set") == 0
    assert position(trail, "forage.source_added", source_id="extra") == 2
    assert position(trail, "forage.source_added", source_id="rogue") is None
    assert position(trail, "forage.plan_written") is None


def test_an_event_recorded_twice_for_one_subject_is_found_and_two_bees_are_not_one() -> None:
    trail = make_trail(
        [
            ("task.succeeded", {"subject_id": TASK}),
            ("task.succeeded", {"subject_id": OTHER}),
            ("task.succeeded", {"subject_id": TASK}),
            _bee("worker.started", BEE_1),
            _bee("worker.started", BEE_2),  # The same task on a second bee: not a repeat.
        ]
    )

    found = repeated(trail, ("task.succeeded", "worker.started"))

    assert found == [("task.succeeded", TASK)]


def test_the_most_bees_at_once_counts_the_overlap_and_not_the_total() -> None:
    trail = make_trail(
        [
            _bee("worker.started", BEE_1),
            _bee("worker.started", BEE_2),
            _bee("worker.done", BEE_1),
            _bee("worker.started", BEE_3),
            _bee("worker.failed", BEE_2),
            _bee("worker.done", BEE_3),
        ]
    )

    assert peak_bees(trail) == 2
    assert peak_bees([]) == 0


def test_the_most_bees_of_one_task_at_once_leaves_the_other_tasks_out() -> None:
    trail = make_trail(
        [
            _bee("worker.started", BEE_1),
            _bee("worker.started", BEE_2, OTHER),
            _bee("worker.started", BEE_3),
            _bee("worker.done", BEE_1),
            _bee("worker.done", BEE_2, OTHER),
            _bee("worker.done", BEE_3),
        ]
    )

    assert peak_bees(trail) == 3  # What the Warden had running, whichever task they were for.
    assert peak_bees(trail, TASK) == 2  # Two bees at once on one task is a task run twice.
    assert peak_bees(trail, OTHER) == 1


def test_a_paused_bee_is_not_working_until_it_is_resumed() -> None:
    held = [
        _bee("worker.started", BEE_1),
        _bee("worker.paused", BEE_1),  # Held at a Handoff: no model call, no command.
        _bee("worker.started", BEE_2),  # Another takes the task up while it is held.
    ]

    # A held bee counted as running made a task that was moved look like one run twice.
    assert peak_bees(make_trail(held)) == 1
    assert peak_bees(make_trail([*held, _bee("worker.resumed", BEE_1)])) == 2
    # The resumed bee is the task's again, and found by the task its start named.
    assert peak_bees(make_trail([*held, _bee("worker.resumed", BEE_1)]), TASK) == 2


def test_an_event_said_twice_under_two_ids_is_found_and_the_same_words_later_are_not() -> None:
    clock = FakeClock()
    first = make_trail_event("task.succeeded", clock, subject_id=TASK)
    twin = make_trail_event("task.succeeded", clock, subject_id=TASK)  # Same instant, a new id.
    other = make_trail_event("task.succeeded", clock, subject_id=OTHER)
    clock.advance(1.0)
    later = make_trail_event("task.succeeded", clock, subject_id=TASK)  # Another moment: a retry.

    found = recorded_twice([first, twin, other, later])

    assert first.id != twin.id
    assert found == [(first.node_id, "task.succeeded", TASK)]
    assert recorded_twice([first, other, later]) == []


def test_the_bees_of_a_task_and_the_events_that_ended_them_are_found_by_worker() -> None:
    trail = make_trail(
        [
            _bee("worker.started", BEE_1),
            _bee("worker.started", BEE_2, OTHER),
            ("worker.done", {"subject_id": BEE_2}),
            ("worker.failed", {"subject_id": BEE_1}),
            _bee("worker.started", BEE_3),
        ]
    )

    assert [bee.subject_id for bee in bees_of(trail, TASK)] == [BEE_1, BEE_3]
    # A bee's end names no task: the worker that started for the task is how it is found.
    assert [(e.kind, e.subject_id) for e in ends_of(trail, TASK)] == [("worker.failed", BEE_1)]
    assert [end.kind for end in ends_of(trail, OTHER)] == ["worker.done"]


def test_the_grant_of_a_task_and_the_calls_made_under_it_are_found_by_what_answered() -> None:
    usage = LlmUsage(input_tokens=1, output_tokens=1, cached_tokens=0, cost_usd=0.0, priced=False)
    granted = ("forage.granted", {"subject_id": GRANT_A, "payload": {"task_id": TASK}})

    def call(provider: str, grant: str) -> tuple[str, dict[str, object]]:
        payload = {"grant_id": grant}
        fields = {"slot": "WORKER", "provider": provider, "usage": usage, "payload": payload}
        return "llm.call", fields

    trail = make_trail(
        [
            granted,
            call("nuc_local", GRANT_A),
            call("shared", GRANT_A),
            call("shared", GRANT_B),
        ]
    )

    assert grant_of(trail, TASK) == GRANT_A and grant_of(trail, OTHER) is None
    assert len(llm_calls(trail)) == 3
    assert [c.provider for c in llm_calls(trail, grant_id=GRANT_A)] == ["nuc_local", "shared"]
    assert len(llm_calls(trail, grant_id=GRANT_A, provider="shared")) == 1
    assert llm_calls(trail, grant_id=OTHER) == []


class _Desk:
    """Stands in for the Queen's `NucDesk`: answers every order PROMOTED with what was asked."""

    async def ask(self, warden: object, order: NucPromote, clock: object) -> NucPromoted:
        return NucPromoted(
            cell_id=order.cell_id,
            device_id=order.device_id,
            action=order.action,
            outcome=NucOutcome.PROMOTED,
            level=RuntimeLevel.NUC,
            server_id=order.server_id,
            loaded_models=order.initial_models,
            reason="Loaded.",
        )


class _Colonizer:
    """Stands in for the gate's `Colonizer`: consumes the frames it is told to."""

    def __init__(self) -> None:
        self._desk = _Desk()
        self.heard_by_gate: list[str] = []

    def heard(self, node: object, envelope: Envelope) -> bool:
        self.heard_by_gate.append(envelope.kind)
        return envelope.kind == "task.result"


def _hive(colonizer: _Colonizer, clock: FakeClock) -> SimpleNamespace:
    """The parts of a running Hive that the tap reaches into: its swarm gate and its clock."""
    return SimpleNamespace(
        swarm=SimpleNamespace(gate=SimpleNamespace(_colonizer=colonizer)), clock=clock
    )


def test_the_tap_records_what_the_door_hears_and_hands_it_on_unchanged() -> None:
    clock = FakeClock()
    colonizer = _Colonizer()
    record = tap(_hive(colonizer, clock))  # type: ignore[arg-type]
    wire = make_wire()
    result = wire.result()

    consumed = colonizer.heard(object(), result)
    passed_on = colonizer.heard(object(), wire.forage_request())

    assert (consumed, passed_on) == (True, False)  # The gate's own answers, as it gave them.
    assert colonizer.heard_by_gate == ["task.result", "forage.request"]
    assert [frame.kind for frame in record.frames] == ["task.result", "forage.request"]
    assert record.of("task.result")[0].task_id == record.frames[0].task_id
    # Kept by id too: that is how a frame sent again is told from a second one of the same words.
    assert record.frames[0].envelope_id == result.id
    assert record.of("task.result", "task_not_this_one") == []
    # Heard when the Hive's clock said so, which is what "after the link came back" compares with.
    assert {frame.heard_at for frame in record.frames} == {clock.now()}


async def test_the_tap_records_each_order_the_queen_sends_with_the_answer_it_got() -> None:
    clock = FakeClock()
    colonizer = _Colonizer()
    record: Tap = tap(_hive(colonizer, clock))  # type: ignore[arg-type]
    order = NucPromote(
        cell_id=new_cell_id(_CLOCK),
        device_id=new_device_id(_CLOCK),
        action=NucAction.PROMOTE,
        server_id="nuc_model",
        initial_models=("nuc_small",),
        deadline_s=60.0,
        reason="Because.",
    )

    reply = await colonizer._desk.ask(object(), order, clock)

    [asked] = record.orders
    assert (asked.order.server_id, asked.order.initial_models) == ("nuc_model", ("nuc_small",))
    assert asked.reply is reply
    assert asked.asked_at <= asked.answered_at
