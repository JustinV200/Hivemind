"""Provide PropolisChecks: read a device's routes and DNS with fixed commands, and judge the tunnel.

A Real Cell in the Propolis tier is a device that already runs a VPN, and the Hive trusts that only
after looking: before work is placed, `PropolisChecks` runs a handful of fixed, read-only commands
through the device's own session and hands their output to the pure judges. The commands are
chosen by the device's operating system from a table, never built from anything a request or a
model said: `cat /proc/net/route`, `cat /proc/net/ipv6_route` and `cat /etc/resolv.conf` on Linux;
`netstat -rn` and `scutil --dns` on macOS. They read; they install, start, stop and reroute
nothing, which on a borrowed machine would change the whole device and not just the lease. A
command that cannot be run or answers with a failure contributes nothing, so its check fails
closed; an operating system with no command table (Windows today) fails a single
`platform_supported` check, because a tier that cannot be verified is not granted. Every run is
recorded as `swarm.shield_verified` or `swarm.shield_refused`, carrying each check's name, result
and short reason code and none of the device's output.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.shield`.
    Built by the composition root and handed to `ShieldedSource`; runs over the lease's
    `CellSession`, so every command passes the Guard like any other. Calls into hivemind.cell,
    the shield's routes and judges, and the swarm recorder.

Key invariants:
    - The commands are fixed per operating system and read-only; no argument is ever taken from
      outside this module.
    - A run that could not measure something reports a failed check, never a pass and never an
      exception to the caller.
    - The trail record names checks and reason codes only: no address, interface or command output.
    - The verifier never installs, starts or reroutes anything.

See Also:
    - README.md "Comb Shield level" for the Propolis tier on a Real Cell.
    - hivemind.swarm.shield.judges for the four checks these commands feed.
    - hivemind.swarm.shield.shielded for the source decorator that calls this.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hivemind.cell import OsFamily
from hivemind.cell.lease import RealCellLease
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession, ExecSpec, run
from hivemind.common.errors import HiveMindError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.shield.judges import CheckResult, ShieldReport, judge_all
from hivemind.swarm.shield.routes import (
    IpAddress,
    Route,
    parse_linux_routes,
    parse_linux_routes6,
    parse_macos_routes,
    parse_nameservers,
)

# Seconds a read-only probe may take: each reads a small kernel table or one short command.
PROBE_TIMEOUT_S = 15.0
# The most of a probe's output that is read; a routing table is a few kilobytes, so more is a
# device doing something unexpected, and the tail is not trusted.
MAX_PROBE_BYTES = 1_048_576

CHECK_PLATFORM = "platform_supported"
VERIFIED_KIND = "swarm.shield_verified"
REFUSED_KIND = "swarm.shield_refused"

__all__ = [
    "CHECK_PLATFORM",
    "MAX_PROBE_BYTES",
    "PROBE_TIMEOUT_S",
    "REFUSED_KIND",
    "VERIFIED_KIND",
    "ProbeCommand",
    "PropolisChecks",
]


@dataclass(frozen=True, slots=True)
class ProbeCommand:
    """One fixed, read-only command and the name its output is kept under."""

    key: str
    argv: tuple[str, ...]


_LINUX = (
    ProbeCommand("routes", ("cat", "/proc/net/route")),
    ProbeCommand("routes6", ("cat", "/proc/net/ipv6_route")),
    ProbeCommand("dns", ("cat", "/etc/resolv.conf")),
)
_MACOS = (
    ProbeCommand("routes", ("netstat", "-rn")),
    ProbeCommand("dns", ("scutil", "--dns")),
)
# One entry per operating system that can be verified; any other fails the platform check.
COMMANDS: dict[OsFamily, tuple[ProbeCommand, ...]] = {
    OsFamily.LINUX: _LINUX,
    OsFamily.MACOS: _MACOS,
}


def _linux_routes(outputs: dict[str, str]) -> tuple[Route, ...]:
    """Read Linux's two route tables; a missing IPv6 table is no IPv6 route."""
    return parse_linux_routes(outputs.get("routes", "")) + parse_linux_routes6(
        outputs.get("routes6", "")
    )


def _macos_routes(outputs: dict[str, str]) -> tuple[Route, ...]:
    """Read macOS's one `netstat` table, both address families."""
    return parse_macos_routes(outputs.get("routes", ""))


_ROUTE_READERS: dict[OsFamily, Callable[[dict[str, str]], tuple[Route, ...]]] = {
    OsFamily.LINUX: _linux_routes,
    OsFamily.MACOS: _macos_routes,
}


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
        commands = COMMANDS.get(os_family)
        if commands is None:
            return ShieldReport((CheckResult(CHECK_PLATFORM, False, "unsupported_platform"),))
        outputs = {command.key: await _read(session, command) for command in commands}
        routes = _ROUTE_READERS[os_family](outputs)
        nameservers: tuple[IpAddress, ...] = parse_nameservers(outputs.get("dns", ""))
        return judge_all(routes, nameservers)

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


async def _read(session: CellSession, command: ProbeCommand) -> str:
    """Run one fixed command and return its standard output, or "" if it could not be had."""
    try:
        completed = await run(session, ExecSpec(argv=command.argv, timeout_s=PROBE_TIMEOUT_S))
    except HiveMindError:
        # Denied, timed out, the link lost: nothing was measured, so the judges see nothing and
        # their checks fail closed.
        return ""
    if completed.exit_code != 0:
        return ""
    return completed.stdout[:MAX_PROBE_BYTES].decode("utf-8", errors="replace")
