"""Define run_checks: call every NightVeilProbe check and collect the results by name.

The one place that knows every `hive.night_veil.probe.NightVeilProbe` method name and calls them
all, in `hive.night_veil.results.CHECK_NAMES`'s own order, so `hive.night_veil.trail.attest_cell`
does not have to (and so a test can call this directly against a `FakeNightVeilProbe` without
going through the trail-recording effectful edge at all).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil`. Called by `hive.night_veil.trail.
    attest_cell`. Calls into `hive.night_veil.probe` (NightVeilProbe) and `hive.night_veil.results`
    (CHECK_NAMES, CheckResult) only.

Key invariants:
    - The returned mapping has exactly one entry per `CHECK_NAMES` name: `run_checks` awaits every
      one of them, in order, never skipping or duplicating one.

See Also:
    - hivemind.hive.night_veil.probe for NightVeilProbe, the Protocol this module drives.
    - hivemind.hive.night_veil.results for CHECK_NAMES and CheckResult.
    - hivemind.hive.night_veil.trail for attest_cell, this module's one caller.
"""

from __future__ import annotations

from hivemind.hive.night_veil.probe import NightVeilProbe
from hivemind.hive.night_veil.results import CHECK_NAMES, CheckResult

__all__ = ["run_checks"]


async def run_checks(probe: NightVeilProbe) -> dict[str, CheckResult]:
    """Await every `CHECK_NAMES` method on `probe`, collecting each result by name.

    Args:
        probe: The NightVeilProbe to run every check against.

    Returns:
        One CheckResult per `CHECK_NAMES` entry, keyed by that same name.
    """
    results: dict[str, CheckResult] = {}
    for name in CHECK_NAMES:
        method = getattr(probe, name)
        results[name] = await method()
    return results
