"""Tests for hivemind.queen.swarm_gate.colonize.arrivals: where a flow waits for its Warden.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/arrivals.py (codingrules section 3). The link
    that arrives is a real `WardenLink` over a memory pair; the waits use real, tiny timeouts.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.arrivals for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_cell
from builders.queen import make_warden_link

from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize import ColonizeStepError
from hivemind.queen.swarm_gate.colonize.arrivals import Arrivals
from waggle.clock import FakeClock
from waggle.ids import DeviceId, new_device_id, new_hive_id, new_node_id, new_warden_id

_SHORT_S = 0.05  # Real seconds: long enough for the loop to turn, short enough not to be felt.


def _link(clock: FakeClock) -> WardenLink:
    """A Warden link for a device, over a memory pair."""
    link, _end = make_warden_link(
        new_hive_id(clock), new_warden_id(clock), new_node_id(clock), make_cell(clock=clock), clock
    )
    return link


def _device(clock: FakeClock) -> DeviceId:
    """A fresh device id."""
    return new_device_id(clock)


async def test_a_device_is_expected_from_expect_until_its_warden_arrives() -> None:
    clock, arrivals = FakeClock(), Arrivals()
    device = _device(clock)
    assert not arrivals.expects(device)

    arrivals.expect(device)
    assert arrivals.expects(device)

    arrivals.arrived(device, _link(clock))
    assert not arrivals.expects(device)  # A second Warden claiming the same Cell is refused.


async def test_a_flow_waiting_for_the_warden_is_woken_by_its_arrival() -> None:
    clock, arrivals = FakeClock(), Arrivals()
    device, link = _device(clock), _link(clock)
    arrivals.expect(device)
    waiting = asyncio.create_task(arrivals.wait(device, 5.0))
    await asyncio.sleep(0)

    arrivals.arrived(device, link)

    assert await waiting is link


async def test_a_warden_that_arrived_before_the_wait_began_is_returned_at_once() -> None:
    clock, arrivals = FakeClock(), Arrivals()
    device, link = _device(clock), _link(clock)
    arrivals.expect(device)
    arrivals.arrived(device, link)

    assert await arrivals.wait(device, 5.0) is link


async def test_only_the_first_arrival_counts() -> None:
    clock, arrivals = FakeClock(), Arrivals()
    device, first, second = _device(clock), _link(clock), _link(clock)
    arrivals.expect(device)

    arrivals.arrived(device, first)
    arrivals.arrived(device, second)

    assert await arrivals.wait(device, 5.0) is first


async def test_a_warden_that_never_arrives_fails_the_wait_with_a_stable_word() -> None:
    clock, arrivals = FakeClock(), Arrivals()
    device = _device(clock)
    arrivals.expect(device)

    with pytest.raises(ColonizeStepError) as raised:
        await arrivals.wait(device, _SHORT_S)

    assert raised.value.word == "warden_never_attached"


async def test_waiting_for_a_device_that_was_never_expected_fails_the_same_way() -> None:
    arrivals = Arrivals()

    with pytest.raises(ColonizeStepError) as raised:
        await arrivals.wait(_device(FakeClock()), _SHORT_S)

    assert raised.value.word == "warden_never_attached"


async def test_forgetting_a_device_stops_expecting_it_and_ignores_a_late_arrival() -> None:
    clock, arrivals = FakeClock(), Arrivals()
    device = _device(clock)
    arrivals.expect(device)

    arrivals.forget(device)
    arrivals.arrived(device, _link(clock))  # Too late: nobody is waiting, nothing breaks.

    assert not arrivals.expects(device)
    arrivals.forget(device)  # Twice is fine.
