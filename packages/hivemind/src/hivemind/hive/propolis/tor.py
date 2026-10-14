"""Provide the Tor-is-disabled check: three fixed reads of a Cell and a pure judge over them.

The Propolis tier is "OpenVPN required, Tor disabled" (README, "Comb Shield level"): a Cell at it
sends its traffic through its VPN tunnel and nothing else, and Tor, which belongs to Night Veil,
must not be running in it. A Real Cell is a borrowed device and what else runs on it is the
operator's affair, so the Tor check is the Virtual Cell's alone; there the Hive attests an image it
chose, and a Tor daemon in it is a sign that the image is not Propolis's. Two things show Tor at
work, and the check looks for both: a process named `tor` (the system daemon, or the one Tor
Browser bundles), and a socket listening on a port Tor usually takes (its SOCKS ports 9050 and 9150,
its control ports 9051 and 9151, its transparent proxy 9040). The reads are `ps -eo comm=`,
`cat /proc/net/tcp` and `cat /proc/net/tcp6`, each a tool the minimal Ubuntu image already carries
and each read-only; `judge_tor_disabled` is a pure function over their text. A read that could not
be had fails the check, because "Tor is disabled" is granted only on a positive look: the process
list and the IPv4 table must be readable, and a Cell with no IPv6 stack has no `tcp6` table and
therefore nothing listening on it.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.propolis`. Called by `hive.propolis.session_probe.
    SessionPropolisProbe` over the Cell's own session. Calls into `hivemind.cell` (CellSession),
    `hivemind.cell.tunnel` (ProbeCommand, read_command: the same bounded, fail-closed reader the
    tunnel checks use) and `hivemind.hive.night_veil` (CheckResult, CheckStatus) only.

Key invariants:
    - `judge_tor_disabled` is pure and total: the same text always gives the same result, and no
      input raises.
    - A result's `detail` is a short fixed sentence naming at most Tor's own well-known ports; it
      never holds a process list, an address or any other text the Cell produced, because the
      result is recorded on the trail.
    - The commands are fixed and read-only; no argument is ever taken from outside this module.
    - A check that could not read what it needs fails, never passes.

See Also:
    - README.md "Comb Shield level" for "Propolis ... OpenVPN required, Tor disabled".
    - hivemind.hive.night_veil.session_commands for Night Veil's opposite check, that Tor is up.
    - docs/adr/0057-propolis-is-verified-by-looking.md for why this check is a Virtual Cell's only.
"""

from __future__ import annotations

from hivemind.cell import CellSession
from hivemind.cell.tunnel import ProbeCommand, read_command
from hivemind.hive.night_veil import CheckResult, CheckStatus

CHECK_TOR = "tor_disabled"

# The command name (`comm`) Tor runs under: the system daemon, and Tor Browser's bundled one (an
# older bundle called it tor.real). `ps` shows the name, not the path, so a renamed copy is not
# seen, which is why the listening ports are looked at as well.
TOR_PROCESS_NAMES = frozenset({"tor", "tor.real"})
# The ports Tor listens on by default: SOCKS (the daemon's, then Tor Browser's), control (likewise)
# and the transparent proxy. Only TCP, because Tor's SOCKS and control interfaces are TCP.
TOR_PORTS = frozenset({9040, 9050, 9051, 9150, 9151})

PROCESSES = ProbeCommand("processes", ("ps", "-eo", "comm="))
TCP = ProbeCommand("tcp", ("cat", "/proc/net/tcp"))
TCP6 = ProbeCommand("tcp6", ("cat", "/proc/net/tcp6"))

_LISTEN = (
    "0A"  # The kernel's code for a socket in the LISTEN state, the fourth column of the table.
)

__all__ = [
    "CHECK_TOR",
    "PROCESSES",
    "TCP",
    "TCP6",
    "TOR_PORTS",
    "TOR_PROCESS_NAMES",
    "check_tor_disabled",
    "judge_tor_disabled",
]


async def check_tor_disabled(session: CellSession) -> CheckResult:
    """Read the Cell's processes and listening sockets, and judge that Tor is not among them.

    Args:
        session: A session on the Cell; the three fixed commands run through it.

    Returns:
        PASS when no Tor process runs and no Tor port listens; FAIL otherwise, or when what the
        check needs could not be read. Never raises for a command that failed.
    """
    processes = await read_command(session, PROCESSES)
    tcp = await read_command(session, TCP)
    tcp6 = await read_command(session, TCP6)
    return judge_tor_disabled(processes, tcp, tcp6)


def judge_tor_disabled(processes: str, tcp: str, tcp6: str) -> CheckResult:
    """Judge that no Tor process runs and no Tor port listens, from the text of three reads.

    Args:
        processes: The output of `ps -eo comm=`: one command name per line; "" if unreadable.
        tcp: The text of `/proc/net/tcp`; "" if unreadable.
        tcp6: The text of `/proc/net/tcp6`; "" when the Cell has no IPv6 stack.

    Returns:
        FAIL when a Tor process runs, a Tor port listens, or the process list or the IPv4 table
        could not be read; PASS otherwise. The detail says which, in a fixed sentence.
    """
    # Nothing readable is nothing seen: an empty process list cannot happen (the reader itself is
    # in it), so "" means the read failed and Tor's absence is unproven.
    if not processes.strip():
        return _fail("the process list could not be read")
    if not tcp.strip():
        return _fail("the listening sockets could not be read")
    if _running(processes):
        return _fail("a process named tor is running")
    ports = _listening(tcp) | _listening(tcp6)
    if ports:
        return _fail(f"a socket listens on Tor port {', '.join(map(str, sorted(ports)))}")
    return CheckResult(status=CheckStatus.PASS, detail="no Tor process and no Tor port listening")


def _fail(detail: str) -> CheckResult:
    """A red result with a fixed `detail` sentence."""
    return CheckResult(status=CheckStatus.FAIL, detail=detail)


def _running(processes: str) -> bool:
    """Whether any line of `ps -eo comm=` names a Tor process."""
    return any(line.strip() in TOR_PROCESS_NAMES for line in processes.splitlines())


def _listening(table: str) -> set[int]:
    """The Tor ports a `/proc/net/tcp` or `/proc/net/tcp6` table shows a socket listening on."""
    found: set[int] = set()
    for line in table.splitlines()[1:]:  # The first row is the column header.
        fields = line.split()
        if len(fields) < 4 or fields[3] != _LISTEN:
            continue
        # The local address is `<hex address>:<hex port>`; the port is the last colon's right.
        try:
            port = int(fields[1].rpartition(":")[2], 16)
        except ValueError:
            continue  # A row the kernel never prints; skipped like the route parsers skip one.
        if port in TOR_PORTS:
            found.add(port)
    return found
