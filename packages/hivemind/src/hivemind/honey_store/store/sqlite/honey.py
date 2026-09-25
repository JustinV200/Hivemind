"""SQL and transactions for `honey`: ripening, reads, clearance changes and retirement.

Every write here runs inside one `hivemind.common.sqlite.transaction` block alongside its
`HoneyEvent` (codingrules section 12); every read decodes a row straight back into a `Honey`.
`ripen_transaction` is idempotent on `(nectar_id, part, chunk_index)` (the schema's own UNIQUE
constraint): a draft matching an already-stored row is returned unchanged rather than re-inserted,
so calling `ripen` twice with the same drafts never duplicates a row. Provenance (`kind`, `origin`,
`scope`, `origin_tier`, `task_id`, `cell_id`, `bee`, `observed_at`) is always copied from the
Nectar row being ripened, never taken from a `HoneyDraft`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.cell` (HoneyClearance, CombShieldLevel), `hivemind.common.sqlite`
    (transaction), `hivemind.honey_store.errors` (HoneyNotFoundError, NectarNotFoundError),
    `hivemind.honey_store.models` (Honey, HoneyDraft, HoneyPart, NectarOrigin, ReadFilter),
    `hivemind.honey_store.store.sqlite.filters` (read_filter_clauses), `hivemind.pheromone`
    (insert_event) and `waggle` only.

Key invariants:
    - `ripen_transaction` writes every new row and its event inside one transaction; a failing
      write leaves neither behind.
    - `raise_clearance`/`lower_clearance` both persist whatever `to` the caller already decided:
      neither re-derives `hivemind.honey_store.clearance`'s own rule (the store trusts its caller).
    - `select_honey_list`'s `WHERE` always applies `read_filter_clauses` before `ORDER BY`/`LIMIT`
      (ADR-0035: "Filtering is policy, not ranking").

See Also:
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.store.sqlite.nectar for the sibling table ripen() reads provenance from.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the ripening and filtering rules.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.honey_store.clearance import raise_label
from hivemind.honey_store.errors import (
    HoneyNotFoundError,
    NectarNotFoundError,
    NectarNotRipenableError,
)
from hivemind.honey_store.models import Honey, HoneyDraft, HoneyPart, NectarOrigin, ReadFilter
from hivemind.honey_store.models.nectar import NectarState
from hivemind.honey_store.store.sqlite.filters import read_filter_clauses
from hivemind.honey_store.store.sqlite.nectar import _optional
from hivemind.pheromone import HoneyEvent, insert_event
from waggle.clock import Clock
from waggle.ids import HoneyId, NectarId, TaskId, new_honey_id
from waggle.messages.honey import NectarKind

# No __all__: internal collaborator of hivemind.honey_store.store.sqlite.store only (see
# hivemind.honey_store.store.sqlite.nectar's identical note).

_INSERT_SQL = """
INSERT INTO honey (
    id, nectar_id, part, chunk_index, title, summary, body, body_sha256, clearance,
    clearance_rank, ripener_model, kind, origin, scope, origin_tier, task_id, cell_id, bee,
    observed_at, created_at, tainted, retired_at, embedding_model
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_NECTAR_FOR_RIPEN_SQL = (
    "SELECT kind, origin, scope, origin_tier, task_id, cell_id, bee, observed_at, state, "
    "clearance FROM honey_nectar WHERE id = ?"
)
_SELECT_EXISTING_PART_SQL = (
    "SELECT * FROM honey WHERE nectar_id = ? AND part = ? AND chunk_index = ?"
)
_MARK_RIPENED_SQL = "UPDATE honey_nectar SET state = ? WHERE id = ?"
_SELECT_BY_ID_SQL = "SELECT * FROM honey WHERE id = ?"
_SELECT_FOR_NECTAR_SQL = "SELECT * FROM honey WHERE nectar_id = ?"
_SELECT_LIST_SQL = "SELECT * FROM honey"
_ORDER_LIST_BY = " ORDER BY created_at DESC, honey_seq DESC LIMIT ? OFFSET ?"
_UPDATE_CLEARANCE_SQL = "UPDATE honey SET clearance = ?, clearance_rank = ? WHERE id = ?"
_UPDATE_RETIRE_SQL = "UPDATE honey SET retired_at = ? WHERE id = ?"
_SELECT_SCOPE_COUNTS_SQL = "SELECT scope, COUNT(*) AS n FROM honey"


@dataclass(frozen=True, slots=True)
class _RipenContext:
    """What every part of one `ripen()` call shares, bundled per codingrules 5.1's param limit."""

    nectar_id: NectarId
    nectar_row: sqlite3.Row
    created_at: datetime
    clock: Clock


def ripen_transaction(
    connection: sqlite3.Connection,
    nectar_id: NectarId,
    drafts: tuple[HoneyDraft, ...],
    event: HoneyEvent,
    clock: Clock,
) -> tuple[Honey, ...]:
    """Insert `drafts` as Honey rows (idempotent per part), mark the Nectar RIPENED; one txn."""
    with transaction(connection):
        nectar_row = connection.execute(_SELECT_NECTAR_FOR_RIPEN_SQL, (nectar_id,)).fetchone()
        if nectar_row is None:
            raise NectarNotFoundError(nectar_id)
        if nectar_row["state"] == NectarState.EPHEMERAL.value:
            # Defence in depth behind pending_nectar (RECEIVED rows only): a Night Veil side
            # channel never becomes Honey, and must stay EPHEMERAL for the teardown purge.
            raise NectarNotRipenableError(nectar_id, nectar_row["state"])
        # Everything but `draft` itself is fixed for the whole call; bundled per codingrules 5.1
        # (a function argument group past four values becomes a dataclass) so the per-draft helper
        # below stays at three parameters instead of six.
        ctx = _RipenContext(
            nectar_id=nectar_id, nectar_row=nectar_row, created_at=event.at, clock=clock
        )
        results = tuple(_ripen_one_part(connection, draft, ctx) for draft in drafts)
        connection.execute(_MARK_RIPENED_SQL, (NectarState.RIPENED.value, nectar_id))
        insert_event(connection, event)
        return results


def select_honey(connection: sqlite3.Connection, honey_id: HoneyId) -> Honey:
    """Return the stored Honey row, or raise HoneyNotFoundError."""
    row = connection.execute(_SELECT_BY_ID_SQL, (honey_id,)).fetchone()
    if row is None:
        raise HoneyNotFoundError(honey_id)
    return _row_to_honey(row)


def select_honey_for_nectar(
    connection: sqlite3.Connection, nectar_id: NectarId
) -> tuple[Honey, ...]:
    """Return every Honey row ripened from `nectar_id`."""
    rows = connection.execute(_SELECT_FOR_NECTAR_SQL, (nectar_id,)).fetchall()
    return tuple(_row_to_honey(row) for row in rows)


def select_honey_list(
    connection: sqlite3.Connection,
    filter_: ReadFilter,
    scope_prefix: str | None,
    limit: int,
    offset: int,
) -> tuple[Honey, ...]:
    """Return live rows within `filter_`, optionally narrowed to `scope_prefix`, newest first."""
    clauses, params = read_filter_clauses(filter_)
    if scope_prefix is not None:
        # A GLOB pattern with a trailing "*" matches an exact scope too (zero extra characters),
        # so one clause covers both "a prefix like 'cell:'" and "an exact scope" (fixed-length
        # ULIDs mean no scope is ever a strict prefix of a different one).
        clauses.append("scope GLOB ?")
        params.append(f"{scope_prefix}*")
    sql = f"{_SELECT_LIST_SQL} WHERE {' AND '.join(clauses)}{_ORDER_LIST_BY}"
    rows = connection.execute(sql, [*params, limit, offset]).fetchall()
    return tuple(_row_to_honey(row) for row in rows)


def select_scope_counts(
    connection: sqlite3.Connection, scope_kind: str, filter_: ReadFilter
) -> dict[str, int]:
    """Count live rows per scope of `scope_kind` within `filter_`.

    ADR-0037: one `GROUP BY`, no scan bound, so `/cells`, `/bees` and `/tasks` are complete at
    any store size.
    """
    clauses, params = read_filter_clauses(filter_)
    # Scoped to one kind's own scopes: "cell:*" matches "cell:<id>" but never "hive" or "bee:<id>".
    clauses.append("scope GLOB ?")
    params.append(f"{scope_kind}:*")
    sql = f"{_SELECT_SCOPE_COUNTS_SQL} WHERE {' AND '.join(clauses)} GROUP BY scope"
    rows = connection.execute(sql, params).fetchall()
    return {row["scope"]: row["n"] for row in rows}


def raise_clearance_transaction(
    connection: sqlite3.Connection, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
) -> Honey:
    """Raise one row's clearance and insert `event`; one transaction (caller already raised it)."""
    return _set_clearance_transaction(connection, honey_id, to, event)


def lower_clearance_transaction(
    connection: sqlite3.Connection, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
) -> Honey:
    """Lower one row's clearance and insert `event`; one transaction (caller already checked it)."""
    return _set_clearance_transaction(connection, honey_id, to, event)


def retire_transaction(
    connection: sqlite3.Connection, honey_id: HoneyId, event: HoneyEvent
) -> Honey:
    """Set one row's `retired_at` and insert `event`; one transaction."""
    with transaction(connection):
        row = connection.execute(_SELECT_BY_ID_SQL, (honey_id,)).fetchone()
        if row is None:
            raise HoneyNotFoundError(honey_id)
        connection.execute(_UPDATE_RETIRE_SQL, (event.at.isoformat(), honey_id))
        insert_event(connection, event)
        return _row_to_honey(row).model_copy(update={"retired_at": event.at})


def _set_clearance_transaction(
    connection: sqlite3.Connection, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
) -> Honey:
    """Shared body of raise_clearance_transaction/lower_clearance_transaction: same mechanics."""
    with transaction(connection):
        row = connection.execute(_SELECT_BY_ID_SQL, (honey_id,)).fetchone()
        if row is None:
            raise HoneyNotFoundError(honey_id)
        connection.execute(_UPDATE_CLEARANCE_SQL, (to.value, to.rank, honey_id))
        insert_event(connection, event)
        return _row_to_honey(row).model_copy(update={"clearance": to})


def _ripen_one_part(connection: sqlite3.Connection, draft: HoneyDraft, ctx: _RipenContext) -> Honey:
    """Return the existing row for `draft`'s part, or insert and return a fresh one."""
    existing = connection.execute(
        _SELECT_EXISTING_PART_SQL, (ctx.nectar_id, draft.part.value, draft.chunk_index)
    ).fetchone()
    if existing is not None:
        return _row_to_honey(existing)  # Idempotent: already ripened, never duplicated.
    honey = _draft_to_honey(draft, ctx.nectar_row, ctx.nectar_id, ctx.created_at, ctx.clock)
    connection.execute(_INSERT_SQL, _honey_insert_params(honey))
    return honey


def _draft_to_honey(
    draft: HoneyDraft,
    nectar_row: sqlite3.Row,
    nectar_id: NectarId,
    created_at: datetime,
    clock: Clock,
) -> Honey:
    """Build a fresh, stored-shaped Honey from a Ripener draft plus its Nectar's provenance."""
    return Honey(
        id=new_honey_id(clock),
        nectar_id=nectar_id,
        part=draft.part,
        chunk_index=draft.chunk_index,
        title=draft.title,
        summary=draft.summary,
        body=draft.body,
        body_sha256=hashlib.sha256(draft.body.encode("utf-8")).hexdigest(),
        # Never below the Nectar's label as it stands in this transaction: a more sensitive
        # duplicate may have raised it while the Ripener was still summarising (raise-only).
        clearance=raise_label(draft.clearance, HoneyClearance(nectar_row["clearance"])),
        ripener_model=draft.ripener_model,
        kind=NectarKind(nectar_row["kind"]),
        origin=NectarOrigin(nectar_row["origin"]),
        scope=nectar_row["scope"],
        origin_tier=CombShieldLevel(nectar_row["origin_tier"]),
        task_id=_optional(TaskId, nectar_row["task_id"]),
        cell_id=nectar_row["cell_id"],
        bee=nectar_row["bee"],
        observed_at=datetime.fromisoformat(nectar_row["observed_at"]),
        created_at=created_at,
        tainted=False,
        retired_at=None,
        embedding_model=None,
    )


def _honey_insert_params(honey: Honey) -> tuple[object, ...]:
    """Build the 23-column parameter tuple `_INSERT_SQL` binds, in its declared column order."""
    return (
        honey.id,
        honey.nectar_id,
        honey.part.value,
        honey.chunk_index,
        honey.title,
        honey.summary,
        honey.body,
        honey.body_sha256,
        honey.clearance.value,
        honey.clearance.rank,
        honey.ripener_model,
        honey.kind.value,
        honey.origin.value,
        honey.scope,
        honey.origin_tier.value,
        honey.task_id,
        honey.cell_id,
        honey.bee,
        honey.observed_at.isoformat(),
        honey.created_at.isoformat(),
        int(honey.tainted),
        honey.retired_at.isoformat() if honey.retired_at is not None else None,
        honey.embedding_model,
    )


def _row_to_honey(row: sqlite3.Row) -> Honey:
    """Decode one `honey` row back into a Honey."""
    return Honey(
        id=row["id"],
        nectar_id=row["nectar_id"],
        part=HoneyPart(row["part"]),
        chunk_index=row["chunk_index"],
        title=row["title"],
        summary=row["summary"],
        body=row["body"],
        body_sha256=row["body_sha256"],
        clearance=HoneyClearance(row["clearance"]),
        ripener_model=row["ripener_model"],
        kind=NectarKind(row["kind"]),
        origin=NectarOrigin(row["origin"]),
        scope=row["scope"],
        origin_tier=CombShieldLevel(row["origin_tier"]),
        task_id=_optional(TaskId, row["task_id"]),
        cell_id=row["cell_id"],
        bee=row["bee"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        tainted=bool(row["tainted"]),
        retired_at=datetime.fromisoformat(row["retired_at"]) if row["retired_at"] else None,
        embedding_model=row["embedding_model"],
    )
