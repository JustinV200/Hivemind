# hivemind.llm.providers.ollama

Optional management of an [Ollama](https://ollama.com) server through its native API (roadmap step
8.2, ADR-0044). Chat, structured output and tool calls with the same server still go through the one
`openai_compat` adapter; this package covers only what that door cannot do.

## What it does

- **`OllamaManager.detect(base_url)`**: true when `GET /api/version` answers with a `version`. Never
  raises; an unreachable or different server is just false. Detection is by probing, never by
  provider name or kind string.
- **`list_models()`**: `GET /api/tags`, the installed models (name, bytes on disk, parameter size,
  quantisation).
- **`pull(model)`**: `POST /api/pull`, an async iterator of `PullProgress` (status, digest, total,
  completed; `fraction`), ending with `status == "success"`. A refused model arrives in-band as an
  error line and is raised as `ProviderRequestError`.
- **`keep_alive(model, duration_s)`**: `POST /api/generate` with no prompt and a `keep_alive`, which
  loads the model and holds it for `duration_s` seconds (0 unloads it, -1 holds it until the server
  stops).
- **`health()`**: the version and the models resident in memory (`GET /api/version`, `/api/ps`).

The native base URL is the provider's `base_url` with a trailing `/v1` dropped, so the host and port
are whatever the operator wrote in the manifest; none is a default here. `httpx` is imported only in
`client.py`, which maps every failure to the `hivemind.llm.errors` tree.

## Other servers

vLLM, llama.cpp's server and LM Studio have no management API here. `hive llm models` lists their
`/models` ids through `OpenAICompatProvider.list_models`, and `hive llm providers` shows their
`/models` health, exactly as before.

## Files

- `manage.py`: `OllamaManager`.
- `client.py`: `OllamaClient` (JSON and NDJSON over httpx) and `native_base_url`.
- `models.py`: frozen models for what the API returns (unknown fields ignored).
- `protocol.py`: `ModelServerManager`, `PullProgress`, `ManagerOpener`.
- `fake.py`: `FakeOllamaManager` and `opener(...)` for the CLI's tests.

## How to test this

`uv run --frozen pytest packages/hivemind/tests/unit/llm/providers/ollama packages/hivemind/tests/unit/cli/test_llm_local.py`.
The client and manager tests use `httpx.MockTransport`; the CLI tests use `FakeOllamaManager`. No
real Ollama is needed; trying it for real is the operator's `hive llm models --provider NAME`.
