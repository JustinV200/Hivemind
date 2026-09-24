# hivemind.llm

The llm package defines the provider-agnostic boundary every model call in the Hive crosses: our
own request/response/message shapes, the declared capability and health shapes, the one
`LLMProvider` protocol every adapter implements, its typed error tree, a scriptable fake for
tests and demos, and `BoundModel`, the resolved slot-plus-provider value later roadmap steps
build. Its `transcription` sub-package is the same boundary for audio (`TranscriptionProvider`,
on the `TRANSCRIBER` slot). No vendor SDK is imported here or anywhere else outside
`llm/providers/<name>/`.

## Public API (roadmap steps 3.2, 3.3)

- **Message shapes** (`hivemind.llm.models`): `Role`, `TextPart`, `ImagePart`, `ToolCallPart`,
  `ToolResultPart`, `ContentPart` (a discriminated union on `kind`), `Message` (with the
  `Message.text(role, text)` helper), `JsonObject` (`dict[str, pydantic.JsonValue]`, the one
  shape for arbitrary JSON), `ToolDefinition`, `ToolCall`, `StopReason`, `Usage` (with a
  cost-aware `__add__`), `LLMRequest`, `LLMResponse` (with `.text`/`.tool_calls` properties and,
  roadmap step 4.7a, an optional `rate_limit: RateLimitSnapshot`) and `LLMChunk`, one streaming
  delta. `RateLimitSnapshot` (roadmap step 4.7a) is a hosted provider's own reported headroom --
  requests/tokens remaining and when each window resets -- read from that call's response
  headers by each adapter's own `mapping.py`/`client.py`, `None` field by field wherever the
  provider (every local server) published nothing.
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
  schema, *, gate=None, options=None)` walks NATIVE (schema-enforced) -> JSON_MODE (JSON-enforced,
  pydantic-validated) -> PROMPTED (fenced ` ```json ` block, extracted and validated), retrying
  each rung with the validation error fed back as a correction before stepping down. Returns a
  `StructuredResult` (`value`, `rung`, `attempts`, `usage`); raises `MalformedOutputError` once
  PROMPTED's own retries are exhausted. `Rung`, `NATIVE_SCHEMA_RETRIES`, `JSON_MODE_RETRIES`,
  `PROMPTED_JSON_RETRIES` are the rung enum and its commented retry-count constants.
  `LadderOptions(observer=None, context=None)` bundles the two rarer settings (codingrules 5.1's
  parameter limit): `observer` as before, and `context` (roadmap step 5.0b) -- forwarded to every
  attempt's own `schema.model_validate` as pydantic's validation context, so a schema's own
  `model_validator` can read `ValidationInfo.context` (`hivemind.queen.planner.plan.plan_goal` is
  the one caller that sets it, carrying `PlanBrief.scratch_root` to `PlannedTask`'s own rule).
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
  Seats are one budget per provider, shared by every binding on it. `Fanner.lane(tempo, grant_id=,
  goal_id=)` hands one bee or call site a `FannerLane`, attributed to a grant and goal when the
  caller names one (roadmap step 4.8's own wiring step: `hivemind.wardens.spawn.spawn.
  spawn_sub_bee` builds one fresh lane per sub-bee from `WardenDeps.lane_for_grant`, so every
  `llm.call` it makes carries its own grant and goal id); `in_flight(provider)` and
  `queued(provider)` are the introspection `hive llm` (a later roadmap step) and tests read.
- **`FannerLane`**: implements `hivemind.llm.ladders.gate.CallGate` exactly, so a ladder can take
  a lane as its `gate=` with no code of its own aware the Fanner exists. `complete(bound, request)`
  walks `bound`'s fallback chain, spilling to the next binding in one of four cases -- the source
  is currently throttled (`SpillReason.THROTTLED`, checked first), its grade is below the calling
  tempo's floor, the model is not loaded there (read as `spec.seats == 0`, since `ModelSourceSpec`
  carries no separate "loaded" field), or seat-queueing ate more than `SPILL_WAIT_FRACTION` of the
  tempo's latency budget -- then rate-limits per provider, queues for a seat ordered by tempo,
  makes the call, and records an `llm.call` or `llm.spill` `LlmEvent`. A `RateLimitedError` from
  the call itself (roadmap step 4.7a) throttles the source that raised it (`ForageMap.throttle`,
  masking its headroom to zero until the window passes) and records `llm.throttled`, then moves
  to the next binding if one exists. With no fallback left it either proceeds on the current
  binding regardless (a static spill reason) or re-raises (a `RateLimitedError` that already
  happened): the Fanner never invents a call, but it cannot un-happen a real failure either.
- **`RateLimit`**: a provider's `requests_per_minute`/`tokens_per_minute` ceiling, straight from
  its manifest row; both `None` means unlimited. Roadmap step 4.7a:
  `ProviderRateLimiter.observe_snapshot` prefers a call's own reported `RateLimitSnapshot` figures
  over this limiter's refill-based guess once it has them, so the manifest row is only ever a
  starting guess for an already-metered dimension, never a permanent ceiling.
- **`SpillReason`**: `THROTTLED`, `GRADE_BELOW_FLOOR`, `MODEL_NOT_LOADED`, `QUEUE_WAIT_EXCEEDED`.
- **`LlmEventRecorder`**, **`NullLlmEventRecorder`**, **`TrailLlmEventRecorder`**: the trail-write
  seam a lane calls with a raw `(kind, subject_id, payload)` triple, mirroring
  `hivemind.llm.ladders.observer`'s `LadderObserver` shape; `TrailLlmEventRecorder` lifts `slot`,
  `provider` and `usage` out of `payload` into `LlmEvent`'s own typed fields. `LLM_THROTTLED_KIND`
  (`"llm.throttled"`, roadmap step 4.7a) is the third occurrence kind, alongside `LLM_CALL_KIND`
  and `LLM_SPILL_KIND`.
- **`CompositeLlmEventRecorder`** (roadmap step 4.8's own wiring step): fans every
  `LlmEventRecorder` call out to several recorders, in the order given, so `FannerLane` still
  calls exactly one recorder while more than one actually listens.
  `hivemind.cli.compose.deps.build_fanner` chains `hivemind.queen.forage.ledger.recorder.
  LedgerRecorder` with a `TrailLlmEventRecorder` through this class, so a completed `llm.call`
  both updates the Forage ledger's live seat and spend books and lands on the trail.
- **`DEFAULT_SEATS`** (`1`): what a provider absent from `FannerDeps.seats` gets. Roadmap step
  4.7a's own **`DEFAULT_THROTTLE_S`** (`60.0`): how long `FannerLane` throttles a source for when
  a `RateLimitedError` carries no `retry_after_s` hint.

## Closing providers at shutdown

Every `LLMProvider` and `TranscriptionProvider` has `aclose()`: idempotent, and the provider's last
call. The openai_compat adapters close their `httpx.AsyncClient`, the anthropic adapter closes its
SDK client (`AsyncAnthropic.close()`), the fakes only record it (`is_closed`).
`ProviderRegistry.aclose(timeout_s=PROVIDER_CLOSE_TIMEOUT_S)` closes everything the registry
constructed (chat providers and transcribers alike), each under its own timeout; a failed close is
logged at warning (`llm.provider_close_failed`, with the provider name) and never stops the next
one; what was closed is forgotten, so a second call is a no-op. The composition root calls it once
when the Hive stops, so no pooled connection outlives it.

## Transcription (roadmap step 6.5a, the subset step 10.5f needs)

Audio in, text out, on the `TRANSCRIBER` slot. The pieces, and the one call a composition root
makes:

```python
transcriber = bind_transcriber(registry, fanner, Tempo(latency_budget_s=10.0))  # at startup
clip = AudioClip.from_upload(data, "audio/webm;codecs=opus", duration_s=4.2)  # per clip
transcript = await transcriber.transcribe(clip, language="en-US")
```

- **Models** (`hivemind.llm.transcription.models`): `AudioClip` (bytes, an `AudioMediaType`, a
  duration), built from a device's upload by `AudioClip.from_upload`, which names the format,
  checks `MAX_CLIP_BYTES` (25 MB) and `MAX_CLIP_SECONDS` (600 s) and fills the duration in: read
  from the header for WAV (`wav_duration_s`, so a WAV's length is measured, never claimed), taken
  from the sender for a compressed format. Every refusal is an `InvalidAudioClipError` with a
  `ClipProblem` (`TOO_LARGE`, `TOO_LONG`, `UNSUPPORTED_FORMAT`, `MISSING_DURATION`, `MALFORMED`)
  for the caller to map to its own response. `Transcript` (text, language, duration, timestamped
  `TranscriptSegment`s) comes back. `AudioChunk` and `clip_from_chunks` gather a push-to-talk
  stream into one clip. Audio bytes and transcript text never appear in a `repr`.
- **Formats** (`AudioMediaType`): WAV, Ogg/Opus, WebM/Opus, MP3 and M4A; `parse` folds every known
  spelling (`audio/x-wav`, `audio/webm;codecs=opus`, ...) onto one and refuses the rest.
- **The door** (`TranscriptionProvider`): `name`, `capabilities` (`TranscriptionCapabilities`:
  segments, language detection, accepted formats; `full()`/`none()`), `transcribe(clip, language)`,
  `stream(chunks, language)` (gathers the chunks into one clip for now: no provider recognises
  incrementally yet), `health()`, `aclose()`. A language hint is reduced to its primary subtag
  (`normalise_language`); an implausible one is dropped and the provider detects instead.
- **Implementations**: `FakeTranscription` (scripted transcripts, a plain string expanded against
  the clip it answers; `set_outage`; scripted `LLMError`s; honest about its capabilities) and
  `hivemind.llm.providers.openai_compat.OpenAICompatTranscription` (hosted speech-to-text APIs and
  local Whisper servers alike, over `POST {base_url}/audio/transcriptions`).
- **Binding** (`ProviderRegistry.transcriber()`): walks `[llm.slots.transcriber]` and its fallback
  chain (`hivemind.llm.slots.walk_chain`) to a `BoundTranscriber`, building each link through
  `default_transcriber_factories()` (`fake`, `openai_compat`), cached per `(provider, model)`. A
  kind with no transcription adapter (`anthropic`) is refused there, with
  `TranscriptionUnsupportedError`, never at the first call. A link is priced from the Forage map's
  `ModelCost.cost_per_audio_minute_usd`.
- **Metering** (`hivemind.llm.fanner.MeteredTranscriber`, built by `bind_transcriber`): itself a
  `TranscriptionProvider`, so the Entrance's voice route depends only on the protocol (and its
  tests pass a `FakeTranscription`). Every call takes a seat on its provider (shared with that
  provider's chat calls), waits under its rate limit, spills and throttles exactly like a chat
  call, and records one `llm.call` on `TRANSCRIBER`: slot, provider, `latency_s`, the audio's
  seconds as `audio_s`, and `usage` with zero tokens and the cost (0 when unpriced). The trail's
  `LlmUsage` has no audio field, which is why the seconds ride beside it. Never the audio, never
  the transcript.

What remains for roadmap step 6.5a proper:

- **`llm/providers/whisper/`**, the in-process faster-whisper adapter (an optional extra, GPU when
  present, one seat per loaded model instance). Not built in this subset: it needs the model
  weights, which cannot be fetched in this environment. It joins `default_transcriber_factories()`
  under a new provider kind (a manifest `ProviderKind` change), and the contract suite then runs
  over three implementations instead of two.
- Listing transcription sources on the Forage map with their own grade, and a distance measure
  that fits audio (a real-time factor rather than `Distance.tokens_per_s`, which this subset
  leaves untouched for a transcriber source).
- Per-provider transcription capability overrides in the manifest, and a `stream` that recognises
  speech incrementally where a provider can.
- Buzz's `listen` (roadmap step 6.5), the second caller, with grant and goal attribution on its
  `llm.call` events.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm
uv run --frozen pytest packages/hivemind/tests/contracts/test_transcription_provider_contract.py
```

Coverage floor is 95% (codingrules section 14.1, "pure cores"):

```bash
COVERAGE_FILE=.coverage.llm uv run --frozen pytest -p no:cacheprovider --cov=hivemind.llm \
    --cov-report=term-missing packages/hivemind/tests/unit/llm
```
