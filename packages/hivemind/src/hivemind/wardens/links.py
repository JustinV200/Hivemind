"""Send on a Warden's Waggle links without letting a link that already went away end its loop.

A Warden (the always-on supervisor of one Cell) holds two kinds of Waggle link (the Hive's
bee-to-bee wire protocol): its own link up to the Queen (`WardenDeps.queen_link`) and one link down
to each sub-bee it spawned (`SubBee.link`). Either can close or drop at any moment: the Queen
stops, a sub-bee finishes, a WebSocket loses its connection. Every Warden send used to call
`transport.send` directly, so a link that had gone raised `TransportClosedError` or
`ConnectionLostError` straight out of the Warden's tick, and `Warden` names no recoverable errors,
so that one send ended `run()` outright (phase 7 handoff, open item 8). `send_guarded` is the one
way Warden-side code sends: it reports whether the frame went out, so a caller with something
durable at stake can react, and it never raises for a link that is already gone. It lives in its
own module, below `hivemind.wardens.deps` and `hivemind.wardens.trail_sync`, so both can import it
at module level without an import cycle.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Called by every module under `hivemind.wardens` that sends a Waggle message (the tick handlers,
    the spawner, the trail sync and the snapshot relay). Calls into `hivemind.common.logging` and
    waggle (envelope, errors, transport) only.

Key invariants:
    - Exactly `TransportClosedError` and `ConnectionLostError` are caught, nothing broader
      (codingrules section 10): any other failure is a real bug and still propagates.
    - A failed send is logged with the envelope's ids and kind only, never its payload
      (codingrules section 12).

See Also:
    - hivemind.queen.deps for `send_guarded` and `WardenLink.send`, the identically shaped guard
      on the Queen's own side of the same links.
    - waggle.transport.base for the Transport contract and the two errors it raises.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.transport.base import Transport

# A transport this final (a send after close) or this dead (a dropped link) is never a Warden's
# own bug to crash a tick over; exactly these two, nothing broader (codingrules section 10).
_LINK_GONE = (TransportClosedError, ConnectionLostError)

log = get_logger(__name__)

__all__ = ["send_guarded"]


async def send_guarded(transport: Transport, envelope: Envelope) -> bool:
    """Send `envelope` on `transport`; False, logged, when the link has already gone.

    Args:
        transport: The link to send on: `WardenDeps.queen_link`, or a `SubBee.link`.
        envelope: The already-wrapped frame to send.

    Returns:
        True once the frame was handed to the link; False when `transport.send` raised
        `TransportClosedError` or `ConnectionLostError`, logged as a warning naming the
        envelope's own ids and kind, never its payload.
    """
    try:
        # Queued at once on an in-process pair, or handed to the WebSocket's own send buffer;
        # never awaited on the far side, so this is never the slow half of a round trip.
        await transport.send(envelope)
    except _LINK_GONE:
        # The peer's link is gone: logged so the gap is visible, never raised, so this can never
        # be the exception that ends a Warden's tick loop.
        log.warning(
            "wardens.link.send_failed",
            recipient=envelope.recipient,
            sender=envelope.sender,
            message_id=envelope.id,
            kind=envelope.kind,
        )
        return False
    return True
