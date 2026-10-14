"""Tests for hivemind.cli.compose.swarm.lease_scope: a device Warden's command guard, per lease.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/swarm/lease_scope.py (codingrules section 3). The Enforcer is
    the real one over the shipped Guard policy, the leases come from a `FakeCellSource` (so their
    scratch roots and access levels are what a device would report), and the trail is in memory.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.swarm.lease_scope for the module under test.
    - hivemind.swarm.session.guard for EnforcedCommandGuard, which makes each check.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.cells import make_cell, make_identity
from builders.swarm import make_hive_party

from hivemind.cell import AccessLevel, Cell, CellKind, LeaseRefusedError, RealCellLease
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.lease import LeaseRequest
from hivemind.cli.compose.swarm import LeaseScopedGuard, LeaseScopedSource
from hivemind.guard import Capability, Enforcer, load_guard_policy
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm import CommandDeniedError, DeviceCommand, SwarmRecorder
from waggle.clock import FakeClock
from waggle.ids import LeaseId, WardenId, new_lease_id, new_message_id, new_warden_id


@dataclass
class Scope:
    """A guard, the source it is scoped to, and what a test reads back."""

    clock: FakeClock
    trail: MemoryPheromoneTrail
    cell: Cell
    warden_id: WardenId
    guard: LeaseScopedGuard
    inner: FakeCellSource
    source: LeaseScopedSource

    async def lease(self, access: AccessLevel) -> RealCellLease:
        """Open a lease on the device's Cell at `access`, through the scoped source."""
        request = LeaseRequest(
            cell_id=self.cell.id, holder=self.warden_id, task_id=None, access_level=access
        )
        return await self.source.lease(request)


def _scope() -> Scope:
    """A guard over a device Cell, nothing leased yet."""
    clock, hive = FakeClock(), make_hive_party(FakeClock())
    trail = MemoryPheromoneTrail(clock)
    identity = make_identity(clock, hive_id=hive.hive_id, node_id=hive.node_id)
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    warden_id = new_warden_id(clock)
    enforcer = Enforcer(load_guard_policy(), trail, clock, identity)
    guard = LeaseScopedGuard(enforcer, SwarmRecorder(trail, clock, identity), warden_id, cell)
    inner = FakeCellSource((cell,), identity, trail, clock)
    return Scope(clock, trail, cell, warden_id, guard, inner, LeaseScopedSource(inner, guard))


def _command(lease_id: LeaseId, *needs: str) -> DeviceCommand:
    """A command on `lease_id` needing each of `needs`, as a session would describe it."""
    return DeviceCommand(
        operation="exec", lease_id=lease_id, needs=tuple(Capability.parse(n) for n in needs)
    )


async def test_a_scratch_lease_may_write_inside_its_scratch_and_nowhere_else() -> None:
    scope = _scope()
    lease = await scope.lease(AccessLevel.SCRATCH)
    inside = f"fs:write:{lease.scratch_root}/report.txt"

    await scope.guard.authorise(_command(lease.id, inside))  # Allowed: `{scratch}` is this lease's.

    with pytest.raises(CommandDeniedError):
        await scope.guard.authorise(_command(lease.id, "fs:write:/etc/hosts"))


async def test_the_scratch_a_lease_may_write_is_its_own_not_another_leases() -> None:
    scope = _scope()
    first = await scope.lease(AccessLevel.SCRATCH)
    await first.release()
    second = await scope.lease(AccessLevel.SCRATCH)

    # The set was rebuilt for the new lease: its root is writable and the old lease's is not.
    await scope.guard.authorise(_command(second.id, f"fs:write:{second.scratch_root}/a.txt"))
    with pytest.raises(CommandDeniedError):
        await scope.guard.authorise(_command(second.id, f"fs:write:{first.scratch_root}/a.txt"))


async def test_a_read_only_lease_runs_no_command_but_may_read() -> None:
    scope = _scope()
    lease = await scope.lease(AccessLevel.READ_ONLY)

    await scope.guard.authorise(_command(lease.id, "fs:read:/etc/hostname"))
    with pytest.raises(CommandDeniedError):
        await scope.guard.authorise(_command(lease.id, "exec:ls"))


async def test_a_full_lease_may_run_a_program_and_reach_outside_its_scratch() -> None:
    scope = _scope()
    lease = await scope.lease(AccessLevel.FULL)

    await scope.guard.authorise(_command(lease.id, "exec:ls"))
    await scope.guard.authorise(
        _command(
            lease.id, "fs:write:/home/user/note.txt", "cell:outside_scratch:/home/user/note.txt"
        )
    )


async def test_a_command_on_a_lease_the_guard_was_never_told_about_is_refused() -> None:
    scope = _scope()
    unknown = new_lease_id(scope.clock)

    with pytest.raises(CommandDeniedError):  # Nothing bound yet: it fails closed.
        await scope.guard.authorise(_command(unknown, "exec:ls"))

    lease = await scope.lease(AccessLevel.FULL)
    with pytest.raises(CommandDeniedError):  # Bound to another lease: still not this one.
        await scope.guard.authorise(_command(unknown, "exec:ls"))
    await scope.guard.authorise(_command(lease.id, "exec:ls"))


async def test_a_new_lease_replaces_the_one_before_it() -> None:
    scope = _scope()
    old = await scope.lease(AccessLevel.FULL)
    await old.release()
    new = await scope.lease(AccessLevel.FULL)

    with pytest.raises(CommandDeniedError):
        await scope.guard.authorise(_command(old.id, "exec:ls"))  # The device holds one at a time.
    await scope.guard.authorise(_command(new.id, "exec:ls"))


async def test_a_command_is_recorded_as_sent_by_the_devices_warden() -> None:
    scope = _scope()
    lease = await scope.lease(AccessLevel.FULL)
    command = _command(lease.id, "exec:ls")

    await scope.guard.authorise(command)
    await scope.guard.record_sent(command, new_message_id(scope.clock))

    [sent] = await scope.trail.query(TrailQuery(kind="swarm.command_sent"))
    assert sent.actor == scope.warden_id  # The trail names the Warden, not the process.
    assert sent.payload["lease_id"] == lease.id
    assert sent.payload["operation"] == "exec"


async def test_a_command_on_an_unknown_lease_is_not_recorded() -> None:
    scope = _scope()

    with pytest.raises(CommandDeniedError):
        await scope.guard.record_sent(
            _command(new_lease_id(scope.clock)), new_message_id(scope.clock)
        )

    assert await scope.trail.query(TrailQuery(kind="swarm.command_sent")) == ()


async def test_the_source_passes_everything_but_the_lease_straight_through() -> None:
    scope = _scope()

    assert scope.source.name == scope.inner.name
    assert await scope.source.cells() == await scope.inner.cells()
    lease = await scope.lease(AccessLevel.SCRATCH)
    session = await scope.source.open_session(lease)
    assert session.scratch_dir == lease.scratch_root


async def test_a_lease_the_source_refuses_leaves_the_guard_as_it_was() -> None:
    scope = _scope()
    first = await scope.lease(AccessLevel.FULL)

    with pytest.raises(LeaseRefusedError):
        await scope.lease(AccessLevel.FULL)  # The device is already leased.

    await scope.guard.authorise(_command(first.id, "exec:ls"))  # Still bound to the first.
