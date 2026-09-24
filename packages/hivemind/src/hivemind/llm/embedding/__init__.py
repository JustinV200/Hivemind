"""Define the provider-agnostic embedding boundary every embed call in the Hive crosses: embedding.

Roadmap step 7.1 names this module `llm/embedding.py` in the target layout, but the concept needs
several files -- request/response shapes, declared capabilities, the provider protocol, a fake, a
resolved-binding value and a call seam, exactly the split `hivemind.llm`'s own chat-side modules
(`models`, `capabilities`, `provider`, `fake`, `slots`, `hivemind.llm.ladders.gate`) already use --
so it is a package instead (codingrules section 5.2: "a concept that needs a second file becomes a
package"). `EmbeddingProvider` (`provider.py`) is the one door every embed call goes through
(codingrules section 8.6); adapters live in `hivemind.llm.providers.<name>` and are never imported
here. `hivemind.llm.registry.ProviderRegistry.embedder` is the one production builder of a
`BoundEmbedder`; nothing above `hivemind.llm` constructs a provider or a bound value directly.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm`. Called by
    `hivemind.llm.fanner.embed` (metered calls), `hivemind.llm.providers.<name>` (adapters
    implementing `EmbeddingProvider`), and, once ripening lands (a later dispatch), the Honey
    Store's ripener. Calls into `hivemind.forage` (for `ModelSlot`) and `hivemind.llm.models` (for
    `Usage`) only.

Key invariants:
    - `hivemind.llm.embedding` never imports a vendor SDK or model-server HTTP client; that stays
      confined to `hivemind.llm.providers.<name>` (codingrules section 8.6, enforced by
      `lint-imports`).
    - Every value here is either frozen pydantic (the boundary models and capabilities) or a
      frozen, slotted dataclass (`BoundEmbedder`), matching codingrules section 8.5.
    - A `BoundEmbedder.fallback` exists only when it serves the same model id as the binding
      before it (ADR-0032); nothing here enforces that on its own, since only `hivemind.llm.
      registry.ProviderRegistry.embedder` ever builds one for real.

See Also:
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the decision this package
      implements.
    - .claude/codingrules.md section 8.6 for the "one door" rule this package extends to embeddings.
    - hivemind.llm for the chat-side boundary this package mirrors throughout.
    - hivemind.llm.registry for ProviderRegistry.embedder, the resolver that builds a BoundEmbedder.

Public API:
    - Boundary models (`hivemind.llm.embedding.models`): EmbeddingRequest, EmbeddingResponse,
      MAX_EMBED_TEXTS.
    - Capabilities (`hivemind.llm.embedding.capabilities`): EmbeddingCapabilities.
    - The one door (`hivemind.llm.embedding.provider`): EmbeddingProvider.
    - The fake (`hivemind.llm.embedding.fake`): FakeEmbedding, FAKE_EMBED_MODEL_ID.
    - The resolved binding (`hivemind.llm.embedding.bound`): BoundEmbedder.
    - The call seam (`hivemind.llm.embedding.gate`): EmbedGate, DirectEmbedGate.
"""

from hivemind.llm.embedding.bound import BoundEmbedder
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.fake import FAKE_EMBED_MODEL_ID, FakeEmbedding
from hivemind.llm.embedding.gate import DirectEmbedGate, EmbedGate
from hivemind.llm.embedding.models import MAX_EMBED_TEXTS, EmbeddingRequest, EmbeddingResponse
from hivemind.llm.embedding.provider import EmbeddingProvider

__all__ = [
    "FAKE_EMBED_MODEL_ID",
    "MAX_EMBED_TEXTS",
    "BoundEmbedder",
    "DirectEmbedGate",
    "EmbedGate",
    "EmbeddingCapabilities",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "FakeEmbedding",
]
