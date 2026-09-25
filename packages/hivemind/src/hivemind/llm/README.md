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
  as_overrides()`-shaped mapping. `default_factories()` covers every chat kind (`"fake"`,
  `"openai_compat"`, `"anthropic"`); `PENDING_KINDS` names any kind neither factory table covers
  (none today; see roadmap step 6.5a below for the transcription table).

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

## Public API (roadmap step 6.5a)

The slot that hears, `ModelSlot.TRANSCRIBER`
(`docs/adr/0033-transcription-provider-whisper-first.md`): audio in, text out, with its own
protocol rather than a chat call. Everything below is re-exported from `hivemind.llm`.

- **The boundary** (`hivemind.llm.transcription`, a package): HiveMind's own values --
  `AudioClip` (a whole WAV file plus the sample rate, channels and duration its own header states,
  checked against that header, its bytes never in a `repr`; `from_wav`/`from_pcm` build one),
  `AudioChunk` (raw 16-bit PCM for push-to-talk), `Transcript` and `TranscriptSegment` (time-ordered
  phrases with a 0-to-1 `confidence`) -- `TranscriptionCapabilities` (`streaming`,
  `word_timestamps`, `max_clip_s`, `languages`) with `check_request`, the guard every adapter runs
  before any work, and the `TranscriptionProvider` protocol (`name`, `capabilities`,
  `transcribe(clip, language=None)`, `stream(chunks, language=None)`, `health()`). An adapter
  without native streaming takes `stream_by_buffering`: buffer while the human talks, transcribe
  once. `FakeTranscription` answers scripted transcripts in order, then silence (or a given
  default); it refuses what a real adapter refuses and reports the clip's duration and the
  caller's hint as a real adapter does.
- **The slot**: `BoundTranscriber` (slot, binding, provider, model, fallback) and
  `resolve_transcriber(bindings, lookup, key=None)`, which walks the same `[llm.slots]` rows and
  chain rules as `resolve`. `ProviderRegistry.transcriber(name, model)` builds a transcriber
  lazily from `RegistryDeps.transcription_factories` (`default_transcription_factories()`:
  `fake`, `openai_compat`, and the transcription-only `whisper_local`), one instance per
  (provider, model) pair; `bound_transcriber()` resolves the slot through it. A chat-only kind
  (`anthropic`) raises `TranscriptionUnsupportedError` naming the kind. `IN_PROCESS_KINDS`
  (`whisper_local`) are local by construction: offline mode checks no `base_url` for them and
  turns their downloads off instead.
- **The seam** (`TranscriptionGate`): `DirectTranscriptionGate` (unmetered) and the Fanner's
  `FannerTranscriptionGate(fanner, tempo, grant_id=, goal_id=)`, which takes a seat from the same
  per-provider `SeatMeter` chat calls use, spills by the lane's rules, throttles a rate-limited
  source, and records one `llm.call` per transcription (slot `TRANSCRIBER`, provider, model, zero
  tokens, `audio_seconds`, `latency_s`; never audio, never text). Both gates move along the
  fallback chain on `ProviderUnavailableError`/`RateLimitedError`, since no ladder sits above a
  transcription.
- **Adapters**: `hivemind.llm.providers.whisper` (faster-whisper in process) and
  `hivemind.llm.providers.openai_compat.transcription` (`/audio/transcriptions`); see their READMEs.
- **`ProviderCapabilities.audio`**: whether a chat provider takes audio parts directly (`full()`
  True, `none()` False, False when unset); the Anthropic and OpenAI-compatible chat adapters
  declare it False, so a caller holding audio transcribes it first.

## Public API (roadmap step 7.1, ADR-0036)

The embedding boundary (`hivemind.llm.embedding`): turns text into vectors, the same "one door"
shape as the chat boundary above, but its own package because it needed several files.

- **The boundary models** (`hivemind.llm.embedding.models`): `EmbeddingRequest` (`texts: tuple[str,
  ...]`, 1 to `MAX_EMBED_TEXTS = 256`, each at least one character) and `EmbeddingResponse`
  (`vectors`, one tuple of floats per text in order; `model`; `dimensions`; `usage: Usage`, a
  validator rejecting a vector whose length or finiteness disagrees with `dimensions`).
- **Capabilities** (`hivemind.llm.embedding.capabilities`): `EmbeddingCapabilities` (`dimensions:
  int | None`, `max_batch`, `max_input_chars`, `normalized`).
- **The one door** (`hivemind.llm.embedding.provider`): `EmbeddingProvider`, a `typing.Protocol`
  with `name`, `capabilities`, `embed` and `health`. Implementations: `hivemind.llm.providers.
  openai_compat.embedding.OpenAICompatEmbedding` (`/embeddings` on the same servers the chat
  adapter speaks to), `hivemind.llm.providers.sentence_transformers.SentenceTransformersEmbedding`
  (in-process, an optional extra), and `FakeEmbedding` below.
- **The fake** (`hivemind.llm.embedding.fake`): `FakeEmbedding`, a deterministic feature-hashing
  `EmbeddingProvider` -- word tokens and character trigrams hashed with `hashlib.blake2b` (never
  Python's own salted `hash()`) into an L2-normalised vector, so lexically similar texts land near
  each other reproducibly. `set_available(False)` simulates an outage; `calls` counts every
  `embed()` call, so a test can assert batching.
- **The resolved binding** (`hivemind.llm.embedding.bound`): `BoundEmbedder`, mirroring
  `BoundModel` except a `.fallback` link is only ever built when it serves the *same* model id
  (ADR-0036: two embedding models' vectors are not comparable).
- **The call seam** (`hivemind.llm.embedding.gate`): `EmbedGate` and `DirectEmbedGate`, mirroring
  `CallGate`/`DirectCallGate`; `DirectEmbedGate` walks `bound.fallback` on
  `ProviderUnavailableError` only (safe here, unlike a chat spill, because the fallback is
  guaranteed same-model).
- **Resolving the slot** (`hivemind.llm.registry`): `ProviderRegistry.embedder(slot=ModelSlot.
  EMBEDDER) -> BoundEmbedder` walks `[llm.slots]` the same way `bound()` does, but caches a
  provider per `(name, model)` pair, raises `EmbeddingUnsupportedError` when the primary binding's
  kind has no embedding factory (`anthropic` today), and cuts a fallback chain at the first
  different model id. `EMBEDDING_ONLY_KINDS` (`sentence_transformers`, no chat factory) and
  `IN_PROCESS_KINDS` (provably local under `[llm] offline = true` with no `base_url` at all) are
  both mirrored in `hivemind.manifest.schema.llm`, kept in sync by a dedicated test.
- **`hive llm test embedder`**: embeds one short text through the resolved slot and prints the
  model, its vector dimension and the latency, in place of a completion's usage and reply.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/llm
uv run --frozen pytest packages/hivemind/tests/contracts/test_transcription_provider_contract.py
```

Transcription tests hear generated clips (`builders.audio`: a second of silence, a second of a
tone), never a recording of anyone.

Coverage floor is 95% (codingrules section 14.1, "pure cores"):

```bash
COVERAGE_FILE=.coverage.llm uv run --frozen pytest -p no:cacheprovider --cov=hivemind.llm \
    --cov-report=term-missing packages/hivemind/tests/unit/llm
```
