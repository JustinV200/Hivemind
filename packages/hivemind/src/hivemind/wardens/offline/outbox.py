"""Keep what a cut-off Warden could not send: a durable queue, a held list, and a replay.

A Warden (the always-on supervisor of one Cell, a unit of compute) whose link to the Queen (the
Hive's central orchestrator) is down must not lose a result or an Alarm, so
`hivemind.wardens.offline.link` hands such a message here (`hivemind.wardens.offline.policy` decides
which). `OfflineOutbox` wraps waggle's `Outbox` (a first-in-first-out JSONL file under the Warden's
state directory that survives a restart of the Warden itself) and adds what the Warden needs of it:
the list of human-bound questions it holds in memory for the reconnection (a question's sub-bee dies
with the Warden, so a question has nothing to survive for), one lock that makes "queue this or send
it live" a single decision, and a replay that sends one queued message at a time.

A message replayed after a reconnection goes out with the id and the time it was queued with, so
the Queen's replay window (ADR-0055) drops it if it had in fact reached her before the link broke
and only the acknowledgement was lost. A *request* is the exception: the Warden is owed an answer,
and an answer to a request the window dropped would never come, so a replayed request goes out in
a fresh envelope (a new id, a new time) and the Queen deduplicates it by the payload's own durable
id where it has one, as she must for any retry. The old entry is acknowledged once the fresh one is
sent.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Built by the composition root over the Warden's state directory; written
    by `hivemind.wardens.offline.link.ReconnectingQueenLink` when a send cannot be made live and
    drained by it after a reconnection. Calls into waggle (the outbox, the envelope and the codec's
    errors) only.

Key invariants:
    - The caller holds `lock` for `queue`, `hold`, `replay_one` and `release_held`: the link's
      decision to queue and its decision to go online are made under the same lock, so a message
      queued while the link comes back is never stranded behind a queue that has been drained.
    - `queue` returns only once the message is on disk (an fsync, run off the event loop); a disk
      that cannot take it is an `OfflineRefusedError`, so the sender hears that it was not kept.
    - `replay_one` acknowledges an entry only after the link took it, so a link that dies mid-send
      leaves the entry queued: at-least-once, with the Queen's replay window making it harmless.
    - An entry that no longer decodes (an upgrade unregistered its kind) is acknowledged and
      skipped, never replayed forever; an entry older than the freshness window is expired.

See Also:
    - docs/waggle/spec.md section 10 and docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md
      for the outbox itself.
    - hivemind.wardens.offline.policy for which messages are queued, held, refused or dropped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hivemind.wardens.offline.policy import OfflineRefusedError, owes_reply
from waggle.clock import Clock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import CodecError, FrameTooLargeError
from waggle.outbox import Outbox, expire_older_than
from waggle.transport.base import Transport

OUTBOX_FILE = "outbox.jsonl"  # The queue's file name under the Warden's state directory.
SEND_TIMEOUT_S = 30.0  # One frame to a link that has just answered a dial; longer means it is dead.

__all__ = ["OUTBOX_FILE", "SEND_TIMEOUT_S", "OfflineOutbox", "open_outbox"]


def open_outbox(state_dir: Path, codec: Codec, clock: Clock) -> OfflineOutbox:
    """Open (creating if need be) the durable queue under `state_dir`.

    Args:
        state_dir: The Warden's state directory; it must exist.
        codec: The codec the link sends with; only its frame limit is used, to refuse a message
            that could never be sent once signed.
        clock: Stamps a replayed request's fresh envelope.

    Returns:
        The outbox, holding whatever an earlier run of the Warden left queued.

    Raises:
        OutboxError: The queue's file is corrupt beyond a torn last line.
        OSError: The file cannot be read or rewritten.
    """
    return OfflineOutbox(Outbox(state_dir / OUTBOX_FILE, codec), clock)


class OfflineOutbox:
    """The durable queue, the held questions and the lock around them.

    Owns mutable state (codingrules 8.5, documented): the held list and the queue behind `_outbox`
    change only under `lock`.
    """

    def __init__(self, outbox: Outbox, clock: Clock) -> None:
        """Wrap an open queue.

        Args:
            outbox: The durable first-in-first-out queue.
            clock: Mints the id and time of a replayed request's fresh envelope.
        """
        self._outbox = outbox
        self._clock = clock
        self._held: list[Envelope] = []
        # One decision at a time: "queue this" and "the link is back, send it live" must not
        # interleave, or a message could be queued just after the last replay and never leave.
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        """The lock every other method here expects its caller to hold."""
        return self._lock

    def queued(self) -> int:
        """Count the messages waiting on disk."""
        return len(self._outbox)

    def held(self) -> int:
        """Count the questions waiting in memory."""
        return len(self._held)

    async def queue(self, envelope: Envelope) -> None:
        """Keep `envelope` on disk, unless its id is already waiting. Hold `lock`.

        Args:
            envelope: The message to send once the link is back.

        Raises:
            OfflineRefusedError: The message is too large to ever be sent once signed, or the disk
                could not take it; either way it was not kept.
        """
        try:
            # Blocking: one fsync, a millisecond on an SSD and tens on an SD card; off the loop.
            await asyncio.to_thread(self._outbox.append, envelope)
        except FrameTooLargeError as exc:
            raise OfflineRefusedError(
                f"Message {envelope.id} ({envelope.kind}) is too large to keep for later."
            ) from exc
        except OSError as exc:
            raise OfflineRefusedError(
                f"Message {envelope.id} ({envelope.kind}) could not be written to the outbox."
            ) from exc

    def hold(self, envelope: Envelope) -> None:
        """Keep `envelope` in memory until the queue has been drained. Hold `lock`.

        Args:
            envelope: A message that waits for the Queen but not for the disk (a question).
        """
        if all(held.id != envelope.id for held in self._held):
            self._held.append(envelope)

    async def expire(self) -> int:
        """Drop queued messages older than the freshness window, which no receiver would read.

        Returns:
            How many entries were dropped.

        Raises:
            OSError: An acknowledgement could not be written.
        """
        # Blocking: one fsync per expired entry; off the event loop like every other write.
        return await asyncio.to_thread(expire_older_than, self._outbox, self._clock)

    async def replay_one(self, transport: Transport) -> bool:
        """Send the oldest queued message through `transport`, then acknowledge it. Hold `lock`.

        Args:
            transport: The live link; its codec signs the frame as it goes.

        Returns:
            True if an entry was dealt with (sent, or acknowledged as undecodable); False once the
            queue is empty.

        Raises:
            waggle.errors.TransportError: The link failed; the entry stays queued.
            TimeoutError: The link took no frame for SEND_TIMEOUT_S; the entry stays queued.
        """
        pending = self._outbox.pending_ids()
        if not pending:
            return False
        message_id = pending[0]
        try:
            envelope = self._outbox.load(message_id)
        except CodecError:
            # Poison: an upgrade unregistered its kind. It never will be sendable, and leaving it
            # at the head would wedge everything behind it.
            await asyncio.to_thread(self._outbox.ack, message_id)
            return True
        # A request goes out fresh (module docstring); an event keeps its id.
        outgoing = self._fresh(envelope) if owes_reply(envelope.kind) else envelope
        async with asyncio.timeout(SEND_TIMEOUT_S):  # One frame; longer than this is a dead link.
            await transport.send(outgoing)
        # Acknowledged only now: a send that raised above left the entry queued for the next try.
        await asyncio.to_thread(self._outbox.ack, message_id)
        return True

    async def release_held(self, transport: Transport) -> int:
        """Send every held question through `transport`, oldest first. Hold `lock`.

        Args:
            transport: The live link.

        Returns:
            How many were sent.

        Raises:
            waggle.errors.TransportError: The link failed; the unsent ones are still held.
            TimeoutError: The link took no frame for SEND_TIMEOUT_S.
        """
        sent = 0
        while self._held:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await transport.send(self._held[0])
            self._held.pop(0)
            sent += 1
        return sent

    def _fresh(self, envelope: Envelope) -> Envelope:
        """Wrap a queued request's payload in a new envelope: a new id and time, the same hop."""
        hop = Hop(sender=envelope.sender, recipient=envelope.recipient, node_id=envelope.node_id)
        return wrap(envelope.payload, hop, clock=self._clock)
