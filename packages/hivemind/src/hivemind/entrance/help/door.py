"""Answer one help question at the door: the budget, the passages, then the model or the links.

The help's Ask (ADR-0070) runs every question through one path. The session's question budget is
charged first, so a session over it costs nothing. The retriever then finds the passages the
question is about; with none, the answer is ``NOTHING_FOUND`` and no model runs, since a model
with nothing to read could only guess. With passages and no answerer (the manifest binds no chat
slot) the passages are the answer, as links into the Help screen (``NO_MODEL``). Otherwise the
answerer makes its one call: its words, with the passages it cited marked, are ``ANSWERED``; a
failure leaves the passages standing as ``MODEL_FAILED``. The question is never kept, scanned or
logged: it reaches a model only as labelled data, and a model with no tools can do nothing with
it but answer.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Called by the
    help route. Calls into this package's services, retrieval, answerer and models.

Key invariants:
    - No model runs for a question over the budget or one no page matches.
    - Exactly one model call per answered question; a failed one is a view, never a retry.

See Also:
    - hivemind.entrance.help.answer for the call.
"""

from __future__ import annotations

from hivemind.entrance.help.answer import cited_indexes
from hivemind.entrance.help.errors import QuestionOverBudgetError
from hivemind.entrance.help.models import HelpAnswerView, HelpOutcome, HelpSourceView, source_view
from hivemind.entrance.help.retrieval import Passage
from hivemind.entrance.help.services import HelpServices

TOP_PASSAGES = 4  # Enough to cover a question's subject; few enough for the cheapest model.

__all__ = ["TOP_PASSAGES", "ask"]


async def ask(services: HelpServices, session_key: str, question: str) -> HelpAnswerView:
    """Answer ``question`` for the session ``session_key`` names.

    Args:
        services: The help's retriever, answerer and budget.
        session_key: What names the asking session (its token's hash), for the budget.
        question: The person's words, already bounded by the route's query model.

    Returns:
        The answer, or the passages alone, with the outcome saying which.

    Raises:
        QuestionOverBudgetError: The session has asked its questions for the minute.
    """
    # Charged before anything runs: a session over its budget never reaches a model.
    if not services.budget.allow(session_key):
        raise QuestionOverBudgetError()
    # Latency: in memory for the keyword ranker; one metered embed call for the embedding one.
    passages = await services.retriever.retrieve(question, TOP_PASSAGES)
    if not passages:
        return _view(question, HelpOutcome.NOTHING_FOUND, None, (), None)
    answerer = services.answerer
    if answerer is None:
        return _view(question, HelpOutcome.NO_MODEL, None, _sources(passages, frozenset()), None)
    # Latency: the one model call, bounded by the answerer's own deadline.
    text = await answerer.answer(question, passages)
    slot = answerer.slot.value
    if text is None:
        return _view(
            question, HelpOutcome.MODEL_FAILED, None, _sources(passages, frozenset()), slot
        )
    sources = _sources(passages, cited_indexes(text))
    return _view(question, HelpOutcome.ANSWERED, text, sources, slot)


def _sources(passages: tuple[Passage, ...], cited: frozenset[int]) -> tuple[HelpSourceView, ...]:
    """Shape the passages as sources, numbered from 1 as the answer's marks count them."""
    return tuple(
        source_view(passage, number in cited) for number, passage in enumerate(passages, start=1)
    )


def _view(
    question: str,
    outcome: HelpOutcome,
    answer: str | None,
    sources: tuple[HelpSourceView, ...],
    slot: str | None,
) -> HelpAnswerView:
    """Build the view the route answers with."""
    return HelpAnswerView(
        question=question, outcome=outcome, answer=answer, sources=sources, slot=slot
    )
