"""Tests for hivemind.hive.night_veil.runner: run_checks.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/runner.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.runner for the module under test.
"""

from __future__ import annotations

from hivemind.hive.night_veil.fake import FakeNightVeilProbe
from hivemind.hive.night_veil.results import CHECK_NAMES
from hivemind.hive.night_veil.runner import run_checks


async def test_run_checks_returns_exactly_one_result_per_check_name() -> None:
    results = await run_checks(FakeNightVeilProbe())

    assert set(results) == set(CHECK_NAMES)
    assert len(results) == len(CHECK_NAMES)
