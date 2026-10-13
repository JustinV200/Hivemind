"""Test hivemind.observation.api.attendant: the inbox, task graph and what is next for a supervisor.

The Queen's reconstructed inbox is scored by the Attendant's own scorer and ordered highest first
(a critical Alarm over a human message over a question); a Warden sees only its own tasks, Cells
and questions and never the human's messages; the graph joins only tasks in view; finished tasks
list newest first; and up-next holds the ready PENDING tasks (a Warden's, its ASSIGNED ones).

Fits into the Hive:
    Mirrors src/hivemind/observation/api/attendant.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

from builders.observation import after, make_event
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import TaskStatus
from hivemind.observation import (
    QUEEN_PRINCIPAL,
    AttendantInputs,
    OpenAlarmView,
    attendant_view,
    open_alarms,
    reconstruct_inbox,
)
from hivemind.supervision.attendant import InboxKind
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_cell_id, new_task_id, new_warden_id

_CLOCK = FakeClock()
_NOW = after(600)


def _alarm(severity: str, task_id: str | None, cell_id: str | None) -> OpenAlarmView:
    """One open Alarm of ``severity``, placed on ``cell_id``."""
    alarm_id = new_alarm_id(_CLOCK)
    payload = {"kind": "WORKER_FAILED", "severity": severity, "task_id": task_id}
    [alarm] = open_alarms(
        [make_event("alarm.raised", alarm_id, after(10), payload=payload)], {}, {}
    )
    return alarm.model_copy(update={"cell_id": cell_id})


def _inputs(principal: str = QUEEN_PRINCIPAL, **fields: object) -> AttendantInputs:
    """Inputs for an empty Hive, with ``fields`` replacing the empty defaults."""
    base: dict[str, object] = {
        "principal": principal,
        "tasks": [],
        "alarms": (),
        "waiting_messages": [],
        "as_of": _NOW,
    }
    base.update(fields)
    return AttendantInputs(**base)  # type: ignore[arg-type]


def test_an_empty_hive_has_an_empty_reconstructed_view() -> None:
    view = attendant_view(_inputs())

    assert view.inbox_is_reconstructed
    assert (view.inbox, view.nodes, view.edges) == ([], [], [])
    assert (view.recently_finished, view.up_next) == ([], [])


def test_the_queens_inbox_is_ordered_by_the_attendants_own_scores() -> None:
    blocked = make_task(TaskStatus.BLOCKED, _CLOCK, updated_at=after(590))
    critical = _alarm("CRITICAL", None, None)
    view = attendant_view(
        _inputs(
            tasks=[blocked],
            alarms=[critical],
            waiting_messages=[("chat_one", after(5))],
        )
    )

    assert [(entry.kind, entry.id) for entry in view.inbox] == [
        (InboxKind.ALARM, critical.id),
        (InboxKind.HUMAN_MESSAGE, "chat_one"),
        (InboxKind.QUESTION, str(blocked.pending_question_id)),
    ]
    scores = [entry.score for entry in view.inbox]
    assert scores == sorted(scores, reverse=True)
    assert all(entry.reasons for entry in view.inbox)
    assert view.inbox[0].severity is not None
    assert view.inbox[2].task_id == blocked.id


def test_a_warden_sees_its_own_tasks_cells_and_questions_and_no_human_messages() -> None:
    warden_id, cell_id = new_warden_id(_CLOCK), new_cell_id(_CLOCK)
    mine = make_task(
        TaskStatus.BLOCKED, _CLOCK, warden_id=warden_id, cell_id=cell_id, updated_at=after(590)
    )
    other = make_task(TaskStatus.BLOCKED, _CLOCK)
    here = _alarm("WARNING", None, cell_id)
    elsewhere = _alarm("WARNING", None, new_cell_id(_CLOCK))

    inputs = _inputs(
        warden_id,
        tasks=[mine, other],
        alarms=[here, elsewhere],
        waiting_messages=[("chat_one", after(5))],
    )
    view = attendant_view(inputs)

    assert [node.task_id for node in view.nodes] == [mine.id]
    assert {entry.id for entry in view.inbox} == {here.id, str(mine.pending_question_id)}
    assert InboxKind.HUMAN_MESSAGE not in {entry.kind for entry in view.inbox}
    assert [item.id for item in reconstruct_inbox(inputs, [mine])] == [
        here.id,
        str(mine.pending_question_id),
    ]


def test_the_graph_joins_only_tasks_in_view_and_counts_each_ones_alarms() -> None:
    first = make_task(TaskStatus.RUNNING, _CLOCK)
    second = make_task(
        TaskStatus.PENDING, _CLOCK, spec=make_task_spec(depends_on=(first.id, new_task_id(_CLOCK)))
    )
    with_alarm = _alarm("INFO", first.id, None)

    view = attendant_view(_inputs(tasks=[second, first], alarms=[with_alarm]))

    assert [(edge.before, edge.after) for edge in view.edges] == [(first.id, second.id)]
    assert {node.task_id: node.open_alarms for node in view.nodes} == {first.id: 1, second.id: 0}


def test_finished_list_newest_first_and_up_next_holds_only_ready_pending_tasks() -> None:
    old = make_task(TaskStatus.SUCCEEDED, _CLOCK)
    new = make_task(TaskStatus.FAILED, _CLOCK, updated_at=old.updated_at + timedelta(seconds=5))
    running = make_task(TaskStatus.RUNNING, _CLOCK)
    ready = make_task(TaskStatus.PENDING, _CLOCK, created_at=after(1), updated_at=after(1))
    waiting = make_task(TaskStatus.PENDING, _CLOCK, spec=make_task_spec(depends_on=(running.id,)))
    after_done = make_task(
        TaskStatus.PENDING,
        _CLOCK,
        created_at=after(2),
        updated_at=after(2),
        spec=make_task_spec(depends_on=(old.id,)),
    )

    view = attendant_view(_inputs(tasks=[old, new, running, ready, waiting, after_done]))

    assert view.recently_finished == [new.id, old.id]
    assert view.up_next == [ready.id, after_done.id]
    assert waiting.id not in view.up_next
