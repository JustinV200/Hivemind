"""Provide PropolisChecks: verify a device's tunnel through its own session and record the outcome.

A Real Cell in the Propolis tier is a device that already runs a VPN, and the Hive trusts that only
after looking: before work is placed, `PropolisChecks` runs the shared tunnel checks
(`hivemind.cell.tunnel.check_tunnel`: a handful of fixed, read-only commands through the device's
own session, judged by pure functions) and records the outcome as `swarm.shield_verified` or
`swarm.shield_refused`, carrying each check's name, result and short reason code and none of the
device's output. The commands are fixed per operating system and never built from anything a
request or a model said; they read, and install, start, stop and reroute nothing, which on a
borrowed machine would change the whole device and not just the lease. What is specific to a Real
Cell is the record and the lease it names; the commands and the judges are the ones a Virtual Cell
is attested by too, which is why they live in `hivemind.cell.tunnel` and not here.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.shield`.
    Built by the composition root and handed to `ShieldedSource`; runs over the lease's
    `CellSession`, so every command passes the Guard like any other. Calls into hivemind.cell,
    hivemind.cell.tunnel and the swarm recorder.

Key invariants:
    - The commands are those of `hivemind.cell.tunnel`: fixed per operating system, read-only,
      with no argument ever taken from outside.
    - A run that could not measure something reports a failed check, never a pass and never an
      exception to the caller.
    - The trail record names checks and reason codes only: no address, interface or command output.
    - The verifier never installs, starts or reroutes anything.

See Also:
    - README.md "Comb Shield level" for the Propolis tier on a Real Cell.
    - hivemind.cell.tunnel.check for the commands and the judging this delegates to.
    - hivemind.swarm.shield.shielded for the source decorator that calls this.
"""

from __future__ import annotations

from hivemind.cell import OsFamily
from hivemind.cell.lease import RealCellLease
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession
from hivemind.cell.tunnel import ShieldReport, check_tunnel
from hivemind.swarm.events import SwarmRecorder

VERIFIED_KIND = "swarm.shield_verified"
REFUSED_KIND = "swarm.shield_refused"

__all__ = ["REFUSED_KIND", "VERIFIED_KIND", "PropolisChecks"]


class PropolisChecks:
    """Verify, with read-only commands, that a device's tunnel is where Propolis needs it."""

    def __init__(self, recorder: SwarmRecorder) -> None:
        """Build the checks over the trail writer that records each run.

        Args:
            recorder: The trail, clock and identity `swarm.shield_*` events are recorded with.
        """
        self._recorder = recorder

    async def verify(self, cell: Cell, lease: RealCellLease, session: CellSession) -> ShieldReport:
        """Run the checks on a leased device and record how they came out.

        Args:
            cell: The device's Cell; its operating system picks the commands.
            lease: The lease the session runs in, named on the record.
            session: The device's session; the commands run through it, and so through the Guard.

        Returns:
            The report; `is_verified` is True only when every check passed.
        """
        report = await self.run(session, cell.capabilities.os)
        await self._record(cell, lease, report)
        return report

    async def run(self, session: CellSession, os_family: OsFamily) -> ShieldReport:
        """Run the fixed commands for `os_family` and judge their output, recording nothing.

        Args:
            session: A session on the device.
            os_family: The device's operating system.

        Returns:
            The report: four checks for a supported system, one failed check for any other.
        """
        return await check_tunnel(session, os_family)

    async def _record(self, cell: Cell, lease: RealCellLease, report: ShieldReport) -> None:
        """Write `swarm.shield_verified` or `swarm.shield_refused` with the per-check results."""
        kind = VERIFIED_KIND if report.is_verified else REFUSED_KIND
        event = self._recorder.swarm_event(
            kind,
            cell.id,
            {
                "cell_id": cell.id,
                "lease_id": lease.id,
                "checks": [
                    {"name": check.name, "passed": check.has_passed, "reason": check.reason}
                    for check in report.checks
                ],
            },
        )
        await self._recorder.record(event)
