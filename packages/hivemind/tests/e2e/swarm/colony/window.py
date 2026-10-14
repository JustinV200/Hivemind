"""Hear what the Queen's replay window drops, including what it drops without a word.

A node whose cable was pulled sends the frames of its last stretch again once it is back, under the
ids they had (ADR-0053), and the Queen's replay window (ADR-0055) is what keeps her from handling
a frame she already holds a second time. When that works it leaves almost no trace: a duplicate
inside the reconnection grace is dropped with no event on the trail, because an outbox replay
produces them as a matter of course. So the trail cannot say whether a frame was sent again, and a
scenario that wants to show that the window did its work, and not that nothing was sent twice,
has to listen where the drop is decided. That is the callback the swarm listener hands the
transport (`TransportRefusals`), which every refusal passes through, silent or recorded.
`window_drops` wraps it for the length of a block, hears each refusal as it is made and hands it on
to the real callback unchanged.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the unnoticed-window
    scenario; reaches into the swarm listener's callback, which has no public way to be listened
    at, and replaces nothing but its call.

Key invariants:
    - The tap only records: the real callback runs on every refusal, after it has been noted, and
      what it records on the trail is what it would have recorded without the tap.
    - The callback is restored when the block ends, whatever the block did.
    - A refusal is kept whole (node, kind, envelope id, time), never a frame or its content.

See Also:
    - hivemind.swarm.gateway.refusals for the callback and the grace it applies.
    - e2e/swarm/colony/orders.py for the tap on what the Queen's door hears.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from hivemind.swarm.gateway import TransportRefusals
from waggle.transport.limits import ConnectionRefusal

__all__ = ["window_drops"]


@contextmanager
def window_drops() -> Iterator[list[ConnectionRefusal]]:
    """Note every refusal the swarm listener's transport limits make while the block runs.

    Yields:
        The refusals, in the order they were made; the list fills as the block runs.
    """
    heard: list[ConnectionRefusal] = []
    real = TransportRefusals.__call__

    async def listening(self: TransportRefusals, refusal: ConnectionRefusal) -> None:
        # Noted before it is handled, so a refusal the real callback fails to record is heard too.
        heard.append(refusal)
        await real(self, refusal)

    with patch.object(TransportRefusals, "__call__", listening):
        yield heard
