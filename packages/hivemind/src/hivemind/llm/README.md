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
  `UnknownProviderError`, `OfflineViolationError`, `ProviderRequestError` (a provider's
  non-retryable 4xx, e.g. an OpenAI-compatible server's own error body or its probe-time refusal
  of an unlisted model) -- every one carrying `provider`, the manifest's `[llm.providers.<name>]`
  key.
- **The fake** (`hivemind.llm.fake`): `FakeLLMProvider`, an honestly-capability-limited,
  scriptable `LLMProvider`. `script(*responses)` queues `LLMResponse`/`LLMError` values FIFO;
  `set_outage(is_down)` simulates a total outage; `calls` records every request seen. A
  `ProviderCapabilities.none()` fake drops `ToolCallPart`s it was scripted to return and never
  claims to count tokens; a script that runs dry raises `ProviderUnavailableError`, never
  `IndexError`. `text_response`/`tool_call_response` build canned `LLMResponse`s to script.
- **Slots** (`hivemind.llm.slots`): `BoundModel`, a frozen dataclass carrying the slot, the
  manifest binding key, a live `LLMProvider`, the model id, effort, context window, per-million
  token prices copied from the Forage map at bind time, and an optional `fallback` chain.

## Public API (roadmap step 3.4)

- **Resolving a slot** (`hivemind.llm.slots`): `resolve(slot, bindings, providers, map=None) ->
  BoundModel` walks `slot`'s own `[llm.slots]` row and its whole fallback chain to a `BoundModel`;
  `resolve_key(key, slot, bindings, providers, map=None) -> BoundModel` does the same starting
  from a named binding (`"local_worker"`) instead, for a rebind that still records which slot the
  result serves. `bindings` is `Iterable[hivemind.forage.map.SlotBinding]` -- the forage-side view
  of `[llm.slots]`, not the manifest's own `LlmSection` -- because codingrules section 4 places
  `llm` and `manifest` as independent Layer 1 siblings that may not import each other; the
  composition root converts a loaded manifest's slot table into these rows once, the same way
  `hivemind.forage.map.ForageMap.for_slot` already does. `providers` is a `ProviderLookup`
  (`__call__(name) -> LLMProvider`); `map`, an optional `hivemind.forage.map.ForageMap`, prices
  every binding in the chain, leaving `cost_per_million_*_usd` `None` when it is absent or has no
  matching source. `UnresolvableSlotError` guards a hand-built `bindings` table that names no row
  for the starting key, or whose fallback chain cycles -- a condition the manifest's own
  validators already prevent for a loaded Hive Manifest.
- **The provider registry** (`hivemind.llm.registry`): `ProviderRegistry` constructs a provider
  lazily on first use and caches it; `provider(name)` raises `UnknownProviderError` for an unknown
  name or a `kind` with no factory, and `OfflineViolationError` when `[llm] offline = true` and
  that provider's `base_url` is not provably loopback -- enforced a second time here, on top of
  the manifest's own load-time check, so a provider whose *default* endpoint is remote (a future
  `ANTHROPIC` adapter with no `base_url` set) is caught too. `bound(slot)`/`bound_for_key(key,
  slot)` call `resolve`/`resolve_key`. `RegistryDeps` groups `factories` (a `kind -> ProviderFactory`
  table), `environ` (the only place this module reads a `HIVEMIND_*` variable, mirroring
  `hivemind.manifest.env.provider_api_key`'s derivation), `clock` and an optional `map`.
  `ProviderConfig` is this package's own, decoupled mirror of one `[llm.providers.<name>]` row
  (`ProviderKind` mirrors the manifest's own `Literal`); `apply_overrides(base, overrides)`
  replaces every field named in a `hivemind.manifest.schema.llm.CapabilityOverrides.
  as_overrides()`-shaped mapping. `default_factories()` covers `"fake"` and `"openai_compat"`;
  `PENDING_KINDS` (`frozenset({"anthropic"})`) names what it does not yet cover until roadmap step
  3.6's adapter lands.

## Public API (roadmap step 3.5)

The degradation ladders (`hivemind.llm.ladders`): the fallback logic codingrules section 8.6
("degrade by ladder, in one place") requires, so a caller never branches on provider name or asks
whether a binding can enforce a schema or call a tool natively.

- **Structured output** (`hivemind.llm.ladders.structured`): `complete_structured(bound, request,
  schema, *, gate=None, observer=None)` walks NATIVE (schema-enforced) -> JSON_MODE (JSON-enforced,
  pydantic-validated) -> PROMPTED (fenced ` ```json ` block, extracted and validated), retrying
  each rung with the validation error fed back as a correction before stepping down. Returns a
  `StructuredResult` (`value`, `rung`, `attempts`, `usage`); raises `MalformedOutputError` once
  PROMPTED's own retries are exhausted. `Rung`, `NATIVE_SCHEMA_RETRIES`, `JSON_MODE_RETRIES`,
  `PROMPTED_JSON_RETRIES` are the rung enum and its commented retry-count constants.
- **Tool calls** (`hivemind.llm.ladders.tools`): `run_tool_loop(bound, request, tools, executor,
  options=None)` runs the native tool-call protocol when the binding declares
  `native_tool_calls`, else a prompted protocol (a preamble listing every tool, fenced ` ```tool `
  blocks parsed back out). Every call, either protocol, is validated with `validate_arguments`
  before it reaches `ToolExecutor.execute`; an invalid call comes back as an `is_error`
  `ToolResultPart`, never a raised exception. Returns a `ToolLoopResult` (`final_text`, `rounds`,
  `calls`, `usage`, `stop_reason`, `is_exhausted`). `ToolLoopOptions` groups `max_rounds`, `gate`
  and `observer`; `MAX_TOOL_ROUNDS_DEFAULT` is its default round cap.
- **Argument validation** (`hivemind.llm.ladders.extraction`): `validate_arguments(schema,
  arguments) -> tuple[str, ...]`, a documented JSON-schema subset (`type`, `properties`,
  `required`, `additionalProperties: false`, `enum`, one level deep) with no `jsonschema`
  dependency (see `docs/adr/0009-structured-output-and-tool-call-degradation-ladders.md`).
- **The call seam** (`hivemind.llm.ladders.gate`): `CallGate`, a `typing.Protocol` with one
  `complete(bound, request)` method every ladder calls through instead of `bound.provider.complete`
  directly, and `DirectCallGate`, the unmetered default. The Fanner (roadmap step 3.12a) will
  implement `CallGate` so every call is seat-metered without either ladder knowing.
- **Reporting a step-down** (`hivemind.llm.ladders.observer`): `LadderObserver`, a `typing.
  Protocol` with one `on_fallback(note)` method; `FallbackNote`, the frozen value a ladder builds
  (`slot`, `from_binding`, `to_binding`, `from_rung`, `to_rung`, `reason: FallbackReason`);
  `NullLadderObserver`, the default that discards every note; `TrailLadderObserver`, which records
  each one as an `llm.fallback` `LlmEvent` on the Pheromone Trail.

## Public API (roadmap step 3.12a)

The Fanner (`hivemind.llm.fanner`): the seat meter every model call in the Hive passes through
(codingrules section 8.10, "the only place seat counts are enforced"), named after the bees that
fan their wings to regulate the hive's airflow.

- **`Fanner`**: owns every provider's `SeatMeter` and `ProviderRateLimiter` for one Hive
  process, built once from a `FannerDeps` (`map`, `seats`, `limits`, `clock`, `recorder`).
  Seats are one budget per provider, shared by every binding on it. `Fanner.lane(tempo)` hands
  one bee or call site a `FannerLane`; `in_flight(provider)` and `queued(provider)` are the
  introspection `hive llm` (a later roadmap step) and tests read.
- **`FannerLane`**: implements `hivemind.llm.ladders.gate.CallGate` exactly, so a ladder can take
  a lane as its `gate=` with no code of its own aware the Fanner exists. `complete(bound, request)`
  walks `bound`'s fallback chain, spilling to the next binding in exactly three cases -- the
  source's grade is below the calling tempo's floor, the model is not loaded there (read as
  `spec.seats == 0`, since `ModelSourceSpec` carries no separate "loaded" field), or seat-queueing
  ate more than `SPILL_WAIT_FRACTION` of the tempo's latency budget -- then rate-limits per
  provider, queues for a seat ordered by tempo, makes the call, and records an `llm.call` or
  `llm.spill` `LlmEvent`. With no fallback left it proceeds on the current binding regardless: the
  Fanner never refuses a call.
- **`RateLimit`**: a provider's `requests_per_minute`/`tokens_per_minute` ceiling, straight from
  its manifest row; both `None` means unlimited.
- **`SpillReason`**: `GRADE_BELOW_FLOOR`, `MODEL_NOT_LOADED`, `QUEUE_WAIT_EXCEEDED`.
- **`LlmEventRecorder`**, **`NullLlmEventRecorder`**, **`TrailLlmEventRecorder`**: the trail-write
  seam a lane calls with a raw `(kind, subject_id, payload)` triple, mirroring
  `hivemind.llm.ladders.observer`'s `LadderObserver` shape; `TrailLlmEventRecorder` lifts `slot`,
  `provider` and `usage` out of `payload` into `LlmEvent`'s own typed fields.
- **`DEFAULT_SEATS`** (`1`): what a provider absent from `FannerDeps.seats` gets.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm
```

Coverage floor is 95% (codingrules section 14.1, "pure cores"):

```bash
COVERAGE_FILE=.coverage.llm uv run --frozen pytest -p no:cacheprovider --cov=hivemind.llm \
    --cov-report=term-missing packages/hivemind/tests/unit/llm
```
