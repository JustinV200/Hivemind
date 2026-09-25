"""SQL and transactions for `honey_vectors`: writing, pruning and finding rows still pending one.

`set_vectors` upserts one float32 blob per `(honey row, model)` pair -- `honey_vectors`' own
primary key -- so a row may hold vectors for more than one model at once while a re-embed is in
flight (ADR-0036); `pending_vectors` is the anti-join that a ripening pass reads from, finding live
rows with no vector yet for the current model, and `prune_vectors_transaction` is the same
anti-join used the other way round: it deletes every other model's vectors, but only once that
join finds nothing missing (ADR-0037). None of the three methods needs a `ReadFilter`: all three
are maintenance bookkeeping over every live row, never a reader's own filtered query.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.common.sqlite` (transaction), `hivemind.honey_store.errors`
    (HoneyNotFoundError), `hivemind.honey_store.models` (Honey), `hivemind.honey_store.store.
    protocol` (PruneEvents, PruneResult), `hivemind.honey_store.store.sqlite.honey`
    (`_row_to_honey`, reused rather than duplicated), `hivemind.honey_store.store.sqlite.vec`
    (encode_vector), `hivemind.pheromone` (insert_event) only.

Key invariants:
    - `set_vectors_transaction` refuses a zero-norm vector before writing anything for it (a
      cosine distance against a zero vector is undefined); every other pair in the same call still
      succeeds independently.
    - Every vector is stored as a little-endian float32 blob (`hivemind.honey_store.store.
      sqlite.vec.encode_vector`), whichever backend later reads it back.
    - `prune_vectors_transaction` never deletes a row while any live Honey row still lacks a
      vector for `kept_model`; the coverage check and the deletion run inside the same
      transaction, so nothing can write a new gap in between.

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the coexisting-models rule.
    - docs/adr/0037-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for the
      prune-on-request rule this module also implements.
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.store.sqlite.vec for encode_vector, the blob codec this module writes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from hivemind.common.sqlite import transaction
from hivemind.honey_store.errors import HoneyNotFoundError
from hivemind.honey_store.models import Honey
from hivemind.honey_store.store.protocol import PruneEvents, PruneResult
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
_PENDING_JOIN_SQL = """
FROM honey h
LEFT JOIN honey_vectors hv ON hv.honey_seq = h.honey_seq AND hv.model = ?
WHERE hv.honey_seq IS NULL AND h.tainted = 0 AND h.retired_at IS NULL
"""
_SELECT_PENDING_SQL = f"SELECT h.* {_PENDING_JOIN_SQL} ORDER BY h.created_at, h.honey_seq LIMIT ?"
# prune_vectors' own coverage check: how many live rows the pending-for-kept_model anti-join
# above still finds, with no LIMIT, since the count itself decides whether to prune at all.
_SELECT_MISSING_COUNT_SQL = f"SELECT COUNT(*) AS n {_PENDING_JOIN_SQL}"
_SELECT_OTHER_MODEL_COUNTS_SQL = (
    "SELECT model, COUNT(*) AS n FROM honey_vectors WHERE model != ? GROUP BY model"
)
_DELETE_OTHER_MODELS_SQL = "DELETE FROM honey_vectors WHERE model != ?"


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


def prune_vectors_transaction(
    connection: sqlite3.Connection, kept_model: str, events: PruneEvents
) -> PruneResult:
    """Delete every other model's vectors, only once every live row has one for `kept_model`.

    ADR-0037: the coverage check and the deletion run in the same transaction (`transaction`'s
    own `BEGIN IMMEDIATE` already holds the write lock, so nothing else can add a fresh gap
    between the two), and `events` is called with the outcome either way, exactly like
    `nectar.add_nectar_transaction`'s own `NectarEvents`.
    """
    with transaction(connection):
        missing = connection.execute(_SELECT_MISSING_COUNT_SQL, (kept_model,)).fetchone()["n"]
        if missing > 0:
            # Refused: some live row still lacks a vector for kept_model, so nothing is dropped.
            result = PruneResult(kept_model=kept_model, missing=missing, dropped={})
        else:
            rows = connection.execute(_SELECT_OTHER_MODEL_COUNTS_SQL, (kept_model,)).fetchall()
            dropped = {row["model"]: row["n"] for row in rows}
            connection.execute(_DELETE_OTHER_MODELS_SQL, (kept_model,))
            result = PruneResult(kept_model=kept_model, missing=0, dropped=dropped)
        for event in events(result):
            insert_event(connection, event)
        return result


def _refuse_zero_norm(honey_id: HoneyId, vector: Sequence[float]) -> None:
    """Raise ValueError when `vector`'s norm is zero: cosine distance against it is undefined."""
    if all(value == 0.0 for value in vector):
        raise ValueError(
            f"honey_id {honey_id!r}: a zero-norm vector is refused (cosine distance against it "
            "is undefined)."
        )
