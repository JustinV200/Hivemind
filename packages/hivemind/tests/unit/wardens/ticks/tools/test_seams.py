"""Tests for hivemind.wardens.ticks.tools.seams: what a Warden hands spawn for the Comb.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/seams.py. A Hive Stand Warden mirrors its registry at
    each spawn, offering only packages it can read back true; a Cell's Warden keeps what it is told.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.seams for the module under test.
    - test_comb.py (the spawn tests) for the three fields a spawned bee receives.
"""

from __future__ import annotations

from builders.comb_tools import make_comb_package, make_promoted
from builders.royal_jelly import make_offer, make_package, make_tool_spec
from builders.wardens_tools import ToolRig, make_resident

from hivemind.pheromone import TrailQuery
from hivemind.royal_jelly import QueenPrincipal, ToolKey
from hivemind.wardens.spawn import CombSeams
from hivemind.wardens.ticks.tools import comb_seams
from hivemind.wardens.ticks.tools.seams import refresh_from_registry
from hivemind.workers.tools.comb import CombToolEntry
from waggle.messages.tool.authoring import ToolScope


def _seams(rig: ToolRig) -> CombSeams:
    """Return the rig Warden's seams; a Warden with a Basket always has them."""
    seams = comb_seams(rig.warden)
    assert seams is not None
    return seams


async def _promote(rig: ToolRig, name: str, *, store: bool = True, version: str = "1.0.0") -> str:
    """Promote a HIVE echo tool in the Hive's registry; store its package unless told not to."""
    spec = make_tool_spec(rig.clock, name=name, version=version, required_capabilities=())
    report, offer = make_offer(spec)
    if store:
        await make_resident(rig, make_package(spec=spec))
    assert rig.access is not None
    await rig.access.registry.promote(spec, report, offer, QueenPrincipal(), "Passed.")
    return offer.package_sha256


async def test_the_hive_stand_warden_mirrors_the_registry_and_skips_an_unreadable_package(
    hive_rig: ToolRig,
) -> None:
    await _promote(hive_rig, "readable")
    missing = await _promote(hive_rig, "missing", store=False)

    entries = await _seams(hive_rig).entries()

    assert [entry.name for entry in entries] == ["readable"]
    assert list(hive_rig.warden._comb_entries) == ["readable"]
    [refusal] = await hive_rig.warden._deps.trail.query(TrailQuery(kind="tool.load_refused"))
    assert refusal.payload["package_sha256"] == missing


async def test_a_retired_tool_leaves_the_table_at_the_next_spawn(hive_rig: ToolRig) -> None:
    await _promote(hive_rig, "short_lived")
    await refresh_from_registry(hive_rig.warden)
    assert list(hive_rig.warden._comb_entries) == ["short_lived"]
    assert hive_rig.access is not None

    await hive_rig.access.registry.retire(
        ToolKey("short_lived", ToolScope.HIVE, None), "1.0.0", QueenPrincipal(), "No longer needed."
    )
    await refresh_from_registry(hive_rig.warden)

    assert hive_rig.warden._comb_entries == {}


async def test_a_cells_warden_keeps_what_it_was_told_and_reads_no_registry(
    cell_rig: ToolRig,
) -> None:
    held = CombToolEntry.from_promoted(
        make_promoted(make_comb_package(name="told"), cell_rig.clock)
    )
    cell_rig.warden._comb_entries[held.name] = held

    entries = await _seams(cell_rig).entries()

    assert tuple(entries) == (held,)
