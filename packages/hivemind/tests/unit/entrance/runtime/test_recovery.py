"""Test hivemind.entrance.runtime.recovery: a confirmed goal a crash cut off is committed on start.

Over real listeners and a real Queen, whose tables start as a crash left them: a program's goal
held for a person's yes was settled CONFIRMED, but its goal request never reached the Queen. On
start, before it listens, the Entrance commits it under the id minted when it was held, exactly
once however often recovery runs; a hold whose device lost its approval meanwhile is dropped.

Fits into the Hive:
    Mirrors src/hivemind/entrance/runtime/recovery.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.entrance.records import (
    entry_event,
    make_device,
    make_pending,
    pending_event,
    walk_to,
)
from builders.entrance.serving import RigOptions, Seed, serving

from hivemind.entrance.auth.confirm import PendingStatus, Settlement
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.runtime import submit_confirmed_goals
from hivemind.entrance.store import MemoryEntranceStore
from hivemind.queen import GoalRequestQuery, GoalRequestState, QueenDeps
from hivemind.queen.intake import GoalRequestNotFoundError, new_goal_request_id
from waggle.ids import DeviceId

_TEXT = "Tidy the garden notes into one page."


@dataclass(slots=True)
class _Left:
    """What the crash left: the device that asked, and the id its goal was minted with."""

    device_id: DeviceId | None = None
    request_id: str | None = None


def _crashed(left: _Left, status: DeviceStatus) -> Seed:
    """Seed a device in ``status`` whose held goal was confirmed but never committed."""

    async def seed(store: MemoryEntranceStore, deps: QueenDeps) -> None:
        clock = deps.clock
        # A device enters INVITED and walks the state machine to where the crash found it.
        invited = make_device(clock, DeviceStatus.INVITED, name="garden-bot")
        await store.put_device(invited, entry_event(invited, clock))
        device = await walk_to(store, invited, status, clock)
        [console] = [d for d in await store.list_devices() if d.loopback_bound]
        request_id = new_goal_request_id(clock)
        payload = {
            "id": request_id,
            "text": _TEXT,
            "budget_usd": 1.5,
            "comb_shield": None,
            "clearance": "C1",
        }
        held = make_pending(device.id, clock, payload=payload)
        await store.pending.put(held, pending_event(held, clock))
        # The person said yes; the crash came before the goal reached the Queen's table.
        confirmed = Settlement(PendingStatus.CONFIRMED, clock.now(), confirmed_by=console.id)
        event = pending_event(held, clock, settled_as=PendingStatus.CONFIRMED)
        await store.pending.settle(held.id, PendingStatus.PENDING, confirmed, event)
        left.device_id, left.request_id = device.id, request_id

    return seed


async def test_a_confirmed_goal_a_crash_kept_from_the_queen_is_committed_once_on_start() -> None:
    left = _Left()
    async with serving(RigOptions(seed=_crashed(left, DeviceStatus.APPROVED))) as rig:
        assert left.request_id is not None and left.device_id is not None
        request = await rig.deps.goal_requests.get(left.request_id)
        again = await submit_confirmed_goals(rig.entrance.services)
        query = GoalRequestQuery(device_id=left.device_id, limit=10)
        requests = await rig.deps.goal_requests.list_requests(query)

    assert (request.text, request.device_id) == (_TEXT, left.device_id)
    assert request.state is GoalRequestState.RECEIVED
    assert again == ()
    assert [one.id for one in requests] == [left.request_id]


async def test_a_confirmed_goal_whose_device_lost_its_approval_is_dropped() -> None:
    left = _Left()
    async with serving(RigOptions(seed=_crashed(left, DeviceStatus.LOCKED))) as rig:
        assert left.request_id is not None
        with pytest.raises(GoalRequestNotFoundError):
            await rig.deps.goal_requests.get(left.request_id)
