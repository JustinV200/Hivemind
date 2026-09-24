"""SQL for ranked search: full-text over `honey_fts`, vector over `honey_vectors`, both backends.

`select_text_candidates` ranks by SQLite FTS5's own `bm25()` (more negative is a better match,
hence `ORDER BY bm25(honey_fts)` ascending); `select_vector_candidates`/`select_nearest_in_scope`
each take a `use_sqlite_vec` flag chosen once at store construction
(`hivemind.honey_store.store.sqlite.vec.load_vector_extension`): true runs `vec_distance_cosine`
in SQL over an ordinary indexed scan, false pulls the filtered candidate rows into Python and ranks
them with `hivemind.honey_store.store.sqlite.vec.cosine_distances` -- identical results, ADR-0031's
"slower, identical" fallback. `select_count_withheld` re-checks the *specific* top-`limit` live
matches a first, filter-free query already picked, rather than compare two independently-limited
queries whose top sets could differ once filtering changes the ranking pool.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.honey_store.models` (ReadFilter, TextCandidate, VectorCandidate),
    `hivemind.honey_store.store.sqlite.filters` (read_filter_clauses, passes_filter),
    `hivemind.honey_store.store.sqlite.honey` (`_row_to_honey`, reused) and `hivemind.honey_store.
    store.sqlite.vec` (encode_vector, cosine_distances) only.

Key invariants:
    - Every filtered method applies `read_filter_clauses` (or, for `nearest_in_scope`, an exact
      `scope = ?`) before `ORDER BY`/`LIMIT`, in SQL, never after (ADR-0031).
    - `select_vector_candidates`/`select_nearest_in_scope` only ever compare vectors written for
      the same `model` (`hv.model = ?`): a row embedded by a different model never surfaces
      (ADR-0032).
    - The two vector backends return the same ordering for the same inputs
      (`tests/contracts/test_honey_store_contract.py` parametrises over both).

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the ranking and fallback rules.
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.store.fts for build_match, `match`'s own builder.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.honey_store.models import ReadFilter, TextCandidate, VectorCandidate
from hivemind.honey_store.store.sqlite.filters import passes_filter, read_filter_clauses
from hivemind.honey_store.store.sqlite.honey import _row_to_honey
from hivemind.honey_store.store.sqlite.vec import cosine_distances, encode_vector

# No __all__: internal collaborator of hivemind.honey_store.store.sqlite.store only (see
# hivemind.honey_store.store.sqlite.nectar's identical note).

_SELECT_TEXT_SQL = """
SELECT h.*, bm25(honey_fts) AS bm25_score
FROM honey_fts
JOIN honey h ON h.honey_seq = honey_fts.rowid
WHERE honey_fts MATCH ? AND {where}
ORDER BY bm25(honey_fts)
LIMIT ?
"""
_SELECT_WITHHELD_CANDIDATES_SQL = """
SELECT h.scope AS scope, h.clearance_rank AS clearance_rank
FROM honey_fts
JOIN honey h ON h.honey_seq = honey_fts.rowid
WHERE honey_fts MATCH ? AND h.tainted = 0 AND h.retired_at IS NULL
ORDER BY bm25(honey_fts)
LIMIT ?
"""
_SELECT_VECTORS_SQLITE_VEC_SQL = """
SELECT h.*, vec_distance_cosine(hv.vector, ?) AS distance
FROM honey h
JOIN honey_vectors hv ON hv.honey_seq = h.honey_seq
WHERE hv.model = ? AND hv.dims = ? AND {where}
ORDER BY distance
LIMIT ?
"""
_SELECT_VECTORS_PYTHON_SQL = """
SELECT h.*, hv.vector AS vector_blob
FROM honey h
JOIN honey_vectors hv ON hv.honey_seq = h.honey_seq
WHERE hv.model = ? AND hv.dims = ? AND {where}
"""
_NEAREST_IN_SCOPE_WHERE = "h.scope = ? AND h.tainted = 0 AND h.retired_at IS NULL"
# Both vector queries also match `hv.dims` to the query's own length: a model id reused for a
# different output size must never reach vec_distance_cosine (which refuses mixed lengths) or the
# Python fallback (whose strict zip would fail the whole query over one stale row).


@dataclass(frozen=True, slots=True)
class _VectorQuery:
    """One vector search's own inputs, bundled per codingrules 5.1's parameter-count limit.

    `use_sqlite_vec` rides along because every caller already has it in hand (the store's own
    backend choice from construction) and both search functions branch on it identically.
    """

    vector: Sequence[float]
    model: str
    use_sqlite_vec: bool


def select_text_candidates(
    connection: sqlite3.Connection, match: str, filter_: ReadFilter, limit: int
) -> tuple[TextCandidate, ...]:
    """Full-text search live rows within `filter_`, best bm25 match first."""
    clauses, params = read_filter_clauses(filter_, table_alias="h")
    sql = _SELECT_TEXT_SQL.format(where=" AND ".join(clauses))
    rows = connection.execute(sql, [match, *params, limit]).fetchall()
    return tuple(TextCandidate(honey=_row_to_honey(row), bm25=row["bm25_score"]) for row in rows)


def select_count_withheld(
    connection: sqlite3.Connection, match: str, filter_: ReadFilter, limit: int
) -> int:
    """Count how many of the top `limit` live text matches `filter_` would exclude."""
    rows = connection.execute(_SELECT_WITHHELD_CANDIDATES_SQL, (match, limit)).fetchall()
    return sum(1 for row in rows if not passes_filter(row["scope"], row["clearance_rank"], filter_))


def select_vector_candidates(
    connection: sqlite3.Connection, query: _VectorQuery, filter_: ReadFilter, limit: int
) -> tuple[VectorCandidate, ...]:
    """Nearest-neighbour search live rows within `filter_`, for one embedding model."""
    clauses, params = read_filter_clauses(filter_, table_alias="h")
    where = " AND ".join(clauses)
    if query.use_sqlite_vec:
        sql = _SELECT_VECTORS_SQLITE_VEC_SQL.format(where=where)
        blob = encode_vector(query.vector)
        rows = connection.execute(
            sql, [blob, query.model, len(query.vector), *params, limit]
        ).fetchall()
        return tuple(
            VectorCandidate(honey=_row_to_honey(row), distance=row["distance"]) for row in rows
        )
    sql = _SELECT_VECTORS_PYTHON_SQL.format(where=where)
    rows = connection.execute(sql, [query.model, len(query.vector), *params]).fetchall()
    return _rank_python_fallback(query.vector, rows, limit)


def select_nearest_in_scope(
    connection: sqlite3.Connection, query: _VectorQuery, scope: str, limit: int
) -> tuple[VectorCandidate, ...]:
    """Nearest-neighbour search live rows in exactly `scope`, any clearance (near-duplicates)."""
    if query.use_sqlite_vec:
        sql = _SELECT_VECTORS_SQLITE_VEC_SQL.format(where=_NEAREST_IN_SCOPE_WHERE)
        blob = encode_vector(query.vector)
        rows = connection.execute(
            sql, (blob, query.model, len(query.vector), scope, limit)
        ).fetchall()
        return tuple(
            VectorCandidate(honey=_row_to_honey(row), distance=row["distance"]) for row in rows
        )
    sql = _SELECT_VECTORS_PYTHON_SQL.format(where=_NEAREST_IN_SCOPE_WHERE)
    rows = connection.execute(sql, (query.model, len(query.vector), scope)).fetchall()
    return _rank_python_fallback(query.vector, rows, limit)


def _rank_python_fallback(
    vector: Sequence[float], rows: Sequence[sqlite3.Row], limit: int
) -> tuple[VectorCandidate, ...]:
    """Decode every row's blob, rank by cosine distance in Python, and take the nearest `limit`."""
    distances = cosine_distances(vector, (row["vector_blob"] for row in rows))
    scored = list(zip(rows, distances, strict=True))
    scored.sort(key=lambda pair: pair[1])  # Nearest (smallest distance) first.
    return tuple(
        VectorCandidate(honey=_row_to_honey(row), distance=distance)
        for row, distance in scored[:limit]
    )
