"""Tests for hivemind.cli.swarm.pinned: a goal pinned to one device, and the wait for the device.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/pinned.py (codingrules section 3). The ceiling is tested against
    the rules that give it meaning, not against a copy of its text: Queen placement's own check (a
    candidate Cell is usable only if the ceiling allows what that Cell asks of a goal), the shipped
    Guard policy's operator role (a ceiling can name nothing the operator could not do) and every
    Worker role's default set (a goal pinned to a device loses no capability a Worker would use).
    The wait runs on a clock that steps itself, and the run is tested with the Hive's goal runner
    replaced by a recorder, so the terms it was handed can be read.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.pinned for the module under test.
    - tests/e2e/test_swarm_level0.py for the same pin driving a real goal on a real packet.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import cast

import pytest
from builders.cells import make_capabilities
from builders.swarm import make_device_party, make_swarm_node

from hivemind.cell import AccessLevel, CombShieldLevel, HoneyClearance
from hivemind.cli.compose.hive import GoalReport, GoalRun, Hive
from hivemind.cli.swarm import pinned
from hivemind.cli.swarm.pinned import (
    DEVICE_POLL_S,
    DEVICE_WORK_CAPABILITIES,
    RELEASE_WAIT_S,
    await_device,
    await_release,
    device_ceiling,
    run_goal_on_device,
)
from hivemind.guard import Capability, CapabilitySet, load_guard_policy, role_set
from hivemind.queen.placement.inventory import RealCandidate
from hivemind.queen.placement.rules import goal_lacks, placement_needs
from hivemind.swarm import GatewayUnavailableError
from hivemind.swarm.models import SwarmNode
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_device_id, new_task_id, new_warden_id
from waggle.messages.task import WorkerRole


class _SteppingClock(FakeClock):
    """A fake clock whose sleep moves its own time, so a polling loop runs without a driver."""

    async def sleep(self, seconds: float) -> None:
        """Advance by `seconds` at once, then let other tasks run."""
        if seconds > 0:
            self.advance(seconds)
        await asyncio.sleep(0)


def _device(clock: FakeClock) -> SwarmNode:
    """An enrolled device with the terms a test needs nothing more of."""
    return make_swarm_node(make_device_party(clock), clock, label="kitchen-pi")


def _candidate(cell_id: CellId, *, is_hive_stand: bool, tier: CombShieldLevel) -> RealCandidate:
    """A Real Cell as placement sees it, with room for one more bee."""
    return RealCandidate(
        warden_id=new_warden_id(FakeClock()),
        cell_id=cell_id,
        capabilities=make_capabilities(),
        comb_shield=tier,
        is_hive_stand=is_hive_stand,
        has_free_capacity=True,
        access_level=AccessLevel.FULL,
    )


def test_the_ceiling_names_the_devices_own_cell_and_no_other_place_to_run() -> None:
    clock = FakeClock()
    node = _device(clock)
    ceiling = CapabilitySet.parse(*device_ceiling(node))

    assert ceiling.allows(Capability.parse(f"cell:real:{node.cell_id}"))
    assert not ceiling.allows(Capability.parse("cell:hive_stand"))
    assert not ceiling.allows(Capability.parse("cell:virtual"))
    assert not ceiling.allows(Capability.parse(f"cell:real:{new_cell_id(clock)}"))
    assert device_ceiling(node)[0] == f"cell:real:{node.cell_id}"


@pytest.mark.parametrize("tier", [CombShieldLevel.MEADOW, CombShieldLevel.PROPOLIS])
def test_placement_finds_the_device_usable_and_the_hive_stand_not(tier: CombShieldLevel) -> None:
    clock = FakeClock()
    node = _device(clock)
    goal = CapabilitySet.parse(*device_ceiling(node))
    device = _candidate(node.cell_id, is_hive_stand=False, tier=tier)
    stand = _candidate(new_cell_id(clock), is_hive_stand=True, tier=tier)

    # `goal_lacks` is the Queen's own rule: the first thing a Cell asks of a goal that the goal's
    # ceiling does not allow, or None when the Cell may take the goal's tasks.
    assert goal_lacks(goal, placement_needs(device)) is None
    lacking = goal_lacks(goal, placement_needs(stand))
    assert lacking is not None and str(lacking) == "cell:hive_stand"


def test_the_ceiling_names_nothing_the_operator_could_not_do() -> None:
    operator = role_set(load_guard_policy(), "operator")

    for text in device_ceiling(_device(FakeClock())):
        assert operator.allows(Capability.parse(text)), text


@pytest.mark.parametrize("role", [role.name.lower() for role in WorkerRole])
def test_the_ceiling_takes_nothing_from_a_worker_of_any_role(role: str) -> None:
    ceiling = CapabilitySet.parse(*device_ceiling(_device(FakeClock())))
    held = role_set(load_guard_policy(), role, PurePosixPath("/scratch"))

    # Attenuating a Worker to the ceiling must keep every capability its role would use.
    for capability in held:
        assert ceiling.allows(capability), f"{role} holds {capability}, the ceiling lacks it"


def test_the_work_capabilities_are_all_well_formed_and_distinct() -> None:
    parsed = [Capability.parse(text) for text in DEVICE_WORK_CAPABILITIES]

    assert len(set(DEVICE_WORK_CAPABILITIES)) == len(DEVICE_WORK_CAPABILITIES)
    assert all(
        str(capability) == text
        for capability, text in zip(parsed, DEVICE_WORK_CAPABILITIES, strict=True)
    )


async def test_await_device_returns_at_once_when_the_device_is_already_served() -> None:
    clock = _SteppingClock()

    await await_device(clock, lambda: True, new_device_id(clock), timeout_s=5.0)

    assert clock.monotonic() == 0.0


async def test_await_device_waits_for_the_warden_to_attach_and_no_longer() -> None:
    clock = _SteppingClock()
    polls = iter([False, False, False, True])

    await await_device(clock, lambda: next(polls), new_device_id(clock), timeout_s=60.0)

    assert clock.monotonic() == pytest.approx(3 * DEVICE_POLL_S)


async def test_await_device_gives_up_naming_the_device_and_what_to_check() -> None:
    clock = _SteppingClock()
    node = _device(clock)

    with pytest.raises(GatewayUnavailableError) as refused:
        await await_device(clock, lambda: False, node.device_id, timeout_s=2.0)

    assert node.device_id in str(refused.value) and "pollen status" in str(refused.value)
    assert clock.monotonic() >= 2.0


async def test_run_goal_on_device_submits_under_the_pin_with_what_the_wait_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _SteppingClock()
    node = _device(clock)
    polls = iter([False, False, True])
    seen: list[tuple[str, GoalRun]] = []
    report = cast("GoalReport", SimpleNamespace(goal_id=new_task_id(clock), timed_out=False))

    async def record(_hive: Hive, goal: str, run: GoalRun) -> GoalReport:
        """Stand in for the Hive's goal runner: keep the terms it was handed."""
        seen.append((goal, run))
        return report

    monkeypatch.setattr(pinned, "run_goal_with", record)

    class _Queen:
        """A Queen whose Wardens change as the wait looks: none twice, then the device's."""

        @property
        def wardens(self) -> list[SimpleNamespace]:
            """The Queen's Wardens as of this look."""
            return [SimpleNamespace(warden_id=node.warden_id)] if next(polls) else []

    hive = cast("Hive", SimpleNamespace(clock=clock, queen=_Queen(), swarm=None))
    run = GoalRun(HoneyClearance.C1, 10.0)

    outcome = await run_goal_on_device(hive, node, "write a report", run)

    assert outcome is report
    ((goal, pinned_run),) = seen
    assert goal == "write a report"
    assert pinned_run.capabilities == device_ceiling(node)
    assert pinned_run.clearance is HoneyClearance.C1
    # Two empty looks cost two polls of the one budget; the goal gets the rest.
    assert pinned_run.timeout_s == pytest.approx(10.0 - 2 * DEVICE_POLL_S)
    # The caller's own GoalRun is not edited: it is a frozen value, replaced.
    assert run.capabilities is None and run.timeout_s == 10.0


async def test_await_release_returns_at_once_when_the_warden_holds_no_lease() -> None:
    clock = _SteppingClock()

    assert await await_release(clock, lambda: False) is True

    assert clock.monotonic() == 0.0


async def test_await_release_waits_for_the_lease_to_go_and_no_longer() -> None:
    clock = _SteppingClock()
    looks = iter([True, True, False])

    assert await await_release(clock, lambda: next(looks)) is True

    assert clock.monotonic() == pytest.approx(2 * DEVICE_POLL_S)


async def test_await_release_gives_up_after_its_budget_and_says_the_lease_is_still_held() -> None:
    clock = _SteppingClock()

    assert await await_release(clock, lambda: True, timeout_s=2.0) is False

    assert 2.0 <= clock.monotonic() < 2.0 + 2 * DEVICE_POLL_S
    assert RELEASE_WAIT_S > 2.0  # The default is longer than the usual beat of a Warden's loop.


class _Gate:
    """A swarm gate whose device Warden holds a lease for some looks, then lets go of it."""

    def __init__(self, looks: int) -> None:
        """Hold the lease for `looks` looks."""
        self.looks_left = looks
        self.looked = 0

    @property
    def wardens(self) -> _Gate:
        """The gate is its own table of Wardens, for the one test that needs one."""
        return self

    def get(self, _device_id: object) -> SimpleNamespace:
        """The device's served Warden; its lease is held while looks remain."""
        self.looked += 1
        held = self.looks_left > 0
        self.looks_left -= 1
        return SimpleNamespace(warden=SimpleNamespace(lease=object() if held else None))


def _stub_goal_runner(monkeypatch: pytest.MonkeyPatch, *, timed_out: bool) -> None:
    """Replace the Hive's goal runner with one that answers a report ended as asked."""
    report = cast("GoalReport", SimpleNamespace(timed_out=timed_out))

    async def finish(_hive: Hive, _goal: str, _run: GoalRun) -> GoalReport:
        """A goal that is over."""
        return report

    monkeypatch.setattr(pinned, "run_goal_with", finish)


async def test_a_finished_goal_is_not_reported_until_the_warden_has_released_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _SteppingClock()
    node = _device(clock)
    gate = _Gate(looks=3)
    swarm = SimpleNamespace(gate=gate)
    queen = SimpleNamespace(wardens=[SimpleNamespace(warden_id=node.warden_id)])
    hive = cast("Hive", SimpleNamespace(clock=clock, queen=queen, swarm=swarm))
    _stub_goal_runner(monkeypatch, timed_out=False)

    await run_goal_on_device(hive, node, "write a report", GoalRun(HoneyClearance.C1, 30.0))

    # Three looks found the lease held and the fourth found it gone: three polls of the wait.
    assert gate.looked == 4
    assert clock.monotonic() == pytest.approx(3 * DEVICE_POLL_S)


async def test_a_goal_that_ran_out_of_time_does_not_wait_for_a_release_that_cannot_come(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _SteppingClock()
    node = _device(clock)
    gate = _Gate(looks=100)  # Its task is still running, so its Warden still holds the lease.
    swarm = SimpleNamespace(gate=gate)
    queen = SimpleNamespace(wardens=[SimpleNamespace(warden_id=node.warden_id)])
    hive = cast("Hive", SimpleNamespace(clock=clock, queen=queen, swarm=swarm))
    _stub_goal_runner(monkeypatch, timed_out=True)

    await run_goal_on_device(hive, node, "write a report", GoalRun(HoneyClearance.C1, 30.0))

    assert gate.looked == 0 and clock.monotonic() == 0.0
