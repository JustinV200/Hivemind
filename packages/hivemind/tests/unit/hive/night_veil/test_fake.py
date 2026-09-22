"""Tests for hivemind.hive.night_veil.fake: FakeNightVeilProbe.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.fake for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.hive.night_veil.fake import FakeNightVeilProbe
from hivemind.hive.night_veil.results import CHECK_NAMES, CheckResult, CheckStatus
from hivemind.hive.night_veil.runner import run_checks


async def test_default_probe_passes_every_check() -> None:
    probe = FakeNightVeilProbe()

    results = await run_checks(probe)

    assert set(results) == set(CHECK_NAMES)
    assert all(result.status is CheckStatus.PASS for result in results.values())


async def test_one_override_flips_only_that_check() -> None:
    red = CheckResult(status=CheckStatus.FAIL, detail="simulated kill-switch failure")
    probe = FakeNightVeilProbe(overrides={"kill_switch_active": red})

    results = await run_checks(probe)

    assert results["kill_switch_active"] == red
    assert results["tor_healthy"].status is CheckStatus.PASS


async def test_every_check_method_reflects_its_own_override() -> None:
    red = CheckResult(status=CheckStatus.FAIL, detail="x")
    for name in CHECK_NAMES:
        probe = FakeNightVeilProbe(overrides={name: red})
        result = await getattr(probe, name)()
        assert result == red


def test_an_unknown_override_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown"):
        FakeNightVeilProbe(
            overrides={"not_a_real_check": CheckResult(status=CheckStatus.PASS, detail="x")}
        )
