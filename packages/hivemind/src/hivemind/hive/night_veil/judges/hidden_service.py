"""Judge the HTTP status curl printed for a plain GET to the Hive Stand's hidden service.

Night Veil's `hidden_service_reachable` check (roadmap step 5.7b) asks the Cell to dial the Hive
Stand's Tor hidden service (a `.onion` address only Tor can reach) through its SOCKS proxy and say
what answered. The Hive Stand's end of that address is the Queen's WebSocket listener (the one
socket every Warden dials), and a plain GET to a WebSocket endpoint is not a handshake, so it
answers `426 Upgrade Required`: that is the status a live Waggle listener gives, and it is the one
this judge accepts. It is judged as a status, not searched for as a digit in the output: a `2`
anywhere in `502` or `521` (a proxy in front of nothing) or in `000` plus a stray byte passed the
old substring test, and so did the 426 itself, by accident. Anything else is red: no answer, a
reply that is not a status code, or a service answering with a different one (a web server, an
error page, a proxy), because that is not the Hive Stand's listener.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hive.night_veil.judges`. Called by `hive.night_veil.
    session_probe.SessionNightVeilProbe` with the `CompletedCommand` of the curl it ran. Calls
    into `hivemind.cell` (CompletedCommand) and `hive.night_veil.results` only.

Key invariants:
    - Pure and total: the same command result always gives the same verdict, and nothing raises.
    - Green needs both a clean exit and exactly the expected, well-formed three-digit status.
    - A result's `detail` is a fixed word or a three-digit status, never text the Cell produced
      beyond that number, because the result is recorded on the trail.

See Also:
    - hivemind.hive.night_veil.session_commands.hidden_service_cmd for the command judged here.
    - docs/adr/0004-waggle-transport-websocket-json.md for the listener behind the address.
"""

from __future__ import annotations

import re

from hivemind.cell import CompletedCommand
from hivemind.hive.night_veil.results import CheckResult, verdict

# What a WebSocket endpoint answers a plain GET with: `Upgrade Required` (RFC 9110, 15.5.22), the
# very reply the Hive Stand's listener gives (checked against a real one by the unit tests).
WEBSOCKET_PROBE_STATUS = 426
# One well-formed HTTP status code and nothing else: three digits, the first 1 to 5. curl's "000"
# (no HTTP response at all) and any stray text around the number are not one.
_STATUS = re.compile(r"[1-5][0-9]{2}")

__all__ = ["WEBSOCKET_PROBE_STATUS", "judge_http_status"]


def judge_http_status(completed: CompletedCommand, *, expected: int, ok_detail: str) -> CheckResult:
    """Pass iff curl exited cleanly and printed exactly the HTTP status `expected`.

    Args:
        completed: The curl run, with `-w %{http_code}` printing only the status to standard out.
        expected: The one status that proves the right service answered.
        ok_detail: The fixed sentence a green result carries.

    Returns:
        PASS when the exit code is zero and the output is `expected`; FAIL otherwise, saying whether
        curl failed, nothing status-like was printed, or another status answered.
    """
    # A non-zero exit means no HTTP exchange completed (refused by Tor, no circuit, a timeout), so
    # whatever was printed is not an answer.
    if completed.exit_code != 0:
        return verdict(passed=False, detail=f"exit_code={completed.exit_code}")
    printed = completed.stdout.decode("utf-8", errors="replace").strip()
    # A well-formed status or nothing: this is what keeps "000" and garbage from reading as a reply.
    if _STATUS.fullmatch(printed) is None:
        return verdict(passed=False, detail="no_http_status")
    if int(printed) != expected:
        return verdict(passed=False, detail=f"http_status={printed}")
    return verdict(passed=True, detail=ok_detail)
