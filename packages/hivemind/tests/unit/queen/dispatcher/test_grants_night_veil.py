"""Tests for hivemind.queen.dispatcher.grants under Night Veil: local bindings only (10.3a).

A grant issued for a Night Veil task keeps only the bindings local to its Cell: a source the Cell
serves itself, or a provider that runs in process. Every other binding is removed, one
`guard.denied` each under the Night Veil floor, whatever either set holds.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/grants.py (codingrules section 5.1: split by feature
    from test_grants.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.grants for the module under test.
"""

from __future__ import annotations

import dataclasses

from builders.cells import make_cell
from builders.forage import make_grant, make_source
from builders.queen import make_queen_deps
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import Task
from hivemind.cell import CellKind, CombShieldLevel, Isolation, TaskNeeds
from hivemind.forage import ForageGrant, ForageMap, ModelSlot
from hivemind.forage.models import AllowedBinding, SeatReservation
from hivemind.forage.slots import Effort
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.grants import authorize_grant
from waggle.clock import FakeClock

_LOCAL_RULE = "guard.tier_floor.night_veil_local_slots"


def _night_veil(tier: CombShieldLevel = CombShieldLevel.NIGHT_VEIL) -> tuple[QueenDeps, WardenLink]:
    """A Queen whose one Warden is on a Virtual Cell at `tier`, with three sources on her map."""
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=tier)
    sources = [
        make_source(source_id="on-cell", provider="cell-server", host_cell_id=cell.id),
        make_source(source_id="in-process", provider="fake"),
        make_source(source_id="hosted", provider="vendor"),
    ]
    deps, link, _end = make_queen_deps(clock, cell=cell, map=ForageMap(sources, clock=clock))
    return dataclasses.replace(deps, in_process_providers=frozenset({"fake"})), link


def _task(tier: CombShieldLevel = CombShieldLevel.NIGHT_VEIL) -> Task:
    """A task asking for `tier`."""
    isolation = Isolation.REQUIRED if tier is CombShieldLevel.NIGHT_VEIL else Isolation.PREFERRED
    needs = TaskNeeds(comb_shield=tier, isolation=isolation)
    return make_task(spec=make_task_spec(needs=needs))


def _grant() -> ForageGrant:
    """A grant binding WORKER on the Cell, JUDGE in process and QUEEN on the hosted source."""
    bindings = (
        AllowedBinding(slot=ModelSlot.WORKER, source_id="on-cell", max_effort=Effort.MEDIUM),
        AllowedBinding(slot=ModelSlot.JUDGE, source_id="in-process", max_effort=Effort.MEDIUM),
        AllowedBinding(slot=ModelSlot.ATTENDANT, source_id="hosted", max_effort=Effort.MEDIUM),
    )
    seats = tuple(SeatReservation(source_id=b.source_id, seats=1) for b in bindings)
    return make_grant(allowed=bindings, seats=seats)


async def test_a_night_veil_grant_keeps_the_cells_own_and_in_process_bindings_only() -> None:
    deps, link = _night_veil()

    kept = await authorize_grant(deps, link, _task(), _grant())

    assert [binding.source_id for binding in kept.allowed] == ["on-cell", "in-process"]
    assert [reservation.source_id for reservation in kept.seats] == ["on-cell", "in-process"]
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["rule"] == _LOCAL_RULE
    assert denial.payload["capability"] == "llm:attendant"


async def test_a_source_the_map_no_longer_knows_is_never_local() -> None:
    deps, link = _night_veil()
    unknown = AllowedBinding(slot=ModelSlot.WORKER, source_id="gone", max_effort=Effort.MEDIUM)

    kept = await authorize_grant(deps, link, _task(), make_grant(allowed=(unknown,), seats=()))

    assert kept.allowed == ()


async def test_a_meadow_grant_keeps_every_binding_its_sets_allow() -> None:
    deps, link = _night_veil(CombShieldLevel.MEADOW)
    fresh = _grant()

    kept = await authorize_grant(deps, link, _task(CombShieldLevel.MEADOW), fresh)

    assert kept is fresh
