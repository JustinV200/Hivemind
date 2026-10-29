"""Define the help Ask's Landing Board shapes: the question a device sends and the answer it gets.

A question arrives on ``GET /v1/help/ask?q=`` (ADR-0069), bounded here so a page of text is a
422 before anything runs. The Hive answers with ``HelpAnswerView``: the question as asked, the
model's words when a model answered, an ``outcome`` saying in one word what happened (so the
screen never has to infer it from an empty field), the ``sources`` the answer was grounded in,
each with the page and section the Help screen lands on and whether the answer cited it, and the
slot the call was filed under for the Cost view's words. Both models forbid unknown fields.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Used by the
    help route and door; published in the OpenAPI document by ``hivemind.entrance.landing_board``.
    Calls into pydantic and this package's retrieval shapes.

Key invariants:
    - A source names a page and, where the passage is a section, its heading id: exactly what
      ``#/help/<page>?s=<section>`` takes.
    - ``answer`` is None exactly when ``outcome`` is not ``ANSWERED``.

See Also:
    - hivemind.entrance.help.door for what builds a HelpAnswerView.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.help.retrieval import Passage

MIN_QUESTION_CHARS = 3  # Shorter than a word is not a question.
MAX_QUESTION_CHARS = 500  # A question, not a document; the model reads it as data either way.
SNIPPET_CHARS = 200  # How much of a passage a source shows, so links alone still say something.
_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Immutable, no strays.

__all__ = [
    "MAX_QUESTION_CHARS",
    "MIN_QUESTION_CHARS",
    "SNIPPET_CHARS",
    "HelpAnswerView",
    "HelpAskQuery",
    "HelpOutcome",
    "HelpSourceView",
    "source_view",
]


class HelpOutcome(Enum):
    """What the Ask did with a question, in one word the screen shows as is."""

    ANSWERED = "ANSWERED"  # A model answered from the sources.
    NO_MODEL = "NO_MODEL"  # No chat binding is configured: the sources are the answer.
    MODEL_FAILED = "MODEL_FAILED"  # The model failed or ran out of time: the sources stand.
    NOTHING_FOUND = "NOTHING_FOUND"  # No page matched the question at all.


class HelpAskQuery(BaseModel):
    """``GET /v1/help/ask``'s query: the question."""

    model_config = _CONFIG

    q: str = Field(
        min_length=MIN_QUESTION_CHARS,
        max_length=MAX_QUESTION_CHARS,
        repr=False,
        description="The question, in the person's own words; read as data, never as an "
        "instruction, and never kept.",
    )


class HelpSourceView(BaseModel):
    """One help passage an answer was grounded in, addressed the way the Help screen is."""

    model_config = _CONFIG

    page: str = Field(description="The help page's id, as #/help/<page> names it.")
    section: str | None = Field(
        description="The heading's id, as ?s=<section> names it; null for the page's opening."
    )
    heading: str = Field(description="The heading's words; empty for the page's opening.")
    snippet: str = Field(description="The passage's first words, cut short.")
    cited: bool = Field(description="Whether the answer cites this passage by its number.")


class HelpAnswerView(BaseModel):
    """What the Ask answered: the words, or the passages alone, and why."""

    model_config = _CONFIG

    question: str = Field(description="The question as it was asked.")
    outcome: HelpOutcome = Field(description="What happened, in one word.")
    answer: str | None = Field(
        description="The model's words, grounded in the sources; null unless ANSWERED."
    )
    sources: tuple[HelpSourceView, ...] = Field(
        description="The passages retrieved for the question, best first, numbered from 1 as "
        "the answer's [n] marks count them; empty when NOTHING_FOUND."
    )
    slot: str | None = Field(
        description="The ModelSlot the answer's call was filed under (the Cost view's row); "
        "null when no model ran."
    )


def source_view(passage: Passage, cited: bool) -> HelpSourceView:
    """Shape one retrieved passage as the screen shows it.

    Args:
        passage: The passage.
        cited: Whether the answer cites it.

    Returns:
        The view: page, section, heading, a snippet and the cited flag.
    """
    section = passage.section
    return HelpSourceView(
        page=section.page,
        section=section.section,
        heading=section.heading,
        snippet=section.text[:SNIPPET_CHARS],
        cited=cited,
    )
