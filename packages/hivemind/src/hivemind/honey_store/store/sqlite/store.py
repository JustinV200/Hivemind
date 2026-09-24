"""Provide SqliteHoneyStore: the durable HoneyStore built on the Honey Store's tables.

Every method here is a thin delegate onto one sibling module's own function (`nectar.py`,
`honey.py`, `vectors.py`, `search.py`, `stats.py`, `lowering.py`, split by responsibility so each
stays under codingrules 5.1's size limit): acquire this instance's lock, run the real work on the
`ConnectionThread`, return the result. That one step is `_StoreParts._run`, written once.
`SqliteHoneyStore` itself is composed from six private mixins (`_NectarMethods`, `_HoneyMethods`,
`_VectorMethods`, `_SearchMethods`, `_MaintenanceMethods`, `_LoweringMethods`), one per
responsibility, purely so each mixin's own class body stays under codingrules 5.1's 200-line class
limit -- `HoneyStore`'s methods do not fit in one class body otherwise, the same reason
`hivemind.honey_store.store.protocol.HoneyStore` itself is composed from six private Protocols.
This module owns only the mixins, the composed class, `create` (which checks for the Pheromone
Trail's table, applies migrations, and picks the vector backend) and nothing else.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by a composition root once
    the Hive Manifest names the database file (`hivemind.cli.stores.open_honey_store`). Calls into
    hivemind.common (connect, transaction, migrations, errors), hivemind.honey_store (errors,
    models, schema, store.protocol, store.sqlite's own siblings) and hivemind.pheromone only.

Key invariants:
    - `create` refuses to proceed unless `pheromone_events` already exists on `connection`'s
      database, matching `SqliteMemoryStore.create`'s own check.
    - Every SQLite call runs on the store's `ConnectionThread`, one transaction per hop, serialised
      by this instance's own `asyncio.Lock` (codingrules section 11).
    - `force_python_vectors=True` always wins over a successfully-loadable sqlite-vec extension,
      so the contract suite can exercise both backends against the same schema on one host.
    - The mixins declare, but never assign, the instance attributes `SqliteHoneyStore.__init__`
      sets (`_connection`, `_thread`, `_lock`, `_clock`, `_use_sqlite_vec`, on `_StoreParts`): a
      standard mypy-legal mixin pattern, since only the composed class is ever instantiated.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0007-pheromone-trail-append-
      only-transactional-and-segmented.md for the decisions this module follows.
    - hivemind.common.sqlite and hivemind.honey_store.schema for the connection/transaction and
      migration runner this module builds on.
    - hivemind.honey_store.store.protocol for the HoneyStore protocol this class implements.
    - hivemind.honey_store.store.sqlite.nectar/.honey/.vectors/.search/.stats/.sources/.lowering
      for the SQL each method delegates to, and .vec for the vector backend `create` picks.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence

from hivemind.cell import HoneyClearance
from hivemind.common.errors import MigrationError
from hivemind.common.sqlite import ConnectionThread
from hivemind.honey_store.lowering.models import (
    LoweringDecision,
    LoweringFiling,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.honey_store.models import (
    Honey,
    HoneyDraft,
    HoneyStats,
    Nectar,
    NectarDraft,
    NectarSource,
    ReadFilter,
    RipenerReading,
    TextCandidate,
    VectorCandidate,
)
from hivemind.honey_store.schema import apply_honey_store_migrations
from hivemind.honey_store.store.protocol import (
    HoneyProposal,
    LoweringEvents,
    NectarAdded,
    NectarEvents,
    PruneEvents,
    PruneResult,
)
from hivemind.honey_store.store.sqlite import honey as honey_sql
from hivemind.honey_store.store.sqlite import lowering as lowering_sql
from hivemind.honey_store.store.sqlite import nectar as nectar_sql
from hivemind.honey_store.store.sqlite import search as search_sql
from hivemind.honey_store.store.sqlite import sources as sources_sql
from hivemind.honey_store.store.sqlite import stats as stats_sql
from hivemind.honey_store.store.sqlite import vectors as vectors_sql
from hivemind.honey_store.store.sqlite.honey import RipenWrite
from hivemind.honey_store.store.sqlite.search import _VectorQuery
from hivemind.honey_store.store.sqlite.stats import _ProposalDraft
from hivemind.honey_store.store.sqlite.vec import (
    VECTOR_BACKEND_PYTHON,
    VECTOR_BACKEND_SQLITE_VEC,
    load_vector_extension,
)
from hivemind.pheromone import HoneyEvent
from waggle.clock import Clock
from waggle.ids import CellId, HoneyId, NectarId

# create()'s loud-failure check, matching SqliteMemoryStore.create's own guard.
_PHEROMONE_TABLE_CHECK_SQL = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pheromone_events'"
)

__all__ = ["SqliteHoneyStore"]


class _StoreParts:
    """What every mixin shares: the instance attributes, and the one delegation step itself."""

    _connection: sqlite3.Connection
    _thread: ConnectionThread
    _lock: asyncio.Lock
    _clock: Clock
    _use_sqlite_vec: bool

    async def _run[ResultT](self, work: Callable[..., ResultT], /, *args: object) -> ResultT:
        """Run `work(connection, *args)` on the store's thread under its lock; return the result.

        Local SQLite on one dedicated thread, milliseconds; a busy database waits out the
        connection's own busy timeout. The lock is what serialises every method of the store.
        """
        async with self._lock:
            return await self._thread.run(work, self._connection, *args)


class _NectarMethods(_StoreParts):
    """`SqliteHoneyStore`'s Nectar-row methods, split out for codingrules 5.1's class-size limit."""

    async def add_nectar(
        self, draft: NectarDraft, content_sha256: str, events: NectarEvents
    ) -> NectarAdded:
        """Insert or dedupe `draft`; see `HoneyStore.add_nectar`."""
        return await self._run(
            nectar_sql.add_nectar_transaction, draft, content_sha256, events, self._clock
        )

    async def get_nectar(self, nectar_id: NectarId) -> Nectar:
        """Return the stored Nectar row; see `HoneyStore.get_nectar`."""
        return await self._run(nectar_sql.select_nectar, nectar_id)

    async def nectar_content(self, nectar_id: NectarId) -> bytes:
        """Return one Nectar row's content bytes; see `HoneyStore.nectar_content`."""
        return await self._run(nectar_sql.select_nectar_content, nectar_id)

    async def pending_nectar(self, limit: int) -> tuple[Nectar, ...]:
        """Return RECEIVED rows, oldest first; see `HoneyStore.pending_nectar`."""
        return await self._run(nectar_sql.select_pending_nectar, limit)

    async def has_source(self, source_key: str) -> bool:
        """Return whether a row or an extra source carries this source_key.

        See `HoneyStore.has_source`. Both reads run under one hold of the lock, so no write can
        land between them.
        """
        async with self._lock:
            if await self._thread.run(nectar_sql.select_has_source, self._connection, source_key):
                return True
            return await self._thread.run(
                sources_sql.select_has_source, self._connection, source_key
            )

    async def nectar_sources(self, nectar_id: NectarId) -> tuple[NectarSource, ...]:
        """Return `nectar_id`'s extra sources; see `HoneyStore.nectar_sources`."""
        return await self._run(sources_sql.select_sources_for_nectar, nectar_id)

    async def mark_nectar_failed(
        self, nectar_id: NectarId, *, discard: bool, event: HoneyEvent
    ) -> Nectar:
        """Record a failed ripening attempt; see `HoneyStore.mark_nectar_failed`."""
        return await self._run(nectar_sql.mark_nectar_failed_transaction, nectar_id, discard, event)

    async def purge_ephemeral(self, cell_id: CellId) -> int:
        """Delete EPHEMERAL rows for `cell_id`; see `HoneyStore.purge_ephemeral`."""
        return await self._run(nectar_sql.purge_ephemeral_transaction, cell_id)


class _HoneyMethods(_StoreParts):
    """`SqliteHoneyStore`'s Honey-row methods, split out for codingrules 5.1's class-size limit."""

    async def ripen(
        self,
        nectar_id: NectarId,
        drafts: Sequence[HoneyDraft],
        event: HoneyEvent,
        *,
        reading: RipenerReading | None = None,
    ) -> tuple[Honey, ...]:
        """Ripen `nectar_id` into `drafts`, with the Ripener's reading; see `HoneyStore.ripen`."""
        write = RipenWrite(nectar_id=nectar_id, drafts=tuple(drafts), event=event, reading=reading)
        return await self._run(honey_sql.ripen_transaction, write, self._clock)

    async def get_honey(self, honey_id: HoneyId) -> Honey:
        """Return the stored Honey row; see `HoneyStore.get_honey`."""
        return await self._run(honey_sql.select_honey, honey_id)

    async def honey_for_nectar(self, nectar_id: NectarId) -> tuple[Honey, ...]:
        """Return every Honey row ripened from `nectar_id`; see `HoneyStore.honey_for_nectar`."""
        return await self._run(honey_sql.select_honey_for_nectar, nectar_id)

    async def list_honey(
        self, filter: ReadFilter, *, scope_prefix: str | None, limit: int, offset: int
    ) -> tuple[Honey, ...]:
        """Return live rows within `filter`; see `HoneyStore.list_honey`."""
        return await self._run(honey_sql.select_honey_list, filter, scope_prefix, limit, offset)

    async def scope_counts(self, scope_kind: str, filter: ReadFilter) -> dict[str, int]:
        """Count live rows per scope of `scope_kind` within `filter`.

        See `HoneyStore.scope_counts`.
        """
        return await self._run(honey_sql.select_scope_counts, scope_kind, filter)

    async def raise_clearance(
        self, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
    ) -> Honey:
        """Raise one row's clearance; see `HoneyStore.raise_clearance`."""
        return await self._run(honey_sql.raise_clearance_transaction, honey_id, to, event)

    async def lower_clearance(
        self, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
    ) -> Honey:
        """Lower one row's clearance; see `HoneyStore.lower_clearance`."""
        return await self._run(honey_sql.lower_clearance_transaction, honey_id, to, event)

    async def retire(self, honey_id: HoneyId, event: HoneyEvent) -> Honey:
        """Retire one row; see `HoneyStore.retire`."""
        return await self._run(honey_sql.retire_transaction, honey_id, event)


class _VectorMethods(_StoreParts):
    """`SqliteHoneyStore`'s vector methods, split out for codingrules 5.1's class-size limit."""

    async def set_vectors(
        self,
        pairs: Sequence[tuple[HoneyId, Sequence[float]]],
        model: str,
        event: HoneyEvent | None,
    ) -> int:
        """Upsert vectors for `pairs`; see `HoneyStore.set_vectors`."""
        return await self._run(vectors_sql.set_vectors_transaction, pairs, model, event)

    async def pending_vectors(self, model: str, limit: int) -> tuple[Honey, ...]:
        """Return live rows with no vector yet for `model`; see `HoneyStore.pending_vectors`."""
        return await self._run(vectors_sql.select_pending_vectors, model, limit)

    async def prune_vectors(self, kept_model: str, events: PruneEvents) -> PruneResult:
        """Drop every other model's vectors, if it is safe to; see `HoneyStore.prune_vectors`."""
        return await self._run(vectors_sql.prune_vectors_transaction, kept_model, events)


class _SearchMethods(_StoreParts):
    """`SqliteHoneyStore`'s search methods, split out for codingrules 5.1's class-size limit."""

    async def search_text(
        self, match: str, filter: ReadFilter, limit: int
    ) -> tuple[TextCandidate, ...]:
        """Full-text search within `filter`; see `HoneyStore.search_text`."""
        return await self._run(search_sql.select_text_candidates, match, filter, limit)

    async def search_vectors(
        self, vector: Sequence[float], model: str, filter: ReadFilter, limit: int
    ) -> tuple[VectorCandidate, ...]:
        """Nearest-neighbour search within `filter`; see `HoneyStore.search_vectors`."""
        query = _VectorQuery(vector=vector, model=model, use_sqlite_vec=self._use_sqlite_vec)
        return await self._run(search_sql.select_vector_candidates, query, filter, limit)

    async def count_withheld(self, match: str, filter: ReadFilter, limit: int) -> int:
        """Count top matches `filter` excludes; see `HoneyStore.count_withheld`."""
        return await self._run(search_sql.select_count_withheld, match, filter, limit)

    async def nearest_in_scope(
        self, vector: Sequence[float], model: str, scope: str, limit: int
    ) -> tuple[VectorCandidate, ...]:
        """Nearest-neighbour search within `scope`; see `HoneyStore.nearest_in_scope`."""
        query = _VectorQuery(vector=vector, model=model, use_sqlite_vec=self._use_sqlite_vec)
        return await self._run(search_sql.select_nearest_in_scope, query, scope, limit)


class _MaintenanceMethods(_StoreParts):
    """`SqliteHoneyStore`'s stats/watermark/proposal methods, split for codingrules 5.1's limit."""

    async def stats(self) -> HoneyStats:
        """Return aggregate counts; see `HoneyStore.stats`."""
        backend = VECTOR_BACKEND_SQLITE_VEC if self._use_sqlite_vec else VECTOR_BACKEND_PYTHON
        return await self._run(stats_sql.select_stats, backend)

    async def get_watermark(self, name: str) -> str | None:
        """Return a named cursor's value; see `HoneyStore.get_watermark`."""
        return await self._run(stats_sql.select_watermark, name)

    async def set_watermark(self, name: str, value: str) -> None:
        """Set a named cursor's value; see `HoneyStore.set_watermark`."""
        await self._run(stats_sql.set_watermark_transaction, name, value)

    async def add_proposal(self, scope: str, title: str, text: str, event: HoneyEvent) -> str:
        """Queue a human-proposed note; see `HoneyStore.add_proposal`."""
        draft = _ProposalDraft(scope=scope, title=title, text=text)
        return await self._run(stats_sql.add_proposal_transaction, draft, event, self._clock)

    async def pending_proposals(self, limit: int) -> tuple[HoneyProposal, ...]:
        """Return not-yet-drained proposals; see `HoneyStore.pending_proposals`."""
        return await self._run(stats_sql.select_pending_proposals, limit)

    async def mark_proposal_drained(self, proposal_id: str, nectar_id: NectarId) -> None:
        """Mark a proposal drained into `nectar_id`; see `HoneyStore.mark_proposal_drained`."""
        await self._run(
            stats_sql.mark_proposal_drained_transaction, proposal_id, nectar_id, self._clock
        )

    async def record(self, event: HoneyEvent) -> None:
        """Insert one event with no row change; see `HoneyStore.record`."""
        await self._run(stats_sql.record_transaction, event)


class _LoweringMethods(_StoreParts):
    """`SqliteHoneyStore`'s label lowering methods (ADR-0034), split for codingrules 5.1's limit."""

    async def lowering_candidates(self, limit: int) -> tuple[Nectar, ...]:
        """Return eligible Nectar no proposal names; see `HoneyStore.lowering_candidates`."""
        return await self._run(lowering_sql.select_lowering_candidates, limit)

    async def add_lowering(
        self, filing: LoweringFiling, events: LoweringEvents
    ) -> LoweringProposal | None:
        """File one PROPOSED proposal; see `HoneyStore.add_lowering`."""
        return await self._run(lowering_sql.add_lowering_transaction, filing, events, self._clock)

    async def list_lowerings(
        self, state: LoweringState, limit: int
    ) -> tuple[LoweringProposal, ...]:
        """Return proposals in `state`, oldest first; see `HoneyStore.list_lowerings`."""
        return await self._run(lowering_sql.select_lowerings, state, limit)

    async def pending_lowerings(self, limit: int) -> tuple[LoweringProposal, ...]:
        """Return the judge's queue; see `HoneyStore.pending_lowerings`."""
        return await self._run(lowering_sql.select_pending_lowerings, limit)

    async def get_lowering(self, proposal_id: LoweringId) -> LoweringProposal:
        """Return one stored proposal; see `HoneyStore.get_lowering`."""
        return await self._run(lowering_sql.select_lowering, proposal_id)

    async def note_lowering(
        self, proposal_id: LoweringId, note: str, *, attempted: bool
    ) -> LoweringProposal:
        """Note a PROPOSED proposal, counting an attempt; see `HoneyStore.note_lowering`."""
        return await self._run(lowering_sql.note_lowering_transaction, proposal_id, note, attempted)

    async def apply_lowering(
        self, decision: LoweringDecision, events: LoweringEvents
    ) -> LoweringProposal:
        """Lower, or reject when it no longer stands; see `HoneyStore.apply_lowering`."""
        return await self._run(lowering_sql.apply_lowering_transaction, decision, events)

    async def reject_lowering(
        self, decision: LoweringDecision, events: LoweringEvents
    ) -> LoweringProposal:
        """Reject a PROPOSED proposal; see `HoneyStore.reject_lowering`."""
        return await self._run(lowering_sql.reject_lowering_transaction, decision, events)


class SqliteHoneyStore(
    _NectarMethods,
    _HoneyMethods,
    _VectorMethods,
    _SearchMethods,
    _MaintenanceMethods,
    _LoweringMethods,
):
    """The durable HoneyStore: SQLite tables, one connection, one lock per instance."""

    def __init__(
        self, connection: sqlite3.Connection, clock: Clock, *, use_sqlite_vec: bool
    ) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has the Honey Store's tables
                (normally produced by `create`, which applies the migration first).
            clock: Injected clock, used to mint ids and stamp maintenance timestamps.
            use_sqlite_vec: Whether `vec_distance_cosine` is callable in SQL on `connection`
                (normally `create`'s own `load_vector_extension` result).
        """
        self._connection = connection
        self._clock = clock
        self._use_sqlite_vec = use_sqlite_vec
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled
        # await can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-honey")
        # Serialises every method, matching SqliteMemoryStore's and SqlitePheromoneTrail's own lock.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls, connection: sqlite3.Connection, clock: Clock, *, force_python_vectors: bool = False
    ) -> SqliteHoneyStore:
        """Check for the Pheromone Trail's table, apply migrations, pick a vector backend, wrap.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`. A composition
                root applies the Pheromone Trail's own migrations on its connection to the same
                file before calling this.
            clock: Injected clock, used for migration timestamps and by this instance.
            force_python_vectors: When True, always use the Python cosine fallback even if
                sqlite-vec loads successfully (the contract suite's own seam, ADR-0031).

        Returns:
            A SqliteHoneyStore whose tables exist and are current.

        Raises:
            MigrationError: `connection`'s database has no `pheromone_events` table yet.
        """
        # Blocking: a single indexed lookup against sqlite_master; sub-millisecond.
        has_pheromone_table = await asyncio.to_thread(_pheromone_table_exists, connection)
        if not has_pheromone_table:
            raise MigrationError(
                "cannot apply honey_store migrations: no pheromone_events table on this "
                "connection; call hivemind.pheromone.trail.sqlite.apply_pheromone_migrations (or "
                "SqlitePheromoneTrail.create) on this database file first."
            )
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_honey_store_migrations, connection, clock)
        # Blocking: one extension-load attempt (or none, when forced off); logged once either way.
        use_sqlite_vec = (
            False
            if force_python_vectors
            else await asyncio.to_thread(load_vector_extension, connection)
        )
        return cls(connection, clock, use_sqlite_vec=use_sqlite_vec)


def _pheromone_table_exists(connection: sqlite3.Connection) -> bool:
    """Return whether `connection`'s database already has a `pheromone_events` table."""
    return connection.execute(_PHEROMONE_TABLE_CHECK_SQL).fetchone() is not None
