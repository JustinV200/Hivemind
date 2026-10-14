"""Tests for hivemind.queen.forage.resend: what a re-attached Warden is sent again.

A Warden that reconnects (roadmap 11.9, ADR-0053) is handed what the Forage ledger already decided
for it: its current ceilings, its Cell's hosting plan and every grant it still holds that has not
lapsed, in that order. The cases drive a real ledger and a real memory link: a Warden the ledger
holds nothing for is sent nothing; each part is re-delivered with the revision the ledger holds
(none is spent, none recorded as a new decision); a lapsed grant, another Warden's grant, and one
whose source left the map are not sent; and what the Warden receives arrives ceilings first.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/resend.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.resend for the module under test.
    - tests.unit.queen.attach.test_admit for the same through the attach path.
"""

from __future__ import annotations

from datetime import timedelta

from builders.forage import make_grant, make_source
from builders.queen import WardenEnd, make_queen_deps

from hivemind.forage import ModelSlot
from hivemind.forage.grant_state import GrantState
from hivemind.forage.models import AllowedBinding, SeatReservation
from hivemind.forage.models.pools import Ceilings
from hivemind.forage.slots import Effort
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage import Resent, resend_state
from hivemind.queen.forage.ceilings import change_ceilings, set_ceilings
from hivemind.queen.forage.hosting import write_hosting_plan

_NOTHING = Resent(ceilings=False, plan=False, grants=0)


def _ceilings(max_sub_bees: int = 4) -> Ceilings:
    """Ceilings with no loadable sources and a seat to export."""
    return Ceilings(
        max_sub_bees=max_sub_bees,
        model_vram_bytes=1_000,
        model_disk_bytes=2_000,
        loadable_sources=(),
        exportable_seats=1,
    )


async def _delivered(end: WardenEnd, expected: int) -> list[str]:
    """Read `expected` messages off the Warden's end; the kinds in arrival order."""
    await end.pump_until(lambda: len(end.received_kinds) >= expected)
    return end.received_kinds[:expected]


async def _forage_events(deps: QueenDeps) -> int:
    """How many Forage decisions the Queen has recorded on her trail."""
    return len([e for e in await deps.trail.query(TrailQuery()) if e.kind.startswith("forage.")])


async def _granted(deps: QueenDeps, link: WardenLink, **overrides: object) -> None:
    """Record one live ACTIVE grant for `link`'s Warden, bound to a source the map knows."""
    source_id = deps.map.sources()[0].source_id
    binding = AllowedBinding(slot=ModelSlot.WORKER, source_id=source_id, max_effort=Effort.MEDIUM)
    fields: dict[str, object] = {
        "clock": deps.clock,
        "holder": link.warden_id,
        "cell_id": link.cell.id,
        "state": GrantState.ACTIVE,
        "allowed": (binding,),
        "seats": (SeatReservation(source_id=source_id, seats=1),),
    }
    fields.update(overrides)
    await deps.ledger.record_grant(make_grant(**fields))  # type: ignore[arg-type]


async def test_a_warden_the_ledger_holds_nothing_for_is_sent_nothing() -> None:
    deps, link, end = make_queen_deps()

    sent = await resend_state(deps, link)

    assert sent == _NOTHING
    assert end.received_kinds == []


async def test_its_ceilings_are_sent_again_at_the_revision_the_ledger_holds() -> None:
    deps, link, end = make_queen_deps()
    await set_ceilings(link, _ceilings(max_sub_bees=4), deps)
    await change_ceilings(link, _ceilings(max_sub_bees=8), deps)  # Revision 1 is the current.
    await _delivered(end, 2)
    before = await _forage_events(deps)

    sent = await resend_state(deps, link)

    assert sent == Resent(ceilings=True, plan=False, grants=0)
    await end.pump_until(lambda: len(end.ceilings_sets) == 3)
    again = end.ceilings_sets[-1]
    assert (again.revision, again.ceilings.max_sub_bees) == (1, 8)
    assert (again.holder, again.cell_id) == (link.warden_id, link.cell.id)
    assert deps.ledger.decisions.ceilings_revision(link.warden_id) == 1  # No revision was spent.
    assert await _forage_events(deps) == before  # And no new decision was recorded.


async def test_its_cells_hosting_plan_is_sent_again_without_writing_a_new_one() -> None:
    deps, link, end = make_queen_deps()
    plan = await write_hosting_plan(link.cell, deps, link)
    await _delivered(end, 1)
    before = await _forage_events(deps)

    sent = await resend_state(deps, link)

    assert sent == Resent(ceilings=False, plan=True, grants=0)
    await end.pump_until(lambda: len(end.plans_written) == 2)
    assert end.plans_written[-1].revision == plan.revision
    assert deps.ledger.decisions.plan_for(link.cell.id) == plan
    assert await _forage_events(deps) == before


async def test_every_live_grant_it_holds_is_sent_again() -> None:
    deps, link, end = make_queen_deps()
    await _granted(deps, link)
    await _granted(deps, link)

    sent = await resend_state(deps, link)

    assert sent == Resent(ceilings=False, plan=False, grants=2)
    await end.pump_until(lambda: len(end.grants) == 2)
    assert {grant.holder for grant in end.grants} == {link.warden_id}


async def test_a_lapsed_grant_and_another_wardens_grant_are_not_sent() -> None:
    deps, link, end = make_queen_deps()
    now = deps.clock.now()
    await _granted(deps, link, expires_at=now + timedelta(seconds=60))
    await _granted(deps, link, expires_at=now - timedelta(seconds=1))  # Lapsed.
    await _granted(deps, link, holder=make_grant(clock=deps.clock).holder)  # Someone else's.

    sent = await resend_state(deps, link)

    assert sent.grants == 1
    await end.pump_until(lambda: len(end.grants) == 1)
    assert end.grants[0].holder == link.warden_id


async def test_a_grant_whose_source_left_the_map_is_skipped_and_the_rest_still_go() -> None:
    deps, link, end = make_queen_deps()
    await _granted(deps, link)
    gone = AllowedBinding(slot=ModelSlot.WORKER, source_id="gone", max_effort=Effort.MEDIUM)
    await _granted(deps, link, allowed=(gone,), seats=())

    sent = await resend_state(deps, link)

    assert sent.grants == 1
    await end.pump_until(lambda: len(end.grants) == 1)


async def test_ceilings_arrive_before_the_plan_and_the_plan_before_the_grants() -> None:
    deps, link, end = make_queen_deps()
    await set_ceilings(link, _ceilings(), deps)
    await write_hosting_plan(link.cell, deps, link)
    await _granted(deps, link)
    await _delivered(end, 2)  # The first two are the originals.
    first = len(end.received_kinds)

    await resend_state(deps, link)

    kinds = await _delivered(end, first + 3)
    assert kinds[first:] == ["ceilings_set", "plan_written", "grant"]


async def test_a_link_that_has_gone_counts_only_what_reached_it() -> None:
    deps, link, _end = make_queen_deps()
    await set_ceilings(link, _ceilings(), deps)
    await _granted(deps, link)
    await link.transport.close()

    sent = await resend_state(deps, link)

    assert sent == _NOTHING  # Held, but nothing reached a closed link.


async def test_a_plan_naming_a_source_that_left_the_map_is_not_sent_and_the_rest_still_are() -> (
    None
):
    deps, link, _end = make_queen_deps()
    await set_ceilings(link, _ceilings(), deps)
    await _granted(deps, link)
    # A Nuc's plan puts its own models first; they leave the map when its Warden stops.
    gone = make_source(source_id="gone", model="gone-model")
    await write_hosting_plan(link.cell, deps, link, local=(gone,))
    assert deps.ledger.decisions.plan_for(link.cell.id) is not None
    assert "gone" not in {source.source_id for source in deps.map.sources()}

    sent = await resend_state(deps, link)

    assert sent == Resent(ceilings=True, plan=False, grants=1)  # No exception, nothing fatal.
