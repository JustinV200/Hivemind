"""Tests for hivemind.entrance.enrol.standing: revoke, lock, unlock and the expiry sweep.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/standing.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.standing for the module under test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from builders.entrance import (
    Enrolment,
    admitted,
    approval,
    enrolment_over,
    entry_event,
    make_device,
    memory_enrolment,
    mint,
)

from hivemind.entrance.enrol import (
    DeviceStatus,
    EnrolledDevice,
    FakeGoalLedger,
    LockReason,
    approve,
    deny,
    expire_due,
    lock,
    revoke,
    unlock,
)
from hivemind.entrance.errors import ConsoleProtectedError, DeviceStatusConflictError
from hivemind.entrance.store import MemoryEntranceStore
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import TaskId

_GOALS = (TaskId("task_01M221E4C10R4XDPNQNRX85AAA"), TaskId("task_01M221E4C10R4XDPNQNRX85AAB"))
_S = DeviceStatus


@pytest.fixture
def rig() -> Enrolment:
    """An enrolment rig over in-memory tables."""
    return memory_enrolment()


async def _console(rig: Enrolment, expires_in: timedelta | None = None) -> EnrolledDevice:
    """Record a loopback-bound console, optionally with an expiry it must never reach."""
    expires_at = rig.clock.now() + expires_in if expires_in is not None else None
    console = make_device(
        rig.clock, _S.APPROVED, loopback_bound=True, interactive=True, expires_at=expires_at
    )
    await rig.store.put_device(console, entry_event(console, rig.clock))
    return console


async def _with_goals(
    finishing: frozenset[TaskId] = frozenset(),
) -> tuple[Enrolment, EnrolledDevice]:
    """A rig and an approved device that has submitted the two goals in _GOALS."""
    goals = FakeGoalLedger(finishing=finishing)
    rig = memory_enrolment(goals)
    device = await admitted(rig)
    goals.submit(device.id, _GOALS)
    return rig, device


# ──────────────────────────────────────────────────────────────────────────────
# Revocation
# ──────────────────────────────────────────────────────────────────────────────


async def test_revoke_withdraws_the_device_and_names_the_goals_left_running() -> None:
    rig, device = await _with_goals()

    revocation = await revoke(rig.deps, device.id, "human", cancel_goals=False)

    assert revocation.device.status is _S.REVOKED
    assert (revocation.goals_cancelled, revocation.goals_left_running) == ((), _GOALS)
    assert rig.goals.cancellations == []
    assert rig.offboarder.offboarded == [(device.id, _S.REVOKED)]
    (event,) = await rig.events("guard.entrance_revoked")
    assert event.payload == {
        "reason": "operator",
        "goals_cancelled": [],
        "goals_cancelled_count": 0,
        "goals_left_running": list(_GOALS),
        "goals_left_running_count": 2,
    }
    assert rig.notifier.notices[-1].event_id == event.id


async def test_revoke_cancels_the_open_goals_in_the_same_step_when_asked() -> None:
    rig, device = await _with_goals()

    revocation = await revoke(rig.deps, device.id, "human", cancel_goals=True)

    assert (revocation.goals_cancelled, revocation.goals_left_running) == (_GOALS, ())
    ((cancelled, reason),) = rig.goals.cancellations
    assert cancelled == _GOALS
    assert device.id in reason
    (event,) = await rig.events("guard.entrance_revoked")
    assert (event.payload["goals_cancelled_count"], event.payload["goals_left_running"]) == (2, [])


async def test_a_goal_that_finishes_before_its_cancellation_is_named_as_left_running() -> None:
    rig, device = await _with_goals(finishing=frozenset({_GOALS[1]}))

    revocation = await revoke(rig.deps, device.id, "human", cancel_goals=True)

    assert (revocation.goals_cancelled, revocation.goals_left_running) == (
        (_GOALS[0],),
        (_GOALS[1],),
    )


async def test_a_locked_device_can_be_revoked(rig: Enrolment) -> None:
    device = await admitted(rig)
    await lock(rig.deps, device.id, "system", LockReason.LOCKOUT)

    revocation = await revoke(rig.deps, device.id, "human", cancel_goals=True)

    assert revocation.device.status is _S.REVOKED
    assert rig.offboarder.offboarded == [(device.id, _S.LOCKED), (device.id, _S.REVOKED)]


async def test_the_console_is_never_revoked(rig: Enrolment) -> None:
    console = await _console(rig)

    with pytest.raises(ConsoleProtectedError, match="operator password --reset"):
        await revoke(rig.deps, console.id, "human", cancel_goals=True)

    assert await rig.store.get_device(console.id) == console
    assert rig.goals.cancellations == []
    assert await rig.events("guard.entrance_revoked") == ()


@pytest.mark.parametrize("status", [_S.PENDING, _S.DENIED])
async def test_revoke_refuses_a_device_that_was_never_admitted(
    rig: Enrolment, status: DeviceStatus
) -> None:
    device = await admitted(rig, _S.PENDING)
    if status is _S.DENIED:
        await deny(rig.deps, device.id, "human", "no")

    with pytest.raises(DeviceStatusConflictError):
        await revoke(rig.deps, device.id, "human", cancel_goals=False)


# ──────────────────────────────────────────────────────────────────────────────
# Lock and unlock
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", list(LockReason))
async def test_lock_cuts_the_device_off_and_records_why(rig: Enrolment, reason: LockReason) -> None:
    device = await admitted(rig)

    locked = await lock(rig.deps, device.id, "system", reason)

    assert locked.status is _S.LOCKED
    assert rig.offboarder.offboarded == [(device.id, _S.LOCKED)]
    (event,) = await rig.events("guard.entrance_locked")
    assert (event.actor, event.payload) == ("system", {"reason": reason.value})
    assert rig.notifier.notices[-1].event_id == event.id


async def test_unlock_reopens_a_locked_device_without_cutting_it_off_again(rig: Enrolment) -> None:
    device = await admitted(rig)
    await lock(rig.deps, device.id, "system", LockReason.DENIAL_BURST)

    unlocked = await unlock(rig.deps, device.id, "human")

    assert unlocked.status is _S.APPROVED
    assert rig.offboarder.offboarded == [(device.id, _S.LOCKED)]
    (event,) = await rig.events("guard.entrance_unlocked")
    assert (event.subject_id, event.actor) == (device.id, "human")


async def test_the_console_can_be_locked_and_unlocked_like_any_device(rig: Enrolment) -> None:
    console = await _console(rig)

    await lock(rig.deps, console.id, "system", LockReason.LOCKOUT)
    unlocked = await unlock(rig.deps, console.id, "human")

    assert unlocked.status is _S.APPROVED
    assert unlocked.loopback_bound is True


async def test_only_an_approved_device_is_locked_and_only_a_locked_one_unlocked(
    rig: Enrolment,
) -> None:
    pending = await admitted(rig, _S.PENDING)
    approved = await admitted(rig)

    with pytest.raises(DeviceStatusConflictError):
        await lock(rig.deps, pending.id, "system", LockReason.LOCKOUT)
    with pytest.raises(DeviceStatusConflictError):
        await unlock(rig.deps, approved.id, "human")


# ──────────────────────────────────────────────────────────────────────────────
# The expiry sweep
# ──────────────────────────────────────────────────────────────────────────────


async def _approved_until(rig: Enrolment, lifetime: timedelta) -> EnrolledDevice:
    """Enrol a device whose approval lapses after ``lifetime``."""
    pending = await admitted(rig, _S.PENDING)
    return await approve(rig.deps, pending.id, approval(expires_at=rig.clock.now() + lifetime))


async def test_the_sweep_expires_every_lapsed_invite_request_and_approval(rig: Enrolment) -> None:
    invited = await mint(rig)
    pending = await admitted(rig, _S.PENDING)
    approved = await _approved_until(rig, timedelta(days=2))
    locked = await _approved_until(rig, timedelta(days=2))
    await lock(rig.deps, locked.id, "system", LockReason.LOCKOUT)
    forever = await admitted(rig)
    console = await _console(rig, expires_in=timedelta(minutes=1))
    rig.clock.advance(timedelta(days=3).total_seconds())

    assert await expire_due(rig.deps) == 4

    expired = {device.id for device in await rig.store.list_devices(_S.EXPIRED)}
    assert expired == {invited.device_id, pending.id, approved.id, locked.id}
    assert {forever.id, console.id} <= {d.id for d in await rig.store.list_devices(_S.APPROVED)}
    swept = [device for device, reason in rig.offboarder.offboarded if reason is _S.EXPIRED]
    assert swept == [approved.id, locked.id]
    events = await rig.events("guard.entrance_expired")
    assert {event.payload["expired_from"] for event in events} == {
        "INVITED",
        "PENDING",
        "APPROVED",
        "LOCKED",
    }
    assert {event.actor for event in events} == {"system"}


async def test_the_sweep_leaves_what_has_not_lapsed(rig: Enrolment) -> None:
    await mint(rig)
    await _approved_until(rig, timedelta(days=2))
    rig.clock.advance(timedelta(minutes=14).total_seconds())

    assert await expire_due(rig.deps) == 0
    assert await rig.events("guard.entrance_expired") == ()


class _DecidedMeanwhile(MemoryEntranceStore):
    """Tables where a decision lands between the sweep's read of PENDING and its move."""

    def __init__(self, trail: MemoryPheromoneTrail) -> None:
        """Start with no decision pending."""
        super().__init__(trail)
        self.meanwhile: Callable[[], Awaitable[object]] | None = None

    async def list_devices(self, status: DeviceStatus | None = None) -> tuple[EnrolledDevice, ...]:
        """List as usual, then let the decision land before the caller acts on the list."""
        listed = await super().list_devices(status)
        if status is _S.PENDING and self.meanwhile is not None:
            meanwhile, self.meanwhile = self.meanwhile, None
            await meanwhile()
        return listed


async def test_the_sweep_never_overrides_a_decision_made_meanwhile() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = _DecidedMeanwhile(trail)
    rig = enrolment_over(store, trail, clock, FakeGoalLedger())
    pending = await admitted(rig, _S.PENDING)
    rig.clock.advance(timedelta(days=2).total_seconds())
    store.meanwhile = lambda: deny(rig.deps, pending.id, "human", "decided in time")

    assert await expire_due(rig.deps) == 0
    assert (await rig.store.get_device(pending.id)).status is _S.DENIED
