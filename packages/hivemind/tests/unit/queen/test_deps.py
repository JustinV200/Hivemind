"""Tests for hivemind.queen.deps: send_guarded and WardenLink.send.

Fits into the Hive:
    Mirrors src/hivemind/queen/deps.py (codingrules section 3). test_queen_invariants.py already
    covers QueenDeps/WardenLink's own field shape; this file covers the phase-7 handoff open item
    8 guard the two callables add.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.deps for send_guarded and WardenLink.send, the callables under test.
"""

from __future__ import annotations

from typing import cast

from builders.queen import make_queen_deps

from hivemind.queen.deps import QueenDeps, WardenLink, send_guarded
from waggle.envelope import Envelope, wrap
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.transport.memory import MemoryTransport


def _intervene_envelope(link: WardenLink, deps: QueenDeps) -> Envelope:
    """Build one well-formed Queen -> Warden envelope, for a pure send-mechanism test."""
    message = Intervene(
        action=InterventionAction.COMPACT,
        subject=None,
        task_id=None,
        slot=None,
        alarm_id=None,
        reason="test",
    )
    return wrap(message, link.hop, clock=deps.clock)


async def test_send_guarded_returns_true_and_delivers_on_an_open_link() -> None:
    deps, link, warden_end = make_queen_deps()
    envelope = _intervene_envelope(link, deps)

    sent = await send_guarded(link.transport, envelope)

    assert sent is True
    delivered = await warden_end.wait_for_intervene()
    assert delivered.action is InterventionAction.COMPACT
    await warden_end.close()


async def test_send_guarded_returns_false_without_raising_on_a_closed_link() -> None:
    deps, link, warden_end = make_queen_deps()
    await warden_end.close()  # The peer's own clean close is final for this end's own send.
    envelope = _intervene_envelope(link, deps)

    sent = await send_guarded(link.transport, envelope)

    assert sent is False


async def test_send_guarded_returns_false_without_raising_on_a_dropped_link() -> None:
    deps, link, warden_end = make_queen_deps()
    # link.transport is typed as the general Transport Protocol, but make_queen_deps always
    # builds it over a real MemoryTransport (builders.queen's own module docstring); drop() is
    # that concrete class's own test hook, not part of Transport itself.
    cast(MemoryTransport, link.transport).drop()
    envelope = _intervene_envelope(link, deps)

    sent = await send_guarded(link.transport, envelope)

    assert sent is False
    await warden_end.close()


async def test_warden_link_send_delegates_to_send_guarded() -> None:
    deps, link, warden_end = make_queen_deps()
    await warden_end.close()
    envelope = _intervene_envelope(link, deps)

    sent = await link.send(envelope)

    assert sent is False
