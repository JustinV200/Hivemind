"""SQL and transactions for aggregate stats, watermarks and the human's "propose a note" queue.

`select_stats` runs a handful of `GROUP BY` counts over `honey_nectar`, `honey` and
`honey_vectors` and reports the vector backend the caller already chose
(`hivemind.honey_store.store.sqlite.vec.load_vector_extension`'s own result) -- never a query's
content, id or text (codingrules section 12). `honey_watermarks` is a plain name -> value table for
whatever cursor a later dispatch's pipeline needs between passes. `honey_proposals` is roadmap
7.10's human "propose a note" queue; `mark_proposal_drained` takes no `HoneyEvent` (its own
protocol signature has none, since draining a proposal is bookkeeping for the `add_nectar` call
that already carried its own event, not a separately audit-worthy transition), so its `drained_at`
comes from the store's own injected clock instead.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    only by `hivemind.honey_store.store.sqlite.store.SqliteHoneyStore`, on its `ConnectionThread`.
    Calls into `hivemind.cell` (HoneyClearance), `hivemind.common.sqlite` (transaction),
    `hivemind.honey_store.errors` (HoneyStoreError), `hivemind.honey_store.models` (HoneyPart,
    HoneyStats, NectarState), `hivemind.honey_store.store.protocol` (HoneyProposal),
    `hivemind.pheromone` (insert_event) and `waggle` only.

Key invariants:
    - `select_stats` never reads a row's own text, only counts and labels.
    - `mark_proposal_drained_transaction` raises `HoneyStoreError` (nothing more specific is named
      for this in `hivemind.honey_store.errors`) when `proposal_id` names no stored row.

See Also:
    - hivemind.honey_store.store.sqlite.store for SqliteHoneyStore, the one caller.
    - hivemind.honey_store.models.search for HoneyStats, this module's own return shape.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the vector-backend reporting rule.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.honey_store.errors import HoneyStoreError
from hivemind.honey_store.models import HoneyPart, HoneyStats, NectarState
from hivemind.honey_store.store.protocol import HoneyProposal
from hivemind.pheromone import HoneyEvent, insert_event
from waggle.clock import Clock
from waggle.ids import NectarId
from waggle.ulid import RANDOMNESS_BYTES, encode_ulid

# No __all__: internal collaborator of hivemind.honey_store.store.sqlite.store only (see
# hivemind.honey_store.store.sqlite.nectar's identical note).

_PROPOSAL_ID_PREFIX = "honeynote_"  # No waggle.ids.IdKind for a proposal; minted the same way
# hivemind.memory.cell_wax.model.new_wax_id mints its own plain-string id.

_COUNT_NECTAR_BY_STATE_SQL = "SELECT state, COUNT(*) AS n FROM honey_nectar GROUP BY state"
_LIVE_HONEY_WHERE = "tainted = 0 AND retired_at IS NULL"
# _LIVE_HONEY_WHERE is a module constant, never caller input, so each f-string below carries no
# injection risk despite matching ruff's S608 pattern (the same reasoning hivemind.common.
# migrations documents for its own SCHEMA_TABLE f-strings).
_COUNT_HONEY_BY_PART_SQL = (
    f"SELECT part, COUNT(*) AS n FROM honey WHERE {_LIVE_HONEY_WHERE} GROUP BY part"  # noqa: S608
)
_COUNT_HONEY_TAINTED_SQL = "SELECT COUNT(*) AS n FROM honey WHERE tainted = 1"
_COUNT_HONEY_RETIRED_SQL = "SELECT COUNT(*) AS n FROM honey WHERE retired_at IS NOT NULL"
_COUNT_HONEY_BY_CLEARANCE_SQL = (
    f"SELECT clearance, COUNT(*) AS n FROM honey WHERE {_LIVE_HONEY_WHERE} "  # noqa: S608
    "GROUP BY clearance"
)
_SELECT_LIVE_SCOPES_SQL = f"SELECT scope FROM honey WHERE {_LIVE_HONEY_WHERE}"  # noqa: S608
_COUNT_VECTORS_BY_MODEL_SQL = "SELECT model, COUNT(*) AS n FROM honey_vectors GROUP BY model"

_SELECT_WATERMARK_SQL = "SELECT value FROM honey_watermarks WHERE name = ?"
_UPSERT_WATERMARK_SQL = (
    "INSERT INTO honey_watermarks (name, value) VALUES (?, ?) "
    "ON CONFLICT (name) DO UPDATE SET value = excluded.value"
)


@dataclass(frozen=True, slots=True)
class _ProposalDraft:
    """The three fields `add_proposal_transaction` writes, bundled per codingrules 5.1's limit."""

    scope: str
    title: str
    text: str


_INSERT_PROPOSAL_SQL = (
    "INSERT INTO honey_proposals (id, scope, title, text, created_at) VALUES (?, ?, ?, ?, ?)"
)
_SELECT_PENDING_PROPOSALS_SQL = (
    "SELECT * FROM honey_proposals WHERE drained_at IS NULL ORDER BY created_at, id LIMIT ?"
)
_SELECT_PROPOSAL_BY_ID_SQL = "SELECT 1 FROM honey_proposals WHERE id = ?"
_MARK_PROPOSAL_DRAINED_SQL = "UPDATE honey_proposals SET drained_at = ?, nectar_id = ? WHERE id = ?"


def select_stats(connection: sqlite3.Connection, vector_backend: str) -> HoneyStats:
    """Run every aggregate count and assemble a HoneyStats; counts and labels only."""
    nectar_by_state = {
        NectarState(row["state"]): row["n"]
        for row in connection.execute(_COUNT_NECTAR_BY_STATE_SQL).fetchall()
    }
    honey_by_part = {
        HoneyPart(row["part"]): row["n"]
        for row in connection.execute(_COUNT_HONEY_BY_PART_SQL).fetchall()
    }
    tainted = connection.execute(_COUNT_HONEY_TAINTED_SQL).fetchone()["n"]
    retired = connection.execute(_COUNT_HONEY_RETIRED_SQL).fetchone()["n"]
    by_clearance = {
        HoneyClearance(row["clearance"]): row["n"]
        for row in connection.execute(_COUNT_HONEY_BY_CLEARANCE_SQL).fetchall()
    }
    vectors_by_model = {
        row["model"]: row["n"] for row in connection.execute(_COUNT_VECTORS_BY_MODEL_SQL).fetchall()
    }
    return HoneyStats(
        nectar_by_state=nectar_by_state,
        honey_by_part=honey_by_part,
        honey_tainted=tainted,
        honey_retired=retired,
        honey_by_clearance=by_clearance,
        honey_by_scope_kind=_count_by_scope_kind(connection),
        vectors_by_model=vectors_by_model,
        vector_backend=vector_backend,
    )


def select_watermark(connection: sqlite3.Connection, name: str) -> str | None:
    """Return a named cursor's stored value, or None when it was never set."""
    row = connection.execute(_SELECT_WATERMARK_SQL, (name,)).fetchone()
    return row["value"] if row is not None else None


def set_watermark_transaction(connection: sqlite3.Connection, name: str, value: str) -> None:
    """Create or overwrite one named cursor's value, in its own transaction."""
    with transaction(connection):
        connection.execute(_UPSERT_WATERMARK_SQL, (name, value))


def add_proposal_transaction(
    connection: sqlite3.Connection, draft: _ProposalDraft, event: HoneyEvent, clock: Clock
) -> str:
    """Insert one proposal row then its event, in one transaction; return the new proposal id."""
    with transaction(connection):
        proposal_id = _new_proposal_id(clock)
        connection.execute(
            _INSERT_PROPOSAL_SQL,
            (proposal_id, draft.scope, draft.title, draft.text, event.at.isoformat()),
        )
        insert_event(connection, event)
        return proposal_id


def select_pending_proposals(
    connection: sqlite3.Connection, limit: int
) -> tuple[HoneyProposal, ...]:
    """Return not-yet-drained proposals, oldest first, at most `limit`."""
    rows = connection.execute(_SELECT_PENDING_PROPOSALS_SQL, (limit,)).fetchall()
    return tuple(_row_to_proposal(row) for row in rows)


def mark_proposal_drained_transaction(
    connection: sqlite3.Connection, proposal_id: str, nectar_id: NectarId, clock: Clock
) -> None:
    """Mark one proposal drained into `nectar_id`, in its own transaction."""
    with transaction(connection):
        if connection.execute(_SELECT_PROPOSAL_BY_ID_SQL, (proposal_id,)).fetchone() is None:
            raise HoneyStoreError(f"No Honey proposal with id {proposal_id!r} exists.")
        connection.execute(
            _MARK_PROPOSAL_DRAINED_SQL, (clock.now().isoformat(), nectar_id, proposal_id)
        )


def record_transaction(connection: sqlite3.Connection, event: HoneyEvent) -> None:
    """Insert one event with no accompanying row change, in its own transaction."""
    with transaction(connection):
        insert_event(connection, event)


def _count_by_scope_kind(connection: sqlite3.Connection) -> dict[str, int]:
    """Bucket every live row's scope by its kind: 'hive', 'cell', 'bee' or 'task'."""
    counts: dict[str, int] = {}
    for row in connection.execute(_SELECT_LIVE_SCOPES_SQL).fetchall():
        scope: str = row["scope"]
        kind = scope if scope == "hive" else scope.split(":", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _row_to_proposal(row: sqlite3.Row) -> HoneyProposal:
    """Decode one `honey_proposals` row back into a HoneyProposal."""
    return HoneyProposal(
        id=row["id"],
        scope=row["scope"],
        title=row["title"],
        text=row["text"],
        created_at=datetime.fromisoformat(row["created_at"]),
        drained_at=datetime.fromisoformat(row["drained_at"]) if row["drained_at"] else None,
        nectar_id=row["nectar_id"],
    )


def _new_proposal_id(clock: Clock) -> str:
    """Mint a fresh `honeynote_`-prefixed ULID, mirroring `hivemind.memory.cell_wax.new_wax_id`."""
    timestamp_ms = int(clock.now().timestamp() * 1000)
    randomness = secrets.token_bytes(RANDOMNESS_BYTES)
    return f"{_PROPOSAL_ID_PREFIX}{encode_ulid(timestamp_ms, randomness)}"
