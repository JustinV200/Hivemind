"""Provide Outbox, the durable first-in-first-out queue of envelopes a node could not send.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). When a
node's link is down, a Warden (the always-on supervisor of one Cell, a unit of compute) keeps
working within what it owns and a Pollen Packet (the thin gateway on an enrolled device) keeps
reporting; what they cannot send waits here, on disk, until the link is back and
``waggle.outbox.replay.replay_outbox`` sends it in order. ``append`` encodes the envelope (the
outer wrapper every message travels in) to its UNSIGNED wire text through a signer-less,
verifier-less view of the caller's codec and writes one ``put`` record, flushed and ``fsync``ed
before it returns; ``ack`` writes one ``ack`` record once the envelope is sent, expired or found
poisonous; ``pending`` decodes what is still queued in the order it was appended. The outbox
stores unsigned frames on purpose: replay re-encodes each through the sending transport's codec,
so signing always happens at send time with the node's current key, a key rotated between a
crash and its replay still yields valid signatures, and a stolen outbox file carries nothing a
peer would accept. It is the node's own durable memory, never a trust boundary, which is why it
verifies nothing on the way back in. The log file itself (record shapes, the torn-line rule,
compaction) is ``waggle.outbox.log``'s.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Written by every bee loop that fails a send and
    read by waggle.outbox.replay on reconnection; calls into waggle.codec for the wire text and
    waggle.outbox.log for the file. Latency class: one fsync per append or ack (milliseconds;
    tens of milliseconds on an SD card), a full read and rewrite on open.

Key invariants:
    - pending() and pending_ids() are first-in-first-out: the order envelopes were appended in,
      acks removed; an id is queued at most once at a time, and append of a pending id is a
      no-op that returns False.
    - Every frame stored fits the codec's limit with SIGNATURE_OVERHEAD_BYTES to spare, so
      signing it at send time can never push it over; append refuses anything larger.
    - append and ack return only after their record is fsynced; a crash loses at most the
      record whose call never returned, which open then ignores as a torn line.
    - The file on disk after open holds exactly the pending put records (compaction).

See Also:
    - waggle.outbox.log for the JSONL log this queue is kept in.
    - waggle.outbox.replay for replay_outbox, ReplayReport and expire_older_than.
    - docs/waggle/spec.md section 10 and docs/adr/0005-waggle-envelope-signing-and-offline-outbox.md
      for the decision that shaped this module.
"""

from __future__ import annotations

from pathlib import Path

from waggle.codec import SIGNATURE_OVERHEAD_BYTES, Codec
from waggle.envelope import Envelope
from waggle.errors import FrameTooLargeError
from waggle.ids import MessageId
from waggle.outbox.log import (
    append_record,
    encode_ack,
    encode_put,
    parse_log,
    pending_after,
    rewrite_log,
)

__all__ = ["Outbox"]


class Outbox:
    """A node's on-disk queue of unsent envelopes, appended, replayed in order and acked.

    Every method is synchronous and the writes block on ``fsync``; the outbox is the one
    owner of its file and of the in-memory pending map, which it mutates in place. Concurrency
    model: one task drives an outbox at a time. PERF: an async caller that cannot afford a
    blocking fsync on the event loop runs ``append`` and ``ack`` under ``asyncio.to_thread``,
    as ``replay_outbox`` does for its acks; the reads (``pending``, ``load``, ``pending_ids``,
    ``len``) are in-memory and CPU-only and need no thread.
    """

    def __init__(self, path: Path, codec: Codec | None = None) -> None:
        """Open the log at ``path``, rebuild what is pending, and compact the file.

        Args:
            path: The JSONL log; created (empty) when it does not exist. Its directory must.
            codec: The codec the node sends with; only its ``max_frame_bytes`` is used, to
                refuse an envelope that would not fit once signed. None means the default
                limit. Its signer and verifier are deliberately not used: the outbox stores
                unsigned frames and reads back its own memory.

        Raises:
            OutboxCorruptError: A record other than a torn last line cannot be read.
            OSError: The log cannot be read, or the compacted log cannot be written.
        """
        limit = (codec if codec is not None else Codec()).max_frame_bytes
        self._path = path
        # The signer-less, verifier-less view of the caller's codec: the same size limit, no
        # signature on the way in (spec section 10) and none demanded on the way out.
        self._codec = Codec(max_frame_bytes=limit)
        # What a stored frame may be at most: the wire limit less what a signature adds, so the
        # sending codec can sign it later without ever hitting its own limit.
        self._stored_limit = limit - SIGNATURE_OVERHEAD_BYTES
        # Replay the log into the pending map, then rewrite the file as exactly that map: acks
        # and a torn tail are gone, and a directory that cannot be written fails here, at open.
        self._pending: dict[MessageId, str] = pending_after(parse_log(_read_log(path)))
        rewrite_log(path, self._pending)

    @property
    def path(self) -> Path:
        """The log file this outbox is kept in.

        Returns:
            The path given at construction, unchanged.
        """
        return self._path

    def __len__(self) -> int:
        """Count the envelopes waiting to be sent.

        Returns:
            How many put records have no ack yet.
        """
        return len(self._pending)

    def append(self, envelope: Envelope) -> bool:
        """Queue ``envelope`` durably, unless its id is already waiting.

        Args:
            envelope: A consistent Envelope; any signature it carries is dropped, since the
                sending codec signs afresh at replay.

        Returns:
            True once the put record is on disk; False when the id was already pending and
            nothing was written (a retry of the same append is idempotent).

        Raises:
            FrameTooLargeError: The unsigned frame would exceed the codec's limit less
                SIGNATURE_OVERHEAD_BYTES, so it could never be sent once signed; nothing is
                written and the queue is never wedged by it.
            OSError: The record could not be written or synced.
        """
        # Encode before touching the file so a refusal costs no I/O; the codec's own limit is
        # the full wire limit, so the tighter, signature-aware check is made here.
        frame = self._codec.encode(envelope)
        if len(frame) > self._stored_limit:
            raise FrameTooLargeError(
                f"The unsigned frame for envelope {envelope.id} is {len(frame)} bytes; the "
                f"outbox stores at most {self._stored_limit} so that the "
                f"{SIGNATURE_OVERHEAD_BYTES} bytes a signature adds still fit the wire limit of "
                f"{self._codec.max_frame_bytes}."
            )
        # A pending id is already promised: writing it again would queue it twice on replay.
        if envelope.id in self._pending:
            return False
        text = frame.decode("utf-8")
        # Disk first, memory second: if the write raises, the map still matches the file.
        append_record(self._path, encode_put(envelope.id, text))
        self._pending[envelope.id] = text
        return True

    def ack(self, message_id: MessageId) -> None:
        """Record that ``message_id`` no longer needs sending: sent, expired or poisonous.

        Args:
            message_id: The id of a pending envelope; an unknown or already acked id is a
                no-op and writes nothing.

        Raises:
            OSError: The record could not be written or synced.
        """
        if message_id not in self._pending:
            return
        # Disk first, memory second, as in append: a failed write leaves the entry pending.
        append_record(self._path, encode_ack(message_id))
        del self._pending[message_id]

    def pending_ids(self) -> tuple[MessageId, ...]:
        """List the ids waiting to be sent, first appended first.

        Returns:
            The pending ids in queue order; a snapshot, unaffected by later appends or acks.
        """
        return tuple(self._pending)

    def load(self, message_id: MessageId) -> Envelope:
        """Decode the pending envelope with ``message_id``.

        Args:
            message_id: An id from ``pending_ids``.

        Returns:
            The envelope as it was appended, unsigned.

        Raises:
            KeyError: ``message_id`` is not pending.
            CodecError: The stored frame no longer decodes on this node, which happens when an
                upgrade unregistered its kind or changed its payload model between the append
                and now; ``replay_outbox`` acks and reports such an entry rather than
                replaying it forever.
        """
        return self._codec.decode(self._pending[message_id].encode("utf-8"))

    def pending(self) -> tuple[Envelope, ...]:
        """Decode every pending envelope, first appended first.

        Returns:
            The envelopes in queue order, unsigned.

        Raises:
            CodecError: A stored frame no longer decodes on this node (see ``load``); use
                ``pending_ids`` and ``load`` to isolate one such entry.
        """
        return tuple(self.load(message_id) for message_id in self._pending)


def _read_log(path: Path) -> bytes:
    """Return the log's bytes, or nothing for a log that does not exist yet."""
    # Catching the miss rather than testing exists() first: one syscall, and no window in
    # which another process could create the file between the test and the read.
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""
