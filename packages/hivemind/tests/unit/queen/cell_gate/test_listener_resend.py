"""Unit tests for the Queen under a Warden that sends the frames of its last stretch again.

A Warden whose cable was pulled out from under it sends again, after it reconnects, the frames of
its last stretch that must arrive (ADR-0053), under the ids they had, and the Queen's replay
window (ADR-0055) is what makes that harmless. These tests state the Queen's half over a real
`CellListener` and a real Queen, with a real WebSocket between them: every kind of frame a
Warden sends again reaches her once however often it arrives; a returning Warden's re-send is
dropped without a trace on her trail, even when the trail is as slow as a real store (the
reconnection grace has to be known before the first frame behind the handshake is read); a
duplicate that is no re-send is still on the record; and the effect of a re-sent result, Alarm
and chunk of Nectar happens once.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Complements test_listener.py, which tests the
    listener's own dispatch; the Cell gate's two collaborators here are the real shared attach
    path and the real transport limits.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.listener and hivemind.queen.attach.serve for the code under test.
    - builders.cell_listener for the listener scenario and the fake Cell that dials it.
    - tests/unit/wardens/offline/test_link_resend.py for the Warden's half.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from builders.cell_listener import (
    WAIT_S,
    FakeCell,
    Scenario,
    attach,
    attached_ids,
    build_scenario,
    recorded,
    until,
    until_async,
)
from builders.honey import open_test_honey_store
from builders.honey_wire import make_honey_access
from builders.offline import Wire
from builders.tasks import make_graph_draft

from hivemind.cell import CombShieldLevel
from hivemind.pheromone import TrailQuery
from hivemind.swarm.gateway.refusals import RECONNECT_GRACE_S
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import TaskId, new_task_id
from waggle.transport.websocket_client import WebSocketClientTransport

Build = Callable[[Wire], Envelope]
_KINDS: dict[str, Build] = {
    "task.result": lambda w: w.result(),
    "task.progress": lambda w: w.progress(),
    "supervision.alarm_raised": lambda w: w.alarm(),
    "forage.request": lambda w: w.forage_request(),
    "cell.lease_released": lambda w: w.lease_released(),
    "honey.nectar_deposit": lambda w: w.nectar(),
    "supervision.question": lambda w: w.question(),
}


def _wire(scenario: Scenario, cell: FakeCell, task_id: TaskId | None = None) -> Wire:
    """The frames this Warden sends the Queen, addressed and signed as a Cell's Warden is."""
    clock = FakeClock()
    return Wire(clock, cell.hop(scenario.hive_id), task_id or new_task_id(clock))


def _arrived(scenario: Scenario, heard: list[str]) -> None:
    """Take what the Queen's tick has not yet read off the Warden's link; keep the envelope ids."""
    heard.extend(str(item.id) for item in scenario.queen._links.drain())


async def _hears(scenario: Scenario, heard: list[str], envelope: Envelope) -> None:
    """Wait until the Queen has read `envelope`, as her tick would, collecting what she reads."""

    def reached() -> bool:
        _arrived(scenario, heard)
        return str(envelope.id) in heard

    await asyncio.wait_for(until(reached), WAIT_S)


async def _refused(scenario: Scenario) -> list[object]:
    """The `guard.replay_refused` events on the Queen's trail."""
    return list(await scenario.trail.query(TrailQuery(kind="guard.replay_refused")))


async def _come_back(scenario: Scenario, cell: FakeCell) -> WebSocketClientTransport:
    """Dial the listener again as the same Warden and complete the handshake."""
    transport = await cell.dial(scenario.queen_node_id, scenario.queen_signer)
    await cell.send_ready(transport, scenario.hive_id)
    await cell.send_heartbeat(transport, scenario.hive_id)
    return transport


@pytest.mark.parametrize("build", list(_KINDS.values()), ids=list(_KINDS))
async def test_each_kind_a_warden_sends_again_reaches_the_queen_once(build: Build) -> None:
    scenario = await build_scenario()
    cell, transport = await attach(scenario)
    envelope = build(_wire(scenario, cell))

    await transport.send(envelope)
    await transport.send(envelope)  # The very frame again: the Warden cannot tell it arrived.
    await asyncio.wait_for(until_async(lambda: _refused_any(scenario)), WAIT_S)

    heard: list[str] = []
    _arrived(scenario, heard)
    assert heard == [str(envelope.id)]  # The Queen's tick would read it once.
    assert cell.warden_id in attached_ids(scenario.queen)  # A repeat costs the Cell nothing.
    await transport.close()
    await scenario.listener.stop()


async def _refused_any(scenario: Scenario) -> bool:
    """Whether the Queen has recorded a repeated envelope id yet."""
    return bool(await _refused(scenario))


async def test_what_a_returning_warden_sends_again_is_dropped_without_a_trace(
    tmp_path: Path,
) -> None:
    # A trail that yields, as the SQLite store does: the listener reads on while an attach runs.
    scenario = await build_scenario(slow_trail=True)
    cell, first = await attach(scenario)
    wire = _wire(scenario, cell)
    stretch = [wire.result(), wire.alarm(), wire.progress(), wire.nectar(), wire.forage_request()]
    heard: list[str] = []
    for envelope in stretch:
        await first.send(envelope)
    await _hears(scenario, heard, stretch[-1])
    await first.close()
    await asyncio.wait_for(
        until(lambda: cell.warden_id not in attached_ids(scenario.queen)), WAIT_S
    )

    second = await _come_back(scenario, cell)
    for envelope in stretch:  # The re-send, straight behind the handshake, as the Warden does it.
        await second.send(envelope)
    news = wire.alarm()
    await second.send(news)  # Then something new, to know the listener has read to the end.
    await _hears(scenario, heard, news)
    await asyncio.wait_for(until_async(lambda: recorded(scenario, "warden.reconnected")), WAIT_S)

    # Each frame once; and no trace: the return was known before the first duplicate was met.
    assert heard == [str(e.id) for e in (*stretch, news)]
    assert await _refused(scenario) == []
    await second.close()
    await scenario.listener.stop()


async def test_a_duplicate_long_after_the_return_is_still_on_the_record() -> None:
    scenario = await build_scenario()
    cell, first = await attach(scenario)
    envelope = _wire(scenario, cell).result()
    await first.send(envelope)
    await first.close()
    await asyncio.wait_for(
        until(lambda: cell.warden_id not in attached_ids(scenario.queen)), WAIT_S
    )
    second = await _come_back(scenario, cell)
    await asyncio.wait_for(until(lambda: cell.warden_id in attached_ids(scenario.queen)), WAIT_S)

    scenario.clock.advance(RECONNECT_GRACE_S + 1.0)  # The grace after the return is over.
    await second.send(envelope)
    await asyncio.wait_for(until_async(lambda: _refused_any(scenario)), WAIT_S)

    [event] = await _refused(scenario)
    assert event.subject_id == cell.node_id  # type: ignore[attr-defined]
    await second.close()
    await scenario.listener.stop()


async def test_a_first_attach_has_no_replay_to_expect_so_its_duplicate_is_on_the_record() -> None:
    scenario = await build_scenario()
    cell, transport = await attach(scenario)
    envelope = _wire(scenario, cell).result()

    await transport.send(envelope)
    await transport.send(envelope)
    await asyncio.wait_for(until_async(lambda: _refused_any(scenario)), WAIT_S)

    assert len(await _refused(scenario)) == 1
    await transport.close()
    await scenario.listener.stop()


async def _running_task(scenario: Scenario, cell: FakeCell) -> TaskId:
    """Put a task on the Warden's Cell, running, for the frames it sends to be about."""
    [task] = await scenario.deps.chamber.submit(make_graph_draft({"root": ()}))
    await scenario.deps.chamber.assign(
        task.id,
        cell.warden_id,
        cell.cell_id,
        "Placed by the test.",
        bound_tier=CombShieldLevel.MEADOW,
    )
    await scenario.deps.chamber.start(task.id)
    return task.id


async def _settled(scenario: Scenario, kind_prefix: str, count: int) -> list[object]:
    """Wait for `count` trail events of a kind, then a moment more for a surplus one to show."""

    async def reached() -> bool:
        return len(await scenario.trail.query(TrailQuery())) > 0 and any(
            e.kind.startswith(kind_prefix) for e in await scenario.trail.query(TrailQuery())
        )

    await asyncio.wait_for(until_async(reached), WAIT_S)
    await asyncio.sleep(0.3)  # Long enough for the Queen's tick to have read a second copy.
    return [e for e in await scenario.trail.query(TrailQuery()) if e.kind.startswith(kind_prefix)]


async def test_a_result_sent_twice_completes_its_task_once() -> None:
    scenario = await build_scenario()
    cell, transport = await attach(scenario)
    task_id = await _running_task(scenario, cell)
    run = asyncio.ensure_future(scenario.queen.run())
    result = _wire(scenario, cell, task_id).result()

    await transport.send(result)
    await transport.send(result)
    done = await _settled(scenario, "task.succeeded", 1)

    assert len(done) == 1
    assert len(await _refused(scenario)) == 1  # The second copy never reached her handlers.
    await scenario.queen.stop()
    await asyncio.wait_for(run, WAIT_S)
    await transport.close()
    await scenario.listener.stop()


async def test_an_alarm_sent_twice_is_handled_once() -> None:
    scenario = await build_scenario()
    cell, transport = await attach(scenario)
    task_id = await _running_task(scenario, cell)
    run = asyncio.ensure_future(scenario.queen.run())
    alarm = _wire(scenario, cell, task_id).alarm()

    await transport.send(alarm)
    await transport.send(alarm)
    handled = await _settled(scenario, "alarm.", 1)

    # Nothing but the window stands between a second copy and a second retry of the same failure:
    # the Queen decides what an Alarm means each time she is shown one.
    assert len(handled) == 1
    await scenario.queen.stop()
    await asyncio.wait_for(run, WAIT_S)
    await transport.close()
    await scenario.listener.stop()


async def test_a_chunk_of_nectar_sent_twice_is_deposited_once(tmp_path: Path) -> None:
    clock = FakeClock()
    store = await open_test_honey_store(tmp_path, clock)
    scenario = await build_scenario(honey=make_honey_access(store, clock))
    cell, transport = await attach(scenario)
    run = asyncio.ensure_future(scenario.queen.run())
    deposit = _wire(scenario, cell).nectar(cell_id=cell.cell_id)

    await transport.send(deposit)
    await transport.send(deposit)
    await asyncio.wait_for(until_async(lambda: _deposited(store)), WAIT_S)
    await asyncio.sleep(0.3)

    assert len(await store.pending_nectar(10)) == 1
    assert len(await _refused(scenario)) == 1
    await scenario.queen.stop()
    await asyncio.wait_for(run, WAIT_S)
    await transport.close()
    await scenario.listener.stop()


async def _deposited(store: object) -> bool:
    """Whether the Honey Store holds a pending chunk of Nectar yet."""
    pending = await store.pending_nectar(10)  # type: ignore[attr-defined]
    return bool(pending)
