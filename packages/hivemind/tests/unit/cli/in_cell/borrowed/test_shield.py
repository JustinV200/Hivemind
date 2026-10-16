"""Tests for hivemind.cli.in_cell.borrowed.shield: the Propolis tier on a colonized device.

The report runs the real tunnel checks through a real `ObserverSession` on this machine, so the
checks it names are the fixed ones for the machine's operating system (or the one refusal for a
system the Hive has no commands for); the shielded source wraps a real `HiveStandSource` over a
temporary directory, with its Cell made to report an operating system the checks cannot run on,
so the refusal is the same on every host whatever tunnel it has.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/shield.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.shield for the module under test.
    - tests/unit/swarm/shield for the checks and the wrapper themselves, over a scripted device.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.borrowed import BorrowedKit, make_kit

from hivemind.cell import AccessLevel, Cell, CellSession, OsFamily, RealCellSource
from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cell.local import HiveStandSource
from hivemind.cell.source import CellIdentity
from hivemind.cell.tunnel import (
    CHECK_DEFAULT_ROUTE,
    CHECK_DNS,
    CHECK_EGRESS,
    CHECK_IPV6,
    CHECK_PLATFORM,
)
from hivemind.cli.in_cell.borrowed import (
    BorrowedConfig,
    build_borrowed_config,
    own_shield_report,
    shielded_source,
)
from hivemind.manifest import read_in_cell_env
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.swarm import ShieldedSource, ShieldUnverifiedError, SwarmRecorder
from hivemind.swarm.shield.propolis import REFUSED_KIND
from waggle.clock import SystemClock

_CLOCK = SystemClock()
_FOUR = {CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_IPV6, CHECK_EGRESS}


class _ReportedAs:
    """A source whose one Cell reports the operating system a test chooses; the rest passes."""

    def __init__(self, inner: RealCellSource, os_family: OsFamily) -> None:
        self._inner = inner
        self._os = os_family

    @property
    def name(self) -> str:
        return self._inner.name

    async def cells(self) -> tuple[Cell, ...]:
        (cell,) = await self._inner.cells()
        capabilities = cell.capabilities.model_copy(update={"os": self._os})
        return (cell.model_copy(update={"capabilities": capabilities}),)

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        return await self._inner.lease(request)

    async def open_session(self, lease: RealCellLease) -> CellSession:
        return await self._inner.open_session(lease)


def _config(tmp_path: Path, tier: str) -> tuple[BorrowedKit, BorrowedConfig]:
    """A device kit at `tier`, and its validated config."""
    kit = make_kit(tmp_path, "ws://127.0.0.1:8720", access_level="FULL")
    kit.environ["HIVEMIND_COMB_SHIELD"] = tier
    return kit, build_borrowed_config(read_in_cell_env(kit.environ), _CLOCK)


def _identity(config: BorrowedConfig) -> CellIdentity:
    """Who the Warden records as: itself, on the device's node, in the Queen's Hive."""
    runtime = config.runtime
    return CellIdentity(
        hive_id=runtime.hive_id, node_id=runtime.node_id, actor=str(runtime.warden_id)
    )


def _machine(config: BorrowedConfig, trail: MemoryPheromoneTrail) -> HiveStandSource:
    """The real source a device Warden leases its own machine through, recording to `trail`."""
    leavings = InMemoryLeavingsStore(trail)
    return HiveStandSource(config.cell, _identity(config), trail, _CLOCK, leavings)


async def _cell_as(config: BorrowedConfig, os_family: OsFamily) -> Cell:
    """The device's Cell as the source describes it, reporting `os_family`."""
    (cell,) = await _ReportedAs(_machine(config, MemoryPheromoneTrail(_CLOCK)), os_family).cells()
    return cell


async def test_a_meadow_device_reports_no_checks(tmp_path: Path) -> None:
    _kit, config = _config(tmp_path, "MEADOW")

    assert await own_shield_report(config, _CLOCK)(await _cell_as(config, OsFamily.LINUX)) == ()


async def test_a_propolis_device_reports_the_tunnel_checks_of_its_own_system(
    tmp_path: Path,
) -> None:
    _kit, config = _config(tmp_path, "PROPOLIS")

    checks = await own_shield_report(config, _CLOCK)(await _cell_as(config, OsFamily.LINUX))

    # The four Real Cell checks, each with the judges' short reason and never raising, whatever
    # this host's routes are: a report, not a gate.
    assert {check.name for check in checks} == _FOUR
    assert all(check.detail for check in checks)


async def test_a_system_the_hive_has_no_commands_for_reports_one_failed_check(
    tmp_path: Path,
) -> None:
    _kit, config = _config(tmp_path, "PROPOLIS")

    checks = await own_shield_report(config, _CLOCK)(await _cell_as(config, OsFamily.WINDOWS))

    # Still a valid CellReady (never empty), and honest: nothing was measured, so nothing passed.
    assert [(check.name, check.has_passed) for check in checks] == [(CHECK_PLATFORM, False)]


async def test_a_meadow_devices_source_is_left_as_it_is(tmp_path: Path) -> None:
    _kit, config = _config(tmp_path, "MEADOW")
    trail = MemoryPheromoneTrail(_CLOCK)
    machine = _machine(config, trail)
    recorder = SwarmRecorder(trail=trail, clock=_CLOCK, identity=_identity(config))

    assert shielded_source(config, machine, recorder) is machine


async def test_a_propolis_devices_lease_is_held_back_until_its_tunnel_is_verified(
    tmp_path: Path,
) -> None:
    _kit, config = _config(tmp_path, "PROPOLIS")
    trail = MemoryPheromoneTrail(_CLOCK)
    machine = _machine(config, trail)
    recorder = SwarmRecorder(trail=trail, clock=_CLOCK, identity=_identity(config))
    # The Cell reports a system the checks cannot run on, so the outcome does not depend on the
    # tunnel this host happens to have.
    source = shielded_source(config, _ReportedAs(machine, OsFamily.WINDOWS), recorder)
    assert isinstance(source, ShieldedSource)
    (cell,) = await source.cells()
    request = LeaseRequest(
        cell_id=cell.id,
        holder=config.runtime.warden_id,
        task_id=None,
        access_level=AccessLevel.FULL,
        allowed_paths=(),
    )

    with pytest.raises(ShieldUnverifiedError):
        await source.lease(request)

    kinds = [event.kind for event in await trail.query(TrailQuery())]
    assert kinds.count("cell.leased") == kinds.count("cell.released") == 1  # Given back at once.
    [refused] = [event for event in await trail.query(TrailQuery()) if event.kind == REFUSED_KIND]
    checks = refused.payload["checks"]
    assert isinstance(checks, list)
    assert [check["name"] for check in checks if isinstance(check, dict)] == [CHECK_PLATFORM]
