"""Test hivemind.entrance.help.models: the query's bounds, and the view's round trip.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.entrance.help import (
    MAX_QUESTION_CHARS,
    HelpAnswerView,
    HelpAskQuery,
    HelpOutcome,
    HelpSourceView,
)


@pytest.mark.parametrize(
    "query",
    [{"q": "hi"}, {"q": "x" * (MAX_QUESTION_CHARS + 1)}, {}, {"q": "night veil", "stray": "1"}],
    ids=["short", "long", "missing", "stray"],
)
def test_the_query_refuses_what_it_does_not_define(query: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HelpAskQuery.model_validate(query)


def test_the_view_round_trips_and_an_unanswered_one_carries_no_words() -> None:
    source = HelpSourceView(
        page="glossary",
        section="night-veil",
        heading="Night Veil",
        snippet="The sealed",
        cited=True,
    )
    view = HelpAnswerView(
        question="What is the Night Veil?",
        outcome=HelpOutcome.ANSWERED,
        answer="The sealed tier [1].",
        sources=(source,),
        slot="ATTENDANT",
    )

    again = HelpAnswerView.model_validate_json(view.model_dump_json())

    assert again == view
    assert again.model_dump(mode="json")["outcome"] == "ANSWERED"
    with pytest.raises(ValidationError):
        HelpAnswerView.model_validate({**view.model_dump(), "extra": 1})
