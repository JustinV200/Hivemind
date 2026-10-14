"""Tests for hivemind.cli.in_cell.propolis: the checks a Propolis Cell makes of itself.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/propolis.py (codingrules section 3). The Cell reads three files
    and judges them with the shared tunnel judges, so each test hands it a table of file texts
    (built in the formats the kernel prints, by `builders.swarm`) instead of its own /proc: a Cell
    with a tunnel passes, one without fails, and a file it cannot read is a failed check, never an
    error and never a pass.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.propolis for the module under test.
    - tests/unit/cell/tunnel/test_check.py for the judging of the same text read through a session.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from builders.swarm import linux_route, linux_route6, resolv_conf

from hivemind.cell.tunnel import CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS, CHECK_IPV6
from hivemind.cli.in_cell.propolis import own_tunnel_attestation, read_text

_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
_ROUTES = Path("/proc/net/route")
_ROUTES6 = Path("/proc/net/ipv6_route")
_RESOLV = Path("/etc/resolv.conf")
_NAMES = [CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_IPV6, CHECK_EGRESS]


def _files(rows: list[tuple[str, str]], nameserver: str) -> dict[Path, str]:
    """The text of the three files of a Cell with these routes and this one resolver."""
    table = "\n".join([_HEADER, *(linux_route(i, n) for i, n in rows)]) + "\n"
    return {
        _ROUTES: table,
        _ROUTES6: linux_route6("tun0", "::/0") + "\n",
        _RESOLV: resolv_conf(nameserver),
    }


def _reader(files: dict[Path, str], asked: list[Path] | None = None) -> Callable[[Path], str]:
    """A file reader over a table; a path the table lacks is a file that could not be read."""

    def read(path: Path) -> str:
        if asked is not None:
            asked.append(path)
        return files.get(path, "")

    return read


def test_a_cell_with_a_full_tunnel_reports_four_passed_checks() -> None:
    files = _files(
        [("tun0", "0.0.0.0/1"), ("tun0", "128.0.0.0/1"), ("eth0", "0.0.0.0/0")], "10.8.0.1"
    )

    checks = own_tunnel_attestation(_reader(files))

    assert [check.name for check in checks] == _NAMES
    assert all(check.has_passed for check in checks)


def test_a_cell_with_no_tunnel_announces_failed_checks_rather_than_refusing_to_announce() -> None:
    files = _files([("eth0", "0.0.0.0/0"), ("tun0", "10.8.0.0/24")], "8.8.8.8")

    checks = own_tunnel_attestation(_reader(files))

    assert {check.name: check.has_passed for check in checks} == {
        CHECK_DEFAULT_ROUTE: False,
        CHECK_DNS: False,
        CHECK_IPV6: True,
        CHECK_EGRESS: False,
    }


def test_the_detail_is_the_judges_short_reason_and_never_what_the_cell_printed() -> None:
    files = _files([("eth0", "0.0.0.0/0")], "8.8.8.8")

    checks = own_tunnel_attestation(_reader(files))

    assert {check.name: check.detail for check in checks}[
        CHECK_DEFAULT_ROUTE
    ] == "direct_default_route"
    assert all("8.8.8.8" not in check.detail and "eth0" not in check.detail for check in checks)


def test_files_that_cannot_be_read_fail_closed_and_the_report_is_still_never_empty() -> None:
    checks = own_tunnel_attestation(_reader({}))

    assert [check.name for check in checks] == _NAMES
    assert [check.has_passed for check in checks] == [False, False, True, False]


def test_exactly_the_three_shared_files_are_read() -> None:
    asked: list[Path] = []

    own_tunnel_attestation(_reader({}, asked))

    assert asked == [_ROUTES, _ROUTES6, _RESOLV]


def test_read_text_returns_a_files_text_and_nothing_for_one_that_is_not_there(
    tmp_path: Path,
) -> None:
    present = tmp_path / "resolv.conf"
    present.write_text("nameserver 10.8.0.1\n", encoding="utf-8")

    assert read_text(present) == "nameserver 10.8.0.1\n"
    assert read_text(tmp_path / "ipv6_route") == ""  # No IPv6 stack: the file is simply absent.
    assert read_text(tmp_path) == ""  # Not a file at all.


def test_read_text_replaces_what_is_not_text_instead_of_raising(tmp_path: Path) -> None:
    odd = tmp_path / "route"
    odd.write_bytes(b"eth0\t\xff\xfe\n")

    assert "eth0" in read_text(odd)
