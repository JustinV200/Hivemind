"""Load the sqlite-vec extension when possible, and the float32 codec and cosine fallback.

`sqlite-vec` is a normal dependency (not the optional `embeddings` extra), but its ability to
*load* as a SQLite extension still depends on the host Python's `sqlite3` build: some distributions
compile it without extension-loading support at all. `load_vector_extension` is the one place that
uncertainty is resolved, once, at store construction; every vector is stored the same way either
way (a little-endian float32 blob, exactly sqlite-vec's own layout), so
`hivemind.honey_store.store.sqlite.search`'s two code paths (`vec_distance_cosine` in SQL, or
`cosine_distance` in Python over the same filtered rows) return identical results, only at
different speed (ADR-0031).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore.create` (`load_vector_extension`)
    and by `.vectors`/`.search` (`encode_vector`/`decode_vector`/`cosine_distance`). Calls into
    `sqlite_vec` (imported lazily, inside the one function that needs it) and `hivemind.common.
    logging` only.

Key invariants:
    - `load_vector_extension` never raises: `ImportError`, `AttributeError` (no extension loading
      in this Python build) and `sqlite3.OperationalError` (the extension file itself failing to
      load) are all caught and turned into a False return plus one warning log line.
    - `encode_vector`/`decode_vector` round-trip exactly for any finite `float` sequence
      representable in IEEE 754 single precision (`tests/unit/honey_store/store/sqlite/
      test_vec.py`).

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for why exact search over a plain table
      beats a `vec0` virtual table at this scale, and the ImportError/AttributeError/
      OperationalError fallback rule this module implements.
    - hivemind.honey_store.store.sqlite.vectors for `set_vectors`/`pending_vectors`, which call
      `encode_vector`.
    - hivemind.honey_store.store.sqlite.search for `search_vectors`/`nearest_in_scope`, which
      branch on the backend this module chose.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from collections.abc import Sequence

from hivemind.common.logging import get_logger

VECTOR_BACKEND_SQLITE_VEC = "sqlite_vec"  # vec_distance_cosine runs in SQL, over an index scan.
VECTOR_BACKEND_PYTHON = "python"  # cosine_distance runs in Python, over the same filtered rows.
_ZERO_NORM_DISTANCE = 1.0  # No direction to compare against; a defined, maximally-neutral answer.

__all__ = [
    "VECTOR_BACKEND_PYTHON",
    "VECTOR_BACKEND_SQLITE_VEC",
    "cosine_distance",
    "decode_vector",
    "encode_vector",
    "load_vector_extension",
]

log = get_logger(__name__)


def encode_vector(values: Sequence[float]) -> bytes:
    """Pack `values` as a little-endian float32 blob, the layout sqlite-vec's own tables use.

    Args:
        values: A non-empty embedding vector.

    Returns:
        `4 * len(values)` bytes, one little-endian IEEE 754 single-precision float per value.
    """
    return struct.pack(f"<{len(values)}f", *values)


def decode_vector(blob: bytes) -> tuple[float, ...]:
    """Unpack a little-endian float32 blob back into a tuple of floats.

    Args:
        blob: Bytes produced by `encode_vector` (or by sqlite-vec itself).

    Returns:
        One float per 4-byte group in `blob`, in order.
    """
    count = len(blob) // 4
    return struct.unpack(f"<{count}f", blob)


def cosine_distance(query: Sequence[float], candidate: Sequence[float]) -> float:
    """Compute `1 - cosine_similarity`, matching sqlite-vec's own `vec_distance_cosine` metric.

    The Python fallback's own ranking metric (ADR-0031): identical results to the SQL function,
    only evaluated in this process instead of the SQLite extension.

    Args:
        query: The query vector.
        candidate: A candidate vector; must be the same length as `query`.

    Returns:
        0.0 for identical direction, 2.0 for exactly opposite; `_ZERO_NORM_DISTANCE` when either
        vector has zero norm (no direction to compare).
    """
    dot = sum(q * c for q, c in zip(query, candidate, strict=True))
    query_norm = math.sqrt(sum(q * q for q in query))
    candidate_norm = math.sqrt(sum(c * c for c in candidate))
    if query_norm == 0.0 or candidate_norm == 0.0:
        return _ZERO_NORM_DISTANCE
    return 1.0 - dot / (query_norm * candidate_norm)


def load_vector_extension(connection: sqlite3.Connection) -> bool:
    """Try to load sqlite-vec onto `connection`; report the Python fallback on any failure.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.

    Returns:
        True when `vec_distance_cosine` is now callable in SQL on `connection`; False when the
        Python fallback (`cosine_distance`) must be used instead.
    """
    try:
        # Imported here, not at module level: kept lazy so a host whose sqlite3 build cannot load
        # extensions at all never pays for the import. type: ignore[import-untyped] reason:
        # sqlite_vec ships no inline types or stub package, and its surface used here is one
        # function (`load`), not worth a typings/sqlite_vec/*.pyi stub for; a pyproject.toml
        # mypy-overrides entry (docker's own sdk_client.py uses one) is outside this dispatch's
        # file ownership, so the ignore is scoped to this one import line instead.
        import sqlite_vec  # type: ignore[import-untyped]

        # SAFETY: extension loading is enabled only long enough to load our own vetted sqlite_vec
        # package's compiled extension, then turned back off immediately; no other path or
        # arbitrary extension is ever loaded through this connection.
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.OperationalError) as exc:
        # ImportError: sqlite_vec is not importable in this environment (should not happen: it is
        # a normal dependency, but a host's install can still be broken). AttributeError:
        # enable_load_extension does not exist on this Python's sqlite3 build. OperationalError:
        # the extension file itself failed to load. All three degrade the same way (ADR-0031).
        log.warning("honey_store.vector_backend", backend=VECTOR_BACKEND_PYTHON, reason=str(exc))
        return False
    log.info("honey_store.vector_backend", backend=VECTOR_BACKEND_SQLITE_VEC)
    return True
