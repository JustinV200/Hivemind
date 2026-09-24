"""Define HoneyStore: the Honey Store's persistence protocol, and its own small return shapes.

This is the one seam every implementation (`hivemind.honey_store.store.sqlite.SqliteHoneyStore`,
and any future in-memory fake) must honour, mirroring `hivemind.memory.store.protocol.MemoryStore`
and `hivemind.brood_chamber.store.protocol.TaskStore`: every write takes the `HoneyEvent`
(`hivemind.pheromone`) to record alongside it and commits both together, in the same transaction
(codingrules section 12). `add_nectar` is the one write whose event depends on what the write
found (a new row or a duplicate, a raised label, a Night Veil deposit that records nothing), so it
takes `NectarEvents`, a function the store calls inside the transaction with the outcome. Split
into five private Protocols purely for codingrules 5.1's class-size limit -- `HoneyStore` itself
is the whole contract every caller and implementation actually names.
`NectarAdded`, `HoneyProposal` and `PruneResult` (ADR-0033's `prune_vectors`, the same
outcome-depends-on-what-was-found shape as `add_nectar`, via its own `PruneEvents`) are this
protocol's own small return shapes, kept here rather than in `hivemind.honey_store.models`
because nothing outside the store's own callers needs them (the same reason `hivemind.pheromone.
trail.protocol` keeps `TrailQuery`/`TrailSegment` beside `PheromoneTrail` itself).

**This module is the contract later dispatches (Nectar intake, the Ripener, the Retriever) code
against; its names and signatures are load-bearing and are not to change without updating every
caller.**

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by
    `hivemind.honey_store.store.sqlite.SqliteHoneyStore`; used by `hivemind.honey_store.nectar`
    (intake), `.ripening` (the Ripener) and `.honey` (retrieval) -- all later dispatches -- and by
    `hive honey`. Calls into `hivemind.honey_store.models`, `hivemind.pheromone` (HoneyEvent) and
    `waggle.ids` only.

Key invariants:
    - Every mutation method's event commits together with the row(s) it describes, or neither
      commits at all (codingrules Appendix C rule 3, mirroring every other store in the Hive).
    - Every `ReadFilter` is applied as SQL, fully, before any ranking or `limit` is applied
      (ADR-0031: "Filtering is policy, not ranking"), on every method that takes one.
    - `add_nectar` and `ripen` are idempotent by key (`sha256`/`source_key`, and
      `(nectar_id, part, chunk_index)`); calling either twice with the same key never duplicates a
      row.

See Also:
    - .claude/codingrules.md section 12 for the same-transaction rule every implementation follows.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the decisions this protocol encodes.
    - hivemind.honey_store.store.sqlite for SqliteHoneyStore, the shipped implementation.
    - hivemind.honey_store.models for Nectar, NectarDraft, NectarSource, Honey, HoneyDraft,
      ReadFilter, TextCandidate, VectorCandidate, HoneyStats, the shapes this protocol passes.
    - docs/adr/0033-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for
      nectar_sources, scope_counts and prune_vectors.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.honey_store.models import (
    Honey,
    HoneyDraft,
    HoneyStats,
    Nectar,
    NectarDraft,
    NectarSource,
    ReadFilter,
    TextCandidate,
    VectorCandidate,
)
from hivemind.pheromone import HoneyEvent
from waggle.ids import CellId, HoneyId, NectarId
from waggle.messages.base import UtcDatetime

# codingrules 8.5: frozen, extra-forbidding config both value shapes in this module share.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "HoneyProposal",
    "HoneyStore",
    "NectarAdded",
    "NectarEvents",
    "PruneEvents",
    "PruneResult",
]


class NectarAdded(BaseModel):
    """`add_nectar`'s own result: the stored row, whether it was new, and any label it raised."""

    model_config = _MODEL_CONFIG

    nectar: Nectar = Field(description="The stored Nectar row, new or already-existing.")
    is_new: bool = Field(description="False when this deposit deduped onto an existing row.")
    raised_from: HoneyClearance | None = Field(
        default=None,
        description="The label before this call, when a dedupe merge raised it; None otherwise.",
    )


# Builds the events one `add_nectar` call records, from what it found: the new row's id only
# exists once the store has minted it, and whether the deposit was new, a duplicate or a label raise
# is only known inside the transaction. Must be pure and fast: it runs on the store's own thread.
type NectarEvents = Callable[[NectarAdded], Sequence[HoneyEvent]]


class PruneResult(BaseModel):
    """`prune_vectors`'s own result: the kept model, and either what dropped or what is missing."""

    model_config = _MODEL_CONFIG

    kept_model: str = Field(description="The embedding model whose vectors were kept untouched.")
    missing: int = Field(
        ge=0,
        description="Live rows still lacking a vector for kept_model; above zero means the prune "
        "was refused and nothing else in this result is meaningful.",
    )
    dropped: dict[str, int] = Field(
        default_factory=dict,
        description="Vector rows removed, by the model they belonged to; empty when refused.",
    )


# Builds the events one `prune_vectors` call records, from what it found: refused (missing > 0)
# builds none, so a refusal that changed nothing leaves nothing on the trail either (mirrors
# NectarEvents' own Night Veil case). Must be pure and fast: it runs on the store's own thread.
type PruneEvents = Callable[[PruneResult], Sequence[HoneyEvent]]


class HoneyProposal(BaseModel):
    """One human-proposed note (roadmap 7.10), queued for the House Bee to drain into Nectar."""

    model_config = _MODEL_CONFIG

    id: str = Field(description="This proposal's own id.")
    scope: str = Field(description="The folder it was proposed in.")
    title: str = Field(description="A one-line label.")
    text: str = Field(description="The proposed note's text.")
    created_at: UtcDatetime = Field(description="When it was proposed.")
    drained_at: UtcDatetime | None = Field(
        default=None, description="When it was turned into Nectar; None while still pending."
    )
    nectar_id: NectarId | None = Field(
        default=None, description="The Nectar row it became, once drained."
    )


class _NectarRowsStore(Protocol):
    """A fifth of HoneyStore (Nectar rows), split out for codingrules 5.1's class-size limit."""

    async def add_nectar(
        self, draft: NectarDraft, content_sha256: str, events: NectarEvents
    ) -> NectarAdded:
        """Insert `draft` as a new Nectar row, or dedupe onto an existing one, atomically.

        Dedupes by `draft.source_key` first (when set), else by `content_sha256` among rows on the
        same side of the Night Veil boundary: an ephemeral draft matches only its own Cell's
        ephemeral rows, and an ordinary draft never matches an ephemeral row (which the Cell's
        teardown would purge, taking the ordinary deposit with it). A duplicate returns the stored
        row with `is_new=False`; when `draft.clearance` ranks higher than the stored row's own,
        the stored label and every Honey row already ripened from it are raised in the same
        transaction, and `raised_from` reports the label before the raise. `events` is called
        once, inside the transaction, with that result, and every event it returns commits with
        the row: `honey.nectar_received` or `honey.nectar_deduplicated` (plus
        `honey.label_raised`), or nothing at all for a Night Veil ephemeral deposit (ADR-0031).

        Args:
            draft: The deposit to store; its `clearance`/`scope` are already computed
                (`hivemind.honey_store.clearance`/`.scope`).
            content_sha256: The digest of `draft.content`, computed by the caller so the store
                never has to hash content itself.
            events: Builds the events to record from the outcome; see `NectarEvents`.

        Returns:
            The result, new or deduped.
        """
        ...

    async def get_nectar(self, nectar_id: NectarId) -> Nectar:
        """Return the stored Nectar row.

        Args:
            nectar_id: The row to look up.

        Returns:
            The matching Nectar.

        Raises:
            NectarNotFoundError: No row with `nectar_id` exists.
        """
        ...

    async def nectar_content(self, nectar_id: NectarId) -> bytes:
        """Return one Nectar row's raw content bytes.

        Args:
            nectar_id: The row to look up.

        Returns:
            The stored content.

        Raises:
            NectarNotFoundError: No row with `nectar_id` exists.
        """
        ...

    async def pending_nectar(self, limit: int) -> tuple[Nectar, ...]:
        """Return RECEIVED Nectar rows, oldest first, for the next ripening pass.

        Args:
            limit: The most rows to return.

        Returns:
            At most `limit` matching rows.
        """
        ...

    async def has_source(self, source_key: str) -> bool:
        """Return whether `source_key` was already taken in, as a row or as an extra source.

        Answers from both `honey_nectar.source_key` and `honey_nectar_sources.source_key`
        (ADR-0033), so a caller waiting on a specific source's own key never hangs just because
        that source's content happened to deduplicate onto an earlier deposit.

        Args:
            source_key: An internal origin's own dedupe key.

        Returns:
            True when `source_key` is recorded on either table.
        """
        ...

    async def nectar_sources(self, nectar_id: NectarId) -> tuple[NectarSource, ...]:
        """Return `nectar_id`'s extra sources: other deposits that deduplicated onto it by content.

        Args:
            nectar_id: The Nectar row to look up.

        Returns:
            Its recorded extra sources, oldest first; empty when every duplicate of this row was
            either the same source delivered again or shared its stored provenance (ADR-0033).
        """
        ...

    async def mark_nectar_failed(
        self, nectar_id: NectarId, *, discard: bool, event: HoneyEvent
    ) -> Nectar:
        """Record a failed ripening attempt, atomically with `event`.

        Args:
            nectar_id: The row that failed to ripen this pass.
            discard: When True, move the row to DISCARDED instead of leaving it RECEIVED.
            event: The accompanying `honey.ripen_failed` event.

        Returns:
            The updated row, with `ripen_attempts` incremented by one.

        Raises:
            NectarNotFoundError: No row with `nectar_id` exists.
        """
        ...

    async def purge_ephemeral(self, cell_id: CellId) -> int:
        """Delete every EPHEMERAL Nectar row belonging to `cell_id` (Night Veil teardown, ADR-0031).

        Args:
            cell_id: The Night Veil Cell being torn down.

        Returns:
            How many rows were deleted.
        """
        ...


class _HoneyRowsStore(Protocol):
    """A fifth of HoneyStore (Honey rows), split out for codingrules 5.1's class-size limit."""

    async def ripen(
        self, nectar_id: NectarId, drafts: Sequence[HoneyDraft], event: HoneyEvent
    ) -> tuple[Honey, ...]:
        """Insert `drafts` as Honey rows ripened from `nectar_id`, and mark it RIPENED, atomically.

        Idempotent on `(nectar_id, part, chunk_index)`: a draft matching an already-stored row
        returns that row unchanged rather than inserting a duplicate. Provenance, scope, kind,
        origin and origin tier are copied from the Nectar row, never taken from `drafts`.

        Args:
            nectar_id: The Nectar row being ripened.
            drafts: One or more parts (one SUMMARY, zero or more CHUNK) to store.
            event: The accompanying `honey.ripened` event.

        Returns:
            Every resulting Honey row, in `drafts`' order.

        Raises:
            NectarNotFoundError: No row with `nectar_id` exists.
        """
        ...

    async def get_honey(self, honey_id: HoneyId) -> Honey:
        """Return the stored Honey row.

        Args:
            honey_id: The row to look up.

        Returns:
            The matching Honey.

        Raises:
            HoneyNotFoundError: No row with `honey_id` exists.
        """
        ...

    async def honey_for_nectar(self, nectar_id: NectarId) -> tuple[Honey, ...]:
        """Return every Honey row ripened from `nectar_id`.

        Args:
            nectar_id: The Nectar row to look up.

        Returns:
            Its Honey rows, in no particular order; empty when `nectar_id` has not been ripened.
        """
        ...

    async def list_honey(
        self, filter: ReadFilter, *, scope_prefix: str | None, limit: int, offset: int
    ) -> tuple[Honey, ...]:
        """Return live Honey rows within `filter`, newest first.

        Args:
            filter: The reader's scope, clearance and requested-scopes filter.
            scope_prefix: When set, only rows whose scope starts with this (e.g. `"cell:"`) or
                equals it exactly; None lists every scope `filter` already allows.
            limit: The most rows to return.
            offset: Rows to skip, for pagination.

        Returns:
            At most `limit` matching rows, ordered by `created_at` descending.
        """
        ...

    async def scope_counts(self, scope_kind: str, filter: ReadFilter) -> dict[str, int]:
        """Count live rows per scope of `scope_kind` within `filter` (ADR-0033).

        The Honey browser's index folders (`/cells`, `/bees`, `/tasks`) are built from this, so
        they list every scope the reader may see at any store size, with no scan bound.

        Args:
            scope_kind: "cell", "bee" or "task".
            filter: The reader's scope, clearance and requested-scopes filter, applied before
                counting (ADR-0031: "Filtering is policy, not ranking").

        Returns:
            Each scope of `scope_kind` holding at least one visible row, mapped to its count;
            empty when the reader may see none.
        """
        ...

    async def raise_clearance(
        self, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
    ) -> Honey:
        """Raise one Honey row's clearance, atomically with `event`.

        The store trusts the caller: `to` should already be the result of
        `hivemind.honey_store.clearance.raise_label`.

        Args:
            honey_id: The row to raise.
            to: The new clearance.
            event: The accompanying `honey.label_raised` event.

        Returns:
            The updated row.

        Raises:
            HoneyNotFoundError: No row with `honey_id` exists.
        """
        ...

    async def lower_clearance(
        self, honey_id: HoneyId, to: HoneyClearance, event: HoneyEvent
    ) -> Honey:
        """Lower one Honey row's clearance, atomically with `event`.

        The store trusts the caller ran `hivemind.honey_store.clearance.check_lowering` first; it
        never re-derives the rule itself.

        Args:
            honey_id: The row to lower.
            to: The new, lower clearance.
            event: The accompanying `honey.label_lowered` event.

        Returns:
            The updated row.

        Raises:
            HoneyNotFoundError: No row with `honey_id` exists.
        """
        ...

    async def retire(self, honey_id: HoneyId, event: HoneyEvent) -> Honey:
        """Retire one Honey row (superseded; never returned again), atomically with `event`.

        Args:
            honey_id: The row to retire.
            event: The accompanying `honey.retired` event.

        Returns:
            The updated row, with `retired_at` set.

        Raises:
            HoneyNotFoundError: No row with `honey_id` exists.
        """
        ...


class _VectorsStore(Protocol):
    """A fifth of HoneyStore (vectors), split out for codingrules 5.1's class-size limit."""

    async def set_vectors(
        self,
        pairs: Sequence[tuple[HoneyId, Sequence[float]]],
        model: str,
        event: HoneyEvent | None,
    ) -> int:
        """Upsert one vector per `(honey_id, vector)` pair for `model`, atomically.

        Args:
            pairs: Rows to embed or re-embed; each `vector` is a dense embedding.
            model: The embedding model id that produced every vector in `pairs`.
            event: The accompanying `honey.reembedded` event, or None for a pass that records its
                own rollup event separately (the caller's choice).

        Returns:
            How many rows were written.

        Raises:
            HoneyNotFoundError: Some `honey_id` in `pairs` does not exist.
            ZeroVectorRefusedError-shaped ValueError: Some vector's norm is zero.
        """
        ...

    async def pending_vectors(self, model: str, limit: int) -> tuple[Honey, ...]:
        """Return live Honey rows with no vector yet for `model`, oldest first.

        Args:
            model: The embedding model to check coverage for.
            limit: The most rows to return.

        Returns:
            At most `limit` matching rows (ADR-0032: "changing the embedder slot ... re-embeds the
            store progressively").
        """
        ...

    async def prune_vectors(self, kept_model: str, events: PruneEvents) -> PruneResult:
        """Delete every vector whose model is not `kept_model`, only when it is safe to.

        Checks, deletes and records atomically (ADR-0033): a live Honey row (not tainted, not
        retired) missing a vector for `kept_model` refuses the whole call and deletes nothing, so
        switching back to a pruned model always costs a full re-embed on purpose, never by
        accident from a prune that ran too early.

        Args:
            kept_model: The embedding model whose vectors must survive untouched.
            events: Builds the events to record from the outcome; see `PruneEvents`.

        Returns:
            `PruneResult` with either the refusal's `missing` count, or `dropped`'s per-model
            counts once every other model's vectors are gone.
        """
        ...


class _SearchStore(Protocol):
    """A fifth of HoneyStore (search), split out for codingrules 5.1's class-size limit."""

    async def search_text(
        self, match: str, filter: ReadFilter, limit: int
    ) -> tuple[TextCandidate, ...]:
        """Full-text search live Honey rows within `filter`, ranked by bm25.

        Args:
            match: An FTS5 MATCH string built by `hivemind.honey_store.store.fts.build_match`.
            filter: The reader's scope, clearance and requested-scopes filter, applied before
                ranking (ADR-0031).
            limit: The most candidates to return.

        Returns:
            At most `limit` candidates, best match first.
        """
        ...

    async def search_vectors(
        self, vector: Sequence[float], model: str, filter: ReadFilter, limit: int
    ) -> tuple[VectorCandidate, ...]:
        """Nearest-neighbour search live Honey rows within `filter`, ranked by cosine distance.

        Args:
            vector: The query embedding.
            model: Only rows whose vector was produced by this model are compared (ADR-0032: a
                query never compares vectors from different models).
            filter: The reader's scope, clearance and requested-scopes filter, applied before
                ranking.
            limit: The most candidates to return.

        Returns:
            At most `limit` candidates, nearest first.
        """
        ...

    async def count_withheld(self, match: str, filter: ReadFilter, limit: int) -> int:
        """Count how many of the top `limit` text matches among live rows `filter` excludes.

        Args:
            match: An FTS5 MATCH string built by `hivemind.honey_store.store.fts.build_match`.
            filter: The reader's scope, clearance and requested-scopes filter.
            limit: How many top-ranked live matches to evaluate.

        Returns:
            How many of those top `limit` matches `filter` would exclude.
        """
        ...

    async def nearest_in_scope(
        self, vector: Sequence[float], model: str, scope: str, limit: int
    ) -> tuple[VectorCandidate, ...]:
        """Nearest-neighbour search live rows in exactly `scope`, any clearance (near-duplicates).

        Args:
            vector: The candidate embedding to compare against.
            model: Only rows whose vector was produced by this model are compared.
            scope: The exact scope to search within (no glob, no requested-scopes widening).
            limit: The most candidates to return.

        Returns:
            At most `limit` candidates, nearest first.
        """
        ...


class _MaintenanceStore(Protocol):
    """A fifth of HoneyStore (stats, watermarks, proposals), split for codingrules 5.1's limit."""

    async def stats(self) -> HoneyStats:
        """Return aggregate counts over the whole store.

        Returns:
            Counts only; no content, id or query text (codingrules section 12).
        """
        ...

    async def get_watermark(self, name: str) -> str | None:
        """Return a named cursor's stored value.

        Args:
            name: The watermark's name.

        Returns:
            Its stored value, or None when nothing has set it yet.
        """
        ...

    async def set_watermark(self, name: str, value: str) -> None:
        """Set a named cursor's value, creating or overwriting it.

        Args:
            name: The watermark's name.
            value: Its new value.
        """
        ...

    async def add_proposal(self, scope: str, title: str, text: str, event: HoneyEvent) -> str:
        """Queue a human-proposed note (roadmap 7.10), atomically with `event`.

        Args:
            scope: The folder the note was proposed in.
            title: A one-line label.
            text: The proposed note's text.
            event: The accompanying `honey.note_proposed` event.

        Returns:
            The new proposal's own id.
        """
        ...

    async def pending_proposals(self, limit: int) -> tuple[HoneyProposal, ...]:
        """Return not-yet-drained proposals, oldest first.

        Args:
            limit: The most proposals to return.

        Returns:
            At most `limit` matching proposals.
        """
        ...

    async def mark_proposal_drained(self, proposal_id: str, nectar_id: NectarId) -> None:
        """Mark a proposal as drained into `nectar_id`.

        Args:
            proposal_id: The proposal that was turned into Nectar.
            nectar_id: The Nectar row it became.

        Raises:
            HoneyStoreError: No proposal with `proposal_id` exists.
        """
        ...

    async def record(self, event: HoneyEvent) -> None:
        """Insert one event with no accompanying row change (e.g. `honey.queried`).

        Args:
            event: The event to insert.
        """
        ...


class HoneyStore(
    _NectarRowsStore, _HoneyRowsStore, _VectorsStore, _SearchStore, _MaintenanceStore, Protocol
):
    """Persist and query Nectar and Honey, atomic with their Pheromone Trail events.

    Composed from the five private Protocols above, split only to keep each one under codingrules
    5.1's class-length limit; `HoneyStore` itself is the whole contract every caller and
    implementation (`SqliteHoneyStore`) actually names. Implementations must be safe to call
    concurrently.
    """
