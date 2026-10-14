"""Tests for hivemind.cell.tunnel.check: reading a Cell's tunnel with fixed read-only commands.

Fits into the Hive:
    Mirrors src/hivemind/cell/tunnel/check.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The commands run over a `FakeSession` scripted with the text the kernel and the
    BSD tools print (built by `builders.swarm`), so the tests show the three properties that make
    the checks safe on a Cell the Hive does not own: the commands are fixed and read-only, a
    command that could not be had makes its checks fail, and a system with no command table is
    never granted the tier.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.tunnel.check for the module under test.
    - tests/unit/swarm/shield/test_propolis.py for the same commands over a real PollenSession.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.swarm import (
    linux_route,
    linux_route6,
    macos_netstat,
    resolv_conf,
    scutil_dns,
)

from hivemind.cell import CompletedCommand, ExecSpec, FakeSession, OsFamily
from hivemind.cell.tunnel import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    CHECK_PLATFORM,
    COMMANDS,
    MAX_PROBE_BYTES,
    PROBE_TIMEOUT_S,
    check_tunnel,
    judge_outputs,
    read_command,
)
from waggle.clock import FakeClock

_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
_FULL_TUNNEL = ("tun0", "0.0.0.0/1"), ("tun0", "128.0.0.0/1"), ("eth0", "0.0.0.0/0")
_LEAKING = (("eth0", "0.0.0.0/0"), ("tun0", "10.8.0.0/24"))
_LINUX_ARGV = tuple(command.argv for command in COMMANDS[OsFamily.LINUX])


def _done(stdout: str, exit_code: int = 0) -> CompletedCommand:
    """A finished command that printed `stdout`."""
    return CompletedCommand(exit_code=exit_code, stdout=stdout.encode(), stderr=b"", duration_s=0.0)


def _linux_table(rows: tuple[tuple[str, str], ...]) -> str:
    """The text of `/proc/net/route` with these (interface, network) rows."""
    return "\n".join([_HEADER, *(linux_route(i, n) for i, n in rows)]) + "\n"


def _session(
    answers: dict[tuple[str, ...], CompletedCommand],
    asked: list[ExecSpec] | None = None,
    slow: frozenset[str] = frozenset(),
) -> FakeSession:
    """A session answering each fixed command from `answers`, and noting what it was asked."""

    def respond(spec: ExecSpec) -> CompletedCommand:
        if asked is not None:
            asked.append(spec)
        return answers.get(tuple(spec.argv), _done("", exit_code=127))

    return FakeSession(Path("/scratch"), FakeClock(), responder=respond, slow_commands=slow)


def _linux_answers(
    rows: tuple[tuple[str, str], ...], nameservers: tuple[str, ...]
) -> dict[tuple[str, ...], CompletedCommand]:
    """What a Linux Cell prints for its three commands: IPv4 routes, no IPv6, its resolvers."""
    table6 = linux_route6("tun0", "::/0") + "\n"
    texts = (_linux_table(rows), table6, resolv_conf(*nameservers))
    return {argv: _done(text) for argv, text in zip(_LINUX_ARGV, texts, strict=True)}


async def test_a_linux_cell_with_a_full_tunnel_passes_every_check() -> None:
    session = _session(_linux_answers(_FULL_TUNNEL, ("10.8.0.1",)))

    report = await check_tunnel(session, OsFamily.LINUX)

    assert report.is_verified
    assert [check.name for check in report.checks] == [
        CHECK_DEFAULT_ROUTE,
        CHECK_DNS,
        CHECK_IPV6,
        CHECK_EGRESS,
    ]


async def test_only_the_three_fixed_reads_are_ever_run() -> None:
    asked: list[ExecSpec] = []
    session = _session(_linux_answers(_FULL_TUNNEL, ("10.8.0.1",)), asked)

    await check_tunnel(session, OsFamily.LINUX)

    # Argument lists, in order, each bounded by the probe timeout: nothing a request could shape.
    assert [spec.argv for spec in asked] == list(_LINUX_ARGV)
    assert {spec.timeout_s for spec in asked} == {PROBE_TIMEOUT_S}


async def test_a_cell_that_leaks_fails_the_checks_that_see_it() -> None:
    session = _session(_linux_answers(_LEAKING, ("8.8.8.8",)))

    report = await check_tunnel(session, OsFamily.LINUX)

    assert not report.is_verified
    assert CHECK_DEFAULT_ROUTE in report.failed
    assert CHECK_DNS in report.failed
    assert CHECK_EGRESS in report.failed


@pytest.mark.parametrize("unreadable", _LINUX_ARGV[:1] + _LINUX_ARGV[2:])
async def test_a_command_that_fails_makes_its_checks_fail_closed(
    unreadable: tuple[str, ...],
) -> None:
    answers = _linux_answers(_FULL_TUNNEL, ("10.8.0.1",))
    answers[unreadable] = _done("", exit_code=1)

    report = await check_tunnel(_session(answers), OsFamily.LINUX)

    assert not report.is_verified


async def test_a_command_that_times_out_fails_closed_and_never_raises() -> None:
    session = _session(_linux_answers(_FULL_TUNNEL, ("10.8.0.1",)), slow=frozenset({"cat"}))

    report = await check_tunnel(session, OsFamily.LINUX)

    assert not report.is_verified
    assert set(report.failed) >= {CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS}


async def test_a_macos_cell_is_read_with_netstat_and_scutil() -> None:
    rows = [
        ("default", "192.168.1.1", "UGScg", "en0"),
        ("0/1", "10.8.0.1", "UGSc", "utun4"),
        ("128.0/1", "10.8.0.1", "UGSc", "utun4"),
    ]
    answers: dict[tuple[str, ...], CompletedCommand] = {
        ("netstat", "-rn"): _done(macos_netstat(rows)),
        ("scutil", "--dns"): _done(scutil_dns("10.8.0.1")),
    }

    report = await check_tunnel(_session(answers), OsFamily.MACOS)

    assert report.is_verified


async def test_a_system_with_no_command_table_is_never_granted_the_tier() -> None:
    asked: list[ExecSpec] = []

    report = await check_tunnel(_session({}, asked), OsFamily.WINDOWS)

    assert not report.is_verified
    assert report.failed == (CHECK_PLATFORM,)
    assert asked == []  # Nothing is run on a system the Hive has no fixed command for.


async def test_read_command_returns_what_the_command_printed() -> None:
    command = COMMANDS[OsFamily.LINUX][2]
    session = _session({command.argv: _done("nameserver 10.8.0.1\n")})

    assert await read_command(session, command) == "nameserver 10.8.0.1\n"


async def test_read_command_caps_what_it_reads() -> None:
    command = COMMANDS[OsFamily.LINUX][0]
    session = _session({command.argv: _done("x" * (MAX_PROBE_BYTES + 10))})

    assert len(await read_command(session, command)) == MAX_PROBE_BYTES


async def test_read_command_is_empty_when_the_command_could_not_be_run() -> None:
    command = COMMANDS[OsFamily.LINUX][0]

    assert await read_command(_session({}), command) == ""  # Not scripted: exit 127.
    assert await read_command(_session({}, slow=frozenset({"cat"})), command) == ""


def test_judge_outputs_judges_text_that_was_read_some_other_way() -> None:
    # What the in-Cell Warden does of its own Cell: the same text, read from files not commands.
    outputs = {
        "routes": _linux_table(_FULL_TUNNEL),
        "routes6": linux_route6("tun0", "::/0") + "\n",
        "dns": resolv_conf("10.8.0.1"),
    }

    assert judge_outputs(OsFamily.LINUX, outputs).is_verified


def test_judge_outputs_fails_closed_for_text_that_was_never_read() -> None:
    report = judge_outputs(OsFamily.LINUX, {})

    assert not report.is_verified
    assert CHECK_DEFAULT_ROUTE in report.failed


def test_judge_outputs_agrees_with_check_tunnel_for_a_system_with_no_commands() -> None:
    assert judge_outputs(OsFamily.WINDOWS, {}).failed == (CHECK_PLATFORM,)
