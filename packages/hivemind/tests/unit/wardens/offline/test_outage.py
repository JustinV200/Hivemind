"""Tests for hivemind.wardens.offline.outage: what a cut-off device Warden remembers.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/outage.py (codingrules section 3). The held tasks (one per
    task, replaced when held again, dropped when the Queen takes the task back), the Handoff a
    stopped bee leaves with its task, the clock of the cut, the wake that ends a tick's wait, and
    the three questions a Warden asks its deps: is there an outage, does it dial the Queen, is its
    link down.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.outage for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.quarantine import settle
from builders.wardens import make_warden_deps
from builders.workers import make_assignment

from hivemind.wardens import DeviceDeps
from hivemind.wardens.offline import (
    Outage,
    RemoteDeps,
    is_link_down,
    outage_of,
    remote_of,
    wake_future,
)
from hivemind.wardens.offline.outage import HeldTask
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance


class _Link:
    """A link whose state a test sets."""

    def __init__(self, is_online: bool) -> None:
        self.is_online = is_online


def _handoff(clock: FakeClock) -> HandoffRef:
    """A reference to a Handoff written now."""
    return HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=HoneyClearance.C1
    )


def _warden(device: DeviceDeps | None) -> Warden:
    """A Warden over fakes with `device`, never started."""
    deps, _queen, warden_id = make_warden_deps(device=device)
    return Warden(warden_id, deps)


def test_a_task_is_held_once_and_held_again_replaces_it() -> None:
    clock = FakeClock()
    outage, first, second = Outage(), make_assignment(clock=clock), make_assignment(clock=clock)
    outage.hold(first, None, clock.now())
    outage.hold(second, None, clock.now())
    ref = _handoff(clock)

    outage.hold(first, ref, clock.now())

    assert outage.is_holding(first.task_id) and outage.has_held
    assert [h.assignment.task_id for h in outage.held()] == [first.task_id, second.task_id]
    assert outage.held()[0].handoff == ref


def test_forgetting_and_taking_remove_tasks() -> None:
    clock = FakeClock()
    outage, first, second = Outage(), make_assignment(clock=clock), make_assignment(clock=clock)
    outage.hold(first, None, clock.now())
    outage.hold(second, None, clock.now())

    outage.forget(first.task_id)
    outage.forget(first.task_id)  # Forgetting what is not held is not an error.
    taken = outage.take()

    assert [h.assignment.task_id for h in taken] == [second.task_id]
    assert not outage.has_held and outage.take() == ()


def test_a_stopped_bees_handoff_joins_its_held_task_and_no_other() -> None:
    clock = FakeClock()
    outage, held, other = Outage(), make_assignment(clock=clock), make_assignment(clock=clock)
    outage.hold(held, None, clock.now())
    ref = _handoff(clock)

    outage.note_handoff(held.task_id, ref)
    outage.note_handoff(other.task_id, ref)  # Not held: nothing to record it on.
    outage.note_handoff(held.task_id, None)  # A bee that wrote none leaves what was there.

    assert outage.held() == (HeldTask(held, ref, outage.held()[0].held_at),)
    assert not outage.is_holding(other.task_id)


def test_the_cut_is_dated_and_ended() -> None:
    clock = FakeClock()
    outage = Outage()

    outage.begin(clock.now(), "The link to the Queen was lost.", {"grant_1": 3})
    began = (outage.cut_since, outage.reason, outage.frozen_grants)
    outage.end()

    assert began == (clock.now(), "The link to the Queen was lost.", {"grant_1": 3})
    assert (outage.cut_since, outage.reason, outage.frozen_grants) == (None, "", {})
    assert not outage.is_clustered_fully


async def test_a_nudge_ends_a_wait_and_acknowledging_it_starts_the_next_one() -> None:
    outage = Outage()
    first = outage.waiter()
    await asyncio.sleep(0)
    assert not first.done()  # Nothing has changed: the tick keeps waiting.

    outage.nudge()
    await settle(first.done)

    assert first.result() is True
    outage.nudge()  # A change while no tick waited is not lost: the next waiter returns at once.
    second = outage.waiter()
    await settle(second.done)
    outage.acknowledge()
    third = outage.waiter()
    await asyncio.sleep(0)
    assert not third.done()
    third.cancel()


def test_a_warden_asks_its_deps_for_the_outage_and_the_link() -> None:
    outage, link = Outage(), _Link(is_online=False)
    remote = RemoteDeps(link, 600.0)
    dialling = _warden(DeviceDeps(outage=outage, remote=remote))
    on_the_stand = _warden(DeviceDeps(outage=outage))
    plain = _warden(None)

    assert (outage_of(dialling), remote_of(dialling)) == (outage, remote)
    assert (outage_of(on_the_stand), remote_of(on_the_stand)) == (outage, None)
    assert (outage_of(plain), remote_of(plain)) == (None, None)
    assert is_link_down(dialling) and not is_link_down(on_the_stand) and not is_link_down(plain)
    link.is_online = True
    assert not is_link_down(dialling)


async def test_the_wake_of_a_warden_with_no_outage_never_resolves_but_can_be_reaped() -> None:
    wake = wake_future(_warden(None))

    await asyncio.sleep(0)

    assert not wake.done()
    wake.cancel()
