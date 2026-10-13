"""Tests for hivemind.queen.ticks.tools.announce: telling the Wardens that can run a new tool.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/announce.py (codingrules section 3). Several fake
    Wardens are attached to one Queen; a promotion is made in the real in-memory registry with a
    package in the real Basket, and the test reads what each Warden was sent, in order.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.announce for the module under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.basket import make_deposit_request
from builders.cells import make_capabilities, make_cell
from builders.queen_tools import ToolRig, WardenScript, make_tool_rig
from builders.royal_jelly import make_offer, make_package, make_tool_spec

from hivemind.basket import MediaKind
from hivemind.cell import Cell, CellKind, OsFamily
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.tools import QueenAnnouncer, announce_current_tools
from hivemind.royal_jelly.registry import QueenPrincipal, RegistryChange, WardenPrincipal
from hivemind.royal_jelly.spec import ToolSpec
from waggle.messages.labels import OsFamily as WireOsFamily
from waggle.messages.tool import ToolPromoted, ToolScope


def _cell(rig: ToolRig, os: OsFamily = OsFamily.LINUX) -> Cell:
    return make_cell(
        kind=CellKind.VIRTUAL, clock=rig.deps.clock, capabilities=make_capabilities(os=os)
    )


async def _promote(rig: ToolRig, **spec_fields: object) -> tuple[RegistryChange, ToolSpec]:
    """Keep a package in the Basket and promote its tool in the registry as the right principal."""
    spec = make_tool_spec(rig.deps.clock, required_capabilities=(), **spec_fields)
    report, offer = make_offer(spec)
    package = make_package(spec=spec)
    request = make_deposit_request(
        package, media_kind=MediaKind.TOOL_PACKAGE, media_type="application/zip"
    )
    await rig.access.deposits.deposit(request)
    cell_scoped = spec.scope is ToolScope.CELL
    principal = (
        WardenPrincipal(rig.link.warden_id, rig.link.cell.id) if cell_scoped else QueenPrincipal()
    )
    change = await rig.registry.promote(spec, report, offer, principal, "a test promotion")
    return change, spec


def _kinds(kinds: list[str]) -> list[str]:
    return [kind for kind in kinds if kind != "basket.ack"]


async def test_a_hive_tool_is_shipped_then_announced_to_each_warden_whose_os_it_supports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    second = rig.add_peer(_cell(rig))
    windows = rig.add_peer(_cell(rig, OsFamily.WINDOWS))
    change, spec = await _promote(rig)

    await QueenAnnouncer(rig.deps, rig.wardens).announce(change, spec)

    for warden in (rig.peer, second):
        # The blob is resident before the Warden is told of the tool.
        assert _kinds(warden.seen) == ["basket.offer", "basket.chunk", "tool.promoted"]
        assert change.row.package_sha256 in warden.blobs
        announced = await warden.next()
        assert isinstance(announced.payload, ToolPromoted) and announced.correlation_id is None
        assert announced.payload.package_sha256 == change.row.package_sha256
        assert announced.payload.supported_os == (WireOsFamily.LINUX,)
    assert windows.seen == []
    events = await rig.deps.trail.query(TrailQuery(kind="tool.announced"))
    assert len(events) == 2 and all(event.payload["delivered"] for event in events)
    await rig.aclose()


async def test_a_cell_tool_is_announced_to_its_own_warden_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    other = rig.add_peer(_cell(rig))
    change, spec = await _promote(
        rig, scope=ToolScope.CELL, cell_id=rig.link.cell.id, requested_by=rig.link.warden_id
    )

    await QueenAnnouncer(rig.deps, rig.wardens).announce(change, spec)

    assert "tool.promoted" in rig.peer.seen and other.seen == []
    await rig.aclose()


async def test_a_sandbox_cell_is_never_announced_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    sandbox = rig.add_peer(_cell(rig))
    rig.deps.tool_lane.sandbox_cells.add(sandbox.link.cell.id)
    change, spec = await _promote(rig)

    await QueenAnnouncer(rig.deps, rig.wardens).announce(change, spec)

    assert sandbox.seen == [] and "tool.promoted" in rig.peer.seen
    await rig.aclose()


async def test_a_warden_that_refuses_the_blob_is_not_announced_and_nothing_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    refusing = rig.add_peer(_cell(rig), WardenScript(refuse_offer=True))
    change, spec = await _promote(rig)

    await QueenAnnouncer(rig.deps, rig.wardens).announce(change, spec)

    assert "tool.promoted" not in refusing.seen and "tool.promoted" in rig.peer.seen
    events = await rig.deps.trail.query(TrailQuery(kind="tool.announced"))
    assert [event.payload["warden_id"] for event in events] == [rig.link.warden_id]
    await rig.aclose()


async def test_a_warden_whose_link_has_closed_is_skipped_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    gone = rig.add_peer(_cell(rig))
    await gone.link.transport.close()
    change, spec = await _promote(rig)

    await QueenAnnouncer(rig.deps, rig.wardens).announce(change, spec)

    assert "tool.promoted" in rig.peer.seen
    await rig.aclose()


async def test_a_warden_attached_later_is_brought_up_to_date_blob_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    await _promote(rig)  # A hive tool, promoted before the newcomer exists.
    await _promote(
        rig, scope=ToolScope.CELL, cell_id=rig.link.cell.id, requested_by=rig.link.warden_id
    )  # A cell tool: not for the newcomer.
    newcomer = rig.add_peer(_cell(rig))
    windows = rig.add_peer(_cell(rig, OsFamily.WINDOWS))

    await announce_current_tools(rig.deps, newcomer.link)
    await rig.settle()

    assert _kinds(newcomer.seen) == ["basket.offer", "basket.chunk", "tool.promoted"]
    assert windows.seen == []
    await rig.aclose()


async def test_attaching_a_warden_to_the_queen_starts_its_catch_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    await _promote(rig)
    newcomer = rig.add_peer(_cell(rig))
    queen = Queen(rig.deps)

    await queen.attach_warden(newcomer.link)
    await rig.settle()

    assert "tool.promoted" in newcomer.seen
    await queen.stop()
    await rig.aclose()


async def test_a_hive_with_no_lab_starts_no_catch_up_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, lab=False)

    await announce_current_tools(rig.deps, rig.link)

    assert rig.deps.tool_lane.background == set()
    await rig.aclose()
