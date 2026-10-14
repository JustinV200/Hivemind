"""Tests for hivemind.wardens.ticks.lease: a device Warden keeps its Cell while it hosts a Nuc.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/lease.py (codingrules section 3); split by feature (14.2)
    from test_lease_device.py. A device's Warden gives its lease back when it settles to WATCH with
    nothing to supervise (roadmap step 11.6), but a model server dies with the lease that started
    it, and a job the Warden owns (a promotion starting that server) works on it. So neither a
    running server nor a job in flight is idle (roadmap step 11.8).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.lease for the module under test.
    - builders.nuc for the rig.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from builders.nuc import NucRig, allow, demote_order, make_nuc_rig, promote_order, reply_of
from builders.quarantine import make_grant_issued, settle
from builders.workers import make_assignment

from hivemind.wardens import DeviceDeps
from hivemind.wardens.state import WardenState
from hivemind.wardens.ticks.basket.jobs import start_job
from hivemind.wardens.ticks.nuc import run_order
from waggle.ids import new_message_id

_STOP_TIMEOUT_S = 5.0  # A hung stop fails the test instead of blocking the suite.
_SETTLE_ROUNDS = 25  # Event-loop turns in which any release that was going to happen, does.


@pytest.fixture
async def rig() -> AsyncIterator[NucRig]:
    """A running device Warden whose server `box` is declared and not running."""
    made = await make_nuc_rig(device=DeviceDeps())
    task = asyncio.ensure_future(made.warden.run())
    yield made
    await made.warden.stop()
    await asyncio.wait_for(task, timeout=_STOP_TIMEOUT_S)


async def _wake(rig: NucRig) -> None:
    """Send the Warden one harmless message, so its tick runs and settles."""
    await rig.queen.send(make_grant_issued(rig.clock, make_assignment(clock=rig.clock)))


async def _idle_rounds() -> None:
    for _ in range(_SETTLE_ROUNDS):
        await asyncio.sleep(0)


async def test_an_idle_device_warden_gives_its_lease_back(rig: NucRig) -> None:
    await _wake(rig)

    await settle(lambda: rig.warden.lease is None)

    assert rig.warden.state is WardenState.WATCH


async def test_a_device_warden_hosting_a_model_server_keeps_its_lease_when_idle(
    rig: NucRig,
) -> None:
    allow(rig.warden, "small")
    await run_order(rig.warden, new_message_id(rig.clock), promote_order(rig.warden, "small"))
    await reply_of(rig)
    lease = rig.warden.lease

    await _wake(rig)
    await settle(lambda: rig.warden.state is WardenState.WATCH)
    await _idle_rounds()

    assert lease is not None and rig.warden.lease is lease  # The server dies with it.
    assert set(rig.warden.hosted_servers) == {"box"}


async def test_the_lease_goes_back_once_the_server_is_demoted(rig: NucRig) -> None:
    allow(rig.warden, "small")
    await run_order(rig.warden, new_message_id(rig.clock), promote_order(rig.warden, "small"))
    await run_order(rig.warden, new_message_id(rig.clock), demote_order(rig.warden))

    await _wake(rig)

    await settle(lambda: rig.warden.lease is None)  # An ordinary colonized Cell again.


async def test_a_device_warden_with_a_job_in_flight_keeps_its_lease(rig: NucRig) -> None:
    gate = asyncio.Event()

    async def unfinished() -> None:
        await gate.wait()

    job = start_job(rig.warden, unfinished())  # Work on the lease that has not finished.
    lease = rig.warden.lease

    await _wake(rig)
    await settle(lambda: rig.warden.state is WardenState.WATCH)
    await _idle_rounds()
    assert lease is not None and rig.warden.lease is lease

    gate.set()
    await job
    await _wake(rig)
    await settle(lambda: rig.warden.lease is None)  # The job is over: idle at last.
