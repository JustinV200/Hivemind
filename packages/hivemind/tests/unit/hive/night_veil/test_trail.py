"""Tests for hivemind.hive.night_veil.trail: attest_cell.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/trail.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.trail for the module under test.
    - .claude/codingrules.md section 12 for the cell.attested skeleton kind this records.
"""

from __future__ import annotations

from hivemind.hive.night_veil.fake import FakeNightVeilProbe
from hivemind.hive.night_veil.results import CheckResult, CheckStatus
from hivemind.hive.night_veil.trail import attest_cell
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery, TrailRecorder
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id


def _recorder(clock: FakeClock) -> tuple[TrailRecorder, MemoryPheromoneTrail]:
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=new_hive_id(clock), node_id=new_node_id(clock)
    )
    return recorder, trail


async def test_attest_cell_records_one_cell_attested_event_on_a_green_run() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)
    cell_id = new_cell_id(clock)

    attestation = await attest_cell(FakeNightVeilProbe(), recorder, cell_id)

    assert attestation.passed is True
    recorded = await trail.query(TrailQuery())
    assert len(recorded) == 1
    event = recorded[0]
    assert event.kind == "cell.attested"
    assert event.subject_id == cell_id
    assert event.node_id == recorder.node_id
    assert event.payload["passed"] is True
    assert event.payload["red"] == []


async def test_attest_cell_still_records_on_a_red_run() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)
    cell_id = new_cell_id(clock)
    red = CheckResult(status=CheckStatus.FAIL, detail="kill-switch not loaded")
    probe = FakeNightVeilProbe(overrides={"kill_switch_active": red})

    attestation = await attest_cell(probe, recorder, cell_id)

    assert attestation.passed is False
    assert attestation.red == ("kill_switch_active",)
    recorded = await trail.query(TrailQuery())
    assert len(recorded) == 1
    event = recorded[0]
    assert event.payload["passed"] is False
    assert event.payload["red"] == ["kill_switch_active"]
    assert event.payload["kill_switch_active"] == {
        "status": "FAIL",
        "detail": "kill-switch not loaded",
    }


async def test_attest_cell_payload_never_carries_more_than_status_and_detail_per_check() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)
    cell_id = new_cell_id(clock)

    await attest_cell(FakeNightVeilProbe(), recorder, cell_id)

    recorded = await trail.query(TrailQuery())
    event = recorded[0]
    kill_switch_payload = event.payload["kill_switch_active"]
    assert isinstance(kill_switch_payload, dict)
    assert set(kill_switch_payload) == {"status", "detail"}
    assert "passed" in event.payload
    assert "red" in event.payload
