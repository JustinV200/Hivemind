"""Tests for hivemind.hive.night_veil.results: CheckStatus, CheckResult, Attestation, attest.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/results.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.results for the module under test.
"""

from __future__ import annotations

from hivemind.hive.night_veil.results import CHECK_NAMES, CheckResult, CheckStatus, attest, verdict

_GREEN = CheckResult(status=CheckStatus.PASS, detail="ok")
_RED = CheckResult(status=CheckStatus.FAIL, detail="bad")
_NA = CheckResult(status=CheckStatus.NOT_APPLICABLE, detail="skipped")


def test_all_green_passes_with_no_red_checks() -> None:
    results = dict.fromkeys(CHECK_NAMES, _GREEN)

    attestation = attest(results)

    assert attestation.passed is True
    assert attestation.red == ()


def test_not_applicable_counts_as_clearing_not_as_red() -> None:
    results = dict.fromkeys(CHECK_NAMES, _GREEN)
    results["webrtc_leak_blocked"] = _NA

    attestation = attest(results)

    assert attestation.passed is True
    assert attestation.red == ()
    assert attestation.results["webrtc_leak_blocked"].status is CheckStatus.NOT_APPLICABLE


def test_a_single_red_check_fails_the_whole_attestation() -> None:
    results = dict.fromkeys(CHECK_NAMES, _GREEN)
    results["kill_switch_active"] = _RED

    attestation = attest(results)

    assert attestation.passed is False
    assert attestation.red == ("kill_switch_active",)


def test_the_randomized_hostname_check_is_attested_and_a_red_one_fails_the_cell_alone() -> None:
    # Added after a tester found a Night Veil Cell could carry its image's or its Cell id's name.
    results = dict.fromkeys(CHECK_NAMES, _GREEN)
    results["randomized_hostname"] = _RED

    attestation = attest(results)

    assert "randomized_hostname" in CHECK_NAMES
    assert (attestation.passed, attestation.red) == (False, ("randomized_hostname",))


def test_verdict_builds_the_pass_or_fail_a_pure_judge_returns() -> None:
    assert verdict(passed=True, detail="ok") == CheckResult(status=CheckStatus.PASS, detail="ok")
    assert verdict(passed=False, detail="bad") == CheckResult(status=CheckStatus.FAIL, detail="bad")


def test_every_check_can_be_red_at_once() -> None:
    results = dict.fromkeys(CHECK_NAMES, _RED)

    attestation = attest(results)

    assert attestation.passed is False
    assert set(attestation.red) == set(CHECK_NAMES)


def test_attest_is_deterministic() -> None:
    results = dict.fromkeys(CHECK_NAMES, _GREEN)
    results["tor_healthy"] = _RED

    first = attest(results)
    second = attest(results)

    assert first == second


def test_attest_preserves_every_result_regardless_of_verdict() -> None:
    results = {"kill_switch_active": _RED, "tor_healthy": _GREEN}

    attestation = attest(results)

    assert attestation.results == results
