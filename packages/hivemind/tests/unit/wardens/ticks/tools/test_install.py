"""Tests for hivemind.wardens.ticks.tools.install: handing a tool to the bees that may call it.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/install.py (codingrules section 3). Uses real tool
    packages and a real resident Basket, so "resident and verified" is the store's own answer.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.install for the module under test.
    - hivemind.wardens.spawn.comb for the attenuation `install_tool` applies.
"""

from __future__ import annotations

from builders.comb_tools import make_comb_package, make_promoted
from builders.wardens_tools import ToolRig, add_sub_bee, make_resident

from hivemind.pheromone import TrailQuery
from hivemind.wardens.ticks.tools import install_tool
from waggle.ids import new_cell_id
from waggle.messages.tool.authoring import ToolScope


async def test_a_resident_tool_enters_the_bundle_of_a_bee_that_may_call_it_and_no_other(
    hive_rig: ToolRig,
) -> None:
    allowed, _ = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    forbidden, _ = await add_sub_bee(hive_rig.warden, hive_rig.clock, capabilities=("fs:read:**",))
    package = make_comb_package(name="word_count")
    await make_resident(hive_rig, package.package)

    installed = await install_tool(hive_rig.warden, make_promoted(package, hive_rig.clock))

    assert installed
    assert allowed.comb_tools is not None and forbidden.comb_tools is not None
    assert [entry.name for entry in allowed.comb_tools.entries()] == ["word_count"]
    assert forbidden.comb_tools.entries() == ()
    assert list(hive_rig.warden._comb_entries) == ["word_count"]


async def test_a_tool_needing_a_capability_the_bee_lacks_is_not_offered_to_it(
    hive_rig: ToolRig,
) -> None:
    holder, _ = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    lacking, _ = await add_sub_bee(
        hive_rig.warden, hive_rig.clock, capabilities=("tool:*", "fs:read:**")
    )
    package = make_comb_package(name="fetcher", required_capabilities=("net:example.org",))
    await make_resident(hive_rig, package.package)

    await install_tool(hive_rig.warden, make_promoted(package, hive_rig.clock))

    assert holder.comb_tools is not None and lacking.comb_tools is not None
    assert len(holder.comb_tools.entries()) == 1
    assert lacking.comb_tools.entries() == ()


async def test_a_later_version_replaces_the_earlier_one_in_the_table_and_the_bundle(
    hive_rig: ToolRig,
) -> None:
    sub_bee, _ = await add_sub_bee(hive_rig.warden, hive_rig.clock)
    first = make_comb_package(name="word_count", version="1.0.0")
    second = make_comb_package(name="word_count", version="1.1.0")
    await make_resident(hive_rig, first.package)
    await make_resident(hive_rig, second.package)

    await install_tool(hive_rig.warden, make_promoted(first, hive_rig.clock))
    await install_tool(hive_rig.warden, make_promoted(second, hive_rig.clock))

    assert sub_bee.comb_tools is not None
    assert [(e.name, e.version) for e in sub_bee.comb_tools.entries()] == [("word_count", "1.1.0")]
    assert hive_rig.warden._comb_entries["word_count"].version == "1.1.0"
    assert sub_bee.comb_tools.version == 2  # Two real changes, so a running attempt rebuilds twice.


async def test_a_package_that_is_not_resident_is_refused_and_recorded_with_its_hash(
    cell_rig: ToolRig,
) -> None:
    sub_bee, _ = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    package = make_comb_package(name="word_count")  # Never stored on this Cell.
    promoted = make_promoted(package, cell_rig.clock)

    installed = await install_tool(cell_rig.warden, promoted)

    assert not installed
    assert sub_bee.comb_tools is not None and sub_bee.comb_tools.entries() == ()
    assert cell_rig.warden._comb_entries == {}
    [event] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="tool.load_refused"))
    assert event.payload["code"] == "not_resident"
    assert event.payload["package_sha256"] == package.entry.package_sha256
    assert event.subject_id == promoted.tool_id


async def test_a_cell_scope_tool_for_another_cell_is_refused(cell_rig: ToolRig) -> None:
    sub_bee, _ = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    package = make_comb_package(
        name="local_tool", scope=ToolScope.CELL, cell_id=new_cell_id(cell_rig.clock)
    )
    await make_resident(cell_rig, package.package)

    installed = await install_tool(cell_rig.warden, make_promoted(package, cell_rig.clock))

    assert not installed
    assert sub_bee.comb_tools is not None and sub_bee.comb_tools.entries() == ()
    [event] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="tool.load_refused"))
    assert event.payload["code"] == "wrong_cell"


async def test_a_cell_scope_tool_for_this_cell_is_installed(cell_rig: ToolRig) -> None:
    sub_bee, _ = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    cell = cell_rig.warden._cell
    assert cell is not None
    package = make_comb_package(name="local_tool", scope=ToolScope.CELL, cell_id=cell.id)
    await make_resident(cell_rig, package.package)

    installed = await install_tool(cell_rig.warden, make_promoted(package, cell_rig.clock))

    assert installed
    assert sub_bee.comb_tools is not None
    assert [entry.name for entry in sub_bee.comb_tools.entries()] == ["local_tool"]
