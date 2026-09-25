"""Construct, cache and health-check every configured model provider, one door per kind of call.

`hivemind.llm.registry` is where a manifest's `[llm.providers.<name>] kind` string turns into a
live provider, and the production lookup `hivemind.llm.slots.resolve` walks a `ModelSlot` over.
It became a package when roadmap steps 6.5a (the transcriber) and 7.1 (the embedder) each added a
door beside the chat one: `config` (the provider row, the kind sets and the offline and secret
rules every door shares), `chat` (the chat factory table and capability overrides),
`transcription` (the transcriber door), `embedding` (the embedder door) and `provider_registry`
(`ProviderRegistry` itself, which owns all three doors). This face re-exports every public name,
so a caller never needs to know which module defines one.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Constructed once by the composition root
    (`hivemind.cli.stores`) and by a Virtual Cell's own in-Cell composition
    (`hivemind.cli.in_cell.providers`), and read by every bee that calls a model. Calls into
    `hivemind.forage`, the rest of `hivemind.llm` and `waggle` only -- never `hivemind.manifest`
    (codingrules section 4: `llm` and `manifest` are independent Layer 1 siblings).

Key invariants:
    - Every name a caller may import is re-exported here; nothing outside this package imports a
      submodule's private helper.

See Also:
    - hivemind.llm.registry.provider_registry for ProviderRegistry's own invariants.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
"""

from hivemind.llm.registry.chat import ProviderFactory, apply_overrides, default_factories
from hivemind.llm.registry.config import (
    EMBEDDING_ONLY_KINDS,
    IN_PROCESS_KINDS,
    PENDING_KINDS,
    RUNS_IN_PROCESS_KINDS,
    MissingDefaultModelError,
    ProviderConfig,
    ProviderKind,
    TranscriptionUnsupportedError,
    runs_in_process,
    runs_locally,
)
from hivemind.llm.registry.embedding import EmbeddingFactory, default_embedding_factories
from hivemind.llm.registry.provider_registry import (
    PROVIDER_CLOSE_TIMEOUT_S,
    ProviderRegistry,
    RegistryDeps,
)
from hivemind.llm.registry.transcription import (
    TranscriptionBuild,
    TranscriptionFactory,
    default_transcription_factories,
)

__all__ = [
    "EMBEDDING_ONLY_KINDS",
    "IN_PROCESS_KINDS",
    "PENDING_KINDS",
    "PROVIDER_CLOSE_TIMEOUT_S",
    "RUNS_IN_PROCESS_KINDS",
    "EmbeddingFactory",
    "MissingDefaultModelError",
    "ProviderConfig",
    "ProviderFactory",
    "ProviderKind",
    "ProviderRegistry",
    "RegistryDeps",
    "TranscriptionBuild",
    "TranscriptionFactory",
    "TranscriptionUnsupportedError",
    "apply_overrides",
    "default_embedding_factories",
    "default_factories",
    "default_transcription_factories",
    "runs_in_process",
    "runs_locally",
]
