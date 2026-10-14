"""Tests for hivemind.hive.propolis.tor: the Propolis check that Tor is not running in a Cell.

Fits into the Hive:
    Mirrors src/hivemind/hive/propolis/tor.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The judge is a pure function over the text of three fixed reads, so most tests
    feed it that text, built in the formats `ps` and the kernel print; the reads themselves run
    over a `FakeSession` to show they are the three fixed argument lists and that one that could
    not be had fails the check. The rule they all share is the tier's: "Tor is disabled" is
    granted only on a positive look.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.propolis.tor for the module under test.
    - README.md "Comb Shield level" for "Propolis ... OpenVPN required, Tor disabled".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import CompletedCommand, ExecSpec, FakeSession
from hivemind.hive.night_veil import CheckStatus
from hivemind.hive.propolis import (
    CHECK_TOR,
    TOR_PORTS,
    TOR_PROCESS_NAMES,
    check_tor_disabled,
    judge_tor_disabled,
)
from hivemind.hive.propolis.tor import PROCESSES, TCP, TCP6
from waggle.clock import FakeClock

_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  inode"
_V4 = "0100007F"  # 127.0.0.1, little-endian as the kernel prints it.
_V6 = "00000000000000000000000001000000"  # ::1, as /proc/net/tcp6 prints it.
_QUIET_PROCESSES = "init\nsleep\nbash\nps\n"
_SSH = f"   0: {_V4}:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0 1 1 1 0"


def _row(port: int, state: str = "0A", host: str = _V4) -> str:
    """One socket row of `/proc/net/tcp`: its local port in hex and its state code."""
    return f"   1: {host}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 0 0 1 1 0"


def _table(*rows: str) -> str:
    """The text of `/proc/net/tcp` (or tcp6) with these rows under its header."""
    return "\n".join([_HEADER, *rows]) + "\n"


def _passes(processes: str, tcp: str, tcp6: str = "") -> bool:
    """Whether the judge passes these three reads."""
    return judge_tor_disabled(processes, tcp, tcp6).status is CheckStatus.PASS


def test_a_cell_with_no_tor_process_and_no_tor_port_passes() -> None:
    result = judge_tor_disabled(_QUIET_PROCESSES, _table(_SSH), _table())

    assert result.status is CheckStatus.PASS
    assert "no Tor" in result.detail


@pytest.mark.parametrize("name", sorted(TOR_PROCESS_NAMES))
def test_a_tor_process_fails_the_check(name: str) -> None:
    result = judge_tor_disabled(f"{_QUIET_PROCESSES}{name}\n", _table(_SSH), _table())

    assert result.status is CheckStatus.FAIL
    assert "process named tor" in result.detail


@pytest.mark.parametrize("port", sorted(TOR_PORTS))
def test_a_socket_listening_on_a_tor_port_fails_the_check(port: int) -> None:
    result = judge_tor_disabled(_QUIET_PROCESSES, _table(_row(port)), _table())

    assert result.status is CheckStatus.FAIL
    assert str(port) in result.detail


def test_a_tor_port_listening_on_ipv6_only_is_seen_too() -> None:
    result = judge_tor_disabled(_QUIET_PROCESSES, _table(_SSH), _table(_row(9050, host=_V6)))

    assert result.status is CheckStatus.FAIL


@pytest.mark.parametrize("port", [9049, 9052, 9149, 9152, 80, 443])
def test_a_port_next_to_tor_s_is_not_tor(port: int) -> None:
    assert _passes(_QUIET_PROCESSES, _table(_row(port)))


@pytest.mark.parametrize("state", ["01", "06", "08"])
def test_a_tor_port_that_is_not_listening_is_not_a_listener(state: str) -> None:
    # An established connection from, or a closing one on, port 9050 is not Tor serving there.
    assert _passes(_QUIET_PROCESSES, _table(_row(9050, state=state)))


def test_a_process_that_merely_contains_tor_in_its_name_is_not_tor() -> None:
    assert _passes("init\ntorsocks\nvictor\nmotor\nps\n", _table(_SSH))


def test_a_process_list_that_could_not_be_read_fails_closed() -> None:
    result = judge_tor_disabled("", _table(_SSH), _table())

    assert result.status is CheckStatus.FAIL
    assert "process list" in result.detail


def test_an_ipv4_table_that_could_not_be_read_fails_closed() -> None:
    result = judge_tor_disabled(_QUIET_PROCESSES, "", _table())

    assert result.status is CheckStatus.FAIL
    assert "listening sockets" in result.detail


def test_a_cell_with_no_ipv6_stack_has_nothing_listening_on_ipv6() -> None:
    # No /proc/net/tcp6 at all is a kernel without IPv6, not a read that failed.
    assert _passes(_QUIET_PROCESSES, _table(_SSH), "")


def test_rows_the_kernel_never_prints_are_skipped_not_raised() -> None:
    junk = "garbage\n\n   2: nothex:zzzz 0:0 0A\n   3: 0100007F:ZZZZ 00000000:0000 0A\n"

    assert _passes(_QUIET_PROCESSES, f"{_HEADER}\n{junk}")


def test_the_detail_never_carries_what_the_cell_printed() -> None:
    private_name = "customer-vpn-profile-name"

    result = judge_tor_disabled(f"{_QUIET_PROCESSES}{private_name}\ntor\n", _table(_SSH), _table())

    assert private_name not in result.detail
    assert len(result.detail) < 100


def _session(
    answers: dict[tuple[str, ...], CompletedCommand], asked: list[ExecSpec]
) -> FakeSession:
    """A session that answers the three reads from `answers` and notes every command it ran."""

    def respond(spec: ExecSpec) -> CompletedCommand:
        asked.append(spec)
        return answers.get(tuple(spec.argv), _done("", exit_code=127))

    return FakeSession(Path("/scratch"), FakeClock(), responder=respond)


def _done(stdout: str, exit_code: int = 0) -> CompletedCommand:
    """A finished command that printed `stdout`."""
    return CompletedCommand(exit_code=exit_code, stdout=stdout.encode(), stderr=b"", duration_s=0.0)


async def test_the_check_runs_exactly_three_fixed_read_only_commands() -> None:
    asked: list[ExecSpec] = []
    answers = {
        PROCESSES.argv: _done(_QUIET_PROCESSES),
        TCP.argv: _done(_table(_SSH)),
        TCP6.argv: _done(_table()),
    }

    result = await check_tor_disabled(_session(answers, asked))

    assert result.status is CheckStatus.PASS
    assert [spec.argv for spec in asked] == [
        ("ps", "-eo", "comm="),
        ("cat", "/proc/net/tcp"),
        ("cat", "/proc/net/tcp6"),
    ]


async def test_a_tor_process_seen_through_the_session_fails_the_check() -> None:
    answers = {
        PROCESSES.argv: _done(f"{_QUIET_PROCESSES}tor\n"),
        TCP.argv: _done(_table(_SSH)),
        TCP6.argv: _done(_table()),
    }

    result = await check_tor_disabled(_session(answers, []))

    assert result.status is CheckStatus.FAIL


async def test_a_command_that_is_not_in_the_image_fails_the_check() -> None:
    # No `ps` (exit 127): Tor's absence is unproven, so the Cell is not attested.
    answers = {TCP.argv: _done(_table(_SSH)), TCP6.argv: _done(_table())}

    result = await check_tor_disabled(_session(answers, []))

    assert result.status is CheckStatus.FAIL
    assert CHECK_TOR == "tor_disabled"
