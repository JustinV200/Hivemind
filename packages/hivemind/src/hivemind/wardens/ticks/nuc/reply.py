"""Build and send swarm.nuc_promoted: what a promotion or a demotion came to.

The Queen (the Hive's central orchestrator) orders a colonized device's Warden to start or stop the
model server that makes the device a Nuc, and waits, bounded, for the answer. The answer is one
``NucPromoted``: PROMOTED with the server now running and the models it offers, DEMOTED, or FAILED
with a stable word saying why, always with the rung the device is on afterwards, so she never
guesses a half-finished move. The envelope is a reply, so it carries the order's id as its
correlation (waggle spec section 3); that is how she matches it to the flow that is waiting. A
reply is sent best-effort: an order answered after the link went away is stale by the time the
link is back, and the Queen has already given up on it and undone what she did.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.nuc sub-package. Called by ``run``. Calls into ``hivemind.wardens.links`` (the guarded
    send) and waggle only.

Key invariants:
    - A reply names ids, counts and stable words only: no path, address or model weight, and no
      text a device wrote.
    - ``loaded_models`` is empty unless the outcome is PROMOTED, and ``server_id`` is set exactly
      then, as ``NucPromoted``'s own validators require.
    - Sending never raises for a link that has gone.

See Also:
    - waggle.messages.swarm.colonized for ``NucPromote`` and ``NucPromoted``.
    - hivemind.queen.swarm_gate.colonize.nuc for the Queen's side, which awaits this reply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.wardens.links import send_guarded
from waggle.envelope import wrap
from waggle.ids import MessageId
from waggle.messages.swarm import NucOutcome, NucPromote, NucPromoted, RuntimeLevel

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["demoted", "failed", "promoted", "send_reply"]


def promoted(order: NucPromote, loaded: tuple[str, ...]) -> NucPromoted:
    """Build the reply for a server that is up: PROMOTED, on the Nuc rung.

    Args:
        order: The order being answered.
        loaded: The Forage map source ids the server now offers.

    Returns:
        The PROMOTED reply, naming the server the order named.
    """
    return NucPromoted(
        cell_id=order.cell_id,
        device_id=order.device_id,
        action=order.action,
        outcome=NucOutcome.PROMOTED,
        level=RuntimeLevel.NUC,
        server_id=order.server_id,
        loaded_models=loaded,
        reason=f"The server is up and offers {len(loaded)} model(s).",
    )


def demoted(order: NucPromote) -> NucPromoted:
    """Build the reply for a server that is down: DEMOTED, back on the colonized rung."""
    return NucPromoted(
        cell_id=order.cell_id,
        device_id=order.device_id,
        action=order.action,
        outcome=NucOutcome.DEMOTED,
        level=RuntimeLevel.COLONIZED,
        server_id=None,
        loaded_models=(),
        reason="No model server runs on this Cell.",
    )


def failed(order: NucPromote, level: RuntimeLevel, word: str) -> NucPromoted:
    """Build the reply for an order that could not be carried out.

    Args:
        order: The order being answered.
        level: The rung the device is on now, which a failed move did not change.
        word: A stable lowercase word for why (``no_ceilings``, ``server_unknown``, ...) or the
            class name of the error that ended it.

    Returns:
        The FAILED reply; it answers either action.
    """
    return NucPromoted(
        cell_id=order.cell_id,
        device_id=order.device_id,
        action=order.action,
        outcome=NucOutcome.FAILED,
        level=level,
        server_id=None,
        loaded_models=(),
        reason=f"The order failed: {word}.",
    )


async def send_reply(warden: Warden, request_id: MessageId, reply: NucPromoted) -> bool:
    """Send ``reply`` to the Queen as the answer to the order whose envelope id is ``request_id``.

    Args:
        warden: The owning Warden, whose Queen link and address it is sent on.
        request_id: The envelope id of the ``NucPromote`` this answers.
        reply: The reply to send.

    Returns:
        True when the link took it; False when the link had gone (the Queen then times out).
    """
    deps = warden._deps
    envelope = wrap(reply, deps.hop, clock=deps.clock, correlation_id=request_id)
    # The Queen's link: a frame to a peer on the same machine or over the Hive's own socket;
    # `send_guarded` returns False rather than raise when it is already gone.
    return await send_guarded(deps.queen_link, envelope)
