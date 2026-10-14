"""Tests for hivemind.wardens.local_pool.hosting.offering: offer, withdraw, load and refuse.

`ModelServerHost` is tested through its public methods in test_host.py and test_host_loading.py;
this module holds the few things `Offering` promises on its own: a source is on the map exactly
when the Queen was told, a warden with no Forage map still reports, loading what is already
offered says nothing, and only a refusal someone asked for is written to the trail.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/offering.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.offering for the module under test.
    - builders.hosting for the rig: a hosting-capable Cell, a scripted session and provider.
"""

from __future__ import annotations

import dataclasses

from builders.hosting import HostingRig, make_hosting_rig, vram_source

from hivemind.pheromone import TrailQuery
from hivemind.wardens.local_pool.hosting import HostedCell
from hivemind.wardens.local_pool.hosting.offering import Offering
from hivemind.wardens.local_pool.hosting.report import ReportPort
from hivemind.wardens.local_pool.hosting.sources import Refusal, hosted
from waggle.messages.forage import SourceRemovalCause


def _offering(rig: HostingRig, *, with_map: bool = True) -> Offering:
    """An Offering over the rig's Warden, with or without its Forage map."""
    place = HostedCell(cell=rig.cell, session=rig.session, ceilings_of=lambda: rig.ceilings)
    deps = rig.deps if with_map else dataclasses.replace(rig.deps, map=None)
    port = ReportPort(
        queen_link=deps.queen_link,
        hop=deps.hop,
        trail=deps.trail,
        identity=deps.identity,
        clock=deps.clock,
        warden_id=rig.warden_id,
        cell_id=rig.cell.id,
    )
    return Offering(deps, place, port)


async def _denials(rig: HostingRig) -> int:
    """How many `guard.denied` rows are on the Warden's trail."""
    return sum(1 for e in await rig.deps.trail.query(TrailQuery()) if e.kind == "guard.denied")


async def test_an_offer_is_on_the_map_and_with_the_queen_and_a_withdrawal_undoes_both() -> None:
    rig = make_hosting_rig()
    offering = _offering(rig)
    source = hosted(vram_source("a", 1), rig.cell.id)

    await offering.offer("box", source)

    assert [s.source_id for s in rig.forage_map.sources()] == ["a"]
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_added))
    await offering.withdraw("box", source, SourceRemovalCause.UNLOADED)
    assert rig.forage_map.sources() == ()
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_removed))
    assert rig.queen_end.source_removed[0].cause is SourceRemovalCause.UNLOADED


async def test_a_warden_with_no_forage_map_still_tells_the_queen() -> None:
    rig = make_hosting_rig()
    offering = _offering(rig, with_map=False)

    await offering.offer("box", hosted(vram_source("a", 1), rig.cell.id))

    assert rig.forage_map.sources() == ()  # There was no map to put it on.
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_added))
    assert [m.source.source_id for m in rig.queen_end.source_added] == ["a"]


async def test_a_refusal_is_a_denial_on_the_trail_only_when_someone_asked() -> None:
    rig = make_hosting_rig()
    offering = _offering(rig)
    refusal = Refusal("a", "loadable_source", "'a' is not in the ceilings' loadable_sources")

    await offering.refuse("box", (refusal,), record=False)
    assert await _denials(rig) == 0

    await offering.refuse("box", (refusal,), record=True)
    assert await _denials(rig) == 1


async def test_loading_a_source_already_offered_returns_the_server_untouched() -> None:
    rig = make_hosting_rig()
    await rig.host.start("box")
    server = rig.host.running["box"]

    grown = await _offering(rig).load(server, "small", in_use=0)

    assert grown is server
    await rig.queen_end.pump_until(lambda: len(rig.queen_end.source_added) == 2)
    assert len(rig.queen_end.source_added) == 2  # The two from the start; none new.
