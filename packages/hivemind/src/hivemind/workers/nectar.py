"""Cut one Worker's Nectar into the Waggle chunks a Honey Store deposit travels as.

Nectar is raw information a Worker (a sub-agent doing one task) brings back for the Honey Store,
the Hive's knowledge base, where the House Bee (the maintenance Worker) later ripens it into
searchable Honey. A deposit crosses Waggle (the bee-to-bee wire protocol) as a run of
`NectarDeposit` messages, because no `bytes` field on the wire may exceed `MAX_CHUNK_BYTES`
(docs/waggle/spec.md section 5). `split_deposit` is that cut, pure and with no I/O: every chunk
repeats the whole deposit's metadata (`DepositMeta`), its byte offset, the whole content's length
and sha256, and whether it is the last one, so the Queen's intake can reassemble the chunks by
`(sender, sha256)` and verify the whole before storing it.

Fits into the Hive:
    Layer 4 (roles that do the work), directly under the workers package so both halves that
    deposit share it: `hivemind.workers.tools.honey.remember` (a finding the model chose to keep)
    and `hivemind.workers.runtime.honey.deposit_handoff` (the Handoff written at a checkpoint).
    Calls into waggle only; the chunks it returns go out through `WorkerContext.honey.deposit`.

Key invariants:
    - The chunks, concatenated in order, are exactly `content`; the first starts at offset 0,
      each later one at the running length, and only the last is `final`.
    - Every chunk carries the sha256 and length of the whole content, never of itself.
    - Pure: the same content and metadata always produce the same chunks.

See Also:
    - docs/waggle/spec.md sections 5 and 8.7 for the chunking rule and NectarDeposit.
    - hivemind.honey_store.nectar.reassembly for the receiving side's reassembly rules.
    - hivemind.workers.context for HoneyChannel, the channel the chunks are sent through.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from waggle.ids import CellId, EventId, TaskId, WorkerId
from waggle.messages import CombShieldLevel, HoneyClearance
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.honey import NectarDeposit, NectarKind

__all__ = ["DepositMeta", "split_deposit"]


@dataclass(frozen=True, slots=True)
class DepositMeta:
    """What one whole deposit is, repeated on every chunk of it (docs/waggle/spec.md 8.7)."""

    kind: NectarKind  # What sort of finding: FINDING for `remember`, HANDOFF for a checkpoint.
    media_type: str  # The content's MIME type; the ripener decodes and chunks by it.
    title: str  # A one-line label for the browser and the ripener (the wire caps it at 200).
    task_id: TaskId | None  # The task it came from; None only for a finding outside any task.
    cell_id: CellId  # The Cell it was gathered on; intake refuses another Cell's deposit.
    worker_id: WorkerId | None  # The depositing Worker; None only when a Warden deposits.
    observed_at: datetime  # When the finding was observed, not when a chunk is sent.
    clearance: HoneyClearance  # The depositor's own label; intake may raise it, never lower it.
    origin_tier: CombShieldLevel  # The Cell's tier as this bee believes it; intake uses its own.
    event_id: EventId | None = None  # For a HANDOFF only: its memory.checkpoint trail event.


def split_deposit(content: bytes, meta: DepositMeta) -> tuple[NectarDeposit, ...]:
    """Cut `content` into consecutive `MAX_CHUNK_BYTES` chunks that share `meta`.

    Args:
        content: The whole deposit; at least one byte (an empty deposit carries nothing).
        meta: The whole-deposit metadata every chunk repeats.

    Returns:
        The chunks in offset order, the last one `final`; a single chunk when `content` fits in
        one.

    Raises:
        ValueError: `content` is empty.
        pydantic.ValidationError: `meta` breaks a NectarDeposit bound or validator (a title over
            200 characters, an `event_id` on anything but a HANDOFF, ...).

    Example:
        >>> chunks = split_deposit(b"x" * (MAX_CHUNK_BYTES + 1), meta)
        >>> [(chunk.offset, len(chunk.chunk), chunk.final) for chunk in chunks]
        [(0, 262144, False), (262144, 1, True)]
    """
    # An empty deposit is refused here, with the reason, rather than by the wire's own
    # total_bytes bound one message later.
    if not content:
        raise ValueError(f"A {meta.kind.value} deposit needs at least one byte of content.")
    # The digest and length of the whole, computed once: every chunk carries the same pair.
    digest = hashlib.sha256(content).hexdigest()
    total = len(content)
    # Every chunk start, in order; the last chunk may be shorter than the rest.
    return tuple(
        _chunk(meta, content[offset : offset + MAX_CHUNK_BYTES], offset, (digest, total))
        for offset in range(0, total, MAX_CHUNK_BYTES)
    )


def _chunk(meta: DepositMeta, piece: bytes, offset: int, whole: tuple[str, int]) -> NectarDeposit:
    """Build one chunk: `meta`, this slice at `offset`, and the whole's digest and length."""
    digest, total = whole
    return NectarDeposit(
        sha256=digest,
        kind=meta.kind,
        media_type=meta.media_type,
        title=meta.title,
        task_id=meta.task_id,
        cell_id=meta.cell_id,
        worker_id=meta.worker_id,
        observed_at=meta.observed_at,
        clearance=meta.clearance,
        origin_tier=meta.origin_tier,
        event_id=meta.event_id,
        chunk=piece,
        offset=offset,
        total_bytes=total,
        # The last chunk is the one that reaches the end of the content.
        final=offset + len(piece) >= total,
    )
