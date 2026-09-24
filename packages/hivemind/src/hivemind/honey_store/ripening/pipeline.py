"""Run the House Bee's ripening pass: pending Nectar into Honey, then pending rows into vectors.

The Honey Store (the Hive's knowledge base) takes raw Nectar in, and the House Bee (the
maintenance Worker) ripens it into Honey: searchable, labelled rows. `Ripener.ripen_pending`
takes up to `[honey.ripening] max_nectar_per_pass` RECEIVED Nectar, oldest first, and for each
one decodes its bytes, chunks the text, summarises it (on the RIPENER model slot, or
heuristically), builds its drafts, drops exact duplicates, embeds the drafts (on the EMBEDDER
slot, when one is bound), drops near duplicates and indexes what is left. A deposit with no text
(binary, or blank) ripens into a SUMMARY row alone. A store refusal, an invalid value or any other
expected failure marks that one Nectar failed (`honey.ripen_failed`), DISCARDED once it has used
`max_attempts`, and the pass moves on: one bad deposit never stops the others. `embed_pending`
then embeds up to `max_embed_per_pass` live rows still lacking a vector for the current model,
which is how a new embedder re-embeds the store progressively (ADR-0032). `run_pass` does both.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Driven by the House Bee's ripening loop beside the Queen, never inside her tick (ADR-0031),
    and by `hive honey ripen --now`/`reembed`. Calls into this package's stage modules
    (`chunk`, `summarise`, `drafts`, `embed`, `dedupe`, `index`) and `hivemind.honey_store`'s
    store, identity and errors.

Key invariants:
    - Every model and embedding call sits under its own timeout (in `summarise` and `embed`), so a
      pass never hangs on a provider; no model failure ever fails a Nectar.
    - A failure of one Nectar is recorded against that Nectar only and never escapes the pass;
      a `HoneyStoreError`, a `NotFoundError` (a Nectar or Honey row gone from under the pass) and
      a `ValueError` (which covers a pydantic ValidationError) count as a Nectar's own failure --
      anything else is a fault of the pass itself and propagates.
    - Every row a pass writes is labelled at least as high as its Nectar was when it was written
      (`hivemind.honey_store.ripening.index`).

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the pipeline and who runs it.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the re-embed rule.
    - hivemind.honey_store.ripening.deps for RipenerDeps, what a pass runs on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.common.errors import HiveMindError, NotFoundError
from hivemind.common.logging import get_logger
from hivemind.honey_store.errors import HoneyStoreError
from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.models import HoneyDraft, Nectar
from hivemind.honey_store.ripening.chunk import chunk_text, decode_text, normalise_text
from hivemind.honey_store.ripening.dedupe import (
    NearDuplicateCheck,
    drop_exact_duplicates,
    drop_near_duplicates,
)
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.drafts import PreparedPart, metadata_draft, ripened_drafts
from hivemind.honey_store.ripening.embed import embed_pending_rows, embed_texts, embedding_text
from hivemind.honey_store.ripening.index import RipenedNectar, index_ripened
from hivemind.honey_store.ripening.summarise import summarise

__all__ = ["PassOutcome", "RipenOutcome", "Ripener"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RipenOutcome:
    """What one `ripen_pending` call did, in counts; sums with `+` across Nectar."""

    ripened: int = 0  # Nectar that became Honey rows.
    failed: int = 0  # Nectar that failed and stay RECEIVED for a later pass.
    discarded: int = 0  # Nectar that failed their last allowed attempt and became DISCARDED.
    rows: int = 0  # Honey rows written.
    deduped: int = 0  # Parts dropped as exact or near duplicates.
    embedded: int = 0  # Rows that got a vector while ripening.

    def __add__(self, other: RipenOutcome) -> RipenOutcome:
        """Return the field-by-field sum of two outcomes."""
        return RipenOutcome(
            ripened=self.ripened + other.ripened,
            failed=self.failed + other.failed,
            discarded=self.discarded + other.discarded,
            rows=self.rows + other.rows,
            deduped=self.deduped + other.deduped,
            embedded=self.embedded + other.embedded,
        )


@dataclass(frozen=True, slots=True)
class PassOutcome:
    """What one whole `run_pass` did: the ripening counts, then the re-embedding count."""

    ripen: RipenOutcome  # What `ripen_pending` did.
    reembedded: int  # Rows `embed_pending` gave a vector for the current embedder's model.


@dataclass(frozen=True, slots=True)
class _Prepared:
    """One Nectar's drafts after summary and exact deduplication, before embedding."""

    drafts: tuple[HoneyDraft, ...]  # SUMMARY first; kept CHUNK drafts after it.
    chunks: int  # How many chunks the text was cut into.
    deduped: int  # Exact duplicates already dropped.
    summarised: bool  # Whether a model wrote the summary.


class Ripener:
    """Turn pending Nectar into Honey and keep every live row embedded for the current model.

    Holds no state between passes: everything a pass needs is in its `RipenerDeps`, and every
    decision about what is pending is the store's, so two Ripeners on one store (the House Bee and
    `hive honey ripen --now`) never duplicate a row (`HoneyStore.ripen` is idempotent).
    """

    def __init__(self, deps: RipenerDeps) -> None:
        """Wire the Ripener to its store, identity, clock, settings and model bindings.

        Args:
            deps: Everything a pass runs on; see RipenerDeps.
        """
        self._deps = deps

    async def run_pass(self) -> PassOutcome:
        """Ripen pending Nectar, then embed rows still pending a vector.

        Returns:
            Both steps' counts.
        """
        ripen = await self.ripen_pending()
        reembedded = await self.embed_pending()
        return PassOutcome(ripen=ripen, reembedded=reembedded)

    async def ripen_pending(self) -> RipenOutcome:
        """Ripen up to `max_nectar_per_pass` RECEIVED Nectar, oldest first.

        Returns:
            The pass's counts: ripened, failed, discarded, rows, deduplicated and embedded.
        """
        # Local SQLite, bounded by the connection's busy timeout.
        pending = await self._deps.store.pending_nectar(self._deps.ripening.max_nectar_per_pass)
        outcome = RipenOutcome()
        # One Nectar at a time; each one's own failure is recorded and the loop carries on.
        for nectar in pending:
            try:
                outcome = outcome + await self._ripen_one(nectar)
            except (HoneyStoreError, NotFoundError, ValueError) as error:
                outcome = outcome + await self._mark_failed(nectar, error)
        return outcome

    async def embed_pending(self) -> int:
        """Embed up to `max_embed_per_pass` live rows lacking a vector for the embedder's model.

        Returns:
            How many rows got a vector; 0 with no embedder bound or when the embedder failed.
        """
        return await embed_pending_rows(self._deps)

    async def _ripen_one(self, nectar: Nectar) -> RipenOutcome:
        """Ripen one Nectar end to end: decode, chunk, summarise, dedupe, embed, index."""
        deps = self._deps
        # Local SQLite, bounded by the connection's busy timeout.
        content = await deps.store.nectar_content(nectar.id)
        prepared = await self._prepare(nectar, content)
        parts, near_duplicates, model = await self._embed_and_filter(nectar, prepared.drafts)
        ripened = RipenedNectar(
            nectar=nectar,
            parts=parts,
            clearance=prepared.drafts[0].clearance,
            chunks=prepared.chunks,
            deduped=prepared.deduped + near_duplicates,
            summarised=prepared.summarised,
            embed_model=model,
        )
        result = await index_ripened(deps, ripened)
        # Another runner finished this Nectar first: nothing was written, nothing to count.
        if not result.is_stored:
            return RipenOutcome()
        return RipenOutcome(
            ripened=1, rows=len(result.rows), deduped=ripened.deduped, embedded=result.embedded
        )

    async def _prepare(self, nectar: Nectar, content: bytes) -> _Prepared:
        """Decode, chunk and summarise one Nectar into its drafts, exact duplicates dropped."""
        decoded = decode_text(content, nectar.media_type)
        text = normalise_text(decoded) if decoded is not None else ""
        # No text to chunk or summarise (binary, or only whitespace): one descriptive SUMMARY.
        if not text:
            return _Prepared(
                drafts=(metadata_draft(nectar, is_binary=decoded is None),),
                chunks=0,
                deduped=0,
                summarised=False,
            )
        settings = self._deps.ripening
        chunks = chunk_text(text, settings.chunk_chars, settings.chunk_overlap_chars)
        summary = await summarise(nectar, text, self._deps)
        drafts, exact_duplicates = drop_exact_duplicates(ripened_drafts(summary, chunks))
        return _Prepared(
            drafts=drafts,
            chunks=len(chunks),
            deduped=exact_duplicates,
            summarised=summary.summarised,
        )

    async def _embed_and_filter(
        self, nectar: Nectar, drafts: Sequence[HoneyDraft]
    ) -> tuple[tuple[PreparedPart, ...], int, str | None]:
        """Embed `drafts` and drop near duplicates; without vectors, keep every draft as it is.

        Returns:
            The parts to store, how many near duplicates were dropped, and the model the vectors
            came from (None when there are no vectors: no embedder, or it failed).
        """
        deps = self._deps
        embedder = deps.embedder
        unembedded = tuple(PreparedPart(draft=draft, vector=None) for draft in drafts)
        if embedder is None:
            return unembedded, 0, None
        texts = [embedding_text(draft) for draft in drafts]
        vectors = await embed_texts(
            texts, embedder, deps.embedding_gate(), deps.ripening.embed_batch
        )
        # The embedder failed: store the rows anyway; embed_pending retries them, this pass and on.
        if vectors is None:
            return unembedded, 0, None
        parts = tuple(
            PreparedPart(draft=draft, vector=vector)
            for draft, vector in zip(drafts, vectors, strict=True)
        )
        check = NearDuplicateCheck(
            store=deps.store,
            model=embedder.model,
            scope=nectar.scope,
            threshold=deps.ripening.near_duplicate_similarity,
        )
        kept, near_duplicates = await drop_near_duplicates(check, parts)
        return kept, near_duplicates, embedder.model

    async def _mark_failed(self, nectar: Nectar, error: Exception) -> RipenOutcome:
        """Record one Nectar's failed attempt (`honey.ripen_failed`), discarding it at the cap."""
        attempts = nectar.ripen_attempts + 1
        discard = attempts >= self._deps.ripening.max_attempts
        code = error.code if isinstance(error, HiveMindError) else type(error).__name__
        # The error's code or class only: its message may quote the deposit's own text.
        log.warning(
            "honey_store.ripen_failed",
            nectar_id=nectar.id,
            error=code,
            attempts=attempts,
            discarded=discard,
        )
        event = honey_event(
            self._deps.identity,
            self._deps.clock,
            "honey.ripen_failed",
            nectar.id,
            error=code,
            attempts=attempts,
            discarded=discard,
        )
        try:
            # Local SQLite, bounded by the connection's busy timeout.
            await self._deps.store.mark_nectar_failed(nectar.id, discard=discard, event=event)
        except (HoneyStoreError, NotFoundError) as mark_error:
            # The row vanished under us (a purge, another runner): nothing left to mark, and the
            # pass must still carry on to the next Nectar.
            log.warning(
                "honey_store.ripen_failure_unrecorded", nectar_id=nectar.id, error=mark_error.code
            )
        return RipenOutcome(discarded=1) if discard else RipenOutcome(failed=1)
