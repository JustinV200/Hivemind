# hivemind.llm.providers.anthropic

Speaks the Anthropic Messages API wire (`POST /v1/messages`, `POST /v1/messages/count_tokens`,
`GET /v1/models`) to hosted Claude models, using the official `anthropic` SDK (`anthropic.
AsyncAnthropic`) rather than raw HTTP. `client.py` is a small async wrapper over the SDK's own
calls plus error mapping; `mapping.py` and its `streaming.py` sibling are the only files where an
Anthropic wire field name (`content_block`, `input_schema`, `output_config`, `stop_reason`, ...)
appears (codingrules section 8.6).

## Public API (roadmap step 3.6)

- **`AnthropicConfig`**: one `[llm.providers.<name>]` manifest section of kind `"anthropic"`,
  validated. `api_key` (`SecretStr | None`; `None` means every call fails typed rather than the
  SDK silently picking up an ambient credential), `base_url` (`None` uses the SDK's own default
  endpoint -- never written as a literal here, see "Why no endpoint literal" below), `timeout_s`
  (default 120.0) and `capabilities` (a `ProviderCapabilities`, defaulting to `.full()` at a
  200,000-token context window).
- **`AnthropicProvider`**: the `LLMProvider` implementation. `AnthropicProvider.from_config(name,
  config, clock)` builds the `anthropic.AsyncAnthropic` client (api key, base url, timeout,
  `max_retries=0`) and returns the provider; the plain constructor takes an already-built SDK
  client directly, so a test can inject one built on the SDK's own mock transport.

## Why no endpoint literal

`scripts/check_no_model_ids.py` greps source for provider URL fragments (`api.anthropic.com`
included); writing the SDK's default endpoint anywhere in this package would fail that hygiene
check just as surely as a hard-coded model id would. `AnthropicConfig.base_url` defaults to
`None`, and `AnthropicProvider.from_config` passes it straight through to the SDK constructor
unchanged -- when it is `None`, the SDK supplies its own default internally, and this package
never needs to know or write down what that default is.

## Capability declaration

Unlike the OpenAI-compatible adapter, Anthropic's own API always has every capability this
adapter can declare (native tool calls, JSON-schema-enforced structured output, JSON mode via the
same, vision, streaming, a reasoning-effort knob, a dedicated system-prompt channel, parallel
tool calls, API-backed token counting) -- `AnthropicConfig.capabilities` defaults to `.full()`.
A manifest override (`[llm.providers.<name>.capabilities]`, applied by the registry before
construction, roadmap step 3.4) can still declare a *reduced* set for testing the degradation
ladders or for deliberately pinning a binding to plain-text behaviour; `mapping.py` honours every
such reduction by omitting the wire field the withheld capability would have populated (`tools`/
`tool_choice` behind `native_tool_calls`, `thinking` and `output_config.effort` behind
`reasoning_control`, `output_config.format` behind `schema_output`, an `image` content block
behind `vision`, refusing rather than silently dropping it when absent).

## Prompt caching, thinking and structured output

- **Prompt caching**: the system prompt, when present, always carries one `cache_control:
  {"type": "ephemeral"}` breakpoint (the stable prefix is tools -> system -> messages;
  the whole system prompt is that binding's stable text, never a timestamp).
- **Adaptive thinking**: sent as `thinking: {"type": "adaptive", "display": "summarized"}` only
  when `capabilities.reasoning_control` is True; a `thinking` response block's summary becomes
  `LLMResponse.reasoning_summary` and is never echoed back onto a later request (episodes are
  stateless, codingrules section 8.8).
- **Effort**: `output_config.effort` (`"low"|"medium"|"high"`, from `LLMRequest.effort`) is
  grouped under the same `reasoning_control` capability as thinking, since both are aspects of
  how much hidden reasoning the model does.
- **Structured output**: `output_config.format = {"type": "json_schema", "schema": ...}` is sent
  only when `LLMRequest.response_schema` is set *and* `capabilities.schema_output` is True.
- **Strict tools**: every tool sent gets `strict: True`, with `additionalProperties: false` and
  an empty `required` list added to its schema when the caller's own `ToolDefinition.parameters`
  did not already carry them.

## Streaming

`AnthropicProvider.stream` uses `client.messages.stream(...)`, which `client.py` wraps as an
async generator yielding every raw stream event and then, as its last item, the stream's own
already-accumulated final `Message` (`get_final_message()`). `streaming.py` turns that into
`LLMChunk`s in the same shape `hivemind.llm.fake.FakeLLMProvider.stream` uses: a text chunk per
text delta as it arrives, then one tool-call chunk per call the final message carries, then one
final chunk with usage and stop reason -- so a caller of `LLMProvider.stream` sees the same
ordering regardless of which provider answered. A provider configured with `capabilities.
streaming = False` still implements `stream()`, by yielding the whole response as one chunk from
a single `complete()` call.

## Error mapping

`client.py`'s `map_error` maps every `anthropic.APIError` to a typed `hivemind.llm.errors.
LLMError`: `RateLimitError` (429) becomes `RateLimitedError` (with `retry_after_s` from the
`retry-after` header, when present and numeric); `APIConnectionError` (connection failures and,
via its `APITimeoutError` subclass, timeouts) and any `APIStatusError` with a status `>= 500`
(covering the SDK's several 5xx-shaped exception classes) become `ProviderUnavailableError`; a
`BadRequestError` (400) whose message names a context-length overflow becomes
`ContextTooLongError`; every other 4xx becomes `ProviderRequestError`. `health()` talks to
`GET /v1/models` directly through the SDK (bypassing `map_error`) so it can tell a 429/5xx
(`DEGRADED`) apart from a connection failure or a 401 (`DOWN`).

## Running a live test later

No live test exists yet (roadmap step 3.6 does not ask for one). When one is added, it belongs
behind the `live_llm` pytest marker (already registered in the root `pyproject.toml`):

```python
@pytest.mark.live_llm  # makes a real call to a hosted LLM provider
async def test_anthropic_against_the_real_api() -> None: ...
```

`live_llm` tests never run in the default CI job (`pytest -m "not integration and not e2e and not
live_llm and not local_llm"`, per the phase 3 gates); a developer runs them by hand with a real
API key set.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm/providers/anthropic
```

Recorded fixtures (a text completion, a tool-call completion, a refusal, a thinking block, a
streamed event sequence, an error body) live under
`packages/hivemind/tests/fixtures/llm/anthropic/` and are loaded by `Path(__file__)`-relative
reads; no network is used. Every test injects the SDK's own mock transport
(`anthropic.AsyncAnthropic(..., http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.
MockTransport(handler)))`) rather than mocking `httpx`/`httpx2` directly, since `anthropic` 1.x is
built on the `httpx2` fork, not `httpx`.
