"""Tests for hivemind.swarm.shield.propolis: reading a device's tunnel with fixed commands.

Fits into the Hive:
    Mirrors src/hivemind/swarm/shield/propolis.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The checks run over a real `PollenSession` on the scripted device, so each
    command crosses the Guard and the link exactly as it would on a real device, and the device's
    side records which commands it was asked to run. That is how the tests show the two properties
    that make the checks safe on a borrowed machine: the commands are fixed and read-only, and a
    check that could not be measured fails.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.shield.propolis for the module under test.
    - README.md "Comb Shield level" for the tier.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.swarm import (
    DeviceScript,
    ExecScript,
    LinkRig,
    RecordingGuard,
    RouteRow,
    linux_probes,
    macos_probes,
    make_link_rig,
    open_rig_session,
)

from hivemind.cell import OsFamily
from hivemind.cell.lease import RealCellLease
from hivemind.cell.session import CellSession
from hivemind.pheromone import TrailQuery
from hivemind.swarm.shield import PropolisChecks
from hivemind.swarm.shield.propolis import (
    CHECK_PLATFORM,
    COMMANDS,
    MAX_PROBE_BYTES,
    REFUSED_KIND,
    VERIFIED_KIND,
)
from waggle.messages.session.commands import SessionExec

_FULL_TUNNEL: list[RouteRow] = [
    ("tun0", "0.0.0.0/1", 0),
    ("tun0", "128.0.0.0/1", 0),
    ("eth0", "0.0.0.0/0", 100),
    ("tun0", "10.8.0.0/24", 0),
]
_LEAKING: list[RouteRow] = [("eth0", "0.0.0.0/0", 100), ("tun0", "10.8.0.0/24", 0)]
_MAC_TUNNEL = [
    ("default", "192.168.1.1", "UGScg", "en0"),
    ("0/1", "10.8.0.1", "UGSc", "utun4"),
    ("128.0/1", "10.8.0.1", "UGSc", "utun4"),
]


async def _open_leased(
    script: DeviceScript, guard: RecordingGuard | None = None
) -> tuple[LinkRig, CellSession, RealCellLease]:
    """A started link to a device running `script`, with a session open on a lease."""
    rig = await make_link_rig(script)
    session, lease, _guard = await open_rig_session(rig, guard)
    return rig, session, lease


async def _open(
    script: DeviceScript, guard: RecordingGuard | None = None
) -> tuple[LinkRig, CellSession]:
    """Like `_open_leased`, for a test that does not need the lease itself."""
    rig, session, _lease = await _open_leased(script, guard)
    return rig, session


@pytest.fixture
async def rigs() -> AsyncIterator[list[LinkRig]]:
    """The rigs a test opened, all closed afterwards."""
    opened: list[LinkRig] = []
    yield opened
    for rig in opened:
        await rig.close()


def _asked(rig: LinkRig) -> list[tuple[str, ...]]:
    """The command lines the device was asked to run, in order."""
    return [
        envelope.payload.argv
        for envelope in rig.device.state.received
        if isinstance(envelope.payload, SessionExec)
    ]


async def test_a_linux_device_with_a_full_tunnel_is_verified_by_three_fixed_reads(
    rigs: list[LinkRig],
) -> None:
    rig, session = await _open(DeviceScript(execs=linux_probes(_FULL_TUNNEL, (), ["10.8.0.1"])))
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).run(session, OsFamily.LINUX)

    assert report.is_verified
    assert _asked(rig) == [
        ("cat", "/proc/net/route"),
        ("cat", "/proc/net/ipv6_route"),
        ("cat", "/etc/resolv.conf"),
    ]


async def test_a_linux_device_that_leaks_fails_the_checks_that_see_it(rigs: list[LinkRig]) -> None:
    rig, session = await _open(DeviceScript(execs=linux_probes(_LEAKING, (), ["8.8.8.8"])))
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).run(session, OsFamily.LINUX)

    assert not report.is_verified
    assert set(report.failed) == {
        "default_route_via_tunnel",
        "dns_via_tunnel",
        "direct_egress_refused",
    }


async def test_a_macos_device_with_a_full_tunnel_is_verified_by_two_fixed_reads(
    rigs: list[LinkRig],
) -> None:
    rig, session = await _open(DeviceScript(execs=macos_probes(_MAC_TUNNEL, ["10.8.0.1"])))
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).run(session, OsFamily.MACOS)

    assert report.is_verified
    assert _asked(rig) == [("netstat", "-rn"), ("scutil", "--dns")]


async def test_an_operating_system_with_no_commands_fails_one_check_and_runs_nothing(
    rigs: list[LinkRig],
) -> None:
    rig, session = await _open(DeviceScript())
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).run(session, OsFamily.WINDOWS)

    assert [(c.name, c.has_passed, c.reason) for c in report.checks] == [
        (CHECK_PLATFORM, False, "unsupported_platform")
    ]
    assert _asked(rig) == []


@pytest.mark.parametrize("failure", ["exit_code", "unstartable", "denied"])
async def test_a_command_that_could_not_be_read_makes_its_checks_fail_closed(
    rigs: list[LinkRig], failure: str
) -> None:
    probes = linux_probes(_FULL_TUNNEL, (), ["10.8.0.1"])
    guard = RecordingGuard()
    if failure == "exit_code":
        probes["cat /proc/net/route"] = ExecScript(stdout=b"anything", exit_code=1)
    elif failure == "unstartable":
        probes["cat /proc/net/route"] = ExecScript(unstartable=True)
    else:
        guard.deny.add("exec:cat")  # The Guard refuses every read.
    rig, session = await _open(DeviceScript(execs=probes), guard)
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).run(session, OsFamily.LINUX)

    assert not report.is_verified
    assert "default_route_via_tunnel" in report.failed and "direct_egress_refused" in report.failed


async def test_output_beyond_the_read_limit_is_not_trusted(rigs: list[LinkRig]) -> None:
    probes = linux_probes(_FULL_TUNNEL, (), ["10.8.0.1"])
    padding = b"#" * (MAX_PROBE_BYTES + 1)
    probes["cat /etc/resolv.conf"] = ExecScript(stdout=padding + b"\nnameserver 10.8.0.1\n")
    rig, session = await _open(DeviceScript(execs=probes))
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).run(session, OsFamily.LINUX)

    assert "dns_via_tunnel" in report.failed  # The nameserver sat past the limit and was cut off.


async def test_a_verified_run_is_recorded_with_check_names_and_reason_codes_only(
    rigs: list[LinkRig],
) -> None:
    rig, session, lease = await _open_leased(
        DeviceScript(execs=linux_probes(_FULL_TUNNEL, (), ["10.8.0.1"]))
    )
    rigs.append(rig)

    await PropolisChecks(rig.recorder).verify(rig.node.as_cell(), lease, session)

    (event,) = await rig.trail.query(TrailQuery(kind=VERIFIED_KIND))
    checks = event.payload["checks"]
    assert event.subject_id == rig.node.cell_id and event.payload["lease_id"] == lease.id
    assert isinstance(checks, list) and len(checks) == 4
    assert all(isinstance(c, dict) and set(c) == {"name", "passed", "reason"} for c in checks)
    assert "10.8.0.1" not in event.model_dump_json() and "tun0" not in event.model_dump_json()


async def test_a_refused_run_is_recorded_as_refused(rigs: list[LinkRig]) -> None:
    rig, session, lease = await _open_leased(
        DeviceScript(execs=linux_probes(_LEAKING, (), ["8.8.8.8"]))
    )
    rigs.append(rig)

    report = await PropolisChecks(rig.recorder).verify(rig.node.as_cell(), lease, session)

    assert not report.is_verified
    assert len(await rig.trail.query(TrailQuery(kind=REFUSED_KIND))) == 1
    assert await rig.trail.query(TrailQuery(kind=VERIFIED_KIND)) == ()


def test_the_commands_are_fixed_per_operating_system_and_name_no_argument_from_outside() -> None:
    assert set(COMMANDS) == {OsFamily.LINUX, OsFamily.MACOS}
    for commands in COMMANDS.values():
        assert all(isinstance(argument, str) for c in commands for argument in c.argv)
        assert {c.argv[0] for c in commands} <= {"cat", "netstat", "scutil"}
