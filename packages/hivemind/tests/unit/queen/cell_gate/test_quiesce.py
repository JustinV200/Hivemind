"""Unit tests for hivemind.queen.cell_gate.quiesce: make_quiesce.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/quiesce.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.quiesce for make_quiesce, the function under test.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from builders.cells import make_cell

from hivemind.cell import CellKind
from hivemind.queen.cell_gate.quiesce import make_quiesce
from hivemind.queen.deps import WardenLink
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.ids import CellId, new_hive_id, new_node_id, new_warden_id
from waggle.messages.cell import CellTeardownRequest, ReleaseCause
from waggle.messages.labels import Urgency
from waggle.transport.memory import MemoryTransport

# Generous against the module's own ~0.05s poll interval; small enough that the "gives up after
# grace" test still runs fast, and comfortably below every "never detaches" test's own grace_s.
_POLL_STEP_S = 0.05
# Bounds every `_next_envelope` wait: a real bug (nothing ever sent) must fail fast, not hang the
# whole suite -- mirrors tests.unit.queen.cell_gate.test_listener's own WAIT_S pattern.
_WAIT_S = 2.0


@dataclass
class _StubQueen:
    """A `.wardens` view a test mutates directly, standing in for `hivemind.queen.queen.Queen`."""

    wardens: tuple[WardenLink, ...] = field(default_factory=tuple)


def _linked_pair(clock: FakeClock) -> tuple[WardenLink, MemoryTransport]:
    """Build a fresh WardenLink (the Queen's own end) plus its peer transport (the Warden's own)."""
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    warden_id = new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=str(new_hive_id(clock)), recipient=str(warden_id), node_id=new_node_id(clock))
    link = WardenLink(warden_id=warden_id, cell=cell, transport=queen_transport, hop=hop)
    return link, warden_transport


async def _next_envelope(transport: MemoryTransport) -> Envelope:
    """Read exactly one envelope off `transport`'s own receive() generator, bounded by `_WAIT_S`."""
    return await asyncio.wait_for(anext(transport.receive()), timeout=_WAIT_S)


async def _advance_until_done(task: asyncio.Task[None], clock: FakeClock, steps: int) -> None:
    """Advance `clock` by `_POLL_STEP_S`, yielding once per step, until `task` finishes or gives up.

    Mirrors codingrules 14.5 ("no sleeping in tests"): every wait `make_quiesce`'s own poll loop
    makes is on the injected Clock, so a FakeClock drives it deterministically -- no real timer.
    """
    for _ in range(steps):
        if task.done():
            return
        clock.advance(_POLL_STEP_S)
        await asyncio.sleep(0)  # Let the woken task re-check _still_attached and re-arm or return.


async def test_quiesce_is_a_no_op_when_no_queen_is_bound_yet() -> None:
    """`queen_getter` returning None (bind_queen has not run) must not raise or block."""
    quiesce = make_quiesce(lambda: None, FakeClock())

    await asyncio.wait_for(quiesce(CellId("cell_1")), timeout=1.0)


async def test_quiesce_is_a_no_op_for_a_cell_not_currently_attached() -> None:
    """A Cell not in the Queen's own `wardens` (never attached, or already gone) is a no-op."""
    clock = FakeClock()
    link, _warden_transport = _linked_pair(clock)
    stub = _StubQueen(wardens=(link,))
    quiesce = make_quiesce(lambda: stub, clock)

    # A different Cell id than the one link's own: nothing is found, so nothing is ever sent.
    await asyncio.wait_for(quiesce(CellId("cell_untracked")), timeout=1.0)


async def test_quiesce_sends_a_graceful_teardown_request_and_returns_once_detached() -> None:
    clock = FakeClock()
    link, warden_transport = _linked_pair(clock)
    stub = _StubQueen(wardens=(link,))
    quiesce = make_quiesce(lambda: stub, clock, grace_s=5.0)

    task = asyncio.ensure_future(quiesce(link.cell.id))
    envelope = await _next_envelope(warden_transport)

    payload = envelope.payload
    assert isinstance(payload, CellTeardownRequest)
    assert payload.cell_id == link.cell.id
    assert payload.lease_id is None
    assert payload.urgency is Urgency.GRACEFUL
    assert payload.cause is ReleaseCause.COMPLETED

    # Simulate hivemind.queen.attach.detach_warden having already run (the listener's own
    # connection-close path): the Cell's own link is gone from the Queen's wardens.
    stub.wardens = ()
    await _advance_until_done(task, clock, steps=10)

    assert task.done()
    await asyncio.wait_for(task, timeout=_WAIT_S)  # Propagate any unexpected exception.


async def test_quiesce_gives_up_after_grace_s_when_the_warden_never_detaches() -> None:
    clock = FakeClock()
    link, warden_transport = _linked_pair(clock)
    stub = _StubQueen(wardens=(link,))
    quiesce = make_quiesce(lambda: stub, clock, grace_s=0.2)

    task = asyncio.ensure_future(quiesce(link.cell.id))
    await _next_envelope(warden_transport)  # Drain the request; the link stays attached below.

    await _advance_until_done(task, clock, steps=10)

    assert task.done()
    await asyncio.wait_for(task, timeout=_WAIT_S)  # A timeout is not an error: must not raise.
    assert stub.wardens == (link,)  # Never detached: the caller's own backend destroy is next.
