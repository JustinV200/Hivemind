"""Define HelpRetriever and its two implementations: find the help passages a question is about.

The help's Ask (ADR-0069) grounds every answer in passages from the help pages, so the retriever
is where a question meets the corpus. ``KeywordRetriever`` ranks sections with the BM25 index
(``rank``) and needs no model. ``EmbeddingRetriever`` uses the Hive's own ``EMBEDDER`` slot (the
non-chat slot that turns text into vectors, ADR-0036), through the Fanner's lane so every embed
call is metered and recorded like any other: it embeds every section once, on the first question
and never at start-up (a hosted embedder would be paid for on every serve otherwise), keeps the
vectors for the life of the process, embeds each question, and ranks by cosine similarity. When
the embedder fails for any reason (a provider down, offline mode, a request refused) it answers
from the keyword ranker instead, so a question is never left unanswered because a model was: the
embedder improves the Ask, it never gates it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help.retrieval``. Built
    by ``hivemind.cli.compose.help``; called by the help door for every question. Calls into
    ``hivemind.llm`` (the embedding boundary and its errors) and this package's own modules.

Key invariants:
    - A retriever returns at most ``limit`` passages, best first, never one with a score of zero
      or below (keyword: no shared word; embedding: no positive similarity).
    - The section vectors are computed at most once per retriever, under a lock, so concurrent
      first questions never embed the corpus twice.
    - An ``LLMError`` from the embedder is logged by its class only and answered from keywords;
      nothing of the question or the passages reaches a log line.

See Also:
    - hivemind.llm.embedding for the EmbedGate seam the embedding retriever calls through.
    - hivemind.entrance.help.retrieval.rank for the keyword ranker.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.entrance.help.retrieval.corpus import HelpCorpus, HelpSection
from hivemind.entrance.help.retrieval.rank import HEADING_WEIGHT, KeywordIndex
from hivemind.llm import (
    MAX_EMBED_TEXTS,
    BoundEmbedder,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbedGate,
    LLMError,
)

log = get_logger(__name__)

__all__ = ["EmbeddingRetriever", "HelpRetriever", "KeywordRetriever", "Passage"]


@dataclass(frozen=True, slots=True)
class Passage:
    """One retrieved section and how well it matched.

    Attributes:
        section: The section.
        score: The ranker's score; BM25 or cosine, comparable only within one retriever's answer.
    """

    section: HelpSection
    score: float


class HelpRetriever(Protocol):
    """Find the passages a question is about, best first."""

    async def retrieve(self, question: str, limit: int) -> tuple[Passage, ...]:
        """Return up to ``limit`` passages for ``question``, best first; none when nothing fits.

        Args:
            question: The person's words, untrusted text.
            limit: The most passages to return; >= 1.

        Returns:
            The passages, best first; empty when no section matches at all.
        """
        ...


def _document_of(section: HelpSection) -> str:
    """The words a section is indexed or embedded by: its heading, weighted, then its text."""
    return " ".join([*([section.heading] * HEADING_WEIGHT), section.text]).strip()


class KeywordRetriever:
    """Rank sections by BM25 over their words; needs no model and never fails."""

    def __init__(self, corpus: HelpCorpus) -> None:
        """Index ``corpus`` once.

        Args:
            corpus: The help sections.
        """
        self._sections = corpus.sections
        self._index = KeywordIndex(
            [_document_of(section) for section in self._sections],
            [section.heading for section in self._sections],
        )

    async def retrieve(self, question: str, limit: int) -> tuple[Passage, ...]:
        """Rank by shared words; see ``HelpRetriever.retrieve``."""
        return tuple(
            Passage(self._sections[index], score)
            for index, score in self._index.rank(question, limit)
        )


class EmbeddingRetriever:
    """Rank sections by cosine similarity on the EMBEDDER slot, falling back to keywords."""

    def __init__(
        self, corpus: HelpCorpus, bound: BoundEmbedder, gate: EmbedGate, fallback: KeywordRetriever
    ) -> None:
        """Hold the corpus and the embedder; embed nothing yet.

        Args:
            corpus: The help sections.
            bound: The EMBEDDER slot's resolved binding.
            gate: What the embed calls go through; the Fanner's lane at the composition root.
            fallback: Answers when the embedder cannot.
        """
        self._sections = corpus.sections
        self._bound = bound
        self._gate = gate
        self._fallback = fallback
        self._vectors: tuple[tuple[float, ...], ...] | None = None
        # Guards the one-time embedding of the corpus: two first questions at once must not
        # both pay for it.
        self._lock = asyncio.Lock()

    async def retrieve(self, question: str, limit: int) -> tuple[Passage, ...]:
        """Rank by vectors, or by words when the embedder fails; see ``HelpRetriever.retrieve``."""
        try:
            vectors = await self._corpus_vectors()
            [asked] = (await self._embed([question])).vectors
        except LLMError as exc:
            # The class alone: a provider's message may name its server, never the question.
            log.warning("entrance.help_embedding_failed", error=type(exc).__name__)
            return await self._fallback.retrieve(question, limit)
        scored = [
            (index, similarity)
            for index, vector in enumerate(vectors)
            if (similarity := _cosine(asked, vector)) > 0.0
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return tuple(Passage(self._sections[index], score) for index, score in scored[:limit])

    async def _corpus_vectors(self) -> tuple[tuple[float, ...], ...]:
        """Embed every section once, in batches the request shape allows; cached afterwards."""
        async with self._lock:
            if self._vectors is not None:
                return self._vectors
            documents = [_document_of(section) for section in self._sections]
            vectors: list[tuple[float, ...]] = []
            # Batched to the request's own ceiling, so a corpus of any size goes through.
            for start in range(0, len(documents), MAX_EMBED_TEXTS):
                response = await self._embed(documents[start : start + MAX_EMBED_TEXTS])
                vectors.extend(response.vectors)
            self._vectors = tuple(vectors)
            return self._vectors

    async def _embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        """One embed call through the gate; bounded by the provider's own timeout."""
        limit = self._bound.provider.capabilities.max_input_chars
        # A section longer than the embedder reads is cut, not refused: its opening words rank it.
        request = EmbeddingRequest(texts=tuple(text[:limit] or " " for text in texts))
        # External await: the embedder's seat queue and the call itself, seconds at most for a
        # local model; the adapter owns the timeout, and a failure is an LLMError the caller
        # answers from keywords.
        return await self._gate.embed(self._bound, request)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors; zero when either has no length."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 0.0
