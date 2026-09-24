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

## Hosted headroom (roadmap step 4.7a)

`OpenAICompatClient.post_json_with_headers` returns a completion's response headers alongside its
parsed body, so `provider.py` can build an `LLMResponse.rate_limit`
(`hivemind.llm.models.RateLimitSnapshot`) from `rate_limit.py`'s
`rate_limit_from_headers` -- a sibling of `mapping.py`, split out purely by codingrules 5.1's size
limit, not by a difference in responsibility. It reads `x-ratelimit-remaining-requests`,
`x-ratelimit-remaining-tokens` and their `-reset` siblings (a Go-style duration like `"6m0s"`,
resolved against the provider's own clock), or `None` when neither remaining-count header was
sent -- which is every server this adapter is actually tested against (Ollama, vLLM, llama.cpp,
LM Studio never send these). A 429's `retry_after_s` (already parsed, see "Error mapping" below)
feeds the Fanner's own `ForageMap.throttle` instead: that source's headroom reads as zero on the
Forage map until the window passes, recorded as one `llm.throttled` trail event, with no code in
this package aware that happens -- see `hivemind.llm.fanner.lane.FannerLane`.

## Images and audio (roadmap step 6.5)

`media.py`, a sibling of `mapping.py` split out by size, maps an `ImagePart` to an `image_url`
part (a data URL) behind `capabilities.vision` and an `AudioPart` to an `input_audio` part (wav or
mp3) behind `capabilities.audio`, refusing either with a `ProviderRequestError` when the binding
does not declare it. A tool result's media (`ToolResultPart.media`: a screenshot from `see`, a
recording from `listen`) cannot ride in a `role: "tool"` message, which is a plain string on this
wire, so one `role: "user"` message follows a turn's tool messages and carries all of it, each
run labelled with the call id that returned it.

## Error mapping

`client.py` maps every `httpx` failure to a typed `hivemind.llm.errors.LLMError`: a connection
failure or a 5xx becomes `ProviderUnavailableError`; a 429 becomes `RateLimitedError` (with
`retry_after_s` from the `Retry-After` header, when present); a 400 whose message mentions context
length becomes `ContextTooLongError`; every other 4xx becomes `ProviderRequestError`. `health()`
talks to `httpx` directly (bypassing that mapping) so it can tell a 5xx (`DEGRADED`) apart from a
connection failure or a 401 (`DOWN`).

## Transcription: `/audio/transcriptions` (roadmap step 6.5a)

The `transcription/` sub-package is the same kind's transcriber for `ModelSlot.TRANSCRIBER`
(ADR-0033): hosted Whisper APIs and local speech servers (faster-whisper-server, whisper.cpp's
server, vLLM serving a Whisper model) all accept the same multipart `POST /audio/transcriptions`,
relative to the same `/v1`-terminated `base_url` the chat side uses.

- **`OpenAICompatTranscriptionConfig`**: `base_url`, `model` (the transcriber binding's own model
  id: the registry builds one transcriber per (provider, model) pair, never from `default_model`,
  which names a chat model), `api_key` (`SecretStr | None`), `timeout_s`, and `capabilities`
  (`hivemind.llm.transcription.TranscriptionCapabilities`: no native streaming, phrase-level
  segments and ten-minute clips unless overridden).
- **`OpenAICompatTranscription`**: the `TranscriptionProvider`, with `.create(name, config,
  clock)`. `transcribe` uploads the clip as `file` with `model`, the optional `language` and
  `response_format=verbose_json`; `stream` buffers the chunks and uploads once; `health()` GETs
  `/models` (a speech server without that route reads `DEGRADED`, not `DOWN`).

`transcription/mapping.py` is the only file where these wire names appear. It accepts a
`verbose_json` reply (time-stamped `segments`, each `avg_logprob` turned into a 0-to-1 confidence)
and a plain `json` reply (text only, mapped to one segment spanning the clip). A caller's language
hint wins; a reply's `language` is kept only when it is an ISO 639 code, since hosted APIs report
an English name (`"english"`) instead. `transcription/client.py` maps failures exactly as the chat
client does: a transport failure or 5xx is `ProviderUnavailableError`, a 429 is
`RateLimitedError` with its `Retry-After`, any other 4xx is `ProviderRequestError`; a 2xx body
that is not one JSON object is refused. A reply with no `text` raises `MalformedOutputError`
whose `raw` names only the reply's fields, never its words.

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

Recorded fixtures (a completion, a tool-call completion, a streamed SSE transcript, an error body,
and roadmap step 6.5a's `transcription_verbose.json`/`transcription_text.json` replies) live under
`packages/hivemind/tests/fixtures/llm/openai_compat/` and are loaded by `Path(__file__)`-relative
reads; no network is used. `httpx.MockTransport` stands in for the wire in every test. The
transcriber's tests live in `packages/hivemind/tests/unit/llm/providers/openai_compat/
transcription/` and, with the other two transcribers, in
`packages/hivemind/tests/contracts/test_transcription_provider_contract.py`.
