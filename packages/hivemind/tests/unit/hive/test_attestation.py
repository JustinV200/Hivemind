"""Tests for hivemind.hive.attestation: CellAttestation, the per-tier choice.

Fits into the Hive:
    Mirrors src/hivemind/hive/attestation.py (codingrules section 3). A Cell is attested at the
    tier it was provisioned at and by that tier's probe alone: MEADOW by nothing, PROPOLIS by the
    tunnel checks and Tor's absence, NIGHT_VEIL by Night Veil's fourteen. A tier that needs a
    probe and has none is refused, and each Cell gets a probe built for it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.attestation for the module under test.
"""

from __future__ import annotations

import pytest
from builders.cells import make_cell

from hivemind.cell import Cell, CellKind, CombShieldLevel
from hivemind.common.errors import ConfigurationError
from hivemind.hive.attestation import CellAttestation
from hivemind.hive.night_veil import CheckResult, CheckStatus, FakeNightVeilProbe
from hivemind.hive.propolis import CHECK_TOR, PROPOLIS_CHECK_NAMES, FakePropolisProbe
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery, TrailRecorder
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_CLOCK = FakeClock()


def _cell(tier: CombShieldLevel) -> Cell:
    """A Virtual Cell provisioned at `tier`."""
    return make_cell(kind=CellKind.VIRTUAL, clock=_CLOCK, comb_shield=tier)


def _recorder() -> tuple[TrailRecorder, MemoryPheromoneTrail]:
    trail = MemoryPheromoneTrail(_CLOCK)
    recorder = TrailRecorder(
        trail=trail, clock=_CLOCK, hive_id=new_hive_id(_CLOCK), node_id=new_node_id(_CLOCK)
    )
    return recorder, trail


async def test_a_meadow_cell_is_attested_by_nothing_and_records_nothing() -> None:
    recorder, trail = _recorder()
    attestation = CellAttestation(
        recorder, lambda _c: FakeNightVeilProbe(), lambda _c: FakePropolisProbe()
    )

    assert await attestation.attest(_cell(CombShieldLevel.MEADOW)) is None
    assert await trail.query(TrailQuery()) == ()


async def test_a_propolis_cell_is_attested_by_its_probe_and_the_event_names_the_tier() -> None:
    recorder, trail = _recorder()
    asked: list[Cell] = []

    def propolis(cell: Cell) -> FakePropolisProbe:
        asked.append(cell)
        return FakePropolisProbe()

    cell = _cell(CombShieldLevel.PROPOLIS)
    attestation = await CellAttestation(recorder, lambda _c: FakeNightVeilProbe(), propolis).attest(
        cell
    )

    assert attestation is not None and attestation.passed
    assert asked == [cell]  # The probe was built for this very Cell.
    [event] = await trail.query(TrailQuery(kind="cell.attested"))
    assert event.subject_id == cell.id
    assert event.payload["comb_shield"] == "PROPOLIS"
    assert set(PROPOLIS_CHECK_NAMES) <= set(event.payload)


async def test_a_red_propolis_check_is_the_attestations_verdict() -> None:
    recorder, _trail = _recorder()
    red = FakePropolisProbe({CHECK_TOR: CheckResult(status=CheckStatus.FAIL, detail="tor runs")})

    attestation = await CellAttestation(
        recorder, lambda _c: FakeNightVeilProbe(), lambda _c: red
    ).attest(_cell(CombShieldLevel.PROPOLIS))

    assert attestation is not None and attestation.red == (CHECK_TOR,)


async def test_a_night_veil_cell_is_attested_by_the_night_veil_probe_and_names_its_tier() -> None:
    recorder, trail = _recorder()

    attestation = await CellAttestation(recorder, lambda _c: FakeNightVeilProbe()).attest(
        _cell(CombShieldLevel.NIGHT_VEIL)
    )

    assert attestation is not None and attestation.passed
    [event] = await trail.query(TrailQuery(kind="cell.attested"))
    assert event.payload["comb_shield"] == "NIGHT_VEIL"
    assert "kill_switch_active" in event.payload  # Night Veil's fourteen, not Propolis's five.


async def test_a_propolis_cell_with_no_probe_configured_is_refused_not_passed() -> None:
    recorder, trail = _recorder()

    with pytest.raises(ConfigurationError, match="no PROPOLIS attestation probe"):
        await CellAttestation(recorder, lambda _c: FakeNightVeilProbe()).attest(
            _cell(CombShieldLevel.PROPOLIS)
        )

    assert await trail.query(TrailQuery()) == ()  # Nothing was attested, so nothing was said.


async def test_each_tier_s_probe_is_asked_only_for_its_own_cells() -> None:
    recorder, _trail = _recorder()
    night_veil: list[Cell] = []
    propolis: list[Cell] = []

    def nv_factory(cell: Cell) -> FakeNightVeilProbe:
        night_veil.append(cell)
        return FakeNightVeilProbe()

    def propolis_factory(cell: Cell) -> FakePropolisProbe:
        propolis.append(cell)
        return FakePropolisProbe()

    attestation = CellAttestation(recorder, nv_factory, propolis_factory)
    for tier in CombShieldLevel:
        await attestation.attest(_cell(tier))

    assert [c.comb_shield for c in night_veil] == [CombShieldLevel.NIGHT_VEIL]
    assert [c.comb_shield for c in propolis] == [CombShieldLevel.PROPOLIS]
