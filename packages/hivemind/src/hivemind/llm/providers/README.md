# hivemind.llm.providers

The providers package holds one sub-package per model vendor or local server kind. These are the
only modules in the whole workspace allowed to import a vendor LLM SDK or an HTTP client aimed
at a model server, so that swapping a provider never touches code above llm/.

## Layout

- **`anthropic/`** (roadmap step 3.6): the hosted-Claude adapter, using the `anthropic` SDK. See
  its own `README.md` for capability declaration, prompt caching, adaptive thinking, structured
  output, streaming and error mapping.
- **`openai_compat/`** (roadmap step 3.7): speaks the OpenAI-compatible chat-completions wire to
  a local or self-hosted server (Ollama, vLLM, llama.cpp's built-in server, LM Studio). See its
  own `README.md` for capability declaration and error mapping.

Each sub-package owns exactly one `[llm.providers.<name>] kind` string. This package's own face
(`__init__.py`) re-exports only an adapter's `LLMProvider` implementation and its config type
(`OpenAICompatProvider`/`OpenAICompatConfig` today) -- never a sub-package's internal wire-mapping
or HTTP-client modules. The registry (`hivemind.llm.registry`, roadmap step 3.4) imports each
adapter by its own module path, keyed by the manifest's `kind` string, rather than through this
face: `hivemind.llm.providers.<kind_matching_name>`, not a lookup collected here.
