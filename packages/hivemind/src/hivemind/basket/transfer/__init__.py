"""Move a blob between two nodes: chunked, resumable, verified, as two pure state machines.

A blob travels as an `Offer`, an `Ack` or `Refuse`, then `Chunk`s each answered by an `Ack`, the
last with `complete` set. `TransferSender` cuts chunks from wherever the receiver says it has got
to; `TransferReceiver` refuses by rule before the first byte, takes chunks in order into a spool
file, and verifies the whole blob's hash and the manifest's signature before handing the file
over. Neither touches the wire: `wire.py` (a later step) maps these values to the `basket.*`
Waggle messages. Every transfer goes through the Hive Stand, since nodes connect out to the Queen.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Driven by the
    Queen's and Wardens' Basket ticks (later steps). Calls into `hivemind.basket` (models,
    manifest, clearance, errors) and waggle only.

Key invariants:
    - `CHUNK_BYTES * 4 / 3 + 4096 < waggle.codec.MAX_FRAME_BYTES`.
    - No byte is kept outside the receiver's spool file before the whole blob has verified.
    - A transfer is resumable from the receiver's `received_bytes`; nothing else carries state.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md.
    - docs/waggle/spec.md, the `basket.*` messages.

Public API:
    - CHUNK_BYTES, Offer, Chunk, Ack, Refuse, RefusalCode (models): the transfer values.
    - TransferPhase, TRANSITIONS, advance (state): the one transfer machine.
    - TransferSender, ChunkSource, BytesChunkSource (sender): the sending side.
    - TransferReceiver, ResidencyPlanner, IntegrityFailure (receiver): the receiving side.
"""

from hivemind.basket.transfer.models import (
    CHUNK_BYTES,
    Ack,
    Chunk,
    Offer,
    RefusalCode,
    Refuse,
)
from hivemind.basket.transfer.receiver import IntegrityFailure, ResidencyPlanner, TransferReceiver
from hivemind.basket.transfer.sender import BytesChunkSource, ChunkSource, TransferSender
from hivemind.basket.transfer.state import TRANSITIONS, TransferPhase, advance

__all__ = [
    "CHUNK_BYTES",
    "TRANSITIONS",
    "Ack",
    "BytesChunkSource",
    "Chunk",
    "ChunkSource",
    "IntegrityFailure",
    "Offer",
    "RefusalCode",
    "Refuse",
    "ResidencyPlanner",
    "TransferPhase",
    "TransferReceiver",
    "TransferSender",
    "advance",
]
