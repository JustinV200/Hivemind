"""Answer one help question from its passages on the cheapest chat binding, with no tools at all.

The help's Ask (ADR-0069) is a reader, not a bee: it holds no tools, takes no action, keeps no
transcript, and may say only what the retrieved passages say. ``render_help_prompt`` assembles
the one prompt from the shipped ``help_answer`` asset (``hivemind.llm.prompts``): the passages as
a numbered, labelled RETRIEVED section and the question as a USER section, both with their
delimiters neutralised, so the question and the pages are data to the model and never
instructions (codingrules section 15). ``HelpAnswerer`` makes the call through a ``CallGate``, the
Fanner's lane at the composition root, so the call is metered, priced and recorded as an
``llm.call`` on the trail like every other model call the Hive makes, and the Cost view counts
it. A model that fails, times out or refuses gives None: the door then answers with the passages
alone rather than a guess. ``cited_indexes`` reads the ``[n]`` marks the prompt asks for, so the
answer's sources can say which passages it drew on.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Called by the
    help door; built by ``hivemind.cli.compose.help``. Calls into ``hivemind.llm`` (the request
    shapes, ``BoundModel``, ``CallGate``, the errors) and ``hivemind.llm.prompts``.

Key invariants:
    - Every request has ``tools=()`` and no response schema: there is nothing the model can call
      and nothing it is asked to structure.
    - One call per question, never a retry here; a failure is None, logged by its class only.
    - Nothing of the question, the passages or the answer reaches a log line.

See Also:
    - hivemind.llm.prompts.help_answer.md for the words the model is given.
    - hivemind.entrance.help.door for what happens with None.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence

from hivemind.common.logging import get_logger
from hivemind.entrance.help.retrieval import Passage
from hivemind.forage import Effort, ModelSlot
from hivemind.llm import BoundModel, CallGate, LLMError, LLMRequest, Message, Role
from hivemind.llm.prompts import PromptName, SectionLabel, neutralise_fences, render

HELP_MAX_OUTPUT_TOKENS = 400  # A few short sentences and their citations; never an essay.
ANSWER_TIMEOUT_S = 30.0  # A small model on a few passages answers in seconds; past this, links.
ANSWER_TEMPERATURE = 0.0  # The most literal reading of the passages, the same every time.
_CITATION = re.compile(r"\[(\d+)\]")  # The `[n]` mark the prompt asks for after each sentence.

log = get_logger(__name__)

__all__ = [
    "ANSWER_TEMPERATURE",
    "ANSWER_TIMEOUT_S",
    "HELP_MAX_OUTPUT_TOKENS",
    "HelpAnswerer",
    "cited_indexes",
    "passages_block",
    "render_help_prompt",
]


def passages_block(passages: Sequence[Passage]) -> str:
    """Render the passages as the numbered, labelled block the prompt's RETRIEVED section holds.

    Args:
        passages: The retrieved passages, best first; numbered from 1 in this order.

    Returns:
        One block per passage: ``[n] Page: <page>; Section: <heading>`` then its words, every
        section delimiter inside the words made inert.
    """
    blocks = []
    for number, passage in enumerate(passages, start=1):
        section = passage.section
        heading = section.heading or "(the page's opening)"
        label = f"[{number}] Page: {section.page}; Section: {heading}"
        blocks.append(f"{label}\n{neutralise_fences(section.text)}")
    return "\n\n".join(blocks)


def render_help_prompt(question: str, passages: Sequence[Passage]) -> str:
    """Assemble the system prompt: the shipped body, the passages, then the question.

    Args:
        question: The person's words, untrusted.
        passages: What was retrieved for it.

    Returns:
        The rendered prompt, RETRIEVED before USER in stable-prefix order.
    """
    return render(
        PromptName.HELP_ANSWER,
        sections={
            SectionLabel.RETRIEVED: passages_block(passages),
            SectionLabel.USER: neutralise_fences(question),
        },
    )


def cited_indexes(answer: str) -> frozenset[int]:
    """Return the passage numbers an answer cites with ``[n]`` marks.

    Args:
        answer: The model's words.

    Returns:
        Every number inside square brackets, as the prompt numbers passages from 1.
    """
    return frozenset(int(mark) for mark in _CITATION.findall(answer))


class HelpAnswerer:
    """Make the one grounded call a question needs, on a binding chosen for its price."""

    def __init__(self, bound: BoundModel, gate: CallGate) -> None:
        """Hold the binding and the gate.

        Args:
            bound: The chat binding the composition root priced lowest (``cheapest_chat_binding``).
            gate: What the call goes through; the Fanner's lane, so it is metered and recorded.
        """
        self._bound = bound
        self._gate = gate

    @property
    def slot(self) -> ModelSlot:
        """The slot the binding serves; what the Cost view files the call under."""
        return self._bound.slot

    async def answer(self, question: str, passages: Sequence[Passage]) -> str | None:
        """Ask the model for an answer grounded in ``passages``; None when it could not answer.

        Args:
            question: The person's words, untrusted.
            passages: What was retrieved for it; never empty (the door answers an empty set
                without a model).

        Returns:
            The model's words, or None when the call failed, ran out of time, or came back empty.
        """
        request = LLMRequest(
            slot=self._bound.slot,
            system=render_help_prompt(question, passages),
            # The question is in the system prompt as labelled data; the turn only asks.
            messages=(Message.text(Role.USER, "Answer the question in the user section."),),
            tools=(),
            max_output_tokens=HELP_MAX_OUTPUT_TOKENS,
            effort=Effort.LOW,
            temperature=ANSWER_TEMPERATURE,
        )
        try:
            # External await: the lane's seat queue and the provider's call; seconds on a small
            # model. Past the deadline the person gets the passages as links instead.
            async with asyncio.timeout(ANSWER_TIMEOUT_S):
                response = await self._gate.complete(self._bound, request)
        except (LLMError, TimeoutError) as exc:
            # The class alone: an adapter's message may name the server, never the question.
            log.warning("entrance.help_answer_failed", error=type(exc).__name__)
            return None
        text = response.text.strip()
        return text or None
