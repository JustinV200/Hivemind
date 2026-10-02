"""Tests for hivemind.supervision.capping.commands: a command the gate runs itself, on its Cell.

Fits into the Hive:
    Mirrors src/hivemind/supervision/capping/commands.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.capping.commands for the module under test.
    - docs/adr/0060-commands-run-only-where-they-are-contained-or-fully-granted.md.
"""

from __future__ import annotations

import pytest
from builders.cells import make_capabilities, make_cell

from hivemind.cell import AccessLevel, CellIdentity, CellKind
from hivemind.guard import Enforcer, load_guard_policy, warden_principal
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.supervision.capping.commands import CommandAdmission, CommandGuard
from waggle.clock import FakeClock
from waggle.ids import HiveId, IdKind, NodeId, WardenId, new_id


def _guard() -> tuple[CommandGuard, MemoryPheromoneTrail, WardenId]:
    """Build a Warden's guard over the shipped policy and an in-memory trail."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = CellIdentity(
        hive_id=HiveId(new_id(IdKind.HIVE, clock)),
        node_id=NodeId(new_id(IdKind.NODE, clock)),
        actor="system",
    )
    warden_id = WardenId(new_id(IdKind.WARDEN, clock))
    enforcer = Enforcer(load_guard_policy(), trail, clock, identity)
    return CommandGuard(enforcer, warden_principal(warden_id)), trail, warden_id


@pytest.mark.parametrize(
    ("kind", "level", "contained"),
    [
        (CellKind.VIRTUAL, AccessLevel.FULL, True),
        (CellKind.REAL, AccessLevel.FULL, False),
        (CellKind.REAL, AccessLevel.SCRATCH, True),
    ],
)
async def test_a_command_may_run_where_the_cells_level_and_containment_allow_it(
    kind: CellKind, level: AccessLevel, contained: bool
) -> None:
    guard, trail, _warden = _guard()
    cell = make_cell(
        kind,
        access_level=level,
        capabilities=make_capabilities(processes_contained=contained),
    )

    refused = await CommandAdmission(cell, guard).refusal(("pytest", "-q"))

    assert refused is None
    assert await trail.query(TrailQuery()) == ()


async def test_a_command_on_an_uncontained_scratch_cell_is_refused_and_recorded() -> None:
    guard, trail, warden_id = _guard()

    refused = await CommandAdmission(make_cell(), guard).refusal(("pytest", "-q"))

    assert refused is not None
    assert "processes are not contained" in refused
    (event,) = await trail.query(TrailQuery())
    assert event.kind == "guard.denied"
    assert event.subject_id == warden_id
    assert event.payload["rule"] == "guard.access_level.scratch"
    assert event.payload["capability"] == "exec:pytest"


async def test_a_read_only_cell_refuses_a_command_even_when_contained() -> None:
    guard, _trail, _warden = _guard()
    cell = make_cell(
        access_level=AccessLevel.READ_ONLY,
        capabilities=make_capabilities(processes_contained=True),
    )

    refused = await CommandAdmission(cell, guard).refusal(("ls",))

    assert refused is not None


async def test_with_no_guard_a_refusal_still_refuses_and_names_the_way_out() -> None:
    refused = await CommandAdmission(make_cell()).refusal(())

    assert refused is not None
    assert "Virtual Cell, or FULL access" in refused
