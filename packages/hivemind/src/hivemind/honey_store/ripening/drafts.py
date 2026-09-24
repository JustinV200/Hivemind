"""Build the HoneyDrafts one Nectar ripens into: a SUMMARY part and one CHUNK part per chunk (pure).

Ripening turns Nectar (a raw deposit in the Honey Store, the Hive's knowledge base) into Honey
rows (ADR-0031): one SUMMARY row whose title is the summary's and whose body is the summary text
itself, and one CHUNK row per chunk of the text whose title is that title plus `(part i of n)`,
whose summary repeats the SUMMARY's text as context for the chunk, and whose body is the chunk.
A deposit with no text -- binary, or text that is only whitespace -- ripens into a SUMMARY row
alone that says what it is (media type and size), so it is still listed and findable by title.
`PreparedPart` pairs a draft with the vector embedding produced for it, the shape the rest of the
pipeline passes along.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by `hivemind.honey_store.ripening.pipeline.Ripener`; the drafts go on to
    `.dedupe`, `.embed` and `.index`. Calls into `hivemind.honey_store.models` and this package's
    `chunk` and `summarise` modules only; no I/O.

Key invariants:
    - The SUMMARY draft is always first and always `chunk_index` 0; CHUNK drafts follow in text
      order with `chunk_index` equal to the chunk's own index, so `(nectar, part, chunk_index)`
      is unique per draft and re-ripening is idempotent (`HoneyStore.ripen`).
    - Every draft carries the outcome's clearance (the Nectar's label, raised by the summary if
      it raised it) and fits every HoneyDraft bound: titles, summaries and bodies are never cut
      by pydantic's own validation.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the SUMMARY/CHUNK row shape.
    - hivemind.honey_store.models.honey for HoneyDraft and its bounds.
    - hivemind.honey_store.ripening.summarise for SummaryOutcome, the summary these drafts carry.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.honey_store.models import HoneyDraft, HoneyPart, Nectar
from hivemind.honey_store.ripening.chunk import TextChunk
from hivemind.honey_store.ripening.summarise import SummaryOutcome, heuristic_title
from waggle.messages.honey.exchange import MAX_TITLE_CHARS

SUMMARY_CHUNK_INDEX = 0  # A SUMMARY row's chunk_index; HoneyDraft's own convention.

__all__ = [
    "SUMMARY_CHUNK_INDEX",
    "PreparedPart",
    "metadata_draft",
    "part_title",
    "ripened_drafts",
]


@dataclass(frozen=True, slots=True)
class PreparedPart:
    """One draft on its way to the store, with the vector embedded for it, if any."""

    draft: HoneyDraft  # The part to store.
    vector: tuple[float, ...] | None  # Its embedding, or None when no vector is available.


def ripened_drafts(outcome: SummaryOutcome, chunks: Sequence[TextChunk]) -> tuple[HoneyDraft, ...]:
    """Build a text Nectar's drafts: its SUMMARY part first, then one CHUNK part per chunk.

    Args:
        outcome: The deposit's summary, title, label and ripener model.
        chunks: Its text's chunks, in order (`hivemind.honey_store.ripening.chunk.chunk_text`).

    Returns:
        `(summary, chunk_0, chunk_1, ...)`, every draft at `outcome.clearance`.
    """
    summary = HoneyDraft(
        part=HoneyPart.SUMMARY,
        chunk_index=SUMMARY_CHUNK_INDEX,
        title=outcome.title,
        summary=outcome.summary_text,
        body=outcome.summary_text,
        clearance=outcome.clearance,
        ripener_model=outcome.ripener_model,
    )
    parts = tuple(
        HoneyDraft(
            part=HoneyPart.CHUNK,
            chunk_index=chunk.index,
            title=part_title(outcome.title, chunk.index, len(chunks)),
            summary=outcome.summary_text,
            body=chunk.text,
            clearance=outcome.clearance,
            ripener_model=outcome.ripener_model,
        )
        for chunk in chunks
    )
    return (summary, *parts)


def metadata_draft(nectar: Nectar, *, is_binary: bool) -> HoneyDraft:
    """Build the one SUMMARY draft for a Nectar with no text to chunk: what it is, not what it says.

    Args:
        nectar: The deposit being ripened.
        is_binary: True when its media type is not textual; False when it was text holding only
            whitespace.

    Returns:
        A SUMMARY draft naming the deposit's media type and size, at the Nectar's own label.
    """
    what = "Binary content" if is_binary else "Text content with no words in it"
    description = f"{what}: {nectar.media_type}, {nectar.size_bytes} bytes."
    return HoneyDraft(
        part=HoneyPart.SUMMARY,
        chunk_index=SUMMARY_CHUNK_INDEX,
        title=heuristic_title(nectar, ""),
        summary=description,
        body=description,
        clearance=nectar.clearance,
        ripener_model=None,
    )


def part_title(title: str, index: int, count: int) -> str:
    """Title one CHUNK part: the summary's title plus `(part i of n)`, within the title bound.

    Args:
        title: The deposit's summary title.
        index: The chunk's 0-based index.
        count: How many chunks the text has.

    Returns:
        `"<title> (part <index + 1> of <count>)"`, the title shortened so the whole fits
        `MAX_TITLE_CHARS`.
    """
    suffix = f" (part {index + 1} of {count})"
    return f"{title[: MAX_TITLE_CHARS - len(suffix)].rstrip()}{suffix}"
