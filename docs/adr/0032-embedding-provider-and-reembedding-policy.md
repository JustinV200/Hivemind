# ADR-0032: One EmbeddingProvider door on the EMBEDDER slot, metered by the Fanner; every vector names its model; re-embedding is progressive and automatic

- Status: Accepted
- Date: 2026-09-24

## Context

The Honey Store's semantic half (ADR-0031) needs embeddings. Codingrules 8.6 already reserves the
seat: `EmbeddingProvider` in `hivemind/llm/`, bound through `ModelSlot.EMBEDDER`, with three
implementations (an OpenAI-compatible server, `sentence-transformers` in process, a fake), and
roadmap open decision 5 asks which embedding model and dimension, and whether
`sentence-transformers` is required. The forces:

- Provider independence (ADR-0008): nothing above `hivemind.llm` may know which server or library
  produced a vector, and no vendor or model-library type may leave an adapter.
- Vectors are only comparable within one model. A fallback binding that serves a different model,
  or an operator changing `[llm.slots.embedder]`, produces vectors that are silently meaningless
  against the stored ones if they are ever compared.
- Every model call is metered (ADR-0015): seats on a local server are shared between chat and
  embeddings, and hosted embeddings cost money.
- Every slot must be bound in the manifest, so existing manifests bind `embedder` to whatever they
  had: a hosted chat provider with no embedding endpoint, or a local chat model that cannot embed.
- Offline is a first-class mode (8.6); an in-process model has no base URL at all.
- `sentence-transformers` pulls a deep learning framework that most Hives running a local server
  never need.

## Decision

**The door.** `hivemind.llm.embedding` (a package; the roadmap's `llm/embedding.py`) defines
`EmbeddingProvider`: `name`, `capabilities` (`EmbeddingCapabilities`: dimensions once known, the
largest batch, the longest input, whether vectors come back unit-normalised), `embed(request) ->
EmbeddingResponse` and `health()`. `EmbeddingRequest` carries the texts; `EmbeddingResponse`
carries one vector per text in order, the model id that produced them, their dimension and a
normalised `Usage`. Adapters: `llm/providers/openai_compat/embedding.py` speaks
`/v1/embeddings` over the existing client, so LM Studio, Ollama, vLLM and llama.cpp servers and
hosted OpenAI-compatible APIs all work; `llm/providers/sentence_transformers/` loads a model in
process, imported lazily so the base install never needs the library, runs `encode` under
`asyncio.to_thread`, and counts as one seat per loaded model; `FakeEmbedding` hashes word tokens
and character trigrams into a fixed dimension and normalises, so lexically similar texts land
near each other deterministically, with an outage switch like `FakeLLMProvider`'s.

**The slot.** The provider registry resolves `ModelSlot.EMBEDDER` to a `BoundEmbedder` through
its own table of embedding factories per provider kind. A kind with no embedding endpoint
(`anthropic`) resolves to a typed `EmbeddingUnsupportedError` that the composition root turns
into "no embedder", logged once; `sentence_transformers` is a new provider kind that serves only
the `EMBEDDER` slot, which the manifest enforces at load time. A fallback link in the embedder's
chain is followed only when it serves the same model id, because a different model's vectors
are not comparable.

**Metering.** `FannerLane.embed` takes the same per-provider seat meter and rate limiter as a chat
call and records the same `llm.call` event with `slot = EMBEDDER`, the input tokens and the cost,
so embeddings appear in `hive forage status`, the ledger and the cost view with no new event kind.

**Every vector names its model.** A stored vector carries the model id and dimension that produced
it, and a query compares only against vectors of the model that embedded the query. A Honey row
with no vector for the current model is pending: every House Bee ripening pass embeds up to
`[honey.ripening] max_embed_per_pass` pending rows. Changing the embedder slot therefore
re-embeds the store progressively with no command at all; `hive honey reembed` runs the whole
backlog now. Rows not yet re-embedded are still found by full-text search, and `hive honey stats`
shows coverage per model. A new model's vector is stored beside the old model's (one row per
Honey row per model), so switching back needs no second re-embed and nothing is ever dropped in
bulk; a query only ever compares its own model's vectors.

**Degrade, never fail closed.** No embedder, an unsupported kind, a provider that is down or a
batch that fails leaves rows pending and retrieval full-text only, with the reason on the
response. Nothing in the Hive waits on an embedding.

**Offline and the default model.** `sentence_transformers` is in process and so provably local
under `[llm] offline = true`, where it loads with `local_files_only` and never reaches a model
hub. `sentence-transformers` is an optional extra (`hivemind[embeddings]`), not a requirement.
The recommended defaults are an embedding model on the same local server that serves the chat
slots (for example `nomic-embed-text` on Ollama or LM Studio, 768 dimensions) or
`all-MiniLM-L6-v2` in process (384 dimensions); the phase 8 evaluation harness decides between
them on measured retrieval quality.

## Consequences

Positive: moving embeddings from a local server to an in-process model, or to a hosted API, is a
manifest change followed by background re-embedding, never a migration. A misbound embedder
cannot corrupt the store, only slow its semantic half. Seats and spend on embeddings are visible
where every other model call already is.

Negative: a re-embed costs one embedding call per Honey row, bounded per pass but real on a large
store. Vectors are stored unquantised as float32 (3 KiB per row at 768 dimensions). The fake's
vectors are lexical, not semantic, so tests prove plumbing and ranking, not model quality; the
`local_llm` job is where real quality is exercised.

## Alternatives considered

Embeddings through `LLMProvider` as another `complete` shape: every chat adapter would grow a
method most cannot serve, and a chat-only provider would have to refuse at call time instead of at
bind time. Following the embedder's fallback chain regardless of model: silently mixes vector
spaces. Re-embedding eagerly in one blocking command on every embedder change: stalls ripening
and leaves no vector search at all until it finishes. Requiring `sentence-transformers`: adds a
framework download to every install for a feature most Hives serve from their model server.
