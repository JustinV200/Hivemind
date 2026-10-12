"""Test hivemind.observation.api.cell_page: everything one Cell's page draws, and no words.

The Warden heads the bee list with its gauge, each sub-bee shows its task's role and state; the
Exoskeleton flag follows the newest attach or detach edge (null before any); the Warden's grants
total issued against used and a request with no answer is pending; Cell Wax shows severity,
proposer and expiry but never text; Alarms and Capping are this Cell's only; and the Patrol report
is null until phase 11.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/cell_page.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.forage import make_grant
from builders.memory import make_cell_wax
from builders.observation import after, make_cell_view, make_event, make_sample, make_warden_view
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import TaskStatus
from hivemind.memory import WaxSeverity, WaxState
from hivemind.observation import (
    CellPageInputs,
    capping_view,
    cell_page,
    grant_view,
    hosting_facts,
    open_alarms,
    pending_requests,
)
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_cell_id, new_grant_id, new_message_id, new_worker_id
from waggle.messages.supervision import WorkerState
from waggle.messages.task import WorkerRole

_CLOCK = FakeClock()
_NOW = after(600)


def _inputs(**fields: object) -> CellPageInputs:
    """Inputs for a Cell with nothing known about it, with ``fields`` replacing the defaults."""
    base: dict[str, object] = {
        "cell": make_cell_view(),
        "warden": None,
        "tasks": [],
        "samples": [],
        "grants": [],
        "forage_events": [],
        "wax": [],
        "alarms": (),
        "capping": capping_view([]),
        "hosting": hosting_facts([], [], []),
        "exoskeleton_edge": None,
        "lease_since": None,
        "as_of": _NOW,
    }
    base.update(fields)
    return CellPageInputs(**base)  # type: ignore[arg-type]


def test_a_cell_with_nothing_known_is_a_page_of_nulls_and_empty_lists() -> None:
    page = cell_page(_inputs())

    assert page.warden is None and page.forage is None
    assert (page.bees, page.tasks, page.wax, page.alarms) == ([], [], [], [])
    assert (page.exoskeleton_attached, page.last_patrol_at, page.lease_age_s) == (None, None, None)
    assert page.hosting.models_from == "unknown"
    assert page.honey_folder == f"/cells/{page.cell.id}"
    assert page.capping.in_flight == []


def test_the_warden_heads_the_bees_and_each_sub_bee_shows_role_state_and_gauge() -> None:
    warden = make_warden_view()
    task = make_task(TaskStatus.RUNNING, _CLOCK, spec=make_task_spec(role=WorkerRole.SCOUT))
    sub_bee, roleless = new_worker_id(_CLOCK), new_worker_id(_CLOCK)
    cell = make_cell_view(id=warden.cell_id, warden_id=warden.id, current_tasks=[task.id])
    samples = [
        make_sample(warden.id, tokens_used=1_000, context_window=4_000),
        make_sample(
            warden.id,
            worker_id=sub_bee,
            worker_state=WorkerState.RUNNING,
            task_id=task.id,
            tokens_used=4_000,
            context_window=4_000,
        ),
        make_sample(warden.id, worker_id=roleless, worker_state=WorkerState.PAUSED),
    ]

    page = cell_page(_inputs(cell=cell, warden=warden, tasks=[task], samples=samples))

    assert [(bee.id, bee.role, bee.status) for bee in page.bees] == [
        (warden.id, "WARDEN", "ACTIVE"),
        (sub_bee, "SCOUT", "RUNNING"),
        (roleless, "UNKNOWN", "PAUSED"),
    ]
    assert [bee.context_fraction for bee in page.bees] == [0.25, 1.0, 0.25]
    assert page.bees[1].task_id == task.id
    assert page.thought_principals == [warden.id, sub_bee, roleless]
    assert [view.id for view in page.tasks] == [task.id]


def test_a_warden_that_has_not_reported_still_heads_the_list_with_no_gauge() -> None:
    warden = make_warden_view(state=None)
    page = cell_page(_inputs(cell=make_cell_view(warden_id=warden.id), warden=warden))

    [bee] = page.bees
    assert (bee.id, bee.status, bee.context_fraction) == (warden.id, "UNKNOWN", None)


def test_the_exoskeleton_flag_follows_the_newest_edge() -> None:
    attached = cell_page(_inputs(exoskeleton_edge="cell.exoskeleton_attached"))
    detached = cell_page(_inputs(exoskeleton_edge="cell.exoskeleton_detached"))

    assert (attached.exoskeleton_attached, detached.exoskeleton_attached) == (True, False)


def test_the_wardens_grants_total_and_an_unanswered_request_is_pending() -> None:
    warden = make_warden_view()
    one = make_grant(clock=_CLOCK, holder=warden.id, token_budget=1_000, tokens_spent=250)
    two = make_grant(clock=_CLOCK, holder=warden.id, token_budget=500, tokens_spent=50)
    answered, waiting = new_grant_id(_CLOCK), new_grant_id(_CLOCK)
    events = [
        make_event("forage.requested", answered, after(1), payload={"request_kind": "SPEND"}),
        make_event("forage.granted", answered, after(2)),
        make_event("forage.requested", waiting, after(3), payload={"request_kind": "SUB_BEES"}),
    ]

    page = cell_page(
        _inputs(
            cell=make_cell_view(warden_id=warden.id),
            warden=warden,
            grants=[grant_view(one), grant_view(two)],
            forage_events=events,
        )
    )

    assert page.forage is not None
    assert (page.forage.token_budget, page.forage.tokens_spent) == (1_500, 300)
    assert [(p.grant_id, p.request_kind) for p in page.forage.pending_requests] == [
        (waiting, "SUB_BEES")
    ]
    assert pending_requests([]) == []


def test_wax_shows_who_and_until_when_but_never_its_text() -> None:
    cell = make_cell_view()
    block = make_cell_wax(
        _CLOCK, cell_id=cell.id, severity=WaxSeverity.BLOCK, expires_at=after(900)
    )
    note = make_cell_wax(_CLOCK, cell_id=cell.id, severity=WaxSeverity.NOTE)
    proposed = make_cell_wax(
        _CLOCK,
        cell_id=cell.id,
        state=WaxState.PROPOSED,
        decided_by=None,
        decided_at=None,
        severity=WaxSeverity.CAUTION,
    )
    lapsed = make_cell_wax(_CLOCK, cell_id=cell.id, expires_at=after(10))

    page = cell_page(_inputs(cell=cell, wax=[note, lapsed, proposed, block]))

    assert [(w.id, w.severity) for w in page.wax] == [
        (block.id, WaxSeverity.BLOCK),
        (proposed.id, WaxSeverity.CAUTION),
        (note.id, WaxSeverity.NOTE),
    ]
    assert page.wax[0].expires_at == after(900)
    assert "text" not in page.model_dump_json()


def test_alarms_and_capping_are_this_cells_only() -> None:
    cell = make_cell_view()
    other = new_cell_id(_CLOCK)
    mine, theirs = new_alarm_id(_CLOCK), new_alarm_id(_CLOCK)
    payload = {"kind": "WORKER_FAILED", "severity": "WARNING", "task_id": None}
    alarms = open_alarms(
        [
            make_event("alarm.raised", mine, after(1), actor="system", payload=payload),
            make_event("alarm.raised", theirs, after(2), actor="system", payload=payload),
        ],
        {},
        {},
    )
    placed = [
        alarm.model_copy(update={"cell_id": c})
        for alarm, c in zip(alarms, (cell.id, other), strict=True)
    ]
    proposals = [
        make_event(
            "capping.proposed",
            new_message_id(_CLOCK),
            after(n),
            payload={"task_id": None, "cell_id": target, "tier": "SCRATCH_WRITE"},
        )
        for n, target in ((1, cell.id), (2, other))
    ]

    page = cell_page(_inputs(cell=cell, alarms=placed, capping=capping_view(proposals)))

    assert [alarm.id for alarm in page.alarms] == [mine]
    assert [row.cell_id for row in page.capping.in_flight] == [cell.id]
