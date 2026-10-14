"""Define SessionPropolisProbe: the real PropolisProbe, run over the Virtual Cell's own session.

A Virtual Cell at the Propolis tier is attested by looking at it, never by configuring it
(ADR-0030's rule for Night Veil, carried to this tier by docs/adr/0057): `SessionPropolisProbe` runs
the very commands and judges a Real Cell is verified by (`hivemind.cell.tunnel.check_tunnel`: the
default route through the tunnel, DNS through it, no direct IPv6 path, direct egress refused) and
then the one check only a Virtual Cell has, that Tor is not running (`hive.propolis.tor`). Every
command is fixed, read-only and runs through the `CellSession` it was given, so it passes the Guard
like any other, and one that cannot be had makes its check fail rather than pass. The tunnel
checks' answers (a name, a pass and a short stable reason) become the attestation's own
`CheckResult`, with the reason as the detail, so the one `cell.attested` event reads the same for
every check.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.propolis`. Implements `hive.propolis.probe.
    PropolisProbe`. Built by whatever composition root can open a session on a Virtual Cell (the
    seam `hivemind.cli.compose.virtual_cells` does not yet have, so it hands the provider a
    fail-closed factory instead). Calls into `hivemind.cell` (CellSession, OsFamily),
    `hivemind.cell.tunnel` (check_tunnel), `hive.propolis.tor` and `hive.night_veil` only.

Key invariants:
    - The tunnel commands are run once and all four tunnel checks are judged from that one
      snapshot of the routing table and resolver file.
    - Nothing here installs, starts, stops or reroutes anything in the Cell.
    - A check's `detail` is the tunnel judges' short stable reason or the Tor judge's fixed
      sentence; never a route, an address or any other text the Cell produced.

See Also:
    - hivemind.cell.tunnel for the checks shared with a Real Cell's shield.
    - hivemind.hive.night_veil.session_probe for Night Veil's counterpart, check by check.
"""

from __future__ import annotations

from hivemind.cell import CellSession, OsFamily
from hivemind.cell.tunnel import CheckResult as TunnelCheck
from hivemind.cell.tunnel import check_tunnel
from hivemind.hive.night_veil import CheckResult, CheckStatus
from hivemind.hive.propolis.tor import CHECK_TOR, check_tor_disabled

__all__ = ["SessionPropolisProbe"]


class SessionPropolisProbe:
    """PropolisProbe over a live CellSession: the tunnel checks, then Tor's absence."""

    def __init__(self, session: CellSession, os_family: OsFamily) -> None:
        """Build the probe for one Cell.

        Args:
            session: The Cell's own terminal session, already open; every command runs on it.
            os_family: The Cell's operating system (`Cell.capabilities.os`), which picks the
                tunnel commands; a Virtual Cell image is Ubuntu, so LINUX.
        """
        self._session = session
        self._os_family = os_family

    async def checks(self) -> dict[str, CheckResult]:
        """Run the tunnel checks and the Tor check against the Cell.

        Returns:
            The tunnel report's checks by name (or `platform_supported` alone, failed, for an
            operating system with no fixed commands) and `tor_disabled`; none of them raises.
        """
        report = await check_tunnel(self._session, self._os_family)
        results = {check.name: _verdict(check) for check in report.checks}
        results[CHECK_TOR] = await check_tor_disabled(self._session)
        return results


def _verdict(check: TunnelCheck) -> CheckResult:
    """Turn a tunnel judge's answer into an attestation result, its reason as the detail."""
    status = CheckStatus.PASS if check.has_passed else CheckStatus.FAIL
    return CheckResult(status=status, detail=check.reason)
