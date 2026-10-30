"""Compose the help's Ask for `hive serve`: pages, retriever, the cheapest binding, the budget.

ADR-0070: the Observation Hive's Help screen answers questions from its own help pages, which the
front end's build copies beside itself as `help/<page>.md`. `build_help_services` reads them from
the build the Entrance serves (`OBSERVATION_BUILD`), so a Hive served without a build has no Ask
and its route stays unmounted. The retriever uses the Hive's `EMBEDDER` slot when its binding can
be built, through the Fanner's lane so every embed is metered and recorded, with the keyword
ranker as its fallback; when the embedder cannot be bound at all (an in-process adapter whose
library is not installed, a provider offline mode refuses) the keyword ranker serves alone. The
answerer runs on the chat binding `cheapest_chat_binding` finds in the manifest's rows and the
Forage map's prices (never a model id in code), bound through the Hive's own registry and called
through the same Fanner lane, so the Cost view counts every answer; a manifest whose chat
bindings cannot be bound gives no answerer, and every answer is the passages as links.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.compose.entrance` while it
    assembles the Entrance's parts. Calls into `hivemind.entrance.help`, `hivemind.llm` (the
    registry, the Fanner, the errors, routing's cheapest rule) and `hivemind.forage`.

Key invariants:
    - Nothing here calls a model: binding is resolution only; the first question pays.
    - Every refusal to bind is logged by its class and falls back; none stops `hive serve`.

See Also:
    - hivemind.entrance.help for what is built.
    - hivemind.cli.compose.entrance for where it is handed over.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cli.compose.hive import Hive
from hivemind.common.logging import get_logger
from hivemind.entrance.auth.limits import QuestionBudget
from hivemind.entrance.help import QUESTIONS_PER_MINUTE, HelpAnswerer, HelpServices
from hivemind.entrance.help.retrieval import (
    EmbeddingRetriever,
    HelpCorpus,
    HelpRetriever,
    KeywordRetriever,
    load_help_corpus,
)
from hivemind.forage import AccuracyBar, Tempo, slot_for_binding
from hivemind.llm import FannerLane, LLMError, UnresolvableSlotError
from hivemind.llm.routing import cheapest_chat_binding

HELP_PAGES_DIR = "help"  # Where the front end's build puts its help pages, under the web root.
# A person is waiting at the Help screen; a low bar never raises a floor, so the cheapest binding
# is never spilled past for its grade.
HELP_TEMPO = Tempo(latency_budget_s=30.0, accuracy=AccuracyBar.LOW)

log = get_logger(__name__)

__all__ = ["HELP_PAGES_DIR", "HELP_TEMPO", "build_help_services"]


def build_help_services(hive: Hive, web_root: Path | None) -> HelpServices | None:
    """Build the help's Ask over the build's pages; None when there are none to answer from.

    Args:
        hive: The Hive `hive serve` runs: its registry, Fanner, bindings and map.
        web_root: The Observation Hive's build directory, or None when it does not exist.

    Returns:
        The help services, or None when `web_root` is None or holds no `help/<page>.md`.
    """
    if web_root is None:
        return None
    corpus = load_help_corpus(web_root / HELP_PAGES_DIR)
    if corpus.is_empty:
        log.info("entrance.help_pages_missing", web_root=str(web_root))
        return None
    lane = hive.fanner.lane(HELP_TEMPO)
    return HelpServices(
        retriever=_retriever(hive, corpus, lane),
        answerer=_answerer(hive, lane),
        budget=QuestionBudget(hive.clock, QUESTIONS_PER_MINUTE),
    )


def _retriever(hive: Hive, corpus: HelpCorpus, lane: FannerLane) -> HelpRetriever:
    """The EMBEDDER slot over keywords when it binds; keywords alone when it cannot."""
    keywords = KeywordRetriever(corpus)
    try:
        bound = hive.registry.embedder()
    except (LLMError, UnresolvableSlotError) as exc:
        # An embedder that cannot be built (its library absent, its host refused offline) costs
        # the Ask nothing but precision: the keyword ranker needs no model.
        log.warning("entrance.help_embedder_unbound", error=type(exc).__name__)
        return keywords
    return EmbeddingRetriever(corpus, bound, lane, keywords)


def _answerer(hive: Hive, lane: FannerLane) -> HelpAnswerer | None:
    """The cheapest chat binding, bound through the registry; None when none can be bound."""
    deps = hive.queen_deps
    # The manifest's [llm.floors] rank a row the map does not price; a hand-built Hive with no
    # rules gets the shipped defaults.
    floors = deps.routing_rules.floors if deps.routing_rules else None
    row = cheapest_chat_binding(deps.bindings, deps.map.sources(), floors)
    if row is None:
        return None  # The manifest validator makes this unreachable; a hand-built Hive may not.
    # The row serves its own slot, or the slot whose chain names it; the Cost view files the
    # answer's calls under that slot.
    slot = slot_for_binding(row.key, deps.bindings)
    if slot is None:
        # cheapest_chat_binding returns only rows a chat slot's chain reaches, so no row lands
        # here; a binding that cannot be attributed is refused, not filed under a guessed slot.
        return None
    try:
        bound = hive.registry.bound_for_key(row.key, slot)
    except (LLMError, UnresolvableSlotError) as exc:
        log.warning("entrance.help_answerer_unbound", binding=row.key, error=type(exc).__name__)
        return None
    return HelpAnswerer(bound, lane)
