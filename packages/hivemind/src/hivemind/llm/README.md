# hivemind.llm

The llm package defines the provider-agnostic boundary every model call in the Hive crosses: our
own request/response/message shapes, the declared capability and health shapes, the one
`LLMProvider` protocol every adapter implements, its typed error tree, a scriptable fake for
tests and demos, and `BoundModel`, the resolved slot-plus-provider value later roadmap steps
build. No vendor SDK is imported here or anywhere else outside `llm/providers/<name>/`.

## Public API (roadmap steps 3.2, 3.3)

- **Message shapes** (`hivemind.llm.models`): `Role`, `TextPart`, `ImagePart`, `ToolCallPart`,
  `ToolResultPart`, `ContentPart` (a discriminated union on `kind`), `Message` (with the
  `Message.text(role, text)` helper), `JsonObject` (`dict[str, pydantic.JsonValue]`, the one
  shape for arbitrary JSON), `ToolDefinition`, `ToolCall`, `StopReason`, `Usage` (with a
  cost-aware `__add__`), `LLMRequest`, `LLMResponse` (with `.text`/`.tool_calls` properties) and
  `LLMChunk`, one streaming delta.
- **Capabilities and health** (`hivemind.llm.capabilities`): `ProviderCapabilities`, with
  `.full()` and `.none()` classmethods for the strongest and weakest declared shapes the
  degradation ladders (a later roadmap step) are proven against; `HealthState` and
  `ProviderHealth`, Appendix C's "Provider health" machine.
- **The one door** (`hivemind.llm.provider`): `LLMProvider`, a `typing.Protocol` with `name`,
  `capabilities`, `complete`, `stream` (a plain `def` returning `AsyncIterator[LLMChunk]`, so an
  implementation may be an `async def` generator function), `count_tokens` and `health`.
- **Errors** (`hivemind.llm.errors`): `LLMError` and its tree -- `RateLimitedError`,
  `ProviderUnavailableError`, `ContextTooLongError`, `RefusedError`, `MalformedOutputError`,
  `UnknownProviderError`, `OfflineViolationError` -- every one carrying `provider`, the manifest's
  `[llm.providers.<name>]` key.
- **The fake** (`hivemind.llm.fake`): `FakeLLMProvider`, an honestly-capability-limited,
  scriptable `LLMProvider`. `script(*responses)` queues `LLMResponse`/`LLMError` values FIFO;
  `set_outage(is_down)` simulates a total outage; `calls` records every request seen. A
  `ProviderCapabilities.none()` fake drops `ToolCallPart`s it was scripted to return and never
  claims to count tokens; a script that runs dry raises `ProviderUnavailableError`, never
  `IndexError`. `text_response`/`tool_call_response` build canned `LLMResponse`s to script.
- **Slots** (`hivemind.llm.slots`): `BoundModel`, a frozen dataclass carrying the slot, the
  manifest binding key, a live `LLMProvider`, the model id, effort, context window, per-million
  token prices copied from the Forage map at bind time, and an optional `fallback` chain.
  `resolve(slot, manifest) -> BoundModel` is **not** implemented here; it is roadmap step 3.4.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm
```

Coverage floor is 95% (codingrules section 14.1, "pure cores"):

```bash
COVERAGE_FILE=.coverage.llm uv run --frozen pytest -p no:cacheprovider --cov=hivemind.llm \
    --cov-report=term-missing packages/hivemind/tests/unit/llm
```
