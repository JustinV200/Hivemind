"""Test hivemind.observation.api.summary: the strip's counters, defined exactly.

An empty Hive reads all zeros; on a populated one a Warden counts as an active bee while ACTIVE or
WATCH, a sub-bee while RUNNING, a Cell is active only when its Warden is ACTIVE, Real and Virtual
sum to the total, and a bee is awake or autopilot by its newest episodes inside the window.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/summary.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from builders.memory import make_episode
from builders.observation import (
    T0,
    after,
    make_cell_view,
    make_event,
    make_sample,
    make_warden_view,
)

from hivemind.cell import CellKind
from hivemind.forage import ModelSlot
from hivemind.memory import EpisodeRecord
from hivemind.observation import (
    ACTIVITY_WINDOW_S,
    SummaryInputs,
    SummaryView,
    open_alarms,
    summary_view,
)
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_worker_id
from waggle.messages.supervision import WardenState, WorkerState

_CLOCK = FakeClock()
_NOW = after(1_000)


def _summary(**records: Any) -> SummaryView:  # noqa: ANN401 -- any one record family, by name.
    """The strip over an empty Hive with only the given record families filled in."""
    fields: dict[str, Any] = {
        "cells": [],
        "wardens": [],
        "samples": [],
        "episodes": [],
        "alarms": (),
        "as_of": _NOW,
    }
    fields.update(records)
    return summary_view(SummaryInputs(**fields))


def test_an_empty_hive_reads_all_zeros() -> None:
    view = _summary()

    assert view.model_dump(exclude={"as_of", "activity_window_s"}) == dict.fromkeys(
        [
            "active_bees",
            "active_wardens",
            "active_sub_bees",
            "cells_total",
            "cells_active",
            "cells_inactive",
            "cells_real",
            "cells_virtual",
            "bees_awake",
            "bees_autopilot",
            "open_alarms",
        ],
        0,
    )
    assert (view.as_of, view.activity_window_s) == (_NOW, ACTIVITY_WINDOW_S)


def test_bees_and_cells_are_counted_by_the_reported_states() -> None:
    working, watching, idle = (
        make_warden_view(state=state)
        for state in (WardenState.ACTIVE, WardenState.WATCH, WardenState.CLUSTERED)
    )
    cells = [
        make_cell_view(id=working.cell_id, warden_id=working.id),
        make_cell_view(id=watching.cell_id, warden_id=watching.id),
        make_cell_view(id=idle.cell_id, warden_id=idle.id, kind=CellKind.VIRTUAL),
        make_cell_view(kind=CellKind.VIRTUAL),  # No Warden attached.
    ]
    samples = [
        make_sample(working.id),
        make_sample(working.id, worker_id=new_worker_id(_CLOCK), worker_state=WorkerState.RUNNING),
        make_sample(working.id, worker_id=new_worker_id(_CLOCK), worker_state=WorkerState.DONE),
    ]

    view = _summary(cells=cells, wardens=[working, watching, idle], samples=samples)

    assert (view.active_wardens, view.active_sub_bees, view.active_bees) == (2, 1, 3)
    assert (view.cells_total, view.cells_active, view.cells_inactive) == (4, 1, 3)
    assert (view.cells_real, view.cells_virtual) == (2, 2)


def test_a_bee_is_awake_or_autopilot_by_its_recent_episodes_only() -> None:
    def episode(principal: str, ago: float, *, autopilot: bool) -> EpisodeRecord:
        """An episode ``ago`` seconds before the strip's instant."""
        return make_episode(
            _CLOCK,
            principal=principal,
            is_autopilot=autopilot,
            slot=ModelSlot.QUEEN,
            at=_NOW - timedelta(seconds=ago),
        )

    episodes = [
        episode("queen", 5, autopilot=False),  # Awake now...
        episode("queen", 6, autopilot=True),  # ...so its autopilot decision does not also count.
        episode("warden_a", 7, autopilot=True),  # Autopilot only.
        episode("warden_b", ACTIVITY_WINDOW_S + 30, autopilot=False),  # Too old to be current.
    ]

    view = _summary(episodes=episodes)

    assert (view.bees_awake, view.bees_autopilot) == (1, 1)


def test_open_alarms_are_counted() -> None:
    alarm_id = new_alarm_id(_CLOCK)
    payload = {"kind": "WORKER_FAILED", "severity": "INFO", "task_id": None}
    alarms = open_alarms([make_event("alarm.raised", alarm_id, T0, payload=payload)], {}, {})

    assert _summary(alarms=alarms).open_alarms == 1
