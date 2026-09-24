"""Define SentenceTransformersEmbedding: an in-process EmbeddingProvider, no server involved.

Unlike every other adapter in `hivemind.llm.providers`, this one talks to no network and no other
process: it loads a `sentence-transformers` model (the `sentence-transformers` PyPI package, an
optional extra, `hivemind[embeddings]`) into this Hive's own process and runs it directly. That
makes it provably local under `[llm] offline = true` (ADR-0032) with no `base_url` at all, and the
one kind `EMBEDDING_ONLY_KINDS`/`IN_PROCESS_KINDS` (`hivemind.llm.registry`) name today. The model
loads exactly once, lazily (on the first `embed()` or `health()` call, never at construction),
under an `asyncio.Lock` so two concurrent first callers never load it twice, and every blocking
call -- loading the model, encoding a batch -- runs under `asyncio.to_thread` (codingrules section
11): `sentence-transformers` is synchronous, CPU- or GPU-bound library code with no async form of
its own. `Loader` is the seam that makes this testable without the real library installed: the
default implementation is the one legal `import sentence_transformers` in this module, deferred
inside the function itself (never at module scope, mirroring `hivemind.hive.backends.docker.
sdk_client`'s own pattern for an optional vendor dependency), so importing this module -- and this
whole package -- succeeds whether or not the extra is installed; only actually loading a model
needs it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    sentence_transformers`. Constructed by the registry's `"sentence_transformers"` embedding
    factory (`hivemind.llm.registry`); implements `hivemind.llm.embedding.provider.
    EmbeddingProvider`. Calls into `hivemind.llm.embedding`, `hivemind.llm.errors`,
    `hivemind.llm.models` and `waggle.clock`; `sentence_transformers` only inside `_default_loader`
    (import-linter's carve-out for this package, root `pyproject.toml`).

Key invariants:
    - `import sentence_transformers` appears exactly once in this module, inside
      `_default_loader`, never at module scope: importing this module must succeed even when the
      `embeddings` extra is not installed, and only fail once someone actually tries to load a
      model (mirrors `hivemind.hive.backends.docker.sdk_client`'s own rule for `docker`).
    - The model loads once: `_ensure_loaded` checks `self._model is not None` under `self._lock`
      before ever calling `self._loader` again, so a second `embed()` call never re-imports or
      re-constructs it.
    - `ImportError` from the loader (the extra is not installed) and `OSError`/`ValueError` from
      it (the model id or local path could not be loaded) both become `ProviderUnavailableError`,
      never a bare Python exception: `EmbeddingProvider.embed`'s contract names only typed
      `LLMError`s.

See Also:
    - .claude/codingrules.md section 8.6 for the vendor-library confinement pattern this module
      mirrors for an in-process model library instead of a vendor API SDK.
    - .claude/codingrules.md section 11 for the to_thread rule every blocking call here follows.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the decision behind this
      adapter, including why it is an optional extra rather than a base dependency.
    - hivemind.hive.backends.docker.sdk_client for the same lazy-import pattern over a different
      optional vendor dependency.
    - hivemind.llm.embedding.provider for the EmbeddingProvider Protocol this class implements.
"""

from __future__ import annotations

import asyncio
import math
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.models import EmbeddingRequest, EmbeddingResponse
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.models import Usage
from waggle.clock import Clock

MAX_BATCH = 256  # roadmap 7.1: one encode() call can take the whole batch; no external chunking.
MAX_INPUT_CHARS = 8_000  # The library truncates internally by token count (max_seq_length), not
# characters, so this is a conservative parity value with the other adapters' own default, not a
# limit this adapter enforces itself.
DEFAULT_BATCH_SIZE = 32  # encode()'s own internal batch_size; overridable by manifest config.
CHARS_PER_TOKEN_ESTIMATE = 4  # Mirrors hivemind.llm.embedding.fake's own rule of thumb for Usage.
_INSTALL_HINT = "the embeddings extra is not installed: uv sync --extra embeddings"

__all__ = [
    "CHARS_PER_TOKEN_ESTIMATE",
    "DEFAULT_BATCH_SIZE",
    "MAX_BATCH",
    "MAX_INPUT_CHARS",
    "EncoderModel",
    "Loader",
    "SentenceTransformersConfig",
    "SentenceTransformersEmbedding",
]


class _EncodedVectors(Protocol):
    """A numpy-array-like `encode()` result; only `.tolist()` of it is ever used."""

    def tolist(self) -> list[list[float]]:
        """Return this result as a plain, JSON-friendly list of lists of floats."""
        ...


class EncoderModel(Protocol):
    """A loaded sentence-transformers model: encode text to vectors, report its dimension."""

    def encode(
        self, sentences: list[str], *, batch_size: int, normalize_embeddings: bool
    ) -> _EncodedVectors:
        """Encode `sentences` into vectors, batching internally by `batch_size`.

        Args:
            sentences: The texts to embed, in the order their vectors should come back.
            batch_size: How many sentences the model encodes at once internally.
            normalize_embeddings: Whether the model should L2-normalise every returned vector.

        Returns:
            An array-like result whose `.tolist()` is a list of one list-of-floats per sentence,
            in the same order.
        """
        ...

    def get_sentence_embedding_dimension(self) -> int | None:
        """Return this model's output vector length, or None when the model does not report one.

        sentence-transformers 5.x also offers it as `get_embedding_dimension`, which
        `_reported_dimension` prefers when present, since 5.x warns on this older name.
        """
        ...


class Loader(Protocol):
    """Build a loaded `EncoderModel`: the testable seam behind the real, lazy library import."""

    def __call__(self, model: str, device: str | None, local_files_only: bool) -> EncoderModel:
        """Load `model` and return it ready to `encode()`.

        Args:
            model: A model name (resolved against the library's model hub) or a local path.
            device: The device to load onto ('cpu', 'cuda', 'mps'), or None to let the library
                choose.
            local_files_only: Never reach the model hub; only load from an already-cached or
                local path.

        Raises:
            ImportError: The `sentence_transformers` package is not installed.
            OSError: The model could not be loaded from `model`.
            ValueError: `model` was not recognised by the library.
        """
        ...


class SentenceTransformersConfig(BaseModel):
    """One `[llm.providers.<name>]` embedding section's options, kind `sentence_transformers`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(description="A sentence-transformers model name or a local path.")
    device: str | None = Field(
        default=None, description="The device to load onto; None lets the library choose."
    )
    batch_size: int = Field(
        default=DEFAULT_BATCH_SIZE, gt=0, description="encode()'s own internal batch size."
    )
    local_files_only: bool = Field(
        default=False,
        description="Never reach the model hub; forced True when [llm] offline = true.",
    )
    normalize: bool = Field(
        default=True, description="Whether encode() should L2-normalise every returned vector."
    )


class SentenceTransformersEmbedding:
    """An in-process EmbeddingProvider over a lazily-loaded sentence-transformers model."""

    def __init__(
        self,
        name: str,
        config: SentenceTransformersConfig,
        clock: Clock,
        loader: Loader | None = None,
    ) -> None:
        """Create a provider that has not loaded a model yet.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            clock: Source of `health()`'s `checked_at`.
            loader: How to build the `EncoderModel`; the real, lazy-importing loader when omitted.
                A test passes a fake loader/encoder instead of installing the real library.
        """
        self._name = name
        self._config = config
        self._clock = clock
        self._loader: Loader = loader if loader is not None else _default_loader
        self._model: EncoderModel | None = None
        self._dimensions: int | None = None
        # Guards `_model`: two concurrent first callers must never both load the model, since
        # loading is the expensive, blocking part this whole adapter exists to do only once.
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; see `EmbeddingProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> EmbeddingCapabilities:
        """Return this provider's declared capabilities; see `EmbeddingProvider.capabilities`."""
        return EmbeddingCapabilities(
            dimensions=self._dimensions,
            max_batch=MAX_BATCH,
            max_input_chars=MAX_INPUT_CHARS,
            normalized=self._config.normalize,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Encode every text in `request` in one call; see `EmbeddingProvider.embed`."""
        model = await self._ensure_loaded()
        texts = list(request.texts)
        # External-ish await: encode() is blocking (numpy/torch under the hood), so it runs off
        # the event loop; the library does its own internal batching by config.batch_size.
        encoded = await asyncio.to_thread(
            model.encode,
            texts,
            batch_size=self._config.batch_size,
            normalize_embeddings=self._config.normalize,
        )
        vectors = tuple(tuple(vector) for vector in encoded.tolist())
        dimensions = self._record_dimensions(vectors)
        usage = Usage(input_tokens=_token_estimate(texts), output_tokens=0, cost_usd=None)
        return EmbeddingResponse(
            vectors=vectors, model=self._config.model, dimensions=dimensions, usage=usage
        )

    async def health(self) -> ProviderHealth:
        """Report whether the model is loaded, or loadable; never raises."""
        try:
            await self._ensure_loaded()
        except ProviderUnavailableError as exc:
            return ProviderHealth(
                state=HealthState.DOWN, detail=str(exc), checked_at=self._clock.now()
            )
        return ProviderHealth(
            state=HealthState.HEALTHY, detail="model loaded", checked_at=self._clock.now()
        )

    async def _ensure_loaded(self) -> EncoderModel:
        """Load the model on first use, under `self._lock`, in a worker thread.

        Raises:
            ProviderUnavailableError: The `embeddings` extra is not installed, or the model could
                not be loaded from its configured name or path.
        """
        async with self._lock:
            if self._model is None:
                self._model = await self._load_model()
                # A model that reports its own dimension upfront saves waiting for a real
                # embed() call to learn it; one that returns None (some custom models) still
                # falls back to _record_dimensions on the first embed().
                self._dimensions = _reported_dimension(self._model)
            return self._model

    async def _load_model(self) -> EncoderModel:
        """Run `self._loader` in a thread, translating its plain exceptions to a typed one."""
        try:
            return await asyncio.to_thread(
                self._loader, self._config.model, self._config.device, self._config.local_files_only
            )
        except ImportError as exc:
            raise ProviderUnavailableError(self._name, _INSTALL_HINT) from exc
        except (OSError, ValueError) as exc:
            raise ProviderUnavailableError(self._name, f"model load failed: {exc}") from exc

    def _record_dimensions(self, vectors: tuple[tuple[float, ...], ...]) -> int:
        """Return this call's dimension, fixing `self._dimensions` from it if still unknown."""
        if self._dimensions is not None:
            return self._dimensions
        dimensions = len(vectors[0])
        self._dimensions = dimensions
        return dimensions


def _default_loader(model: str, device: str | None, local_files_only: bool) -> EncoderModel:
    """Import sentence_transformers lazily and load `model`; the default `Loader`.

    See `Loader.__call__` for the full contract; this implementation is the one place in this
    module (and the one place `import-linter`'s carve-out for this package allows) that the
    `sentence_transformers` package is actually imported.
    """
    # SAFETY: this is the one legal `import sentence_transformers` in the whole workspace (root
    # pyproject.toml's import-linter contract confines it to this package); every other module
    # sees only EncoderModel's own Protocol shape. The package is an optional extra
    # (hivemind[embeddings]) with no bundled stubs, so mypy cannot resolve it in this environment
    # either -- codingrules section 9's "reason code and comment" for an untyped dependency.
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    loaded: EncoderModel = SentenceTransformer(
        model, device=device, local_files_only=local_files_only
    )
    return loaded


def _reported_dimension(model: EncoderModel) -> int | None:
    """Return the dimension `model` reports up front, through whichever name its library has.

    sentence-transformers 5.x renamed `get_sentence_embedding_dimension` to
    `get_embedding_dimension` and warns on the old name, while 3.x and 4.x (inside this extra's
    `>=3,<6` pin) have only the old one. Asking for the new name first keeps a current library
    quiet: the old one's FutureWarning failed the phase 7 `local_llm` eval on its first real
    model (2026-09-24), because warnings are errors in this repository's tests.
    """
    current = getattr(model, "get_embedding_dimension", None)
    # A library new enough to have the new name: use it, never the deprecated one.
    if callable(current):
        reported: int | None = current()
        return reported
    return model.get_sentence_embedding_dimension()


def _token_estimate(texts: list[str]) -> int:
    """Return the summed, rounded-up chars/4 estimate across every text (roadmap 7.1's own rule)."""
    return math.ceil(sum(len(text) for text in texts) / CHARS_PER_TOKEN_ESTIMATE)
