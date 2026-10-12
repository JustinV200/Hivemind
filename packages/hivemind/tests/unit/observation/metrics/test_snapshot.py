"""Test hivemind.observation.metrics.snapshot: every figure the endpoint exposes, as one value.

An empty Hive reads zeros and empty families; on a populated one Cells count by kind and status,
Wardens by where they run, grants and headroom sum, open Alarms count by kind and level, a bee's
episodes count inside the last hour only, tasks count under every status (zero included), and the
costliest goal-slot-provider series are the ones kept.

Fits into the Hive:
    Mirrors src/hivemind/observation/metrics/snapshot.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from builders.forage import make_grant
from builders.memory import make_episode
from builders.observation import after, make_cell_view, make_event, make_llm_call, make_warden_view

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HIVE_STAND_SOURCE, CellKind
from hivemind.hive import VirtualCellStatus
from hivemind.memory import EpisodeRecord
from hivemind.observation import (
    EPISODE_WINDOW,
    MAX_COST_SERIES,
    HeadroomView,
    MetricsInputs,
    MetricsSnapshot,
    ReserveView,
    SummaryInputs,
    cost_lines,
    grant_view,
    metrics_snapshot,
    open_alarms,
    summary_view,
)
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_task_id

_CLOCK = FakeClock()
_NOW = after(10_000)
_NO_HEADROOM = HeadroomView(sub_bees=0, shared_seats=0)  # A Hive with nothing free.
_NO_RESERVE = ReserveView(seats=0, memory_bytes=0, headroom_fraction=0.0)  # And nothing held.


def _inputs(**records: Any) -> MetricsInputs:  # noqa: ANN401 -- any record family, by name.
    """Inputs for a Hive with only the given records; the summary is derived from them."""
    cells, wardens = records.get("cells", ()), records.get("wardens", ())
    alarms, episodes = records.get("alarms", ()), records.get("episodes", ())
    return MetricsInputs(
        summary=summary_view(SummaryInputs(cells, wardens, [], episodes, alarms, _NOW)),
        cells=cells,
        wardens=wardens,
        alarms=alarms,
        grants=records.get("grants", ()),
        headroom=records.get("headroom", _NO_HEADROOM),
        reserve=records.get("reserve", _NO_RESERVE),
        episodes=episodes,
        task_counts=records.get("task_counts", {}),
        tool_promotions=records.get("tool_promotions", 0),
        denials=records.get("denials", 0),
        cost_lines=records.get("lines", ()),
        as_of=_NOW,
    )


def test_an_empty_hive_reads_zeros_and_empty_families() -> None:
    snapshot = metrics_snapshot(_inputs())

    assert (snapshot.cells, snapshot.wardens, snapshot.alarms, snapshot.episodes) == (
        [],
        [],
        [],
        [],
    )
    assert (snapshot.grants.live, snapshot.tool_promotions, snapshot.denials) == (0, 0, 0)
    assert snapshot.llm == []
    # Every status is present at zero, so a dashboard never has a missing series.
    assert [(t.status, t.count) for t in snapshot.tasks] == [
        (status.value.lower(), 0) for status in TaskStatus
    ]
    assert snapshot.summary.active_bees == 0
    assert MetricsSnapshot.model_validate(snapshot.model_dump()) == snapshot


def test_cells_wardens_and_the_summary_counters_agree_with_the_strip() -> None:
    stand_warden, virtual_warden = make_warden_view(), make_warden_view()
    cells = [
        make_cell_view(
            id=stand_warden.cell_id, warden_id=stand_warden.id, source=HIVE_STAND_SOURCE
        ),
        make_cell_view(
            id=virtual_warden.cell_id,
            warden_id=virtual_warden.id,
            kind=CellKind.VIRTUAL,
            virtual_status=VirtualCellStatus.GRANTED,
        ),
        make_cell_view(kind=CellKind.VIRTUAL, virtual_status=VirtualCellStatus.DORMANT),
    ]

    snapshot = metrics_snapshot(_inputs(cells=cells, wardens=[stand_warden, virtual_warden]))

    assert [(c.kind, c.status, c.count) for c in snapshot.cells] == [
        ("real", "active", 1),
        ("virtual", "dormant", 1),
        ("virtual", "granted", 1),
    ]
    assert [(w.location, w.count) for w in snapshot.wardens] == [("hive_stand", 1), ("on_cell", 1)]
    summary = snapshot.summary
    assert (summary.cells_total, summary.cells_real, summary.cells_virtual) == (3, 1, 2)
    assert (summary.active_wardens, summary.cells_active, summary.cells_inactive) == (2, 2, 1)


def test_grants_headroom_alarms_and_tasks_are_summed_and_counted() -> None:
    grants = [
        grant_view(
            make_grant(clock=_CLOCK, token_budget=1_000, tokens_spent=100, spend_budget=2.0)
        ),
        grant_view(make_grant(clock=_CLOCK, token_budget=500, tokens_spent=50, spent=0.5)),
    ]
    payload = {"kind": "WORKER_FAILED", "severity": "CRITICAL", "task_id": None}
    alarms = open_alarms(
        [
            make_event("alarm.raised", new_alarm_id(_CLOCK), after(1), payload=payload),
            make_event("alarm.raised", new_alarm_id(_CLOCK), after(2), payload=payload),
        ],
        {},
        {},
    )

    snapshot = metrics_snapshot(
        _inputs(
            grants=grants,
            alarms=alarms,
            task_counts={TaskStatus.RUNNING: 3, TaskStatus.FAILED: 1},
            headroom=HeadroomView(sub_bees=4, shared_seats=2),
            reserve=ReserveView(seats=1, memory_bytes=0, headroom_fraction=0.1),
            tool_promotions=2,
            denials=7,
        )
    )

    totals = snapshot.grants
    assert (totals.live, totals.tokens_issued, totals.tokens_spent) == (2, 1_500, 150)
    assert (totals.spend_issued_usd, totals.spent_usd) == (12.0, 0.5)
    assert (totals.headroom_sub_bees, totals.headroom_shared_seats, totals.reserve_seats) == (
        4,
        2,
        1,
    )
    assert [(a.kind, a.level, a.count) for a in snapshot.alarms] == [
        ("worker_failed", "critical", 2)
    ]
    by_status = {t.status: t.count for t in snapshot.tasks}
    assert (by_status["running"], by_status["failed"], by_status["pending"]) == (3, 1, 0)
    assert (snapshot.tool_promotions, snapshot.denials) == (2, 7)


def test_episodes_count_per_bee_inside_the_last_hour_only() -> None:
    def episode(principal: str, ago: timedelta, *, autopilot: bool) -> EpisodeRecord:
        """An episode ``ago`` before the snapshot's instant."""
        return make_episode(_CLOCK, principal=principal, is_autopilot=autopilot, at=_NOW - ago)

    episodes = [
        episode("queen", timedelta(minutes=5), autopilot=False),
        episode("queen", timedelta(minutes=6), autopilot=False),
        episode("queen", timedelta(minutes=7), autopilot=True),
        episode("warden_a", timedelta(minutes=1), autopilot=True),
        episode("warden_a", EPISODE_WINDOW + timedelta(minutes=1), autopilot=False),
    ]

    snapshot = metrics_snapshot(_inputs(episodes=episodes))

    assert [(e.principal, e.awake, e.autopilot) for e in snapshot.episodes] == [
        ("queen", 2, 1),
        ("warden_a", 0, 1),
    ]


def test_only_the_costliest_series_are_kept() -> None:
    calls = [
        make_llm_call(slot="WORKER", cost_usd=float(n), lane={"goal_id": new_task_id(_CLOCK)})
        for n in range(MAX_COST_SERIES + 5)
    ]

    snapshot = metrics_snapshot(_inputs(lines=cost_lines(calls, {})))

    assert len(snapshot.llm) == MAX_COST_SERIES
    assert min(line.cost_usd for line in snapshot.llm) == 5.0
