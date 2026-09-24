"""SQL and transactions for `honey_vectors`: writing embeddings and finding rows still pending one.

`set_vectors` upserts one float32 blob per `(honey row, model)` pair -- `honey_vectors`' own
primary key -- so a row may hold vectors for more than one model at once while a re-embed is in
flight (ADR-0032); `pending_vectors` is the anti-join that pipeline pass reads from, finding live
rows with no vector yet for the current model. Neither method needs a `ReadFilter`: both are the
ripening pipeline's own internal bookkeeping, never a reader's query.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.common.sqlite` (transaction), `hivemind.honey_store.errors`
    (HoneyNotFoundError), `hivemind.honey_store.models` (Honey), `hivemind.honey_store.store.
    sqlite.honey` (`_row_to_honey`, reused rather than duplicated), `hivemind.honey_store.store.
    sqlite.vec` (encode_vector) and `hivemind.pheromone` (insert_event) only.

Key invariants:
    - `set_vectors_transaction` refuses a zero-norm vector before writing anything for it (a
      cosine distance against a zero vector is undefined); every other pair in the same call still
      succeeds independently.
    - Every vector is stored as a little-endian float32 blob (`hivemind.honey_store.store.
      sqlite.vec.encode_vector`), whichever backend later reads it back.

See Also:
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the coexisting-models rule.
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.store.sqlite.vec for encode_vector, the blob codec this module writes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from hivemind.common.sqlite import transaction
from hivemind.honey_store.errors import HoneyNotFoundError
from hivemind.honey_store.models import Honey
from hivemind.honey_store.store.sqlite.honey import _row_to_honey
from hivemind.honey_store.store.sqlite.vec import encode_vector
from hivemind.pheromone import HoneyEvent, insert_event
from waggle.ids import HoneyId

# No __all__: internal collaborator of hivemind.honey_store.store.sqlite.store only (see
# hivemind.honey_store.store.sqlite.nectar's identical note).

# honey_seq is resolved from the public HoneyId in the same statement, so a caller never needs a
# separate lookup; ON CONFLICT covers the "a vector for this model already exists" re-embed case.
_UPSERT_SQL = """
INSERT INTO honey_vectors (honey_seq, model, dims, vector)
SELECT honey_seq, ?, ?, ? FROM honey WHERE id = ?
ON CONFLICT (honey_seq, model) DO UPDATE SET dims = excluded.dims, vector = excluded.vector
"""
_UPDATE_EMBEDDING_MODEL_SQL = "UPDATE honey SET embedding_model = ? WHERE id = ?"
_SELECT_PENDING_SQL = """
SELECT h.* FROM honey h
LEFT JOIN honey_vectors hv ON hv.honey_seq = h.honey_seq AND hv.model = ?
WHERE hv.honey_seq IS NULL AND h.tainted = 0 AND h.retired_at IS NULL
ORDER BY h.created_at, h.honey_seq
LIMIT ?
"""


def set_vectors_transaction(
    connection: sqlite3.Connection,
    pairs: Sequence[tuple[HoneyId, Sequence[float]]],
    model: str,
    event: HoneyEvent | None,
) -> int:
    """Upsert one vector per pair for `model`, then insert `event` when given; one transaction."""
    with transaction(connection):
        written = 0
        for honey_id, vector in pairs:
            _refuse_zero_norm(honey_id, vector)
            blob = encode_vector(vector)
            cursor = connection.execute(_UPSERT_SQL, (model, len(vector), blob, honey_id))
            if cursor.rowcount == 0:
                raise HoneyNotFoundError(honey_id)
            connection.execute(_UPDATE_EMBEDDING_MODEL_SQL, (model, honey_id))
            written += 1
        if event is not None:
            insert_event(connection, event)
        return written


def select_pending_vectors(
    connection: sqlite3.Connection, model: str, limit: int
) -> tuple[Honey, ...]:
    """Return live rows with no vector yet for `model`, oldest first, at most `limit`."""
    rows = connection.execute(_SELECT_PENDING_SQL, (model, limit)).fetchall()
    return tuple(_row_to_honey(row) for row in rows)


def _refuse_zero_norm(honey_id: HoneyId, vector: Sequence[float]) -> None:
    """Raise ValueError when `vector`'s norm is zero: cosine distance against it is undefined."""
    if all(value == 0.0 for value in vector):
        raise ValueError(
            f"honey_id {honey_id!r}: a zero-norm vector is refused (cosine distance against it "
            "is undefined)."
        )
