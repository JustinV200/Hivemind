"""Define OpenAICompatEmbedding: speak the OpenAI-compatible /embeddings wire to a model server.

The same servers `hivemind.llm.providers.openai_compat.provider.OpenAICompatProvider` speaks
`/chat/completions` to (Ollama, vLLM, llama.cpp's built-in server, LM Studio) also expose
`POST /embeddings` in the same shape OpenAI's own hosted API does: `{"model": ..., "input": [...]}`
in, `{"data": [{"index": ..., "embedding": [...]}], "usage": {"prompt_tokens": ...}}` out. This
module is that adapter's embedding half, built the same way (`create(name, config, clock)` mirrors
`OpenAICompatProvider.create` exactly: a bearer header when an API key is set, the configured
timeout) and sharing the same `hivemind.llm.providers.openai_compat.client.OpenAICompatClient` for
the wire mechanics, so `mapping.py`'s streaming/tool-call machinery has nothing to do with this
file at all -- embeddings are a single request/response round trip, batched client-side.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    openai_compat`. Constructed by the registry's `"openai_compat"` embedding factory
    (`hivemind.llm.registry`); implements `hivemind.llm.embedding.provider.EmbeddingProvider`.
    Calls into `hivemind.llm.embedding`, `hivemind.llm.errors`, `hivemind.llm.providers.
    openai_compat.client` and `waggle.clock` only; `httpx` only through that client.

Key invariants:
    - The API key, when set, is held in `OpenAICompatEmbeddingConfig.api_key: SecretStr` and never
      appears in a `repr`, a log line or an error message (codingrules section 13); only
      `.get_secret_value()` in `create()` ever reads it.
    - `capabilities.dimensions` starts `None` and is fixed the moment the first batch's response is
      decoded; every later batch, in this call or a later one, whose vectors disagree with that
      fixed length is refused with `ProviderRequestError` rather than silently accepted
      (ADR-0032: a stored vector's model and dimension must never drift under one adapter instance).
    - `capabilities.normalized` is always False: this adapter cannot verify whether the server it
      is pointed at returns unit-normalised vectors, and codingrules section 8.6 forbids assuming
      a capability it has not confirmed.

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules this implements.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the decision behind this
      adapter and the same-model-id rule its dimension check protects.
    - hivemind.llm.providers.openai_compat.client for the HTTP layer this class calls.
    - hivemind.llm.providers.openai_compat.provider for OpenAICompatProvider, this package's
      chat-side sibling this module's `create()` mirrors.
    - hivemind.llm.embedding.provider for the EmbeddingProvider Protocol this class implements.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.models import EmbeddingRequest, EmbeddingResponse
from hivemind.llm.errors import ContextTooLongError, ProviderRequestError
from hivemind.llm.models import JsonObject, Usage
from hivemind.llm.providers.openai_compat.client import OpenAICompatClient
from waggle.clock import Clock

EMBEDDINGS_PATH = "/embeddings"  # Relative to OpenAICompatEmbeddingConfig.base_url; see
# hivemind.llm.providers.openai_compat.provider's own MODELS_PATH comment for why no "/v1" prefix.
MODELS_PATH = "/models"  # health() probes the same endpoint the chat provider's health() does.
DEFAULT_MAX_BATCH = 256  # OpenAI's own /v1/embeddings hard cap on one request's `input` length;
# matches hivemind.llm.embedding.models.MAX_EMBED_TEXTS, so this ceiling can never reject a text
# an EmbeddingRequest was ever allowed to carry.
DEFAULT_BATCH_SIZE = 32  # A conservative default round trip size: large enough to amortise HTTP
# overhead, small enough that one failed batch never wastes a huge request. Overridable by
# [llm.providers.<name>.embedding] batch_size.
DEFAULT_MAX_INPUT_CHARS = 8_000  # roadmap 7.1's own constant; overridable by the same section.
# A later response's vector length disagreeing with the first is a real request-shape conflict
# (ADR-0032), synthesized since no real HTTP response is involved (mirrors provider.py's own
# UNLISTED_MODEL_STATUS_CODE convention for a locally-detected refusal).
DIMENSION_CONFLICT_STATUS_CODE = 409
EMPTY_RESPONSE_STATUS_CODE = 502  # Synthesized the same way: a server that answered 200 but
# carried no usable embedding is behaving like a broken upstream, the closest real HTTP status.
CONTEXT_OVERFLOW_STATUS_CODE = 400  # The real status client.py's own ContextTooLongError came
# from; re-wrapped as a ProviderRequestError so embed()'s error surface stays the three types
# EmbeddingProvider.embed documents (see _embed_batch's own docstring).

__all__ = ["OpenAICompatEmbedding", "OpenAICompatEmbeddingConfig"]


class OpenAICompatEmbeddingConfig(BaseModel):
    """One `[llm.providers.<name>]` manifest section's embedding options, kind `openai_compat`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(description="The server's base URL, e.g. 'http://127.0.0.1:PORT/v1'.")
    model: str = Field(description="The embedding model id this provider instance requests.")
    api_key: SecretStr | None = Field(
        default=None, description="A bearer token for servers that require one; None for local."
    )
    timeout_s: float = Field(gt=0, description="The httpx client's timeout, in seconds.")
    batch_size: int = Field(
        default=DEFAULT_BATCH_SIZE, gt=0, description="How many texts to send per /embeddings call."
    )
    max_input_chars: int = Field(
        default=DEFAULT_MAX_INPUT_CHARS, gt=0, description="Truncate each text to this many chars."
    )


class OpenAICompatEmbedding:
    """Talk to one OpenAI-compatible server's `/embeddings` endpoint."""

    def __init__(
        self,
        name: str,
        config: OpenAICompatEmbeddingConfig,
        client: httpx.AsyncClient,
        clock: Clock,
    ) -> None:
        """Wrap an already-constructed httpx client; prefer `create()` outside of tests.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            client: An `httpx.AsyncClient` already carrying `base_url`, the bearer header (when
                `config.api_key` is set) and `timeout_s`.
            clock: Source of `health()`'s `checked_at`.
        """
        self._name = name
        self._config = config
        self._http = client
        self._clock = clock
        # No chat context window applies to embeddings; OpenAICompatClient only reads this to
        # build a ContextTooLongError message, which _embed_batch re-wraps before it ever escapes.
        self._client = OpenAICompatClient(client, name, context_window=1)
        self._dimensions: int | None = None

    @classmethod
    def create(
        cls, name: str, config: OpenAICompatEmbeddingConfig, clock: Clock
    ) -> OpenAICompatEmbedding:
        """Build the httpx client from `config` and return a provider wrapping it.

        Mirrors `OpenAICompatProvider.create` exactly (bearer header when set, timeout).

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            clock: Source of `health()`'s `checked_at`.

        Returns:
            A ready OpenAICompatEmbedding.
        """
        headers: dict[str, str] = {}
        if config.api_key is not None:
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        http = httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_s, headers=headers
        )
        return cls(name, config, http, clock)

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; see `EmbeddingProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> EmbeddingCapabilities:
        """Return this provider's declared capabilities; see `EmbeddingProvider.capabilities`."""
        return EmbeddingCapabilities(
            dimensions=self._dimensions,
            max_batch=DEFAULT_MAX_BATCH,
            max_input_chars=self._config.max_input_chars,
            # This adapter never confirms unit normalisation (module docstring's "Key invariants"):
            # it does not control which server or model is on the other end of base_url.
            normalized=False,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed every text in `request`, batching by the server's own limits.

        See `EmbeddingProvider.embed` for the full contract.
        """
        chunk_size = min(self._config.batch_size, DEFAULT_MAX_BATCH)
        texts = [_truncate(text, self._config.max_input_chars) for text in request.texts]
        vectors: list[tuple[float, ...]] = []
        input_tokens = 0
        # One /embeddings call per chunk_size-sized slice, in order, so the concatenated result
        # preserves request.texts's own ordering without re-sorting across chunk boundaries.
        for start in range(0, len(texts), chunk_size):
            batch_vectors, batch_tokens = await self._embed_batch(texts[start : start + chunk_size])
            vectors.extend(batch_vectors)
            input_tokens += batch_tokens
        if self._dimensions is None:
            # _check_dimensions (inside _embed_batch) sets this from the first vector it ever
            # sees; still None here means every batch's response carried zero embeddings, which
            # is the server breaking its own contract, not a shape this adapter should guess at.
            raise ProviderRequestError(
                self._name, EMPTY_RESPONSE_STATUS_CODE, detail="no embedding vectors returned"
            )
        return EmbeddingResponse(
            vectors=tuple(vectors),
            model=self._config.model,
            dimensions=self._dimensions,
            usage=Usage(input_tokens=input_tokens, output_tokens=0, cost_usd=None),
        )

    async def health(self) -> ProviderHealth:
        """GET `/models`, the same probe the chat provider's `health()` uses."""
        try:
            # codingrules section 11: this await inherits self._http's configured timeout.
            response = await self._http.get(MODELS_PATH)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderHealth(
                state=HealthState.DOWN, detail=detail, checked_at=self._clock.now()
            )
        state, detail = _health_from_status(response.status_code)
        return ProviderHealth(state=state, detail=detail, checked_at=self._clock.now())

    async def aclose(self) -> None:
        """Close this adapter's HTTP client; see `OpenAICompatProvider.aclose` for why."""
        # Local, milliseconds: closes idle pooled sockets; no request is in flight at shutdown.
        await self._http.aclose()

    async def _embed_batch(self, texts: list[str]) -> tuple[list[tuple[float, ...]], int]:
        """POST one batch to /embeddings and return its vectors (index order) and prompt tokens.

        Raises:
            ProviderRequestError: The server rejected the batch, including a context-length 400
                despite `max_input_chars` truncation (some models' real limit is shorter than the
                configured character cap): `EmbeddingProvider.embed`'s contract names only
                `ProviderUnavailableError`/`RateLimitedError`/`ProviderRequestError`, so this
                adapter never lets `OpenAICompatClient`'s `ContextTooLongError` escape unchanged.
        """
        # A fresh list, typed against JsonValue at construction (not `texts` itself, `list[str]`,
        # which mypy never treats as a `list[JsonValue]`: list is invariant in its element type).
        wire_texts: list[JsonValue] = list(texts)
        body: JsonObject = {"model": self._config.model, "input": wire_texts}
        try:
            payload = await self._client.post_json(EMBEDDINGS_PATH, body)
        except ContextTooLongError as exc:
            raise ProviderRequestError(
                self._name,
                CONTEXT_OVERFLOW_STATUS_CODE,
                error_type="context_length_exceeded",
                detail=str(exc),
            ) from exc
        vectors = _vectors_from_payload(payload)
        if len(vectors) != len(texts):
            # A short (or padded) reply cannot be matched back to its texts by position, and a
            # vector stored against the wrong Honey row is worse than no vector at all.
            raise ProviderRequestError(
                self._name,
                EMPTY_RESPONSE_STATUS_CODE,
                error_type="vector_count_mismatch",
                detail=f"server returned {len(vectors)} vectors for {len(texts)} texts",
            )
        self._check_dimensions(vectors)
        usage = payload.get("usage")
        tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        return vectors, tokens if isinstance(tokens, int) else 0

    def _check_dimensions(self, vectors: list[tuple[float, ...]]) -> None:
        """Record this adapter's dimension from the first vector ever seen, or refuse a change.

        Raises:
            ProviderRequestError: A later batch's vector length disagrees with the one already
                recorded (ADR-0032: a stored vector's model and dimension must never drift).
        """
        for vector in vectors:
            if self._dimensions is None:
                self._dimensions = len(vector)
            elif len(vector) != self._dimensions:
                raise ProviderRequestError(
                    self._name,
                    DIMENSION_CONFLICT_STATUS_CODE,
                    error_type="dimension_mismatch",
                    detail=f"server returned a {len(vector)}-dimension vector after "
                    f"{self._dimensions} was already recorded for model {self._config.model!r}",
                )


def _vectors_from_payload(payload: JsonObject) -> list[tuple[float, ...]]:
    """Extract every embedding from a `/embeddings` reply, sorted by its own `index` field.

    Args:
        payload: The parsed `/embeddings` JSON body.

    Returns:
        One vector per `data` entry, in `index` order (never assumed to already be in order:
        the OpenAI-compatible shape only promises `index` names each entry's request position).
    """
    entries = payload.get("data")
    if not isinstance(entries, list):
        return []
    ordered = sorted(entries, key=_entry_index)
    vectors: list[tuple[float, ...]] = []
    for entry in ordered:
        embedding = entry.get("embedding") if isinstance(entry, dict) else None
        if not isinstance(embedding, list):
            continue  # A malformed entry (wrong shape, or no "embedding" key) is dropped, not
            # crashed on: _check_dimensions/the caller's own dimensions check catches a short list.
        vectors.append(tuple(float(c) for c in embedding if isinstance(c, int | float)))
    return vectors


def _entry_index(entry: object) -> int:
    """Return one `/embeddings` `data` entry's own `index`, or 0 when it is malformed."""
    index = entry.get("index", 0) if isinstance(entry, dict) else 0
    # A non-integer index would make sorted() compare a str with an int and raise TypeError.
    return index if isinstance(index, int) else 0


def _truncate(text: str, max_chars: int) -> str:
    """Truncate `text` to `max_chars`, unchanged when it is already shorter."""
    return text if len(text) <= max_chars else text[:max_chars]


def _health_from_status(status_code: int) -> tuple[HealthState, str]:
    """Map a raw `/models` status code to a `(HealthState, detail)` pair.

    Mirrors `hivemind.llm.providers.openai_compat.provider`'s own `_health_from_status`: a small,
    deliberate duplication rather than a cross-module import of a private helper, since the two
    adapters are otherwise independent (the chat provider's `health()` reasoning is documented on
    its own module, not shared machinery this one should depend on).
    """
    if status_code == 200:
        return HealthState.HEALTHY, "ok"
    if status_code == 401:
        return HealthState.DOWN, "unauthorized (401)"
    return HealthState.DEGRADED, f"status {status_code}"
