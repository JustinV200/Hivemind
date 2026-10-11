"""Tests for hivemind.wardens.local_pool.hosting.report: what the Queen and the trail are told.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/report.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.report for the module under test.
"""

from __future__ import annotations

from builders.hosting import HostingRig, make_hosting_rig, vram_source

from hivemind.pheromone import TrailQuery
from hivemind.wardens.local_pool.hosting.report import (
    ReportPort,
    announce_added,
    announce_removed,
    record_never_loaded,
)
from hivemind.wardens.local_pool.hosting.sources import hosted
from waggle.messages.forage import SourceRemovalCause


def _port(rig: HostingRig) -> ReportPort:
    deps = rig.deps
    return ReportPort(
        queen_link=deps.queen_link,
        hop=deps.hop,
        trail=deps.trail,
        identity=deps.identity,
        clock=deps.clock,
        warden_id=rig.warden_id,
        cell_id=rig.cell.id,
    )


async def _kinds(rig: HostingRig) -> list[str]:
    return [event.kind for event in await rig.deps.trail.query(TrailQuery())]


async def test_announce_added_sends_the_event_and_records_a_trail_row() -> None:
    rig = make_hosting_rig()
    source = hosted(vram_source("a", 10), rig.cell.id)

    await announce_added(_port(rig), "box", source, "loaded")
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_added))

    sent = rig.queen_end.source_added[0]
    assert (sent.server_id, sent.source.source_id, sent.reason) == ("box", "a", "loaded")
    events = [e for e in await rig.deps.trail.query(TrailQuery()) if e.kind.startswith("forage.")]
    assert [e.kind for e in events] == ["forage.source_added"]
    assert events[0].subject_id == rig.cell.id
    assert events[0].payload["source_id"] == "a"
    assert events[0].payload["reporter"] == "warden"


async def test_announce_removed_carries_the_cause_on_both() -> None:
    rig = make_hosting_rig()
    source = hosted(vram_source("a", 10), rig.cell.id)

    await announce_removed(_port(rig), "box", source, SourceRemovalCause.UNLOADED, "gone")
    await rig.queen_end.pump_until(lambda: bool(rig.queen_end.source_removed))

    assert rig.queen_end.source_removed[0].cause is SourceRemovalCause.UNLOADED
    events = [e for e in await rig.deps.trail.query(TrailQuery()) if e.kind.startswith("forage.")]
    assert events[0].payload["cause"] == "UNLOADED"


async def test_a_closed_queen_link_loses_the_wire_event_but_never_the_trail_row() -> None:
    rig = make_hosting_rig()
    await rig.deps.queen_link.close()
    source = hosted(vram_source("a", 10), rig.cell.id)

    await announce_added(_port(rig), "box", source, "loaded")

    assert "forage.source_added" in await _kinds(rig)


async def test_record_never_loaded_writes_trail_rows_and_sends_nothing() -> None:
    rig = make_hosting_rig()

    await record_never_loaded(_port(rig), "box", ("a", "b"), SourceRemovalCause.SERVER_DIED)

    events = [e for e in await rig.deps.trail.query(TrailQuery()) if e.kind.startswith("forage.")]
    assert [e.payload["source_id"] for e in events] == ["a", "b"]
    assert all(e.payload["loaded"] is False for e in events)
    assert rig.queen_end.source_removed == []
