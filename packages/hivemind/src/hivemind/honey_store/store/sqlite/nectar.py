"""SQL and transactions for `honey_nectar`: intake's dedupe-and-raise-only-label merge.

Every write here runs inside one `hivemind.common.sqlite.transaction` block alongside its
`HoneyEvent` (codingrules section 12); every read decodes a row straight back into a `Nectar`.
`add_nectar_transaction` is the one function with real decision logic: it dedupes by
`draft.source_key` first, then by `content_sha256` on the draft's own side of the Night Veil
boundary, builds its events from the outcome, and on a duplicate only ever raises the stored
label, never lowers it (`hivemind.honey_store.clearance.raise_label`'s own rule, re-applied here
directly since this module already holds both rows' clearances in hand). A duplicate found by
content rather than by `source_key`, whose provenance differs from the stored row's own, also
records an extra source (`hivemind.honey_store.store.sqlite.sources`, ADR-0033) in the same
transaction, so the second sender's own provenance survives the merge. The same merge keeps the
higher of each of the two labelling facts intake records (`declared_clearance`,
`floor_clearance`, ADR-0034), so a later depositor who declared more can only make the Nectar less
eligible for a judge-reviewed lowering, never more.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.cell` (HoneyClearance, CombShieldLevel), `hivemind.common.sqlite`
    (transaction), `hivemind.honey_store.clearance` (raise_label, for the merged labelling facts),
    `hivemind.honey_store.errors` (NectarNotFoundError), `hivemind.honey_store.
    models` (Nectar, NectarDraft, NectarOrigin, NectarState), `hivemind.honey_store.store.protocol`
    (NectarAdded, NectarEvents), `.sources` (insert_source_if_new), `hivemind.pheromone`
    (insert_event) and `waggle` only.

Key invariants:
    - `add_nectar_transaction` writes its row (or its merge, and any extra source) and its events
      inside one transaction (codingrules section 12); a failing write leaves neither behind.
    - An ordinary deposit never dedupes onto an EPHEMERAL row, nor an ephemeral one onto another
      Cell's or an ordinary row: a teardown purge can never take an ordinary deposit with it.
    - A duplicate deposit's label is only ever raised, never lowered, and only the Honey rows
      already below the new rank are touched (`_raise_honey_for_nectar`'s own `WHERE
      clearance_rank < ?`).
    - A fresh row's `state` is `EPHEMERAL` when `draft.ephemeral_cell_id` is set, `RECEIVED`
      otherwise (ADR-0031: a Night Veil Cell's own side channel is never ripened).
    - An extra source is recorded only for a duplicate matched by content, and only when its
      (source_key, task, Cell, bee) differs from the stored row's own; `sources.
      insert_source_if_new`'s own unique indexes make a retried or already-known one a no-op.
    - A merged labelling fact is the higher of the two, and unknown (NULL) when either side's is
      unknown: a row from before ADR-0034, or a draft built outside intake, never gains a fact.

See Also:
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.store.sqlite.sources for insert_source_if_new, this module's own callee.
    - hivemind.honey_store.clearance for raise_label, the rule this module re-applies inline.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the dedupe-and-merge rule.
    - docs/adr/0033-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for the
      extra-source rule this module adds to it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.honey_store.clearance import raise_label
from hivemind.honey_store.errors import NectarNotFoundError
from hivemind.honey_store.models import Nectar, NectarDraft, NectarOrigin, NectarState
from hivemind.honey_store.store.protocol import NectarAdded, NectarEvents
from hivemind.honey_store.store.sqlite import sources as sources_sql
from hivemind.pheromone import HoneyEvent, insert_event
from waggle.clock import Clock
from waggle.ids import CellId, EventId, NectarId, TaskId, new_nectar_id
from waggle.messages.honey import NectarKind

# No __all__: every name here is an internal collaborator of
# hivemind.honey_store.store.sqlite.store, imported by that sibling module only (codingrules
# 5.4's forbidden-underscore-import contract polices cross-subsystem boundaries, not sibling
# modules in the same package; hivemind.memory.store.sqlite.records follows the same convention).

_INSERT_SQL = """
INSERT INTO honey_nectar (
    id, sha256, kind, origin, media_type, title, content, size_bytes, task_id, cell_id, bee,
    observed_at, received_at, clearance, clearance_rank, origin_tier, scope, state,
    ripen_attempts, tainted, source_key, event_id, ephemeral_cell_id, declared_clearance,
    declared_clearance_rank, floor_clearance, floor_clearance_rank
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_BY_ID_SQL = "SELECT * FROM honey_nectar WHERE id = ?"
_SELECT_CONTENT_SQL = "SELECT content FROM honey_nectar WHERE id = ?"
_SELECT_BY_SOURCE_KEY_SQL = "SELECT * FROM honey_nectar WHERE source_key = ?"
# `IS ?` rather than `= ?`: it matches NULL to NULL, so an ordinary draft (no ephemeral Cell)
# only ever finds ordinary rows, and an ephemeral one only its own Cell's ephemeral rows.
_SELECT_BY_SHA256_SQL = "SELECT * FROM honey_nectar WHERE sha256 = ? AND ephemeral_cell_id IS ?"
_SELECT_PENDING_SQL = "SELECT * FROM honey_nectar WHERE state = ? ORDER BY received_at, id LIMIT ?"
_SELECT_HAS_SOURCE_SQL = "SELECT 1 FROM honey_nectar WHERE source_key = ?"
_UPDATE_CLEARANCE_SQL = "UPDATE honey_nectar SET clearance = ?, clearance_rank = ? WHERE id = ?"
_UPDATE_FACTS_SQL = (
    "UPDATE honey_nectar SET declared_clearance = ?, declared_clearance_rank = ?, "
    "floor_clearance = ?, floor_clearance_rank = ? WHERE id = ?"
)
_UPDATE_ATTEMPTS_SQL = "UPDATE honey_nectar SET ripen_attempts = ?, state = ? WHERE id = ?"
_DELETE_EPHEMERAL_SQL = "DELETE FROM honey_nectar WHERE ephemeral_cell_id = ? AND state = ?"
# Only rows still below the new rank are raised, so a Honey row a ripener already labelled higher
# than its Nectar is never touched (raise-only, never a lowering in disguise).
_RAISE_HONEY_FOR_NECTAR_SQL = (
    "UPDATE honey SET clearance = ?, clearance_rank = ? WHERE nectar_id = ? AND clearance_rank < ?"
)


def add_nectar_transaction(
    connection: sqlite3.Connection,
    draft: NectarDraft,
    content_sha256: str,
    events: NectarEvents,
    clock: Clock,
) -> NectarAdded:
    """Insert `draft` as a new row, or merge it onto a duplicate, then its events; one txn."""
    with transaction(connection):
        # Read once: a new row's received_at, and a duplicate's own extra-source received_at,
        # are the same "now" either way.
        received_at = clock.now()
        existing_row, by_key = _select_duplicate_row(connection, draft, content_sha256)
        if existing_row is not None:
            result = _merge_duplicate(connection, existing_row, draft, by_key, received_at)
        else:
            nectar = _draft_to_nectar(draft, content_sha256, received_at, clock)
            connection.execute(_INSERT_SQL, _nectar_insert_params(nectar, draft.content))
            result = NectarAdded(nectar=nectar, is_new=True, raised_from=None)
        # The caller's events depend on the outcome (a new id, a duplicate, a raised label, or
        # nothing for a Night Veil deposit), so they are built only now, inside the same txn.
        for event in events(result):
            insert_event(connection, event)
        return result


def select_nectar(connection: sqlite3.Connection, nectar_id: NectarId) -> Nectar:
    """Return the stored Nectar row, or raise NectarNotFoundError."""
    row = connection.execute(_SELECT_BY_ID_SQL, (nectar_id,)).fetchone()
    if row is None:
        raise NectarNotFoundError(nectar_id)
    return _row_to_nectar(row)


def select_nectar_content(connection: sqlite3.Connection, nectar_id: NectarId) -> bytes:
    """Return one Nectar row's raw content bytes, or raise NectarNotFoundError."""
    row = connection.execute(_SELECT_CONTENT_SQL, (nectar_id,)).fetchone()
    if row is None:
        raise NectarNotFoundError(nectar_id)
    return bytes(row["content"])


def select_pending_nectar(connection: sqlite3.Connection, limit: int) -> tuple[Nectar, ...]:
    """Return RECEIVED rows, oldest first, at most `limit`."""
    rows = connection.execute(_SELECT_PENDING_SQL, (NectarState.RECEIVED.value, limit)).fetchall()
    return tuple(_row_to_nectar(row) for row in rows)


def select_has_source(connection: sqlite3.Connection, source_key: str) -> bool:
    """Return whether a row with this `source_key` already exists."""
    return connection.execute(_SELECT_HAS_SOURCE_SQL, (source_key,)).fetchone() is not None


def mark_nectar_failed_transaction(
    connection: sqlite3.Connection, nectar_id: NectarId, discard: bool, event: HoneyEvent
) -> Nectar:
    """Increment `ripen_attempts`, DISCARD if asked, then insert `event`; one transaction."""
    with transaction(connection):
        row = connection.execute(_SELECT_BY_ID_SQL, (nectar_id,)).fetchone()
        if row is None:
            raise NectarNotFoundError(nectar_id)
        nectar = _row_to_nectar(row)
        new_state = NectarState.DISCARDED if discard else nectar.state
        updated = nectar.model_copy(
            update={"ripen_attempts": nectar.ripen_attempts + 1, "state": new_state}
        )
        connection.execute(
            _UPDATE_ATTEMPTS_SQL, (updated.ripen_attempts, updated.state.value, nectar_id)
        )
        insert_event(connection, event)
        return updated


def purge_ephemeral_transaction(connection: sqlite3.Connection, cell_id: CellId) -> int:
    """Delete every EPHEMERAL row for `cell_id`, in one transaction; return the count removed."""
    with transaction(connection):
        cursor = connection.execute(_DELETE_EPHEMERAL_SQL, (cell_id, NectarState.EPHEMERAL.value))
        return cursor.rowcount


def _select_duplicate_row(
    connection: sqlite3.Connection, draft: NectarDraft, sha256: str
) -> tuple[sqlite3.Row | None, bool]:
    """Find an existing row by `source_key` first, then by `sha256` (ADR-0031's dedupe order).

    The sha256 match stays on `draft`'s own side of the Night Veil boundary: an ordinary deposit
    merged onto an ephemeral row would be deleted by that Cell's teardown purge, and an ephemeral
    deposit merged onto an ordinary row would let a Night Veil Cell change persistent state.

    Returns:
        `(row, True)` when found by `source_key` (the same source delivered again, ADR-0033);
        `(row, False)` when found by `sha256` instead; `(None, False)` when neither matches.
    """
    if draft.source_key is not None:
        # sqlite3.Cursor.fetchone() is typed Any by typeshed; the explicit annotation is what lets
        # mypy --strict confirm this really is a Row | None before it is returned.
        by_source_key: sqlite3.Row | None = connection.execute(
            _SELECT_BY_SOURCE_KEY_SQL, (draft.source_key,)
        ).fetchone()
        if by_source_key is not None:
            return by_source_key, True
    by_sha256: sqlite3.Row | None = connection.execute(
        _SELECT_BY_SHA256_SQL, (sha256, draft.ephemeral_cell_id)
    ).fetchone()
    return by_sha256, False


def _merge_duplicate(
    connection: sqlite3.Connection,
    existing_row: sqlite3.Row,
    draft: NectarDraft,
    matched_by_source_key: bool,
    received_at: datetime,
) -> NectarAdded:
    """Raise the stored row's (and its Honey rows') label when `draft` outranks it; else no-op.

    A content match (not the same `source_key`) whose provenance differs from the stored row's
    own also records an extra source (ADR-0033), whichever way the label moves.
    """
    existing = _row_to_nectar(existing_row)
    if not matched_by_source_key and _provenance_differs(existing, draft):
        sources_sql.insert_source_if_new(connection, existing.id, draft, received_at)
    merged = _merge_facts(connection, existing, draft)
    if draft.clearance.rank <= existing.clearance.rank:
        # Not a raise: the stored label already covers the new deposit's own.
        return NectarAdded(nectar=merged, is_new=False, raised_from=None)
    raised = merged.model_copy(update={"clearance": draft.clearance})
    connection.execute(
        _UPDATE_CLEARANCE_SQL, (draft.clearance.value, draft.clearance.rank, existing.id)
    )
    connection.execute(
        _RAISE_HONEY_FOR_NECTAR_SQL,
        (draft.clearance.value, draft.clearance.rank, existing.id, draft.clearance.rank),
    )
    return NectarAdded(nectar=raised, is_new=False, raised_from=existing.clearance)


def _merge_facts(connection: sqlite3.Connection, existing: Nectar, draft: NectarDraft) -> Nectar:
    """Keep the higher of each labelling fact on the stored row (ADR-0034); return it as merged.

    Written only when a fact actually changes, inside the caller's merge transaction.
    """
    declared = _higher_fact(existing.declared_clearance, draft.declared_clearance)
    floor = _higher_fact(existing.floor_clearance, draft.floor_clearance)
    # Nothing moved: the stored facts already cover the new deposit's own.
    if (declared, floor) == (existing.declared_clearance, existing.floor_clearance):
        return existing
    connection.execute(
        _UPDATE_FACTS_SQL, (*_label_pair(declared), *_label_pair(floor), existing.id)
    )
    return existing.model_copy(update={"declared_clearance": declared, "floor_clearance": floor})


def _higher_fact(
    stored: HoneyClearance | None, incoming: HoneyClearance | None
) -> HoneyClearance | None:
    """Return the higher of two labelling facts, or None when either is unknown.

    An unknown fact on either side leaves the merged fact unknown: guessing it from the other side
    alone could only ever make a Nectar look more lowerable than it is.
    """
    if stored is None or incoming is None:
        return None
    return raise_label(stored, incoming)


def _label_pair(label: HoneyClearance | None) -> tuple[str | None, int | None]:
    """Return a nullable label's (value, rank) column pair, both None when the label is."""
    return (None, None) if label is None else (label.value, label.rank)


def _provenance_differs(existing: Nectar, draft: NectarDraft) -> bool:
    """Return whether `draft`'s (source_key, task, Cell, bee) differs from the stored row's own.

    ADR-0033: a duplicate whose whole provenance already equals the first depositor's own would
    otherwise record a source that only repeats what `honey_nectar` already says.
    """
    stored = (existing.source_key, existing.task_id, existing.cell_id, existing.bee)
    incoming = (draft.source_key, draft.task_id, draft.cell_id, draft.bee)
    return stored != incoming


def _draft_to_nectar(
    draft: NectarDraft, sha256: str, received_at: datetime, clock: Clock
) -> Nectar:
    """Build a fresh, stored-shaped Nectar from an intake draft."""
    is_ephemeral = draft.ephemeral_cell_id is not None
    return Nectar(
        id=new_nectar_id(clock),
        sha256=sha256,
        size_bytes=len(draft.content),
        kind=draft.kind,
        origin=draft.origin,
        media_type=draft.media_type,
        title=draft.title,
        task_id=draft.task_id,
        cell_id=draft.cell_id,
        bee=draft.bee,
        observed_at=draft.observed_at,
        received_at=received_at,
        clearance=draft.clearance,
        origin_tier=draft.origin_tier,
        scope=draft.scope,
        source_key=draft.source_key,
        event_id=draft.event_id,
        ephemeral_cell_id=draft.ephemeral_cell_id,
        # A Night Veil Cell's own side channel is never ripened (ADR-0031); every other fresh
        # deposit starts RECEIVED, the ripening pipeline's own starting state.
        state=NectarState.EPHEMERAL if is_ephemeral else NectarState.RECEIVED,
        ripen_attempts=0,
        tainted=False,
        declared_clearance=draft.declared_clearance,
        floor_clearance=draft.floor_clearance,
    )


def _nectar_insert_params(nectar: Nectar, content: bytes) -> tuple[object, ...]:
    """Build the 27-column parameter tuple `_INSERT_SQL` binds, in its declared column order."""
    return (
        nectar.id,
        nectar.sha256,
        nectar.kind.value,
        nectar.origin.value,
        nectar.media_type,
        nectar.title,
        content,
        nectar.size_bytes,
        nectar.task_id,
        nectar.cell_id,
        nectar.bee,
        nectar.observed_at.isoformat(),
        nectar.received_at.isoformat(),
        nectar.clearance.value,
        nectar.clearance.rank,
        nectar.origin_tier.value,
        nectar.scope,
        nectar.state.value,
        nectar.ripen_attempts,
        int(nectar.tainted),
        nectar.source_key,
        nectar.event_id,
        nectar.ephemeral_cell_id,
        *_label_pair(nectar.declared_clearance),
        *_label_pair(nectar.floor_clearance),
    )


def _row_to_nectar(row: sqlite3.Row) -> Nectar:
    """Decode one `honey_nectar` row back into a Nectar."""
    return Nectar(
        id=row["id"],
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        kind=NectarKind(row["kind"]),
        origin=NectarOrigin(row["origin"]),
        media_type=row["media_type"],
        title=row["title"],
        task_id=_optional(TaskId, row["task_id"]),
        cell_id=row["cell_id"],
        bee=row["bee"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        clearance=HoneyClearance(row["clearance"]),
        origin_tier=CombShieldLevel(row["origin_tier"]),
        scope=row["scope"],
        source_key=row["source_key"],
        event_id=_optional(EventId, row["event_id"]),
        ephemeral_cell_id=_optional(CellId, row["ephemeral_cell_id"]),
        state=NectarState(row["state"]),
        ripen_attempts=row["ripen_attempts"],
        tainted=bool(row["tainted"]),
        declared_clearance=_optional(HoneyClearance, row["declared_clearance"]),
        floor_clearance=_optional(HoneyClearance, row["floor_clearance"]),
        ripener_clearance=_optional(HoneyClearance, row["ripener_clearance"]),
    )


def _optional[IdT](wrapper: Callable[[str], IdT], value: str | None) -> IdT | None:
    """Apply `wrapper` to `value` unless it is None; every nullable id column reads through this.

    `wrapper` is typed as a plain callable, not `type[IdT]`: a `waggle.ids` `NewType` (`TaskId`,
    `EventId`, `CellId`, ...) is a callable that returns its own alias, never an actual `type`
    object, so `type[IdT]` fails to unify against one and silently falls back to `object` (mypy
    then reports "too many arguments for object" at every call site).
    """
    return None if value is None else wrapper(value)
