"""Tests for hivemind.hive.propolis.fake: FakePropolisProbe.

Fits into the Hive:
    Mirrors src/hivemind/hive/propolis/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.propolis.fake for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis import CHECK_TOR, PROPOLIS_CHECK_NAMES, FakePropolisProbe

_RED = CheckResult(status=CheckStatus.FAIL, detail="a tor process is running")


async def test_a_fake_with_no_overrides_passes_every_check_in_the_recorded_order() -> None:
    results = await FakePropolisProbe().checks()

    assert tuple(results) == PROPOLIS_CHECK_NAMES
    assert {result.status for result in results.values()} == {CheckStatus.PASS}


async def test_an_override_makes_that_check_and_only_that_check_red() -> None:
    results = await FakePropolisProbe({CHECK_TOR: _RED}).checks()

    assert results[CHECK_TOR] == _RED
    assert [n for n, r in results.items() if r.status is CheckStatus.FAIL] == [CHECK_TOR]


async def test_asking_twice_gives_the_same_answer() -> None:
    probe = FakePropolisProbe({CHECK_TOR: _RED})

    assert await probe.checks() == await probe.checks()


def test_an_override_naming_no_check_is_a_caller_bug_and_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown check"):
        FakePropolisProbe({"tor_is_disabled": _RED})
