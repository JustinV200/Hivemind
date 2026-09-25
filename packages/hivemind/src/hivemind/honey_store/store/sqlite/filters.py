"""Translate a ReadFilter into SQL, and re-check it in Python for count_withheld's own purpose.

Every `HoneyStore` read that takes a `ReadFilter` (`list_honey`, `search_text`, `search_vectors`,
`count_withheld`) applies the same five clauses, in the same order, before anything is ranked or
limited (ADR-0035): a reader's scope globs, an optional exact-scope narrowing, the clearance
ceiling, and live-only (not tainted, not retired). Building that `WHERE` fragment in one place
keeps `hivemind.honey_store.store.sqlite.honey` and `.search` from drifting apart on what
"readable" means; `passes_filter` is the same rule re-evaluated in Python, for `count_withheld`,
which must answer "of these already-selected rows, how many would the filter have excluded" rather
than run a second SQL query that could select a different top-`limit` set once filtering changes
the ranking pool.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by `hivemind.honey_store.store.sqlite.honey` (`list_honey`) and `.search` (`search_text`,
    `search_vectors`, `count_withheld`). Calls into `hivemind.honey_store.models` (ReadFilter) and
    `fnmatch` only.

Key invariants:
    - `read_filter_clauses`'s SQL and `passes_filter`'s Python re-implement the same rule: SQLite's
      `GLOB` and Python's `fnmatch.fnmatchcase` both treat `*` as "any run of characters, `/`
      included", the only wildcard the Honey Store's own scope globs ever use, so the two never
      disagree for a pattern this module actually receives
      (`tests/unit/honey_store/store/sqlite/test_filters.py` checks it).
    - An empty `filter.readable` matches nothing (`"0"`, a literal false clause): a caller with no
      `honey:read` capability at all sees zero rows, never every row.

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for "Filtering is policy, not ranking."
    - hivemind.honey_store.models.search for ReadFilter itself.
    - hivemind.honey_store.store.sqlite.search for count_withheld, passes_filter's one caller.
"""

from __future__ import annotations

import fnmatch

from hivemind.honey_store.models import ReadFilter

__all__ = ["passes_filter", "read_filter_clauses"]


def read_filter_clauses(
    filter_: ReadFilter, *, table_alias: str = ""
) -> tuple[list[str], list[object]]:
    """Translate `filter_` into `WHERE` fragments and their `?` parameters (ADR-0035).

    Always applies, in this order: an OR'd `scope GLOB ?` per readable glob, `scope IN (...)`
    when `requested` is non-empty, `clearance_rank <= ?`, `tainted = 0`, `retired_at IS NULL`.

    Args:
        filter_: The reader's scope, clearance and requested-scopes filter.
        table_alias: When set, every column is qualified `"<table_alias>.<column>"`; used when the
            caller's query joins more than one table (e.g. `honey` and `honey_vectors`).

    Returns:
        `(clauses, params)`: AND every clause in `clauses` together and bind `params` in order.
    """
    prefix = f"{table_alias}." if table_alias else ""
    clauses: list[str] = []
    params: list[object] = []
    if filter_.readable:
        globs = " OR ".join(f"{prefix}scope GLOB ?" for _ in filter_.readable)
        clauses.append(f"({globs})")
        params.extend(filter_.readable)
    else:
        clauses.append("0")  # No readable globs at all: fail closed, nothing matches.
    if filter_.requested:
        placeholders = ",".join("?" for _ in filter_.requested)
        clauses.append(f"{prefix}scope IN ({placeholders})")
        params.extend(filter_.requested)
    clauses.append(f"{prefix}clearance_rank <= ?")
    params.append(filter_.max_clearance.rank)
    clauses.append(f"{prefix}tainted = 0")
    clauses.append(f"{prefix}retired_at IS NULL")
    return clauses, params


def passes_filter(scope: str, clearance_rank: int, filter_: ReadFilter) -> bool:
    """Re-check one already-selected (live) row against `filter_`'s scope and clearance clauses.

    `count_withheld`'s own tool: it first selects the top `limit` live text matches ignoring
    `filter_` entirely, then asks this function which of those *specific* rows `filter_` would
    have excluded, rather than compare two independently-limited queries whose top-`limit` sets
    could differ once filtering changes the ranking pool.

    Args:
        scope: The row's own scope.
        clearance_rank: The row's own `HoneyClearance.rank`.
        filter_: The reader's scope, clearance and requested-scopes filter.

    Returns:
        True when `filter_` would have let this row through (its readable globs and requested
        scopes and clearance ceiling all pass); the row is already known to be live.
    """
    if not any(fnmatch.fnmatchcase(scope, glob) for glob in filter_.readable):
        return False
    if filter_.requested and scope not in filter_.requested:
        return False
    return clearance_rank <= filter_.max_clearance.rank
