"""Define FakePropolisProbe: an in-memory PropolisProbe, all-green by default.

Shipped code, not test-only (codingrules section 14.4: "fakes live in src/ beside their
Protocol"), for the same reason `FakeNightVeilProbe` is: a composition root, a test or a demo can
run the whole provision-and-attest path for a Propolis Cell without a Cell that has a tunnel to
look at. Every check defaults to `CheckStatus.PASS`; a caller makes one red with `overrides`, keyed
by the exact name in `hive.propolis.probe.PROPOLIS_CHECK_NAMES`, so a test says only the fact
under test.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.propolis`. Implements `hive.propolis.probe.
    PropolisProbe`. Constructed directly by tests and by any composition root that has not wired a
    real `SessionPropolisProbe`. Calls into `hive.propolis.probe` (PROPOLIS_CHECK_NAMES) and
    `hive.night_veil` (CheckResult, CheckStatus) only.

Key invariants:
    - `FakePropolisProbe()` with no overrides passes every check.
    - `overrides` is validated against `PROPOLIS_CHECK_NAMES` at construction: an unknown key is a
      caller bug (a mistyped check name) worth failing on loudly, not ignoring.
    - Asking twice gives the same answer both times; the fake holds no state a call could change.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code".
    - hivemind.hive.propolis.probe for PropolisProbe, the Protocol this implements.
    - hivemind.hive.night_veil.fake for FakeNightVeilProbe, its Night Veil counterpart.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis.probe import PROPOLIS_CHECK_NAMES

_GREEN = CheckResult(status=CheckStatus.PASS, detail="FakePropolisProbe default: PASS.")

__all__ = ["FakePropolisProbe"]


class FakePropolisProbe:
    """An in-memory PropolisProbe: every check green unless `overrides` names it otherwise."""

    def __init__(self, overrides: Mapping[str, CheckResult] | None = None) -> None:
        """Build a FakePropolisProbe, all-green apart from `overrides`.

        Args:
            overrides: Check name (a `PROPOLIS_CHECK_NAMES` entry) to the result that check
                reports instead of the green default; None or empty means every check passes.

        Raises:
            ValueError: `overrides` names a key that is not one of `PROPOLIS_CHECK_NAMES`.
        """
        self._overrides = dict(overrides) if overrides is not None else {}
        unknown = set(self._overrides) - set(PROPOLIS_CHECK_NAMES)
        if unknown:
            raise ValueError(f"FakePropolisProbe overrides names unknown check(s): {unknown!r}.")

    async def checks(self) -> dict[str, CheckResult]:
        """Return every check's result, the override where there is one and green elsewhere."""
        return {name: self._overrides.get(name, _GREEN) for name in PROPOLIS_CHECK_NAMES}
