"""Provide check_tunnel: read a Cell's routes and DNS with fixed commands, and judge its tunnel.

A Cell in the Propolis tier (the Comb Shield tier that requires a VPN) keeps its tunnel running
itself, and the Hive trusts that only after looking. `check_tunnel` runs a handful of fixed,
read-only commands through the Cell's own session and hands their output to the pure judges. The
commands are chosen by the Cell's operating system from a table, never built from anything a
request or a model said: `cat /proc/net/route`, `cat /proc/net/ipv6_route` and
`cat /etc/resolv.conf` on Linux; `netstat -rn` and `scutil --dns` on macOS. They read; they
install, start, stop and reroute nothing, which on a borrowed machine would change the whole
device and not just the lease, and on a Virtual Cell would mean configuring a Cell the Hive is
meant to attest from its image. A command that cannot be run or answers with a failure
contributes nothing, so its check fails closed; an operating system with no command table
(Windows today) fails a single `platform_supported` check, because a tier that cannot be verified
is not granted. The same function serves a Real Cell (`hivemind.swarm.shield`, before each lease)
and a Virtual one (`hivemind.hive.propolis`, before the Cell is ready), which is why it sits here.
`judge_outputs` is the pure half: the judging of already-read text, for a caller that reads the
three files another way, as the in-Cell Warden does of its own Cell (`hivemind.cli.in_cell`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.tunnel`. Called
    by `hivemind.swarm.shield.propolis` and `hivemind.hive.propolis.session_probe`, each over the
    `CellSession` of the Cell it verifies, so every command passes the Guard like any other;
    `judge_outputs` also by `hivemind.cli.in_cell.propolis`. Calls into `hivemind.cell.session`,
    the tunnel judges and routes, and the standard library.

Key invariants:
    - The commands are fixed per operating system and read-only; no argument is ever taken from
      outside this module.
    - A run that could not measure something reports a failed check, never a pass and never an
      exception to the caller.
    - Nothing here records anything: the caller names the checks and reason codes on its own
      trail record and never the Cell's output.

See Also:
    - README.md "Comb Shield level" for the Propolis tier.
    - hivemind.cell.tunnel.judges for the four checks these commands feed.
    - hivemind.swarm.shield.propolis and hivemind.hive.propolis for the two callers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from hivemind.cell.needs import OsFamily
from hivemind.cell.session import CellSession, ExecSpec, run
from hivemind.cell.tunnel.judges import CheckResult, ShieldReport, judge_all
from hivemind.cell.tunnel.routes import (
    IpAddress,
    Route,
    parse_linux_routes,
    parse_linux_routes6,
    parse_macos_routes,
    parse_nameservers,
)
from hivemind.common.errors import HiveMindError

# Seconds a read-only probe may take: each reads a small kernel table or one short command.
PROBE_TIMEOUT_S = 15.0
# The most of a probe's output that is read; a routing table is a few kilobytes, so more is a
# Cell doing something unexpected, and the tail is not trusted.
MAX_PROBE_BYTES = 1_048_576

CHECK_PLATFORM = "platform_supported"

__all__ = [
    "CHECK_PLATFORM",
    "COMMANDS",
    "MAX_PROBE_BYTES",
    "PROBE_TIMEOUT_S",
    "ProbeCommand",
    "check_tunnel",
    "judge_outputs",
    "read_command",
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


def _linux_routes(outputs: Mapping[str, str]) -> tuple[Route, ...]:
    """Read Linux's two route tables; a missing IPv6 table is no IPv6 route."""
    return parse_linux_routes(outputs.get("routes", "")) + parse_linux_routes6(
        outputs.get("routes6", "")
    )


def _macos_routes(outputs: Mapping[str, str]) -> tuple[Route, ...]:
    """Read macOS's one `netstat` table, both address families."""
    return parse_macos_routes(outputs.get("routes", ""))


_ROUTE_READERS: dict[OsFamily, Callable[[Mapping[str, str]], tuple[Route, ...]]] = {
    OsFamily.LINUX: _linux_routes,
    OsFamily.MACOS: _macos_routes,
}


async def check_tunnel(session: CellSession, os_family: OsFamily) -> ShieldReport:
    """Run the fixed commands for `os_family` on a Cell and judge their output, recording nothing.

    Args:
        session: A session on the Cell; every command runs through it.
        os_family: The Cell's operating system, which picks the commands.

    Returns:
        The report: four checks for a supported system, one failed check for any other.
    """
    commands = COMMANDS.get(os_family)
    if commands is None:
        return _unsupported()  # Nothing is run on a system the Hive has no fixed command for.
    outputs = {command.key: await read_command(session, command) for command in commands}
    return judge_outputs(os_family, outputs)


def judge_outputs(os_family: OsFamily, outputs: Mapping[str, str]) -> ShieldReport:
    """Judge a Cell's tunnel from the text its commands printed, however that text was read.

    Pure: no command runs and nothing is read here.

    Args:
        os_family: The Cell's operating system, which says how its text is laid out.
        outputs: The text of each command, by `ProbeCommand.key` ("routes", "routes6" and "dns"
            on Linux, "routes" and "dns" on macOS); a key left out is text that could not be had.

    Returns:
        The report: four checks for a supported system, one failed check for any other.
    """
    reader = _ROUTE_READERS.get(os_family)
    if reader is None:
        return _unsupported()
    nameservers: tuple[IpAddress, ...] = parse_nameservers(outputs.get("dns", ""))
    return judge_all(reader(outputs), nameservers)


def _unsupported() -> ShieldReport:
    """The report for a system with no fixed commands: one failed platform check."""
    return ShieldReport((CheckResult(CHECK_PLATFORM, False, "unsupported_platform"),))


async def read_command(session: CellSession, command: ProbeCommand) -> str:
    """Run one fixed command and return its standard output, or "" if it could not be had.

    Args:
        session: A session on the Cell.
        command: The fixed command to run.

    Returns:
        Its standard output, decoded and capped at `MAX_PROBE_BYTES`; "" when the command was
        denied, timed out, lost its link or exited non-zero, so the judges see nothing and fail
        closed.
    """
    try:
        completed = await run(session, ExecSpec(argv=command.argv, timeout_s=PROBE_TIMEOUT_S))
    except HiveMindError:
        # Denied, timed out, the link lost: nothing was measured, so the judges see nothing and
        # their checks fail closed.
        return ""
    if completed.exit_code != 0:
        return ""
    return completed.stdout[:MAX_PROBE_BYTES].decode("utf-8", errors="replace")
