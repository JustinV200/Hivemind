"""Store one ripened Nectar: its Honey rows and `honey.ripened` event, its vectors, any label raise.

The last step of ripening (the House Bee's turning of Nectar, raw deposits in the Honey Store,
into Honey, searchable rows). `index_ripened` first re-reads the Nectar, because a summary can
take tens of seconds and intake may have deduplicated a more sensitive copy onto it meanwhile:
every part is raised to the Nectar's label as it stands now, and a Nectar another runner already
ripened (`hive honey ripen --now` beside the House Bee) is left alone. Then `HoneyStore.ripen`
writes every part and the `honey.ripened` event in one transaction; `set_vectors` stores each
part's non-zero vector tagged with the embedding model that made it; and when the summary raised
the label above the Nectar's own, a `honey.label_raised` event records that. The rows already
carry the raised label when that event is written, so a failure between the two can only lose
the note of a raise, never the raise itself.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by `hivemind.honey_store.ripening.pipeline.Ripener` once per Nectar that ripens.
    Calls into `hivemind.honey_store` (clearance, identity, models, store) and this package's
    `deps`, `drafts` and `embed` modules only.

Key invariants:
    - No part is ever stored below the Nectar's current label (`raise_label`), however stale the
      label the pipeline started from.
    - Every event payload is ids, counts and flags only (rows, chunks, deduped, summarised,
      embedded; `from`/`to` clearance names), never a title or text.
    - A zero-norm vector is never handed to the store; its row stays pending for `embed_pending`.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the ripening and labelling rules.
    - hivemind.honey_store.store.protocol for ripen, set_vectors, get_nectar and record.
    - hivemind.honey_store.clearance for raise_label.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.honey_store.clearance import raise_label
from hivemind.honey_store.identity import honey_event
from hivemind.honey_store.models import Honey, Nectar, NectarState
from hivemind.honey_store.ripening.deps import RipenerDeps
from hivemind.honey_store.ripening.drafts import PreparedPart
from hivemind.honey_store.ripening.embed import is_zero_vector

__all__ = ["IndexResult", "RipenedNectar", "index_ripened"]


@dataclass(frozen=True, slots=True)
class RipenedNectar:
    """One Nectar's parts, ready to store, and the counts its `honey.ripened` event reports."""

    nectar: Nectar  # The Nectar as the pass read it (its label may since have been raised).
    parts: tuple[PreparedPart, ...]  # SUMMARY first, then the kept CHUNK parts, with vectors.
    clearance: HoneyClearance  # The label the summary step decided (the Nectar's, or higher).
    chunks: int  # How many chunks the text was cut into, before deduplication.
    deduped: int  # How many parts were dropped as exact or near duplicates.
    summarised: bool  # Whether a model wrote the summary.
    embed_model: str | None  # The model the vectors came from; None when there are none.


@dataclass(frozen=True, slots=True)
class IndexResult:
    """What indexing one Nectar wrote."""

    rows: tuple[Honey, ...]  # The Honey rows ripened from it, in part order.
    embedded: int  # How many of them got a vector in this step.
    is_stored: bool  # False when the Nectar was no longer waiting to ripen; nothing was written.


async def index_ripened(deps: RipenerDeps, ripened: RipenedNectar) -> IndexResult:
    """Write one Nectar's parts, vectors and events, at no less than its current label.

    Args:
        deps: The store, identity and clock the writes and events use.
        ripened: The parts to store and the counts to report.

    Returns:
        The stored rows and how many got a vector; `is_stored=False` (and nothing written) when
        the Nectar is no longer RECEIVED because another runner ripened or discarded it first.

    Raises:
        NectarNotFoundError: The Nectar row no longer exists.
    """
    # Local SQLite reads and writes below, each bounded by the connection's busy timeout.
    current = await deps.store.get_nectar(ripened.nectar.id)
    if current.state is not NectarState.RECEIVED:
        return IndexResult(rows=(), embedded=0, is_stored=False)
    label = raise_label(ripened.clearance, current.clearance)
    parts = tuple(_at_label(part, label) for part in ripened.parts)
    with_vectors = [
        part for part in parts if part.vector is not None and not is_zero_vector(part.vector)
    ]
    event = honey_event(
        deps.identity,
        deps.clock,
        "honey.ripened",
        current.id,
        rows=len(parts),
        chunks=ripened.chunks,
        deduped=ripened.deduped,
        summarised=ripened.summarised,
        embedded=len(with_vectors) if ripened.embed_model is not None else 0,
    )
    rows = await deps.store.ripen(current.id, tuple(part.draft for part in parts), event)
    embedded = await _store_vectors(deps, rows, parts, ripened.embed_model)
    # The summary judged the deposit more sensitive than its label: the rows already carry the
    # raise, so this event only records it.
    if label.rank > current.clearance.rank:
        raised = honey_event(
            deps.identity,
            deps.clock,
            "honey.label_raised",
            current.id,
            **{"from": current.clearance.value, "to": label.value},
        )
        await deps.store.record(raised)
    return IndexResult(rows=rows, embedded=embedded, is_stored=True)


def _at_label(part: PreparedPart, label: HoneyClearance) -> PreparedPart:
    """Return `part` with its draft raised to `label` (a copy; drafts are frozen)."""
    if part.draft.clearance is label:
        return part
    draft = part.draft.model_copy(update={"clearance": raise_label(part.draft.clearance, label)})
    return PreparedPart(draft=draft, vector=part.vector)


async def _store_vectors(
    deps: RipenerDeps,
    rows: tuple[Honey, ...],
    parts: tuple[PreparedPart, ...],
    model: str | None,
) -> int:
    """Store every part's non-zero vector against its new row, tagged with `model`; return count."""
    if model is None:
        return 0  # No embedder answered: every row stays pending for a later pass.
    # `ripen` returns rows in the order of the drafts it was given, so rows and parts pair up.
    pairs = [
        (row.id, part.vector)
        for row, part in zip(rows, parts, strict=True)
        if part.vector is not None and not is_zero_vector(part.vector)
    ]
    if not pairs:
        return 0
    return await deps.store.set_vectors(pairs, model, None)
