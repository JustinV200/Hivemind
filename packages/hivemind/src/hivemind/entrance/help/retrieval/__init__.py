"""Find the help passages a question is about: the corpus, a keyword ranker and two retrievers.

The help's Ask (ADR-0070) answers only from the Observation Hive's own help pages, so this
package is where those pages become something a question can be matched against: ``corpus``
cuts each page into the sections the Help screen itself addresses, ``rank`` scores sections by
their words (BM25, in memory), and ``retriever`` defines the ``HelpRetriever`` seam with a
keyword implementation that always works and an embedding one on the Hive's ``EMBEDDER`` slot
(text to vectors) that falls back to keywords when its provider fails.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help``. Built by
    ``hivemind.cli.compose.help``; read by the help door. Calls into ``hivemind.llm``'s embedding
    boundary and the standard library.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A passage's ``section`` is the id the Help screen gives the same heading, so a citation
      always lands where the words are.

See Also:
    - docs/adr/0070-help-ask-answers-from-the-help-pages-on-the-cheapest-binding.md

Public API:
    - HelpCorpus, HelpSection, corpus_from_pages, load_help_corpus, split_sections, slug_of,
      plain_words, HELP_PAGE_SUFFIX: the pages as sections (corpus).
    - KeywordIndex, tokenize, STOP_WORDS: the BM25 ranker (rank).
    - HelpRetriever, Passage, KeywordRetriever, EmbeddingRetriever: the seam and its two
      implementations (retriever).
"""

from hivemind.entrance.help.retrieval.corpus import (
    HELP_PAGE_SUFFIX,
    HelpCorpus,
    HelpSection,
    corpus_from_pages,
    load_help_corpus,
    plain_words,
    slug_of,
    split_sections,
)
from hivemind.entrance.help.retrieval.rank import STOP_WORDS, KeywordIndex, tokenize
from hivemind.entrance.help.retrieval.retriever import (
    EmbeddingRetriever,
    HelpRetriever,
    KeywordRetriever,
    Passage,
)

__all__ = [
    "HELP_PAGE_SUFFIX",
    "STOP_WORDS",
    "EmbeddingRetriever",
    "HelpCorpus",
    "HelpRetriever",
    "HelpSection",
    "KeywordIndex",
    "KeywordRetriever",
    "Passage",
    "corpus_from_pages",
    "load_help_corpus",
    "plain_words",
    "slug_of",
    "split_sections",
    "tokenize",
]
