"""Define PropolisProbe: the Protocol that reports every check a Propolis Cell is attested by.

A Virtual Cell at the Propolis tier is attested from its image before it is ready (docs/adr/0057):
the four tunnel checks a Real Cell passes before each lease, and one more, that Tor is not running.
`PropolisProbe` is the seam the attestation calls, so that `attest_propolis_cell` needs to know
nothing of how the checks are made: `SessionPropolisProbe` makes them over the Cell's own session,
`FakePropolisProbe` answers from a table. Unlike `NightVeilProbe` it has one method and not one
per check: the four tunnel checks are judged from the same snapshot of one routing table
and one resolver file, so they are read together or not at all, and a probe that read them one
by one could judge a table that changed between two of them.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.propolis`. Implemented by `hive.propolis.fake.
    FakePropolisProbe` (tests, and any composition root before a real one exists) and `hive.
    propolis.session_probe.SessionPropolisProbe` (the real one, over a `CellSession`). Called by
    `hive.propolis.trail.attest_propolis_cell`. Calls into `hivemind.cell.tunnel` and `hive.
    propolis.tor` for the check names and into `hive.night_veil` for `CheckResult`.

Key invariants:
    - `checks` takes no arguments: whatever a probe needs (a session, the Cell's operating system)
      is bound when it is built, so the attestation runs any implementation the same way.
    - A check that fails is a `CheckResult(status=FAIL)` in the mapping, never an exception; a
      probe may still raise when it could not run at all (a session that was never opened).
    - `PROPOLIS_CHECK_NAMES` lists every check a complete probe reports, in the order they are
      recorded; `attest_propolis_cell` treats one that is missing as failed.

See Also:
    - hivemind.hive.night_veil.probe for NightVeilProbe, the per-check Protocol this one differs
      from, and for why that one is shaped as it is.
    - hivemind.cell.tunnel.judges for the four tunnel checks and their names.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from hivemind.cell.tunnel import CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS, CHECK_IPV6
from hivemind.hive.night_veil import CheckResult
from hivemind.hive.propolis.tor import CHECK_TOR

# Every check a complete Propolis probe reports, in the order it is recorded: the Real Cell's four
# (default route, DNS, the IPv6 judge, direct egress) and then Tor's absence.
PROPOLIS_CHECK_NAMES: tuple[str, ...] = (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_IPV6,
    CHECK_EGRESS,
    CHECK_TOR,
)

__all__ = ["PROPOLIS_CHECK_NAMES", "PropolisProbe"]


class PropolisProbe(Protocol):
    """Run every check a Propolis Cell is attested by and report each one's result by name."""

    async def checks(self) -> Mapping[str, CheckResult]:
        """Run the checks against the Cell this probe was built for.

        Returns:
            One `CheckResult` per name in `PROPOLIS_CHECK_NAMES`; a Cell whose operating system
            has no fixed commands (`platform_supported`) may report that one failed check in the
            tunnel checks' place. A check that could not be answered is FAIL, never absent.
        """
        ...
