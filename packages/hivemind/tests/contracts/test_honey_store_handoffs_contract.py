"""Contract suite for HoneyStore.handoff_nectars: a task's Handoffs from one Cell, three harnesses.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). States the one clause of
    hivemind.honey_store.store.protocol.HoneyStore's contract that the Queen's take-back of a lost
    colonized device depends on: the HANDOFF Nectar a task's bees deposited from one Cell, newest
    first, and never anything a Night Veil Cell keeps to itself (ADR-0030). It runs against the same
    three harnesses as `test_honey_store_contract.py` (`honey_store_contract_harness.open_harness`),
    in its own module so both stay under codingrules 5.1's 400-line test-file limit. The `harness`
    fixture stays local, as the harness module explains.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.store.protocol for the HoneyStore contract under test.
    - hivemind.queen.swarm_gate.recall for the one caller.
    - packages/hivemind/tests/contracts/honey_store_contract_harness.py for the shared harness.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from builders.honey import make_nectar_draft
from contracts.honey_store_contract_harness import (
    HARNESS_KINDS,
    Harness,
    nectar_events,
    open_harness,
    sha,
)

from hivemind.cell import CombShieldLevel
from hivemind.honey_store.models import NectarOrigin, NectarState
from waggle.ids import CellId, NectarId, TaskId, new_cell_id, new_event_id, new_task_id
from waggle.messages.honey import NectarKind

_LIMIT = 10  # More than any test deposits, so a limit is only ever the one a test sets.


@pytest.fixture(params=HARNESS_KINDS)
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    """Build the parametrised harness: a fresh honey store over an already-trailed connection."""
    return await open_harness(request.param, tmp_path)


async def _deposit(
    harness_: Harness, task_id: TaskId, cell_id: CellId, age_s: int = 0, **overrides: object
) -> NectarId:
    """Deposit one Handoff of `task_id` gathered on `cell_id`, `age_s` old; return its Nectar id."""
    event_id = new_event_id(harness_.clock)
    fields: dict[str, object] = {
        "kind": NectarKind.HANDOFF,
        "task_id": task_id,
        "cell_id": cell_id,
        "observed_at": harness_.clock.now() - timedelta(seconds=age_s),
        "content": f"a Handoff ({event_id})".encode(),
        "source_key": f"handoff:{event_id}",
        "event_id": event_id,
    }
    fields.update(overrides)
    draft = make_nectar_draft(clock=harness_.clock, **fields)
    added = await harness_.store.add_nectar(draft, sha(draft), nectar_events(harness_.clock))
    return added.nectar.id


async def test_a_tasks_handoffs_come_back_newest_observed_first(harness: Harness) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    oldest = await _deposit(harness, task, cell, age_s=30)
    newest = await _deposit(harness, task, cell, age_s=0)
    middle = await _deposit(harness, task, cell, age_s=10)

    found = await harness.store.handoff_nectars(task, cell, _LIMIT)

    # By when the checkpoint was written, not by when the row arrived: a late relay sorts last.
    assert [nectar.id for nectar in found] == [newest, middle, oldest]
    assert all(nectar.event_id is not None for nectar in found)


async def test_a_limit_keeps_the_newest_rows(harness: Harness) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    await _deposit(harness, task, cell, age_s=20)
    newest = await _deposit(harness, task, cell, age_s=0)
    next_newest = await _deposit(harness, task, cell, age_s=10)

    found = await harness.store.handoff_nectars(task, cell, 2)

    assert [nectar.id for nectar in found] == [newest, next_newest]


async def test_another_task_cell_or_kind_is_never_returned(harness: Harness) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    mine = await _deposit(harness, task, cell)
    await _deposit(harness, new_task_id(harness.clock), cell)  # Another task on the same Cell.
    await _deposit(harness, task, new_cell_id(harness.clock))  # The same task from another Cell.
    await _deposit(harness, task, cell, kind=NectarKind.FINDING, source_key=None, event_id=None)

    found = await harness.store.handoff_nectars(task, cell, _LIMIT)

    assert [nectar.id for nectar in found] == [mine]


async def test_a_handoff_ripened_out_of_the_queens_own_bee_bread_is_not_returned(
    harness: Harness,
) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    deposited = await _deposit(harness, task, cell, age_s=5)
    # Her own Bee Bread is hers already; only what a bee sent up is a copy she may be missing.
    await _deposit(harness, task, cell, origin=NectarOrigin.BEE_BREAD)

    found = await harness.store.handoff_nectars(task, cell, _LIMIT)

    assert [nectar.id for nectar in found] == [deposited]


async def test_a_task_with_no_handoff_has_none(harness: Harness) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)

    assert await harness.store.handoff_nectars(task, cell, _LIMIT) == ()


async def test_a_night_veil_cells_side_channel_is_never_returned(harness: Harness) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    ordinary = await _deposit(harness, task, cell, age_s=5)
    # What intake stores for a Night Veil Cell's Handoff: EPHEMERAL, no source key, its tier.
    ephemeral = await _deposit(
        harness,
        task,
        cell,
        source_key=None,
        origin_tier=CombShieldLevel.NIGHT_VEIL,
        ephemeral_cell_id=cell,
    )

    found = await harness.store.handoff_nectars(task, cell, _LIMIT)

    assert [nectar.id for nectar in found] == [ordinary]
    assert (await harness.store.get_nectar(ephemeral)).state is NectarState.EPHEMERAL


async def test_an_ephemeral_row_is_never_returned_whatever_tier_it_names(
    harness: Harness,
) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    # A row the Cell's teardown purges is not one to resume from, tier or no tier.
    await _deposit(harness, task, cell, source_key=None, ephemeral_cell_id=cell)

    assert await harness.store.handoff_nectars(task, cell, _LIMIT) == ()


async def test_a_row_of_the_night_veil_tier_is_never_returned_even_when_ordinary(
    harness: Harness,
) -> None:
    task, cell = new_task_id(harness.clock), new_cell_id(harness.clock)
    # A tier is the Queen's own record of the Cell; whatever else is true of the row, a Night Veil
    # Cell's memory does not leave it.
    await _deposit(harness, task, cell, origin_tier=CombShieldLevel.NIGHT_VEIL)

    assert await harness.store.handoff_nectars(task, cell, _LIMIT) == ()
