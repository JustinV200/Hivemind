"""Tests for hivemind.hive.propolis.trail: attest_propolis_cell.

Fits into the Hive:
    Mirrors src/hivemind/hive/propolis/trail.py (codingrules section 3). Each test attests a Cell
    through a `FakePropolisProbe` (or a probe that misreports) and reads the one `cell.attested`
    event on the trail: it is recorded win or lose, it names the tier, and a probe cannot pass
    vacuously by leaving a check out or calling one not applicable.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.propolis.trail for the module under test.
    - tests/unit/hive/night_veil/test_trail.py for the Night Veil counterpart.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis import (
    CHECK_TOR,
    PROPOLIS_CHECK_NAMES,
    FakePropolisProbe,
    attest_propolis_cell,
)
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery, TrailRecorder
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id

_RED = CheckResult(status=CheckStatus.FAIL, detail="a process named tor is running")
_GREEN = CheckResult(status=CheckStatus.PASS, detail="tunnel")


class _Report:
    """A probe that reports exactly the mapping it is given, however incomplete."""

    def __init__(self, reported: Mapping[str, CheckResult]) -> None:
        self._reported = reported

    async def checks(self) -> Mapping[str, CheckResult]:
        return self._reported


def _recorder(clock: FakeClock) -> tuple[TrailRecorder, MemoryPheromoneTrail]:
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=new_hive_id(clock), node_id=new_node_id(clock)
    )
    return recorder, trail


async def test_a_green_attestation_is_recorded_once_and_names_the_tier() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)
    cell_id = new_cell_id(clock)

    attestation = await attest_propolis_cell(FakePropolisProbe(), recorder, cell_id)

    assert attestation.passed is True
    [event] = await trail.query(TrailQuery())
    assert event.kind == "cell.attested"
    assert event.subject_id == cell_id
    assert event.payload["passed"] is True
    assert event.payload["red"] == []
    assert event.payload["comb_shield"] == "PROPOLIS"
    assert set(PROPOLIS_CHECK_NAMES) <= set(event.payload)


async def test_a_red_attestation_is_recorded_too_with_the_check_that_failed() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)

    attestation = await attest_propolis_cell(
        FakePropolisProbe({CHECK_TOR: _RED}), recorder, new_cell_id(clock)
    )

    assert attestation.passed is False
    assert attestation.red == (CHECK_TOR,)
    [event] = await trail.query(TrailQuery())
    assert event.payload["passed"] is False
    assert event.payload["red"] == [CHECK_TOR]
    assert event.payload[CHECK_TOR] == {"status": "FAIL", "detail": _RED.detail}


async def test_a_probe_that_reports_nothing_does_not_pass_vacuously() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)

    attestation = await attest_propolis_cell(_Report({}), recorder, new_cell_id(clock))

    assert attestation.passed is False
    assert set(attestation.red) == set(PROPOLIS_CHECK_NAMES)
    [event] = await trail.query(TrailQuery())
    assert event.payload["passed"] is False


async def test_a_check_the_probe_left_out_is_red_and_the_rest_keep_their_results() -> None:
    clock = FakeClock()
    recorder, _trail = _recorder(clock)
    reported = {name: _GREEN for name in PROPOLIS_CHECK_NAMES if name != CHECK_TOR}

    attestation = await attest_propolis_cell(_Report(reported), recorder, new_cell_id(clock))

    assert attestation.red == (CHECK_TOR,)
    assert attestation.results[CHECK_TOR].detail == "the probe did not report this check"


async def test_a_check_reported_as_not_applicable_is_red_at_this_tier() -> None:
    clock = FakeClock()
    recorder, _trail = _recorder(clock)
    skipped = CheckResult(status=CheckStatus.NOT_APPLICABLE, detail="no browser to drive")
    reported = dict.fromkeys(PROPOLIS_CHECK_NAMES, _GREEN) | {CHECK_TOR: skipped}

    attestation = await attest_propolis_cell(_Report(reported), recorder, new_cell_id(clock))

    assert attestation.passed is False
    assert attestation.red == (CHECK_TOR,)


async def test_a_check_beyond_the_expected_ones_is_kept_after_them_and_judged() -> None:
    clock = FakeClock()
    recorder, trail = _recorder(clock)
    platform = CheckResult(status=CheckStatus.FAIL, detail="unsupported_platform")
    reported = dict.fromkeys(PROPOLIS_CHECK_NAMES, _GREEN) | {"platform_supported": platform}

    attestation = await attest_propolis_cell(_Report(reported), recorder, new_cell_id(clock))

    assert attestation.red == ("platform_supported",)
    assert tuple(attestation.results)[: len(PROPOLIS_CHECK_NAMES)] == PROPOLIS_CHECK_NAMES
    [event] = await trail.query(TrailQuery())
    assert event.payload["platform_supported"] == {"status": "FAIL", "detail": platform.detail}
