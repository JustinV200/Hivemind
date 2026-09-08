"""Replay an outbox through a transport after reconnection, and expire what waited too long.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The outbox
(``waggle.outbox``) is a node's durable queue of envelopes (the outer wrapper every message
travels in) it could not send; when a Warden (the always-on supervisor of one Cell, a unit of
compute) or a Pollen Packet (the thin gateway on an enrolled device) gets its link back, it calls
``connect`` on its transport and then ``replay_outbox`` here, which sends each pending envelope
in order, acks it after the send succeeds, and stops at the first transport error so the unsent
tail stays pending for the next reconnection. Replay never wedges on one bad entry: an envelope
the node can no longer decode (its kind unregistered by an upgrade) or that the sending codec
refuses (a frame grown past that codec's limit) is acked and named in the returned
``ReplayReport`` instead of being retried forever. Each ack runs under ``asyncio.to_thread``
because it ``fsync``s. ``expire_older_than`` applies the freshness rule of spec section 3 to the
queue itself: an envelope whose ``sent_at`` is older than ``MAX_MESSAGE_AGE_S`` would be dropped
by every receiver, so acking it before replay saves the send.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by every bee loop right after a reconnect;
    calls into waggle.outbox, waggle.transport.base and the injected Clock. Latency class: one
    network send plus one fsync per pending envelope.

Key invariants:
    - Replay sends in queue order and acks an entry only after its send returned or after the
      entry proved poisonous; a transport error acks nothing and ends the replay at once.
    - Replaying an outbox twice sends nothing the second time, because everything sent or
      poisoned the first time was acked.
    - Replay preserves each envelope's id and sent_at, so a receiver can dedupe by id and tell
      a late message from a fresh one.

See Also:
    - waggle.outbox for the queue this module drains.
    - waggle.transport.base for the Transport protocol and the errors that stop a replay.
    - docs/waggle/spec.md section 10 (replay) and section 3 (duplicates and freshness).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from waggle.clock import Clock
from waggle.errors import CodecError, TransportError
from waggle.ids import MessageId
from waggle.messages.base import MAX_MESSAGE_AGE_S
from waggle.outbox.queue import Outbox
from waggle.transport.base import Transport

__all__ = ["ReplayReport", "expire_older_than", "replay_outbox"]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What one ``replay_outbox`` call did, for the caller's log and its next decision."""

    sent: int  # Envelopes sent and acked, in queue order.
    poisoned: tuple[MessageId, ...]  # Entries acked without a send because they can never go.
    stopped_by: TransportError | None  # The error that ended the replay early, or None.

    @property
    def is_complete(self) -> bool:
        """Whether the whole queue was drained, so nothing waits for the next reconnection.

        Returns:
            True when no transport error stopped the replay.
        """
        return self.stopped_by is None


async def replay_outbox(outbox: Outbox, transport: Transport) -> ReplayReport:
    """Send every pending envelope in order through ``transport``, acking each as it goes.

    Args:
        outbox: The queue to drain; its acks run under ``asyncio.to_thread``.
        transport: A connected transport; its codec signs each frame at send time.

    Returns:
        A ReplayReport: how many were sent, which entries were acked as poisonous, and the
        transport error that stopped the replay, if one did. The entries after that error
        are still pending.

    Raises:
        OSError: An ack could not be written; the entry it was for stays pending.
    """
    sent = 0
    poisoned: list[MessageId] = []
    # A snapshot of the ids: the acks below mutate the outbox as the loop runs.
    for message_id in outbox.pending_ids():
        try:
            # Network send: milliseconds on loopback, longer over a VPN or Tor route; a link
            # that dies mid-send raises ConnectionLostError, which ends this replay below and
            # leaves the entry pending, so the worst case is a duplicate the receiver drops.
            await transport.send(outbox.load(message_id))
        except CodecError:
            # Poison: the frame cannot be decoded here any more (an upgrade unregistered its
            # kind) or cannot be encoded by this codec (over its limit). It never will be, so
            # it is acked and reported rather than blocking everything behind it.
            await asyncio.to_thread(outbox.ack, message_id)
            poisoned.append(message_id)
            continue
        except TransportError as exc:
            # The link is gone: nothing after this can be sent in order, so stop and report.
            return ReplayReport(sent=sent, poisoned=tuple(poisoned), stopped_by=exc)
        # Blocking fsync in a thread; a cancellation here may leave the ack written or not,
        # and either way the next replay is correct (at worst one duplicate, deduped by id).
        await asyncio.to_thread(outbox.ack, message_id)
        sent += 1
    return ReplayReport(sent=sent, poisoned=tuple(poisoned), stopped_by=None)


def expire_older_than(outbox: Outbox, clock: Clock, max_age_s: float = MAX_MESSAGE_AGE_S) -> int:
    """Ack every pending envelope whose ``sent_at`` is more than ``max_age_s`` in the past.

    A receiver drops such an envelope unread (spec section 3), so sending it would only spend
    the link. An entry that no longer decodes has no readable ``sent_at`` and is left in place
    for ``replay_outbox``, which acks and reports it. PERF: blocking (one fsync per expired
    entry); an async caller runs it under ``asyncio.to_thread``.

    Args:
        outbox: The queue to prune.
        clock: The node's Clock; its ``now`` is what the age is measured against.
        max_age_s: The freshness window in seconds; MAX_MESSAGE_AGE_S (seven days) by default,
            the same window every receiver applies.

    Returns:
        How many entries were acked as expired.

    Raises:
        OSError: An ack could not be written.
    """
    cutoff = clock.now() - timedelta(seconds=max_age_s)
    expired = 0
    # Snapshot the ids: the acks mutate the outbox as the loop runs.
    for message_id in outbox.pending_ids():
        try:
            envelope = outbox.load(message_id)
        except CodecError:
            # Undecodable: not this function's to judge; replay_outbox names it in its report.
            continue
        # Strictly older than the window: an envelope exactly max_age_s old is still fresh.
        if envelope.sent_at < cutoff:
            outbox.ack(message_id)
            expired += 1
    return expired
