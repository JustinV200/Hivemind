"""Test hivemind.entrance.help.door: budget first, then the passages, then the model or the links.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/door.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance.help import help_services

from hivemind.entrance.help import HelpOutcome, QuestionOverBudgetError, ask
from hivemind.llm import FakeLLMProvider, ProviderUnavailableError, text_response
from waggle.clock import FakeClock

_SESSION = "session-hash"


async def test_a_model_answers_from_the_passages_and_the_cited_ones_are_marked() -> None:
    provider = FakeLLMProvider()
    provider.script(text_response("One supervisor per Cell [1]."))
    services = help_services(provider, FakeClock(), questions_per_minute=1)

    view = await ask(services, _SESSION, "What does a Warden do?")

    assert (view.outcome, view.answer, view.slot) == (
        HelpOutcome.ANSWERED,
        "One supervisor per Cell [1].",
        "ATTENDANT",
    )
    # The entry the question names comes first, and is the one the answer cites.
    assert (view.sources[0].page, view.sources[0].section, view.sources[0].cited) == (
        "glossary",
        "warden",
        True,
    )
    assert not any(s.cited for s in view.sources[1:])
    # The budget is charged before anything runs: the second question is refused unanswered.
    with pytest.raises(QuestionOverBudgetError):
        await ask(services, _SESSION, "What does a Warden do?")
    assert len(provider.calls) == 1


async def test_without_a_model_the_passages_are_the_answer_as_links() -> None:
    view = await ask(help_services(None), _SESSION, "How do I sort the Fleet?")

    assert (view.outcome, view.answer, view.slot) == (HelpOutcome.NO_MODEL, None, None)
    assert (view.sources[0].page, view.sources[0].section) == ("screens", "fleet")
    assert view.sources[0].snippet.startswith("Every Cell in one table.")
    assert not any(s.cited for s in view.sources)


async def test_a_failed_model_leaves_the_passages_standing() -> None:
    provider = FakeLLMProvider()
    provider.script(ProviderUnavailableError("fake", "down"))

    view = await ask(help_services(provider), _SESSION, "How do I sort the Fleet?")

    assert (view.outcome, view.answer, view.slot) == (HelpOutcome.MODEL_FAILED, None, "ATTENDANT")
    assert view.sources[0].section == "fleet"


async def test_a_question_no_page_matches_runs_no_model() -> None:
    provider = FakeLLMProvider()

    view = await ask(help_services(provider), _SESSION, "xyzzy plugh")

    assert (view.outcome, view.answer, view.sources, view.slot) == (
        HelpOutcome.NOTHING_FOUND,
        None,
        (),
        None,
    )
    assert provider.calls == []
