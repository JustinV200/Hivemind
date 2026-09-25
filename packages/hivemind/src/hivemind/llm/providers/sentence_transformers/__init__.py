"""Embed text in-process with a loaded sentence-transformers model; no server, no network.

Roadmap step 7.1's in-process `EmbeddingProvider`: `SentenceTransformersConfig` (the manifest's
`[llm.providers.<name>]` section, of `kind = "sentence_transformers"`, turned into a validated
value) and `SentenceTransformersEmbedding` (the `hivemind.llm.embedding.provider.EmbeddingProvider`
implementation built from it, over `embedding.py`'s `Loader`/`EncoderModel` seam). The
`sentence-transformers` PyPI package is an optional extra (`hivemind[embeddings]`, ADR-0036) that
this package's own `embedding.py` imports lazily, inside a function, never at module scope, so
importing this package succeeds whether or not the extra is installed.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers`. Imported
    by the registry (`hivemind.llm.registry`) by module, never re-exported further than
    `hivemind.llm.providers`'s own face. Calls into `hivemind.llm.embedding`, `hivemind.llm.errors`,
    `hivemind.llm.models` and `waggle.clock`; `sentence_transformers` only inside `embedding.py`'s
    `_default_loader` (codingrules section 8.6).

Key invariants:
    - No name beyond `SentenceTransformersConfig`/`SentenceTransformersEmbedding` is exported
      here; `embedding.py`'s `Loader`/`EncoderModel` Protocols and its default loader are this
      package's own implementation detail, imported directly by its own tests only.
    - Importing this package has no side effect and needs no installed model library: loading a
      model (`SentenceTransformersEmbedding._ensure_loaded`) is the composition root's job, lazily,
      on first use, never at import time (codingrules section 5.5).

See Also:
    - .claude/codingrules.md section 8.6 for the vendor-library confinement pattern this package
      follows for an in-process model library.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the decision behind this
      adapter.
    - hivemind.llm.providers.sentence_transformers.embedding for the full implementation.
    - hivemind.llm.embedding.provider for the EmbeddingProvider Protocol this package implements.

Public API:
    - SentenceTransformersConfig: this provider's validated configuration.
    - SentenceTransformersEmbedding: the EmbeddingProvider implementation.
"""

from hivemind.llm.providers.sentence_transformers.embedding import (
    SentenceTransformersConfig,
    SentenceTransformersEmbedding,
)

__all__ = ["SentenceTransformersConfig", "SentenceTransformersEmbedding"]
