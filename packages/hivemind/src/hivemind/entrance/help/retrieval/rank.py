"""Rank help sections by their words: a small BM25 over a corpus that fits in memory.

The help's Ask (ADR-0069) needs a ranker that always works, costs nothing and needs no store:
the help pages are a few hundred short sections, so a plain BM25 (the standard term-weighting
rule behind most keyword search) over an in-memory index is enough, and it is the fallback the
embedding retriever drops to when the ``EMBEDDER`` slot is unbound or its provider fails. Words
are lower-cased runs of letters and digits; a short list of stop words (``the``, ``what``,
``how``) is dropped so a question's own shape does not outweigh its subject. A heading's words
count twice, since a section about a thing names it in its heading.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.help.retrieval``. Built
    once per corpus by ``KeywordRetriever``; called on every question. Calls into the standard
    library only.

Key invariants:
    - Pure and deterministic: the same corpus and query give the same order.
    - A document scores only when it holds at least one query word; ``rank`` never returns a
      zero score, so an empty result means nothing matched.
    - The Honey Store's FTS5 search is not reused: it needs a SQLite file and a Honey row per
      passage, and the help pages are not Honey (they are the front end's own words).

See Also:
    - hivemind.entrance.help.retrieval.retriever for the retrievers built on this index.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

K1 = 1.2  # BM25's term-frequency saturation; the textbook default.
B = 0.75  # BM25's length normalisation; the textbook default.
HEADING_WEIGHT = 2  # A heading's words are counted this many times in its section's document.
TITLE_BONUS = 1.0  # A document whose title the query names scores this much more (doubled).
_WORD = re.compile(r"[a-z0-9]+")  # A token: a run of letters and digits, lower-cased first.
# Words a question is made of that say nothing about its subject.
STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
        "how", "i", "in", "is", "it", "its", "my", "of", "on", "or", "that", "the", "this",
        "to", "what", "when", "where", "which", "who", "why", "with", "you", "your",
    }
)  # fmt: skip

__all__ = ["HEADING_WEIGHT", "K1", "STOP_WORDS", "TITLE_BONUS", "B", "KeywordIndex", "tokenize"]


def tokenize(text: str) -> list[str]:
    """Return the words of ``text`` the index counts: lower-cased, stop words dropped.

    Args:
        text: A section's or a question's words.

    Returns:
        The tokens in order, repeats kept (frequency matters to BM25).
    """
    return [word for word in _WORD.findall(text.lower()) if word not in STOP_WORDS]


class KeywordIndex:
    """A BM25 index over a fixed list of documents, each a string of words.

    Built once; the documents never change, so every statistic is computed in ``__init__`` and
    ``rank`` only reads.
    """

    def __init__(self, documents: Sequence[str], titles: Sequence[str] = ()) -> None:
        """Index ``documents``.

        Args:
            documents: One string per document, in the order ``rank`` numbers them.
            titles: One title per document (a section's heading), or none: a document whose
                title's every word the query holds scores ``TITLE_BONUS`` more, so an entry
                about the thing asked beats a document that merely mentions it often.
        """
        self._titles = [frozenset(tokenize(title)) for title in titles]
        self._counts = [Counter(tokenize(document)) for document in documents]
        self._lengths = [sum(count.values()) for count in self._counts]
        total = sum(self._lengths)
        # A corpus of empty documents has no average; one avoids a division by zero below.
        self._average_length = total / len(self._counts) if total else 1.0
        # How many documents hold each word: the idf's input.
        frequency: Counter[str] = Counter()
        for count in self._counts:
            frequency.update(count.keys())
        self._idf = {
            word: _idf(len(self._counts), document_count)
            for word, document_count in frequency.items()
        }

    def rank(self, query: str, limit: int) -> list[tuple[int, float]]:
        """Return the best-scoring documents for ``query``, highest first.

        Args:
            query: The question's words.
            limit: The most documents to return; >= 1.

        Returns:
            ``(document index, score)`` pairs, score descending then index ascending, at most
            ``limit`` of them, every score above zero.
        """
        words = set(tokenize(query))
        scored = [
            (index, score)
            for index, count in enumerate(self._counts)
            if (score := self._score(index, words, count)) > 0.0
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit]

    def _score(self, index: int, words: set[str], count: Counter[str]) -> float:
        """Sum BM25 over the query words one document holds, the title bonus on top."""
        score = 0.0
        normalised_length = K1 * (1 - B + B * self._lengths[index] / self._average_length)
        for word in words:
            frequency = count.get(word, 0)
            if frequency == 0:
                continue  # A word the document lacks contributes nothing.
            score += self._idf[word] * frequency * (K1 + 1) / (frequency + normalised_length)
        # The entry whose heading the question names is the one the person meant, as the Help
        # screen's own search puts a heading hit first; a title with no words never qualifies.
        title = self._titles[index] if index < len(self._titles) else frozenset()
        if score > 0.0 and title and title <= words:
            score *= 1 + TITLE_BONUS
        return score


def _idf(document_count: int, holding: int) -> float:
    """BM25's inverse document frequency for a word ``holding`` of ``document_count`` hold."""
    return math.log(1 + (document_count - holding + 0.5) / (holding + 0.5))
