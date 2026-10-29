"""Test hivemind.entrance.help.retrieval.retriever: the right page, by words or by vectors.

Over the real help pages the front end ships, the keyword retriever finds the glossary's Night
Veil entry, the screens page's Fleet section and the Help section for a question about the tour.
The embedding retriever ranks by the vectors its gate returns, embeds the corpus once however
many questions are asked, and answers from keywords when the embedder fails.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/retrieval/retriever.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.entrance.help import PAGES
from builders.llm import make_bound_embedder

from hivemind.entrance.help.retrieval import (
    EmbeddingRetriever,
    HelpRetriever,
    KeywordRetriever,
    corpus_from_pages,
    load_help_corpus,
)
from hivemind.llm import (
    BoundEmbedder,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderUnavailableError,
    Usage,
)

# The pages the front end bundles: tests/unit/entrance/help/retrieval -> the checkout.
REAL_PAGES = Path(__file__).resolve().parents[7] / "packages/observation-web/src/views/help/content"


@pytest.mark.parametrize(
    ("question", "page", "section"),
    [
        ("What is the Night Veil?", "glossary", "night-veil"),
        ("How do I sort the Fleet table?", "screens", "fleet"),
        ("How do I take the tour?", "start", "the-tour"),
        ("What is a Warden?", "glossary", "warden"),
    ],
)
async def test_keywords_find_the_section_about_the_question_in_the_real_pages(
    question: str, page: str, section: str
) -> None:
    retriever = KeywordRetriever(load_help_corpus(REAL_PAGES))

    passages = await retriever.retrieve(question, limit=3)

    assert (passages[0].section.page, passages[0].section.section) == (page, section)
    assert await retriever.retrieve("xyzzy plugh", limit=3) == ()


class _ScriptedGate:
    """An EmbedGate that maps a text about the veil to one axis and everything else to another."""

    def __init__(self, *, down: bool = False) -> None:
        self.calls = 0
        self.texts: list[str] = []
        self.down = down

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls += 1
        if self.down:
            raise ProviderUnavailableError(bound.provider.name, "scripted outage")
        self.texts.extend(request.texts)
        vectors = tuple(
            (1.0, 0.0) if "veil" in text.lower() else (0.0, 1.0) for text in request.texts
        )
        return EmbeddingResponse(
            vectors=vectors, model=bound.model, dimensions=2, usage=Usage.zero()
        )


def _embedding(gate: _ScriptedGate) -> HelpRetriever:
    corpus = corpus_from_pages(PAGES)
    return EmbeddingRetriever(corpus, make_bound_embedder(), gate, KeywordRetriever(corpus))


async def test_vectors_rank_the_closest_section_and_the_corpus_is_embedded_once() -> None:
    gate = _ScriptedGate()
    retriever = _embedding(gate)

    first = await retriever.retrieve("the veil?", limit=2)
    second = await retriever.retrieve("the veil again", limit=2)

    assert (first[0].section.page, first[0].section.section) == ("glossary", "night-veil")
    assert [p.section.section for p in second] == ["night-veil"]  # Nothing else is similar.
    # One call for the whole corpus (five sections, within one batch) and one per question.
    assert gate.calls == 3
    assert len([t for t in gate.texts if "sealed tier" in t]) == 1


async def test_a_failing_embedder_falls_back_to_keywords() -> None:
    retriever = _embedding(_ScriptedGate(down=True))

    passages = await retriever.retrieve("how do I sort the table", limit=2)

    assert (passages[0].section.page, passages[0].section.section) == ("screens", "fleet")
