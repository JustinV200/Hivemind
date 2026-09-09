# hivemind.llm.providers.openai_compat

Speaks the OpenAI-compatible chat-completions wire (`POST /chat/completions`, `GET /models`) to a
local or self-hosted model server: Ollama, vLLM, llama.cpp's built-in server, and LM Studio have
all been checked against this shape. No vendor SDK is used -- `client.py` is a small `httpx` layer,
and `mapping.py` is the only file where an OpenAI wire field name (`messages`, `tool_calls`,
`finish_reason`, `delta`, ...) appears (codingrules section 8.6).

## Public API (roadmap step 3.7)

- **`OpenAICompatConfig`**: one `[llm.providers.<name>]` manifest section of kind
  `"openai_compat"`, validated. `base_url` (already including the server's own `/v1` segment, per
  the manifest convention in codingrules section 13), `model` (the id this provider instance
  requests -- see "Why `model` is on the config" below), `api_key` (`SecretStr | None`),
  `timeout_s`, `capabilities` (a `ProviderCapabilities`), `probe_models` (default `True`) and
  `token_estimate_margin` (default `1.15`).
- **`OpenAICompatProvider`**: the `LLMProvider` implementation. `OpenAICompatProvider.create(name,
  config, clock)` builds the `httpx.AsyncClient` (base URL, bearer header when `api_key` is set,
  timeout) and returns the provider; the plain constructor takes an already-built
  `httpx.AsyncClient` directly, so a test can inject one built on `httpx.MockTransport`.

## Why `model` is on the config, not the request

`LLMRequest` (`hivemind.llm.models`) carries no per-call model id -- it names a `ModelSlot`, and
the model id a slot resolves to is a manifest concern, not something `LLMProvider.complete` takes
as a parameter. The wire's `/chat/completions` body has a *mandatory* `model` field, though, so
this adapter's own config carries it: `OpenAICompatConfig.model`. A local OpenAI-compatible server
overwhelmingly runs exactly one model, so this matches how these servers are actually deployed.

## Capability probing

`GET /models` only ever returns a list of model ids -- never whether the server supports native
tool calls, JSON schema enforcement, vision, or streaming. So capabilities always come from
`OpenAICompatConfig.capabilities`, the manifest's `[llm.providers.<name>.capabilities]` override
(defaults filled in by the registry, roadmap step 3.4), never from a probe. What `probe()` (called
once by the composition root before the provider serves traffic) actually contributes is
narrower: when `probe_models` is `True`, it records the server's own `/models` listing, and every
later `complete`/`stream` call refuses with a `ProviderRequestError` if `config.model` is not in
it -- turning a manifest typo into a loud startup-time failure instead of a confusing first call.

## Error mapping

`client.py` maps every `httpx` failure to a typed `hivemind.llm.errors.LLMError`: a connection
failure or a 5xx becomes `ProviderUnavailableError`; a 429 becomes `RateLimitedError` (with
`retry_after_s` from the `Retry-After` header, when present); a 400 whose message mentions context
length becomes `ContextTooLongError`; every other 4xx becomes `ProviderRequestError`. `health()`
talks to `httpx` directly (bypassing that mapping) so it can tell a 5xx (`DEGRADED`) apart from a
connection failure or a 401 (`DOWN`).

## Running a live test later

No live test exists yet (roadmap step 3.7 says not to write one). When one is added, it belongs
behind the `local_llm` pytest marker (already registered in the root `pyproject.toml`):

```python
@pytest.mark.local_llm  # needs a local model server (Ollama, vLLM, llama.cpp) already running
async def test_openai_compat_against_a_real_local_server() -> None: ...
```

`local_llm` tests never run in the default CI job (`pytest -m "not integration and not e2e and
not live_llm and not local_llm"`, per the phase 3 gates); a developer runs them by hand against a
server they started themselves.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm/providers/openai_compat
```

Recorded fixtures (a completion, a tool-call completion, a streamed SSE transcript, an error body)
live under `packages/hivemind/tests/fixtures/llm/openai_compat/` and are loaded by
`Path(__file__)`-relative reads; no network is used. `httpx.MockTransport` stands in for the wire
in every test.
