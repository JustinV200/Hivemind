"""Tests for hivemind.swarm.session.guard: the check and the record in front of a device command.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/guard.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The real Guard (the shipped policy) decides; the trail is in memory.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.guard for the module under test.
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the Guard.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest
from builders.cells import make_identity

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.guard import (
    Capability,
    Enforcer,
    PolicyContext,
    load_guard_policy,
    warden_principal,
    warden_set,
)
from hivemind.manifest import GuardSection
from hivemind.pheromone import GuardEvent, SwarmEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.errors import CommandDeniedError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.session.guard import (
    COMMAND_SENT_KIND,
    DeviceCommand,
    EnforcedCommandGuard,
)
from waggle.clock import FakeClock
from waggle.ids import new_lease_id, new_message_id, new_task_id, new_warden_id

_SCRATCH = PurePosixPath("/pollen/scratch/lease_1")


def _guard(
    access: AccessLevel,
) -> tuple[EnforcedCommandGuard, MemoryPheromoneTrail, FakeClock, str]:
    """Build a guard for a Warden holding its ceiling for `access`, over an in-memory trail."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = make_identity(clock)
    policy = load_guard_policy(None, GuardSection())
    warden = new_warden_id(clock)
    guard = EnforcedCommandGuard(
        Enforcer(policy, trail, clock, identity),
        SwarmRecorder(trail, clock, identity),
        warden_principal(warden),
        warden_set(policy, access, _SCRATCH),
        PolicyContext(comb_shield=CombShieldLevel.MEADOW, access_level=access),
    )
    return guard, trail, clock, warden


def _command(clock: FakeClock, *needs: str, **fields: object) -> DeviceCommand:
    """Build a device command needing the given capability strings."""
    base: dict[str, object] = {
        "operation": "exec",
        "lease_id": new_lease_id(clock),
        "needs": tuple(Capability.parse(need) for need in needs),
        "program": "ls",
        "argument_count": 1,
    }
    base.update(fields)
    return DeviceCommand(**base)  # type: ignore[arg-type]  # a builder's untyped fields.


async def test_a_command_the_warden_holds_is_authorised_and_writes_nothing() -> None:
    guard, trail, clock, _warden = _guard(AccessLevel.FULL)

    await guard.authorise(_command(clock, "exec:ls"))

    assert await trail.query(TrailQuery()) == ()


async def test_a_command_the_warden_does_not_hold_is_denied_and_recorded_as_guard_denied() -> None:
    guard, trail, clock, warden = _guard(AccessLevel.READ_ONLY)
    command = _command(clock, "exec:ls")

    with pytest.raises(CommandDeniedError) as raised:
        await guard.authorise(command)

    (event,) = await trail.query(TrailQuery())
    assert isinstance(event, GuardEvent)
    assert event.kind == "guard.denied"
    assert event.subject_id == warden
    assert raised.value.needed == "exec:ls"
    assert raised.value.lease_id == command.lease_id


async def test_every_needed_capability_is_checked_and_the_first_refusal_stops_the_rest() -> None:
    guard, trail, clock, _warden = _guard(AccessLevel.SCRATCH)
    # SCRATCH holds the read; it runs no command on a borrowed machine (ADR-0060).
    command = _command(clock, "fs:read:/etc/hosts", "cell:outside_scratch:/etc/passwd", "exec:cat")

    with pytest.raises(CommandDeniedError) as raised:
        await guard.authorise(command)

    assert raised.value.needed == "cell:outside_scratch:/etc/passwd"
    assert len(await trail.query(TrailQuery())) == 1


async def test_a_command_with_no_needs_is_always_authorised() -> None:
    guard, _trail, clock, _warden = _guard(AccessLevel.READ_ONLY)

    await guard.authorise(_command(clock, operation="stop"))


async def test_the_sent_record_names_ids_and_counts_and_is_attributed_to_the_warden() -> None:
    guard, trail, clock, warden = _guard(AccessLevel.FULL)
    task = new_task_id(clock)
    command = _command(clock, "exec:ls", task_id=task, outside_scratch=True)
    request_id = new_message_id(clock)

    await guard.record_sent(command, request_id)

    (event,) = await trail.query(TrailQuery(kind=COMMAND_SENT_KIND))
    assert isinstance(event, SwarmEvent)
    assert event.actor == warden
    assert event.subject_id == command.lease_id
    assert event.payload == {
        "lease_id": command.lease_id,
        "request_id": request_id,
        "operation": "exec",
        "program": "ls",
        "argument_count": 1,
        "outside_scratch": True,
        "task_id": task,
        "worker_id": None,
    }


async def test_the_sent_record_holds_no_argument_path_or_content_key() -> None:
    guard, trail, clock, _warden = _guard(AccessLevel.FULL)
    command = _command(clock, "fs:write:/secret/path", operation="put_file", program=None)

    await guard.record_sent(command, new_message_id(clock))

    (event,) = await trail.query(TrailQuery(kind=COMMAND_SENT_KIND))
    assert "/secret/path" not in str(event.payload)
    assert set(event.payload) == {
        "lease_id",
        "request_id",
        "operation",
        "program",
        "argument_count",
        "outside_scratch",
        "task_id",
        "worker_id",
    }
