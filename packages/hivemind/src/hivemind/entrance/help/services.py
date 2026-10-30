"""Define what the help door needs: the retriever, the answerer when there is one, and the budget.

The help's Ask (ADR-0070) is handed one ``HelpServices`` by the composition root, as voice is
handed ``VoiceServices``: the retriever over the help pages (keyword, or the ``EMBEDDER`` slot
with a keyword fallback), the ``HelpAnswerer`` on the chat binding the Forage map priced lowest
(None when the manifest binds no chat slot at all, in which case every answer is the passages as
links), and the per-session ``QuestionBudget``. The Entrance holds None in its place while no
help pages were found, which leaves the Ask route unmounted.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Built by
    ``hive serve``'s composition root (``hivemind.cli.compose.help``) and by tests; read by the
    help door. Calls into this package and the limiter's ``QuestionBudget``.

Key invariants:
    - ``answerer`` is None only when no chat binding could be bound; a bound one that fails at
      call time is the door's ``MODEL_FAILED``, not a missing answerer.

See Also:
    - hivemind.entrance.help.door for how each is used.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.entrance.auth.limits import QuestionBudget
from hivemind.entrance.help.answer import HelpAnswerer
from hivemind.entrance.help.retrieval import HelpRetriever

QUESTIONS_PER_MINUTE = 6  # A person reads an answer before asking the next; a script does not.

__all__ = ["QUESTIONS_PER_MINUTE", "HelpServices"]


@dataclass(frozen=True, slots=True)
class HelpServices:
    """Everything the help door needs besides the Entrance's own services.

    Attributes:
        retriever: Finds the passages a question is about.
        answerer: Answers from them on the cheapest chat binding; None when none is bound, so
            the passages are the whole answer.
        budget: Each session's questions a minute, charged before anything runs.
    """

    retriever: HelpRetriever
    answerer: HelpAnswerer | None
    budget: QuestionBudget
