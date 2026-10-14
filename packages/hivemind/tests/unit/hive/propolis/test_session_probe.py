"""Tests for hivemind.hive.propolis.session_probe: SessionPropolisProbe over a FakeSession.

Fits into the Hive:
    Mirrors src/hivemind/hive/propolis/session_probe.py (codingrules section 3). The probe runs the
    Real Cell's own tunnel commands and the Tor reads through a `FakeSession` scripted with the text
    the kernel and `ps` print (built by `builders.swarm`), so the tests show what the attestation
    is made of: the tunnel checks keep their names and become results with the judge's reason as
    the detail, Tor's absence is the fifth, and a Cell the Hive has no commands for is red, not
    skipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.propolis.session_probe for the module under test.
    - tests/unit/cell/tunnel/test_check.py for the tunnel checks themselves.
"""

from __future__ import annotations

from pathlib import Path

from builders.swarm import linux_route, linux_route6, resolv_conf

from hivemind.cell import CompletedCommand, ExecSpec, FakeSession, OsFamily
from hivemind.cell.tunnel import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    CHECK_PLATFORM,
)
from hivemind.hive.night_veil import CheckStatus
from hivemind.hive.propolis import CHECK_TOR, PROPOLIS_CHECK_NAMES, SessionPropolisProbe
from waggle.clock import FakeClock

_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
_TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid"
_FULL_TUNNEL = (("tun0", "0.0.0.0/1"), ("tun0", "128.0.0.0/1"), ("eth0", "0.0.0.0/0"))
_LEAKING = (("eth0", "0.0.0.0/0"), ("tun0", "10.8.0.0/24"))
_QUIET = "init\nsleep\nps\n"


def _done(stdout: str) -> CompletedCommand:
    """A command that exited 0 having printed `stdout`."""
    return CompletedCommand(exit_code=0, stdout=stdout.encode(), stderr=b"", duration_s=0.0)


def _cell(
    routes: tuple[tuple[str, str], ...] = _FULL_TUNNEL,
    nameservers: tuple[str, ...] = ("10.8.0.1",),
    processes: str = _QUIET,
) -> tuple[FakeSession, list[tuple[str, ...]]]:
    """A session on a Linux Cell showing these routes, resolvers and processes, and its log."""
    table = "\n".join([_HEADER, *(linux_route(i, n) for i, n in routes)]) + "\n"
    answers = {
        ("cat", "/proc/net/route"): table,
        ("cat", "/proc/net/ipv6_route"): linux_route6("tun0", "::/0") + "\n",
        ("cat", "/etc/resolv.conf"): resolv_conf(*nameservers),
        ("ps", "-eo", "comm="): processes,
        ("cat", "/proc/net/tcp"): _TCP_HEADER + "\n",
        ("cat", "/proc/net/tcp6"): _TCP_HEADER + "\n",
    }
    asked: list[tuple[str, ...]] = []

    def respond(spec: ExecSpec) -> CompletedCommand:
        asked.append(tuple(spec.argv))
        text = answers.get(tuple(spec.argv))
        return (
            _done(text)
            if text is not None
            else CompletedCommand(exit_code=127, stdout=b"", stderr=b"", duration_s=0.0)
        )

    return FakeSession(Path("/scratch"), FakeClock(), responder=respond), asked


async def test_a_cell_with_a_tunnel_and_no_tor_passes_all_five_checks() -> None:
    session, _asked = _cell()

    results = await SessionPropolisProbe(session, OsFamily.LINUX).checks()

    assert tuple(results) == PROPOLIS_CHECK_NAMES
    assert {result.status for result in results.values()} == {CheckStatus.PASS}


async def test_the_probe_runs_the_tunnel_reads_once_and_then_the_tor_reads() -> None:
    session, asked = _cell()

    await SessionPropolisProbe(session, OsFamily.LINUX).checks()

    assert asked == [
        ("cat", "/proc/net/route"),
        ("cat", "/proc/net/ipv6_route"),
        ("cat", "/etc/resolv.conf"),
        ("ps", "-eo", "comm="),
        ("cat", "/proc/net/tcp"),
        ("cat", "/proc/net/tcp6"),
    ]


async def test_a_cell_that_leaves_by_its_own_interface_fails_the_tunnel_checks_only() -> None:
    session, _asked = _cell(routes=_LEAKING, nameservers=("8.8.8.8",))

    results = await SessionPropolisProbe(session, OsFamily.LINUX).checks()

    red = {name for name, result in results.items() if result.status is CheckStatus.FAIL}
    assert red == {CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS}
    assert results[CHECK_IPV6].status is CheckStatus.PASS
    assert results[CHECK_TOR].status is CheckStatus.PASS


async def test_a_cell_running_tor_fails_the_tor_check_and_nothing_else() -> None:
    session, _asked = _cell(processes=f"{_QUIET}tor\n")

    results = await SessionPropolisProbe(session, OsFamily.LINUX).checks()

    red = {name for name, result in results.items() if result.status is CheckStatus.FAIL}
    assert red == {CHECK_TOR}


async def test_a_result_s_detail_is_the_judge_s_reason_and_never_what_the_cell_printed() -> None:
    session, _asked = _cell(routes=_LEAKING, nameservers=("8.8.8.8",))

    results = await SessionPropolisProbe(session, OsFamily.LINUX).checks()

    assert results[CHECK_DEFAULT_ROUTE].detail == "direct_default_route"
    assert results[CHECK_DNS].detail == "dns_not_via_tunnel"
    for result in results.values():
        assert "8.8.8.8" not in result.detail
        assert "eth0" not in result.detail


async def test_a_cell_the_hive_has_no_commands_for_fails_a_platform_check() -> None:
    session, asked = _cell()

    results = await SessionPropolisProbe(session, OsFamily.WINDOWS).checks()

    assert results[CHECK_PLATFORM].status is CheckStatus.FAIL
    assert results[CHECK_PLATFORM].detail == "unsupported_platform"
    assert CHECK_DEFAULT_ROUTE not in results
    # The tunnel commands were never run; the Tor reads still were, and were still judged.
    assert ("cat", "/proc/net/route") not in asked
    assert results[CHECK_TOR].status is CheckStatus.PASS
