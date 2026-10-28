"""Tests for hivemind.queen.requeening.lost: a Warden gone while she runs, its work placed afresh.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/lost.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.lost for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.cell import CombShieldLevel
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.releases import drain_releases
from hivemind.queen.requeening import PendingRecovery, RequeenCounts, take_back_lost
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_warden_id

_AFTER_S = 30.0  # The book's lost_after_s in every test here.


async def _running_away(deps: QueenDeps) -> Task:
    """One task RUNNING on a Warden she placed it on, which is not attached."""
    (task,) = await deps.chamber.submit(make_graph_draft({"poem": ()}))
    away, cell = new_warden_id(deps.clock), new_cell_id(deps.clock)
    deps.dispatch.placed_on.add(away)
    await deps.chamber.assign(task.id, away, cell, "Placed.", bound_tier=CombShieldLevel.MEADOW)
    await deps.chamber.start(task.id)
    deps.requeening.lost_after_s = _AFTER_S
    return await deps.chamber.get(task.id)


def _advance(deps: QueenDeps, seconds: float) -> None:
    """Move the builder's FakeClock on, past the once-a-second read throttle too."""
    assert isinstance(deps.clock, FakeClock)
    deps.clock.advance(seconds)


async def test_a_warden_away_past_the_limit_is_lost_and_its_task_and_cell_given_back() -> None:
    deps, _link, _end = make_queen_deps()
    released: list[tuple[CellId, TaskOutcome]] = []

    async def on_task_finished(cell_id: CellId, outcome: TaskOutcome) -> None:
        released.append((cell_id, outcome))

    deps = dataclasses.replace(deps, on_task_finished=on_task_finished)
    task = await _running_away(deps)

    first = await take_back_lost(deps, ())  # Noted away from now; not lost yet.
    _advance(deps, _AFTER_S - 1)
    early = await take_back_lost(deps, ())
    _advance(deps, 2)
    counts = await take_back_lost(deps, ())
    await drain_releases(deps)

    assert (first, early) == (None, None)
    assert counts == RequeenCounts(given_back=1)
    assert (await deps.chamber.get(task.id)).status is TaskStatus.PENDING
    assert [(cell, outcome.status) for cell, outcome in released] == [
        (task.cell_id, TaskStatus.FAILED)
    ]
    assert deps.requeening.lost == {}


@pytest.mark.parametrize("why", ["came back", "cell held", "requeening settles"])
async def test_a_warden_is_never_lost_while_it_is_back_held_or_a_requeening_judges_it(
    why: str,
) -> None:
    deps, link, _end = make_queen_deps()
    task = await _running_away(deps)
    attached: tuple[WardenLink, ...] = ()
    if why == "came back":
        # Away at the first look, attached again (redialled) by the second.
        await take_back_lost(deps, ())
        assert task.warden_id is not None
        attached = (dataclasses.replace(link, warden_id=task.warden_id),)
    elif why == "cell held":
        deps.dispatch.held.hold(task.cell_id, "Its device is away.")  # type: ignore[arg-type]
    else:
        deps.requeening.pending = PendingRecovery(
            deadline=deps.clock.now(),
            expected=frozenset(),
            counts=RequeenCounts(),
            snapshot_at=None,
        )

    await take_back_lost(deps, attached)
    _advance(deps, _AFTER_S * 2)
    counts = await take_back_lost(deps, attached)

    assert counts is None
    assert (await deps.chamber.get(task.id)).status is TaskStatus.RUNNING
