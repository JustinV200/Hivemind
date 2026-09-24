"""Tests for hivemind.wardens.links: send_guarded.

Fits into the Hive:
    Mirrors src/hivemind/wardens/links.py (codingrules section 3). Covers the guard phase 7
    handoff open item 8 added for every Warden -> Queen and Warden -> sub-bee send.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.links for send_guarded, the callable under test.
"""

from __future__ import annotations

from typing import cast

from builders.supervision import make_telemetry
from builders.wardens import make_warden_deps

from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.links import send_guarded
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.transport.memory import MemoryTransport


def _heartbeat_envelope(deps: WardenDeps) -> Envelope:
    """Build one well-formed Warden -> Queen envelope, for a pure send-mechanism test."""
    message = Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )
    return wrap(message, deps.hop, clock=deps.clock)


async def test_send_guarded_returns_true_and_delivers_on_an_open_link() -> None:
    deps, queen_end, _warden_id = make_warden_deps()
    envelope = _heartbeat_envelope(deps)

    sent = await send_guarded(deps.queen_link, envelope)

    assert sent is True
    delivered = await queen_end.wait_for_heartbeat()
    assert delivered.warden_state is WardenState.ACTIVE


async def test_send_guarded_returns_false_without_raising_on_a_closed_link() -> None:
    deps, queen_end, _warden_id = make_warden_deps()
    await queen_end.close()  # The peer's own clean close is final for this end's own send.
    envelope = _heartbeat_envelope(deps)

    sent = await send_guarded(deps.queen_link, envelope)

    assert sent is False


async def test_send_guarded_returns_false_without_raising_on_a_dropped_link() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    # deps.queen_link is typed as the general Transport Protocol, but make_warden_deps always
    # builds it over a real MemoryTransport; drop() is that concrete class's own test hook.
    cast(MemoryTransport, deps.queen_link).drop()
    envelope = _heartbeat_envelope(deps)

    sent = await send_guarded(deps.queen_link, envelope)

    assert sent is False


async def test_send_guarded_works_for_a_sub_bee_link_too_not_only_the_queen_link() -> None:
    """`send_guarded` takes any `Transport`; a `SubBee.link` is one, never wrapped further."""
    deps, _queen_end, warden_id = make_warden_deps()
    # A fresh MemoryTransport pair, the same shape hivemind.wardens.spawn.spawn._start_runtime
    # opens for a real sub-bee (SubBee.link is this Warden's own end of exactly this pair).
    warden_end, worker_end = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=warden_id, recipient=warden_id, node_id=deps.identity.node_id)
    envelope = wrap(
        Heartbeat(
            telemetry=make_telemetry(),
            task_id=None,
            worker_state=None,
            warden_state=WardenState.ACTIVE,
            children=(),
            grant_id=None,
            grant_spend=None,
            interval_s=5.0,
        ),
        hop,
        clock=deps.clock,
    )
    await worker_end.close()  # The sub-bee's own end is gone; the Warden's send now finds it so.

    sent = await send_guarded(warden_end, envelope)

    assert sent is False
