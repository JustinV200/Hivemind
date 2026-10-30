"""Answer questions about the Hive from its own help pages: the Help screen's Ask.

ADR-0070: the Observation Hive's Help screen carries an Ask, a small assistant that answers a
person's question from the help pages the front end bundles and nothing else. ``GET
/v1/help/ask?q=`` is its one route; the door charges the session's question budget, retrieves
the passages the question is about (BM25 over the pages, or the Hive's ``EMBEDDER`` slot with
BM25 as its fallback), and asks the chat binding the Forage map prices lowest for an answer
grounded in those passages, cited by number; a Hive with no chat binding, or a model that fails,
answers with the passages as links instead. The assistant has no tools, takes no action, keeps
nothing, and reads the question and the pages as data, never as instructions.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. The route joins the
    route table (``hivemind.entrance.routes.registry``); the services are built by ``hive
    serve``'s composition root (``hivemind.cli.compose.help``) from the build's ``help/`` pages.
    Calls into the gate, the limiter's question budget, ``hivemind.llm`` and its prompts.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No model runs for a question over its session's budget or one no page matches; every
      request a model sees has no tools and no schema.

See Also:
    - docs/adr/0070-help-ask-answers-from-the-help-pages-on-the-cheapest-binding.md
    - docs/observation/design.md section 5.12 for the screen.

Public API:
    - ask, TOP_PASSAGES: the one door (door).
    - HelpAnswerer, render_help_prompt, passages_block, cited_indexes, HELP_MAX_OUTPUT_TOKENS,
      ANSWER_TIMEOUT_S, ANSWER_TEMPERATURE: the grounded call (answer).
    - HelpAskQuery, HelpAnswerView, HelpSourceView, HelpOutcome, source_view,
      MIN_QUESTION_CHARS, MAX_QUESTION_CHARS, SNIPPET_CHARS: the Landing Board's shapes (models).
    - HELP_ROUTES, HELP_PATH, HELP_REFUSALS, help_of: ``GET /v1/help/ask`` (route).
    - HelpServices, QUESTIONS_PER_MINUTE: what answering needs (services).
    - HelpNotServedError, QuestionOverBudgetError: the refusals (errors).
    - hivemind.entrance.help.retrieval: the corpus, the ranker and the retrievers.
"""

from hivemind.entrance.help.answer import (
    ANSWER_TEMPERATURE,
    ANSWER_TIMEOUT_S,
    HELP_MAX_OUTPUT_TOKENS,
    HelpAnswerer,
    cited_indexes,
    passages_block,
    render_help_prompt,
)
from hivemind.entrance.help.door import TOP_PASSAGES, ask
from hivemind.entrance.help.errors import HelpNotServedError, QuestionOverBudgetError
from hivemind.entrance.help.models import (
    MAX_QUESTION_CHARS,
    MIN_QUESTION_CHARS,
    SNIPPET_CHARS,
    HelpAnswerView,
    HelpAskQuery,
    HelpOutcome,
    HelpSourceView,
    source_view,
)
from hivemind.entrance.help.route import HELP_PATH, HELP_REFUSALS, HELP_ROUTES, help_of
from hivemind.entrance.help.services import QUESTIONS_PER_MINUTE, HelpServices

__all__ = [
    "ANSWER_TEMPERATURE",
    "ANSWER_TIMEOUT_S",
    "HELP_MAX_OUTPUT_TOKENS",
    "HELP_PATH",
    "HELP_REFUSALS",
    "HELP_ROUTES",
    "MAX_QUESTION_CHARS",
    "MIN_QUESTION_CHARS",
    "QUESTIONS_PER_MINUTE",
    "SNIPPET_CHARS",
    "TOP_PASSAGES",
    "HelpAnswerView",
    "HelpAnswerer",
    "HelpAskQuery",
    "HelpNotServedError",
    "HelpOutcome",
    "HelpServices",
    "HelpSourceView",
    "QuestionOverBudgetError",
    "ask",
    "cited_indexes",
    "help_of",
    "passages_block",
    "render_help_prompt",
    "source_view",
]
