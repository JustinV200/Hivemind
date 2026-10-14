"""Unit tests for hivemind.queen.attach.admit: attach_warden and detach_warden.

A Warden the Queen already knows is a reconnection (roadmap 11.9, ADR-0053), not a stranger: the
new link replaces the old and its reader is reaped, `warden.reconnected` is recorded instead of
`warden.spawned`, the dispatcher's hold on its Cell is lifted, and the ceilings, plan and live
grants the Forage ledger holds for it are sent again over the new link, with the counts on the
event and none of the content. "Known" holds across a detach, so a Warden whose Cell was torn down
and who dials again is met as the Warden it is.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/attach/admit.py
    (codingrules section 3). Covers attach_warden's `warden_spawn` point (roadmap step 10.3), its
    reconnection and detach_warden directly; hivemind.queen.cell_gate's own listener tests already
    exercise the attach end to end through a real connection.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.attach for detach_warden, the function under test.
    - hivemind.queen.queen for Queen.attach_warden, the edge this mirrors.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from builders.forage import make_grant
from builders.queen import WardenEnd, make_queen_deps, make_warden_link, with_guard_policy
from builders.supervision import make_telemetry

from hivemind.forage.grant_state import GrantState
from hivemind.forage.models.pools import Ceilings
from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import WardenSpawnRefusedError
from hivemind.queen.attach import detach_warden, warden_state
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage.ceilings import set_ceilings
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.ids import WardenId, new_node_id
from waggle.messages.supervision import Heartbeat, WardenState


async def test_attach_warden_records_warden_spawned_for_the_admitted_warden() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)

    await queen.attach_warden(link)

    [spawned] = await deps.trail.query(TrailQuery(kind="warden.spawned"))
    assert spawned.subject_id == link.warden_id
    assert spawned.payload == {"cell_id": link.cell.id}
    assert await deps.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_attach_warden_names_the_node_its_link_proved() -> None:
    # Roadmap step 10.6: the Queen's own record of which node speaks for which Cell.
    deps, link, _end = make_queen_deps()
    proved = dataclasses.replace(link, node_id=new_node_id(FakeClock()))

    await Queen(deps).attach_warden(proved)

    [spawned] = await deps.trail.query(TrailQuery(kind="warden.spawned"))
    assert spawned.payload == {"cell_id": link.cell.id, "node_id": proved.node_id}


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


def _again(deps: QueenDeps, link: WardenLink) -> tuple[WardenLink, WardenEnd]:
    """The same Warden dialling again: a fresh link and its far end, on the same ids."""
    return make_warden_link(
        deps.identity.hive_id, link.warden_id, link.hop.node_id, link.cell, deps.clock
    )


def _ceilings() -> Ceilings:
    """Ceilings with no loadable sources, so a Warden holds something the Queen can resend."""
    return Ceilings(
        max_sub_bees=4,
        model_vram_bytes=1_000,
        model_disk_bytes=2_000,
        loadable_sources=(),
        exportable_seats=1,
    )


async def test_a_new_warden_is_not_a_reconnection_and_a_known_one_is() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    again, _again_end = _again(deps, link)

    first = await queen.attach_warden(link)
    second = await queen.attach_warden(again)

    assert (first, second) == (False, True)


async def test_a_reconnection_records_warden_reconnected_and_not_warden_spawned_again() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    again, _again_end = _again(deps, link)

    await queen.attach_warden(again)

    assert len(await deps.trail.query(TrailQuery(kind="warden.spawned"))) == 1
    [back] = await deps.trail.query(TrailQuery(kind="warden.reconnected"))
    assert back.subject_id == link.warden_id
    assert back.payload == {"cell_id": link.cell.id, "ceilings": False, "plan": False, "grants": 0}


async def test_a_reconnection_names_the_node_its_new_link_proved() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    again, _again_end = _again(deps, link)
    moved = dataclasses.replace(again, node_id=new_node_id(FakeClock()))

    await queen.attach_warden(moved)

    [back] = await deps.trail.query(TrailQuery(kind="warden.reconnected"))
    assert back.payload["node_id"] == moved.node_id  # A migrated Warden speaks as its new node.


async def test_a_reconnection_replaces_the_link_and_reaps_the_old_reader() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    before = asyncio.all_tasks()
    await queen.attach_warden(link)
    again, _again_end = _again(deps, link)

    await queen.attach_warden(again)

    assert queen.wardens == (again,)
    assert len(asyncio.all_tasks() - before) == 1  # One reader, for the new link only.
    await detach_warden(queen, link.warden_id)
    assert asyncio.all_tasks() == before


async def test_a_warden_dialling_again_after_it_was_detached_is_still_a_reconnection() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    await detach_warden(queen, link.warden_id)
    again, _again_end = _again(deps, link)

    returning = await queen.attach_warden(again)

    assert returning is True
    assert len(await deps.trail.query(TrailQuery(kind="warden.spawned"))) == 1
    assert len(await deps.trail.query(TrailQuery(kind="warden.reconnected"))) == 1


async def test_a_reconnection_lifts_the_dispatchers_hold_on_its_cell() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    deps.dispatch.held.hold(link.cell.id, "link_lost")
    again, _again_end = _again(deps, link)

    await queen.attach_warden(again)

    assert not deps.dispatch.held.is_held(link.cell.id)


async def test_a_first_attach_does_not_touch_a_hold_on_a_cell() -> None:
    deps, link, _end = make_queen_deps()
    deps.dispatch.held.hold(link.cell.id, "link_lost")  # Held for some other reason.

    await Queen(deps).attach_warden(link)

    assert deps.dispatch.held.is_held(link.cell.id)


async def test_a_reconnection_sends_the_wardens_ceilings_again_over_the_new_link() -> None:
    deps, link, end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    await set_ceilings(link, _ceilings(), deps)
    await end.wait_for_ceilings_set()
    again, again_end = _again(deps, link)

    await queen.attach_warden(again)

    resent = await again_end.wait_for_ceilings_set()
    assert (resent.holder, resent.revision, resent.ceilings.max_sub_bees) == (link.warden_id, 0, 4)
    [back] = await deps.trail.query(TrailQuery(kind="warden.reconnected"))
    assert back.payload["ceilings"] is True


async def test_a_reconnection_counts_the_live_grants_it_sent_again_and_only_counts() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, cell_id=link.cell.id, state=GrantState.ACTIVE
    )
    await deps.ledger.record_grant(grant)
    again, again_end = _again(deps, link)

    await queen.attach_warden(again)

    assert (await again_end.wait_for_grant()).grant_id == grant.id
    [back] = await deps.trail.query(TrailQuery(kind="warden.reconnected"))
    assert back.payload["grants"] == 1
    assert grant.id not in str(back.payload)  # The count, never the grant.


async def test_a_warden_the_ledger_remembers_is_sent_its_ceilings_on_its_first_attach_too() -> None:
    # A restarted Queen meets a Warden for the first time, but her restored ledger remembers it.
    deps, link, end = make_queen_deps()
    await deps.ledger.decisions.record_ceilings(link.warden_id, _ceilings())
    queen = Queen(deps)

    returning = await queen.attach_warden(link)

    assert returning is False
    assert (await end.wait_for_ceilings_set()).holder == link.warden_id


def _heartbeat(state: WardenState) -> Heartbeat:
    """A Heartbeat in which a Warden says it is in `state`."""
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=state,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def test_warden_state_is_none_before_the_warden_has_sent_a_heartbeat() -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)

    assert warden_state(queen, link.warden_id) is None  # Nothing is inferred for a silent Warden.


async def test_warden_state_is_none_for_a_warden_that_is_not_attached() -> None:
    deps, link, _end = make_queen_deps()

    assert warden_state(Queen(deps), link.warden_id) is None


async def test_warden_state_is_what_the_wardens_newest_heartbeat_said() -> None:
    deps, link, end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    await end.send(_heartbeat(WardenState.WATCH))
    for _ in range(5_000):
        if warden_state(queen, link.warden_id) is not None:
            break
        await asyncio.sleep(0)

    assert warden_state(queen, link.warden_id) is WardenState.WATCH
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    await end.close()
