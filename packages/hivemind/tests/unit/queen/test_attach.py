"""Unit tests for hivemind.queen.attach: attach_warden and detach_warden.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/attach.py
    (codingrules section 3). Covers attach_warden's `warden_spawn` point (roadmap step 10.3) and
    detach_warden directly; hivemind.queen.cell_gate's own listener tests already exercise both
    end to end through a real connection.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.attach for detach_warden, the function under test.
    - hivemind.queen.queen for Queen.attach_warden, the edge this mirrors.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.queen import make_queen_deps, with_guard_policy

from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import WardenSpawnRefusedError
from hivemind.queen.attach import detach_warden
from hivemind.queen.queen import Queen
from waggle.ids import WardenId


async def test_attach_warden_records_warden_spawned_for_the_admitted_warden() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)

    await queen.attach_warden(link)

    [spawned] = await deps.trail.query(TrailQuery(kind="warden.spawned"))
    assert spawned.subject_id == link.warden_id
    assert spawned.payload == {"cell_id": link.cell.id}
    assert await deps.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_attach_warden_refused_warden_spawn_attaches_nothing() -> None:
    # A policy whose `queen` role lacks `warden:spawn`: the Guard refuses the admission.
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("forage:request",))})
    base, link, _end = make_queen_deps()
    deps = with_guard_policy(base, load_guard_policy(None, section))
    queen = Queen(deps)

    with pytest.raises(WardenSpawnRefusedError):
        await queen.attach_warden(link)

    assert queen.wardens == ()
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "warden_spawn"
    assert denial.payload["capability"] == "warden:spawn"
    assert await deps.trail.query(TrailQuery(kind="warden.spawned")) == ()


async def test_detach_warden_removes_an_attached_link() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    assert link.warden_id in {w.warden_id for w in queen.wardens}

    await detach_warden(queen, link.warden_id)

    assert link.warden_id not in {w.warden_id for w in queen.wardens}


async def test_detach_warden_reaps_the_links_reader_task() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    before = asyncio.all_tasks()
    # Attaching starts the link's own reader task (hivemind.queen.inbox.links.LinkReaders).
    await queen.attach_warden(link)
    assert len(asyncio.all_tasks() - before) == 1

    await detach_warden(queen, link.warden_id)

    assert asyncio.all_tasks() == before


async def test_detach_warden_is_a_no_op_for_an_unattached_id() -> None:
    deps, _link, _end = make_queen_deps()
    queen = Queen(deps)

    await detach_warden(queen, WardenId("warden_never_attached"))  # Must not raise.
