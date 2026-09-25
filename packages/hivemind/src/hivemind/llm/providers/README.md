# hivemind.llm.providers

The providers package holds one sub-package per model vendor or local server kind. These are the
only modules in the whole workspace allowed to import a vendor LLM SDK or an HTTP client aimed
at a model server, so that swapping a provider never touches code above llm/.

## Layout

- **`anthropic/`** (roadmap step 3.6): the hosted-Claude adapter, using the `anthropic` SDK. See
  its own `README.md` for capability declaration, prompt caching, adaptive thinking, structured
  output, streaming and error mapping.
- **`openai_compat/`** (roadmap step 3.7, transcription half roadmap step 6.5a, embedding half
  roadmap step 7.1): speaks the OpenAI-compatible chat-completions wire to a local or self-hosted
  server (Ollama, vLLM, llama.cpp's built-in server, LM Studio), and the same servers' `/embeddings`
  wire. See its own `README.md` for capability declaration and error mapping. Its `transcription/`
  sub-package speaks the same servers' (and hosted Whisper APIs') multipart
  `/audio/transcriptions` wire, as `OpenAICompatTranscription` for `ModelSlot.TRANSCRIBER`.
- **`whisper/`** (roadmap step 6.5a, ADR-0033): `kind = "whisper_local"`, Whisper in process on
  faster-whisper (the optional `hivemind[whisper]` extra, imported lazily and only there), the GPU
  when present and the CPU at int8 otherwise, one seat per loaded model. See its own `README.md`.
- **`sentence_transformers/`** (roadmap step 7.1): an in-process `EmbeddingProvider`, no server
  involved -- loads a `sentence-transformers` model (the `hivemind[embeddings]` optional extra)
  into this Hive's own process. Serves only the `EMBEDDER` slot (`EMBEDDING_ONLY_KINDS`,
  `hivemind.llm.registry`); its own module docstring explains the lazy-import pattern that keeps
  the base install free of the dependency.

Each sub-package owns exactly one `[llm.providers.<name>] kind` string. This package's own face
(`__init__.py`) re-exports only an adapter's `LLMProvider`/`EmbeddingProvider` implementation and
its config type (`OpenAICompatProvider`/`OpenAICompatConfig`, `OpenAICompatEmbedding`/
`OpenAICompatEmbeddingConfig`, `SentenceTransformersEmbedding`/`SentenceTransformersConfig`) --
never a sub-package's internal wire-mapping or HTTP-client modules. The registry
(`hivemind.llm.registry`, roadmap step 3.4) imports each adapter by its own module path, keyed by
the manifest's `kind` string, rather than through this face: `hivemind.llm.providers.
<kind_matching_name>`, not a lookup collected here. The transcription adapters
(`hivemind.llm.providers.whisper`, and `hivemind.llm.providers.openai_compat`'s
`OpenAICompatTranscription`) are reached the same way, from the registry's own transcription
factory table; they are not re-exported by this face.

A transcription adapter implements `hivemind.llm.transcription.TranscriptionProvider`, not
`LLMProvider`: its own `mapping.py` is still the only file where the library's or the wire's field
names appear, and every failure still leaves as a typed `hivemind.llm.errors.LLMError`.
