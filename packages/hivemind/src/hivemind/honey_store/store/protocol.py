"""Define HoneyStore: the Honey Store's persistence protocol, and its own small return shapes.

This is the one seam every implementation (`hivemind.honey_store.store.sqlite.SqliteHoneyStore`,
and any future in-memory fake) must honour, mirroring `hivemind.memory.store.protocol.MemoryStore`
and `hivemind.brood_chamber.store.protocol.TaskStore`: every write takes the `HoneyEvent`
(`hivemind.pheromone`) to record alongside it and commits both together, in the same transaction
(codingrules section 12). `add_nectar` is the one write whose event depends on what the write
found (a new row or a duplicate, a raised label, a Night Veil deposit that records nothing), so it
takes `NectarEvents`, a function the store calls inside the transaction with the outcome; the
three label-lowering writes (ADR-0034) take `LoweringEvents` for the same reason. Split into six
private Protocols purely for codingrules 5.1's class-size limit -- `HoneyStore` itself is the whole
contract every caller and implementation actually names. The lowering shapes it passes
(`LoweringProposal`, `LoweringFiling`, `LoweringDecision`, `LoweringState`, `LoweringId`) live in
`hivemind.honey_store.lowering` and are imported here for type checking only: that package's
service imports this one, so a runtime import back would cycle.
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
    (intake), `.ripening` (the Ripener), `.honey` (retrieval) and `.lowering` (label lowering), and
    by `hive honey`. Calls into `hivemind.honey_store.models`, `hivemind.pheromone` (HoneyEvent) and
    `waggle.ids` only (and `hivemind.honey_store.lowering` for type checking).

Key invariants:
    - Every mutation method's event commits together with the row(s) it describes, or neither
      commits at all (codingrules Appendix C rule 3, mirroring every other store in the Hive).
    - Every `ReadFilter` is applied as SQL, fully, before any ranking or `limit` is applied
      (ADR-0031: "Filtering is policy, not ranking"), on every method that takes one.
    - `add_nectar` and `ripen` are idempotent by key (`sha256`/`source_key`, and
      `(nectar_id, part, chunk_index)`); calling either twice with the same key never duplicates a
      row. `add_lowering` is too: one proposal per Nectar, ever (ADR-0034).
    - A lowering proposal's state changes only through `apply_lowering`/`reject_lowering`, each of
      which checks `hivemind.honey_store.lowering.state.assert_transition` inside its own
      transaction.

See Also:
    - .claude/codingrules.md section 12 for the same-transaction rule every implementation follows.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the decisions this protocol encodes.
    - hivemind.honey_store.store.sqlite for SqliteHoneyStore, the shipped implementation.
    - hivemind.honey_store.models for Nectar, NectarDraft, NectarSource, Honey, HoneyDraft,
      ReadFilter, TextCandidate, VectorCandidate, HoneyStats, the shapes this protocol passes.
    - docs/adr/0033-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for
      nectar_sources, scope_counts and prune_vectors.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the lowering methods.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol

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
    RipenerReading,
    TextCandidate,
    VectorCandidate,
)
from hivemind.pheromone import HoneyEvent
from waggle.ids import CellId, HoneyId, NectarId
from waggle.messages.base import UtcDatetime

if TYPE_CHECKING:
    # Type checking only: hivemind.honey_store.lowering's service imports this module, so a runtime
    # import back would cycle (module docstring). Every use below is an annotation or a lazily
    # evaluated `type` alias, so nothing here needs these names at runtime.
    from hivemind.honey_store.lowering.models import (
        LoweringDecision,
        LoweringFiling,
        LoweringId,
        LoweringProposal,
    )
    from hivemind.honey_store.lowering.state import LoweringState

# codingrules 8.5: frozen, extra-forbidding config both value shapes in this module share.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "HoneyProposal",
    "HoneyStore",
    "LoweringEvents",
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


# Builds the events one lowering write records, from the proposal as that write left it: a new
# proposal's id exists only once the store minted it, and whether an apply lowered the label or
# found it no longer eligible is only known inside the transaction (ADR-0034). Must be pure and
# fast: it runs on the store's own thread.
type LoweringEvents = Callable[[LoweringProposal], Sequence[HoneyEvent]]


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
    """A sixth of HoneyStore (Nectar rows), split out for codingrules 5.1's class-size limit."""

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
    """A sixth of HoneyStore (Honey rows), split out for codingrules 5.1's class-size limit."""

    async def ripen(
        self,
        nectar_id: NectarId,
        drafts: Sequence[HoneyDraft],
        event: HoneyEvent,
        *,
        reading: RipenerReading | None = None,
    ) -> tuple[Honey, ...]:
        """Insert `drafts` as Honey rows ripened from `nectar_id`, and mark it RIPENED, atomically.

        Idempotent on `(nectar_id, part, chunk_index)`: a draft matching an already-stored row
        returns that row unchanged rather than inserting a duplicate. Provenance, scope, kind,
        origin and origin tier are copied from the Nectar row, never taken from `drafts`. The
        Ripener's own reading of the text is stored on the Nectar in the same transaction
        (`Nectar.ripener_clearance`, ADR-0034); it never changes any label.

        Args:
            nectar_id: The Nectar row being ripened.
            drafts: One or more parts (one SUMMARY, zero or more CHUNK) to store.
            event: The accompanying `honey.ripened` event.
            reading: The model's own label and reason for the text; None (a heuristic summary)
                stores no reading, clearing any earlier one.

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
    """A sixth of HoneyStore (vectors), split out for codingrules 5.1's class-size limit."""

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
    """A sixth of HoneyStore (search), split out for codingrules 5.1's class-size limit."""

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
    """A sixth of HoneyStore (stats, watermarks, proposals), split for codingrules 5.1's limit."""

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


class _LoweringStore(Protocol):
    """A sixth of HoneyStore (label lowering proposals, ADR-0034), split for codingrules 5.1."""

    async def lowering_candidates(self, limit: int) -> tuple[Nectar, ...]:
        """Return Nectar a judge may be asked to lower that no proposal names yet, oldest first.

        Selects exactly what `hivemind.honey_store.lowering.rules.lowering_target` accepts --
        RIPENED, untainted, outside Night Veil, not HUMAN or WATCH origin, every labelling fact
        known, the floor alone holding the label up and the Ripener's reading below it -- so no
        ineligible row ever takes a place under `limit` from an eligible one.

        Args:
            limit: The most rows to return.

        Returns:
            At most `limit` rows, oldest received first.
        """
        ...

    async def add_lowering(
        self, filing: LoweringFiling, events: LoweringEvents
    ) -> LoweringProposal | None:
        """File one PROPOSED lowering proposal and its events, atomically.

        The store trusts its caller ran `lowering_target` (the apply transaction runs it again).
        The Ripener's reason is copied from the Nectar row onto the proposal, for the human.

        Args:
            filing: The Nectar, and the labels to lower it from and to.
            events: Builds the events to record from the new proposal (`honey.lowering_proposed`).

        Returns:
            The new proposal; None, with nothing written, when the Nectar already has one (one
            proposal per Nectar, ever).

        Raises:
            NectarNotFoundError: No Nectar row with `filing.nectar_id` exists.
        """
        ...

    async def list_lowerings(
        self, state: LoweringState, limit: int
    ) -> tuple[LoweringProposal, ...]:
        """Return proposals in `state`, oldest first.

        Args:
            state: The state to list.
            limit: The most proposals to return.

        Returns:
            At most `limit` proposals, oldest proposed first.
        """
        ...

    async def pending_lowerings(self, limit: int) -> tuple[LoweringProposal, ...]:
        """Return the judge's queue: PROPOSED proposals with no note, oldest first.

        A note on a PROPOSED proposal means it waits for the human (its text is over the judge's
        bound, or the judge could not answer often enough), so the judge passes it over.

        Args:
            limit: The most proposals to return.

        Returns:
            At most `limit` proposals, oldest proposed first.
        """
        ...

    async def get_lowering(self, proposal_id: LoweringId) -> LoweringProposal:
        """Return one stored proposal.

        Args:
            proposal_id: The proposal to look up.

        Returns:
            The matching proposal.

        Raises:
            LoweringNotFoundError: No proposal with `proposal_id` exists.
        """
        ...

    async def note_lowering(
        self, proposal_id: LoweringId, note: str, *, attempted: bool
    ) -> LoweringProposal:
        """Set a PROPOSED proposal's note, counting a failed judge attempt when `attempted`.

        Bookkeeping, not a transition, so no event; a proposal already decided is left as it is.

        Args:
            proposal_id: The proposal to note.
            note: Why it waits for the human, or "" to leave it in the judge's queue.
            attempted: Whether this records one more judge answer failure.

        Returns:
            The proposal as stored after the call.

        Raises:
            LoweringNotFoundError: No proposal with `proposal_id` exists.
        """
        ...

    async def apply_lowering(
        self, decision: LoweringDecision, events: LoweringEvents
    ) -> LoweringProposal:
        """Lower a proposal's Nectar if it still stands, or reject it if not; atomically.

        Re-reads the Nectar and re-runs `lowering_target`. When the proposal's target still
        stands, lowers the Nectar and every Honey row of it still at the old label, reads them
        back (the postcondition), and moves the proposal to LOWERED; when it does not (a merge, a
        raise or a taint since filing), a PROPOSED proposal is REJECTED with the note "no longer
        eligible" instead. Either way `events` records the outcome in the same transaction.

        Args:
            decision: Who approved it, when, and the judge's verdict or the human's reason.
            events: Builds the events to record from the decided proposal.

        Returns:
            The proposal as decided: LOWERED, or REJECTED when it no longer stood.

        Raises:
            LoweringNotFoundError: No proposal with `decision.proposal_id` exists.
            LoweringTransitionError: The approver may not lower it from its state (a judge on a
                REJECTED proposal, anyone on a LOWERED one).
            LoweringIneligibleError: The human approved a REJECTED proposal that no longer stands.
            LoweringPostconditionError: The rows read back still carry the old label; nothing
                was written.
        """
        ...

    async def reject_lowering(
        self, decision: LoweringDecision, events: LoweringEvents
    ) -> LoweringProposal:
        """Reject a PROPOSED proposal (a judge's REJECT or the human's denial), atomically.

        Args:
            decision: Who rejected it, when, and the judge's verdict or the human's reason.
            events: Builds the events to record from the rejected proposal.

        Returns:
            The proposal, now REJECTED.

        Raises:
            LoweringNotFoundError: No proposal with `decision.proposal_id` exists.
            LoweringTransitionError: It is not PROPOSED.
        """
        ...


class HoneyStore(
    _NectarRowsStore,
    _HoneyRowsStore,
    _VectorsStore,
    _SearchStore,
    _MaintenanceStore,
    _LoweringStore,
    Protocol,
):
    """Persist and query Nectar and Honey, atomic with their Pheromone Trail events.

    Composed from the six private Protocols above, split only to keep each one under codingrules
    5.1's class-length limit; `HoneyStore` itself is the whole contract every caller and
    implementation (`SqliteHoneyStore`) actually names. Implementations must be safe to call
    concurrently.
    """
