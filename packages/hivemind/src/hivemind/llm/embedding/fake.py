"""Provide FakeEmbedding: an honest, deterministic EmbeddingProvider for tests and demos.

A fake embedder turns text into vectors without a network call or a model library, so a unit test
or an offline demo path can exercise everything above `hivemind.llm.embedding` (codingrules 14.4)
with no provider installed. "Deterministic feature hashing" means the vector for a given text is
always the same, on every process and every run: lowercase the text, split it into word tokens
(`[a-z0-9]+`) and, per word, its character trigrams (three-character windows of the word wrapped in
`^`/`$` boundary marks, so `"cat"` contributes `"^ca"`, `"cat"`, `"at$"`), hash each feature with
`hashlib.blake2b` (never Python's own `hash()`, which is salted per-process by `PYTHONHASHSEED` and
would make the same text embed differently across two runs) to one vector index and a sign, add a
word's own weight (1.0) or a trigram's smaller weight (0.5) at that index, then L2-normalise the
whole vector. Two lexically similar texts land near each other because they share tokens and
trigrams; this proves plumbing and ranking, never real semantic quality (ADR-0032's own
"Negative": "the fake's vectors are lexical, not semantic").

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Used by every test that needs an
    `EmbeddingProvider` without a real one, and by `hivemind.llm.registry`'s `"fake"` embedding
    factory for `hive doctor` and demo paths. Calls into `hivemind.llm.capabilities`,
    `hivemind.llm.embedding.capabilities`, `hivemind.llm.embedding.models`, `hivemind.llm.errors`,
    `hivemind.llm.models` and `waggle.clock` only.

Key invariants:
    - `calls` counts every `embed()` call this instance actually ran, so a test can assert
      batching (how many separate calls a batching caller made) without inspecting requests.
    - A text with no word token (empty after tokenising, e.g. only punctuation) still maps to a
      vector: a fixed, deterministic unit vector, the same for every such text, rather than an
      all-zero one nothing could normalise.
    - `set_available(False)` makes every `embed()` call raise `ProviderUnavailableError` and every
      `health()` call report `HealthState.DOWN`, mirroring `hivemind.llm.fake.FakeLLMProvider.
      set_outage`.

See Also:
    - .claude/codingrules.md section 14.4 for the fakes-over-mocks rule this module follows.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the decision this supports.
    - hivemind.llm.fake for FakeLLMProvider, the chat-side sibling this mirrors.
    - hivemind.llm.embedding.provider for the EmbeddingProvider protocol this class implements.
"""

from __future__ import annotations

import hashlib
import math
import re

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.models import MAX_EMBED_TEXTS, EmbeddingRequest, EmbeddingResponse
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.models import Usage
from waggle.clock import Clock

FAKE_EMBED_MODEL_ID = "fake-embed-model"  # A neutral model id (codingrules 8.6): never real.
DEFAULT_DIMENSIONS = 128  # Small enough for a fast test, large enough that two unrelated texts'
# hashed features rarely collide into the same index.
WORD_WEIGHT = 1.0  # A whole word token counts more than any one of its trigrams.
TRIGRAM_WEIGHT = 0.5  # Character trigrams add a softer, misspelling-tolerant signal.
CHARS_PER_TOKEN_ESTIMATE = 4  # Mirrors hivemind.llm.fake's own rule of thumb for Usage.
DEFAULT_MAX_INPUT_CHARS = 8_000  # Matches the other adapters' own default (see their modules);
# the fake never actually truncates, since hashing has no real length limit, but a caller reading
# this capability should still see the same ballpark every adapter declares.
_EMPTY_TEXT_SENTINEL = "\x00hivemind-embed-empty\x00"  # A fixed marker, hashed exactly like a
# real word token, so "no token in this text" resolves to one stable, deterministic unit vector.
_WORD_PATTERN = re.compile(r"[a-z0-9]+")

__all__ = ["CHARS_PER_TOKEN_ESTIMATE", "DEFAULT_DIMENSIONS", "FAKE_EMBED_MODEL_ID", "FakeEmbedding"]


class FakeEmbedding:
    """A deterministic, offline EmbeddingProvider that hashes text into vectors.

    Not thread- or task-safe against concurrent `set_available` calls; `calls` and `_available`
    are meant to be set up by one test before use, then read by the code under test, the same
    pattern `hivemind.llm.fake.FakeLLMProvider` documents for its own state.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        dimensions: int = DEFAULT_DIMENSIONS,
        clock: Clock,
        capabilities: EmbeddingCapabilities | None = None,
        model: str = FAKE_EMBED_MODEL_ID,
    ) -> None:
        """Create a FakeEmbedding that hashes text into `dimensions`-length vectors.

        Args:
            name: This provider's manifest-style name; "fake" by default.
            dimensions: The length of every vector this instance produces.
            clock: Source of `health()`'s `checked_at`.
            capabilities: The capability set to declare; a normalised, `dimensions`-sized default
                when omitted.
            model: The model id every response reports. The registry passes the binding's own
                model, so `response.model` always equals `BoundEmbedder.model`, the same as every
                real adapter: the Honey Store tags and filters vectors by that one id (ADR-0032).
        """
        self._name = name
        self._model = model
        self._dimensions = dimensions
        self._clock = clock
        self._capabilities = (
            capabilities
            if capabilities is not None
            else EmbeddingCapabilities(
                dimensions=dimensions,
                max_batch=MAX_EMBED_TEXTS,
                max_input_chars=DEFAULT_MAX_INPUT_CHARS,
                normalized=True,
            )
        )
        self._available = True
        self._calls = 0

    @property
    def name(self) -> str:
        """Return this provider's name; see `EmbeddingProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> EmbeddingCapabilities:
        """Return this provider's declared capabilities; see `EmbeddingProvider.capabilities`."""
        return self._capabilities

    @property
    def calls(self) -> int:
        """Return how many `embed()` calls this instance has actually run."""
        return self._calls

    def set_available(self, available: bool) -> None:
        """Simulate the provider being entirely down (or recovered).

        Args:
            available: When False, every subsequent `embed()` call raises
                `ProviderUnavailableError`, and `health()` reports `HealthState.DOWN`.
        """
        self._available = available

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Hash every text in `request` into a vector; see `EmbeddingProvider.embed`."""
        self._check_available()
        self._calls += 1
        vectors = tuple(_embed_text(text, self._dimensions) for text in request.texts)
        input_tokens = sum(_token_estimate(text) for text in request.texts)
        return EmbeddingResponse(
            vectors=vectors,
            model=self._model,
            dimensions=self._dimensions,
            usage=Usage(input_tokens=input_tokens, output_tokens=0, cost_usd=None),
        )

    async def health(self) -> ProviderHealth:
        """Return DOWN while unavailable, HEALTHY otherwise; see `EmbeddingProvider.health`."""
        if not self._available:
            return ProviderHealth(
                state=HealthState.DOWN, detail="set_available(False)", checked_at=self._clock.now()
            )
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    def _check_available(self) -> None:
        """Raise ProviderUnavailableError when `set_available(False)` is in effect."""
        if not self._available:
            raise ProviderUnavailableError(self._name, "set_available(False)")


def _token_estimate(text: str) -> int:
    """Return `text`'s rough token count: chars/4, rounded up (never zero for non-empty text)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def _embed_text(text: str, dimensions: int) -> tuple[float, ...]:
    """Hash `text`'s word tokens and their trigrams into one L2-normalised vector.

    Args:
        text: The text to embed; may be empty or hold no word token.
        dimensions: The output vector's length.

    Returns:
        A unit-length vector of `dimensions` floats, deterministic for the same `text`.
    """
    tokens = _WORD_PATTERN.findall(text.lower())
    if not tokens:
        # No word survived tokenising (empty text, or punctuation-only): a fixed sentinel stands
        # in for "nothing here", so this case still returns a stable, reproducible unit vector.
        return _weighted_vector([(_EMPTY_TEXT_SENTINEL, 1.0)], dimensions)
    # Every word contributes its own full-weight feature, plus a softer-weighted feature per
    # character trigram, so texts sharing only a misspelled word still land close together.
    features = [(token, WORD_WEIGHT) for token in tokens]
    features.extend((trigram, TRIGRAM_WEIGHT) for token in tokens for trigram in _trigrams(token))
    return _weighted_vector(features, dimensions)


def _trigrams(token: str) -> list[str]:
    """Return `token`'s character trigrams, wrapped in `^`/`$` boundary marks.

    Args:
        token: A non-empty word token.

    Returns:
        Every 3-character window of `f"^{token}$"`, at least one even for a 1-character token
        (`"a"` -> `["^a$"]`).
    """
    wrapped = f"^{token}$"
    return [wrapped[i : i + 3] for i in range(len(wrapped) - 2)]


def _weighted_vector(features: list[tuple[str, float]], dimensions: int) -> tuple[float, ...]:
    """Hash every `(feature, weight)` pair into `dimensions` slots and L2-normalise the result."""
    vector = [0.0] * dimensions
    for feature, weight in features:
        index, sign = _hash_index_and_sign(feature, dimensions)
        vector[index] += sign * weight
    return _normalise(vector)


def _hash_index_and_sign(feature: str, dimensions: int) -> tuple[int, float]:
    """Hash `feature` deterministically to a vector index and a sign.

    Args:
        feature: A word token or a character trigram.
        dimensions: The output vector's length; the index is reduced modulo this.

    Returns:
        `(index, sign)`, `index` in `[0, dimensions)` and `sign` either `+1.0` or `-1.0`. Uses
        `hashlib.blake2b`, never Python's built-in `hash()`: that one is salted per process
        (`PYTHONHASHSEED`), which would make the same text embed differently on every run.
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dimensions
    sign = 1.0 if value & 1 == 0 else -1.0  # The digest's own low bit; independent enough of the
    # index (taken from the same 64 bits via modulo) for this fake's purposes.
    return index, sign


def _normalise(vector: list[float]) -> tuple[float, ...]:
    """Return `vector` scaled to unit L2 length, or a fixed unit vector if it summed to zero."""
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # Every hashed weight cancelled out exactly (possible in principle, vanishingly unlikely
        # in practice): still must return *some* unit vector, never one that fails to normalise.
        return _weighted_vector([(_EMPTY_TEXT_SENTINEL, 1.0)], len(vector))
    return tuple(component / norm for component in vector)
