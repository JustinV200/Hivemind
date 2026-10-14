"""Report the Propolis checks an in-Cell Warden makes of its own Cell, for the CellReady it sends.

A Cell announced at the Propolis tier (the Comb Shield tier that requires a VPN and disables Tor)
must carry check results on its `CellReady`: the wire model requires some exactly when the tier is
not MEADOW, so that a tiered Cell can never announce itself with no checks run, which is also why
a Cell used to refuse the tier outright. What this Cell can truthfully say of itself is the Real
Cell's four tunnel checks (the default route, DNS, the IPv6 path and direct egress), made with the
very same pure judges (`hivemind.cell.tunnel`). It does not run commands to make them: it is the
machine, so it reads the three files those commands would print (`/proc/net/route`,
`/proc/net/ipv6_route` and `/etc/resolv.conf`, taken from the shared command table so the two lists
cannot drift) and hands the text to `judge_outputs`. A file it cannot read is a failed check, and a
failed check is announced as failed: the announcement is a report, not a gate. The Hive attests the
image from outside before the Cell is ready (`hivemind.hive.propolis`) and that attestation alone
decides readiness, exactly as Night Veil's does over the checks its Cell reports of its own link.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Called by
    `hivemind.cli.in_cell.main` when the Cell's tier is PROPOLIS, to fill `CellLinkDeps.
    attestation`. Calls into `hivemind.cell` (OsFamily), `hivemind.cell.tunnel` (the shared command
    table and judges), waggle's `AttestationCheck` and the standard library.

Key invariants:
    - Nothing is run, installed, started or rerouted: three files are read, and only to be judged.
    - An unreadable file is empty text, so its checks fail closed; this never raises.
    - A check's detail is the judges' short stable reason, never a route, an address or any other
      text the Cell printed.
    - A Virtual Cell image is Ubuntu Linux (codingrules section 2), so the Linux layout is used.

See Also:
    - docs/adr/0057-propolis-is-verified-by-looking.md for the decision.
    - hivemind.cli.in_cell.link for `control_link_attestation`, Night Veil's counterpart.
    - hivemind.cell.tunnel.check for `judge_outputs` and the command table.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from hivemind.cell import OsFamily
from hivemind.cell.tunnel import COMMANDS, MAX_PROBE_BYTES, judge_outputs
from waggle.messages.cell.status import AttestationCheck

# A way of reading one file's text; a test hands in a table so the Cell's own /proc is not read.
FileReader = Callable[[Path], str]

# The three files the shared Linux commands print, by the key their output is kept under. Each of
# those commands is `cat <path>`, so the path is the second word of its argument list.
_FILES: dict[str, Path] = {
    command.key: Path(command.argv[1]) for command in COMMANDS[OsFamily.LINUX]
}

__all__ = ["FileReader", "own_tunnel_attestation", "read_text"]


def own_tunnel_attestation(read: FileReader | None = None) -> tuple[AttestationCheck, ...]:
    """Judge this Cell's own tunnel and return the checks its CellReady carries.

    Args:
        read: How to read a file's text; None reads it from this machine with `read_text`.

    Returns:
        One `AttestationCheck` per tunnel check, in the judges' order, each carrying the judge's
        short reason as its detail. Never empty, so the CellReady that carries it is valid.
    """
    reader = read if read is not None else read_text
    outputs = {key: reader(path) for key, path in _FILES.items()}
    report = judge_outputs(OsFamily.LINUX, outputs)
    return tuple(
        AttestationCheck(name=check.name, has_passed=check.has_passed, detail=check.reason)
        for check in report.checks
    )


def read_text(path: Path) -> str:
    """Return the text of `path`, capped at the size the tunnel checks read, or "" if unreadable."""
    try:
        return path.read_bytes()[:MAX_PROBE_BYTES].decode("utf-8", errors="replace")
    except OSError:
        # Missing (no IPv6 stack has no ipv6_route), denied, or gone: nothing was read, so the
        # judges see nothing and their checks fail closed, as they do for a command that failed.
        return ""
