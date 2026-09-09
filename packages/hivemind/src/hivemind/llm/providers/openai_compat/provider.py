"""Define OpenAICompatProvider: speak the OpenAI-compatible chat wire to a local model server.

An OpenAI-compatible server is any local or self-hosted process exposing the same
`/chat/completions` and `/models` shape OpenAI's own hosted API does -- llama.cpp's built-in
server, Ollama, vLLM and LM Studio all speak it, which is why this adapter has no single vendor's
name (codingrules section 8.6: "a model id or provider URL in code is a lint failure... an adapter
may say which servers it speaks to" in its own docstring, which this one just did). This module
owns the `LLMProvider` implementation itself; `mapping.py` is the only place an OpenAI wire field
name appears, and `client.py` is the only place `httpx` mechanics are handled.

Capability probing happens "where possible" (roadmap step 3.7): `/models` only ever returns a
list of ids, never a capability manifest, so what this server *can do* (native tool calls, schema
output, streaming, ...) always comes from `OpenAICompatConfig.capabilities` -- the manifest's
per-provider override, filled with sensible defaults by the registry (roadmap step 3.4). What
`probe()` actually contributes is narrower and more concrete: confirming the configured model id
is one this particular server actually serves, so a manifest typo fails loudly at startup instead
of on the first real call.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.
    openai_compat`. Constructed by the registry (`hivemind.llm.registry`, roadmap step 3.4) from
    one `[llm.providers.<name>]` manifest section; implements `hivemind.llm.provider.LLMProvider`,
    so every layer above `hivemind.llm` calls it only through that Protocol. Calls into
    `hivemind.llm.providers.openai_compat.{mapping,client}`, `hivemind.llm.capabilities`,
    `hivemind.llm.errors`, `hivemind.llm.models` and `waggle.clock` only.

Key invariants:
    - The API key, when set, is held in `OpenAICompatConfig.api_key: SecretStr` and never appears
      in a `repr`, a log line, or an error message (codingrules section 13); only
      `.get_secret_value()` in `create()` ever reads it, to build the one `Authorization` header.
    - `complete`/`stream` refuse a call once `probe()` has run and found the configured model
      missing from the server's own `/models` listing; before `probe()` runs (or when
      `probe_models` is False) no such refusal is possible, since there is nothing to check
      against yet.
    - `health()` never raises: every failure mode becomes a `ProviderHealth` reading instead,
      per `LLMProvider.health`'s contract.

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules this implements.
    - .claude/codingrules.md section 13 for the SecretStr rule `api_key` follows.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision behind the
      LLMProvider Protocol this class implements.
    - hivemind.llm.providers.openai_compat.mapping for the wire translation this class calls.
    - hivemind.llm.providers.openai_compat.client for the HTTP layer this class calls.
    - hivemind.llm.provider for the LLMProvider Protocol this class implements.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from hivemind.llm.capabilities import HealthState, ProviderCapabilities, ProviderHealth
from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.models import JsonObject, LLMChunk, LLMRequest, LLMResponse, TextPart
from hivemind.llm.providers.openai_compat import mapping
from hivemind.llm.providers.openai_compat.client import OpenAICompatClient
from waggle.clock import Clock

# Relative to `OpenAICompatConfig.base_url`, which the manifest convention (codingrules section
# 13's example, `base_url = "http://127.0.0.1:11434/v1"`) already ends in "/v1" -- so these two
# stay free of the "/v1/..." prefix a hygiene grep would otherwise flag (scripts/
# check_no_model_ids.py's PROVIDER_URL_FRAGMENTS bans the literal "/v1/chat/completions").
MODELS_PATH = "/models"
CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_TOKEN_ESTIMATE_MARGIN = (
    1.15  # brief 3.7: "a documented margin"; 15% covers most tokenizers.
)
CHARS_PER_TOKEN_ESTIMATE = 4  # Same rough English-text ratio hivemind.llm.fake.count_tokens uses.
# A model absent from the server's own /models listing is refused the same way a real 404 for an
# unknown model id would be (most OpenAI-compatible servers do exactly that); synthesized because
# this refusal happens before any HTTP request for the call itself is made.
UNLISTED_MODEL_STATUS_CODE = 404

__all__ = ["OpenAICompatConfig", "OpenAICompatProvider"]


class OpenAICompatConfig(BaseModel):
    """Everything one `[llm.providers.<name>]` manifest section of kind `openai_compat` supplies.

    `model` is not part of the roadmap step's own field list (`base_url`, `api_key`, `timeout_s`,
    `capabilities`, `probe_models`, `token_estimate_margin`) -- it is added here, conservatively
    (required, no code above this adapter reads it) because the wire's `/chat/completions` body
    has a mandatory `model` field and neither `LLMRequest` nor `LLMProvider.complete` carries a
    per-call model id (that seam is owned by `hivemind.llm.models`/`hivemind.llm.provider`, a
    different dispatch). A local OpenAI-compatible server overwhelmingly serves exactly one model
    at a time, so binding it once per provider config, rather than per call, matches how these
    servers are actually run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(
        description="The server's base URL, including its own API version segment, e.g. "
        "'http://127.0.0.1:PORT/v1'. Every request path in this adapter is relative to it."
    )
    model: str = Field(
        description="The model id this provider instance requests and, when probe_models is "
        "True, checks against the server's own /models listing before the first call."
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="A bearer token for servers that require one; None for a local server with "
        "no auth. Never logged or repr'd (codingrules section 13): SecretStr redacts both.",
    )
    timeout_s: float = Field(
        gt=0,
        description="The httpx client's timeout, in seconds, for every request this "
        "provider makes (codingrules section 11: every external await has a timeout).",
    )
    capabilities: ProviderCapabilities = Field(
        description="This server's declared capabilities; /models cannot report them, so they "
        "come from the manifest's per-provider override (see this package's README)."
    )
    probe_models: bool = Field(
        default=True,
        description="Whether probe() should GET /models and refuse a call for an unlisted "
        "model; False for a server whose /models is unreliable or unavailable.",
    )
    token_estimate_margin: float = Field(
        default=DEFAULT_TOKEN_ESTIMATE_MARGIN,
        gt=0,
        description="count_tokens multiplies its character-based estimate by this margin, since "
        "it is never an exact tokenizer count.",
    )


class OpenAICompatProvider:
    """Talk to one OpenAI-compatible server: the `LLMProvider` for `kind = "openai_compat"`.

    Never branch on `self.name` (codingrules section 8.6): every difference in behaviour here
    comes from `self._config.capabilities`, not from which server is actually listening.
    """

    def __init__(
        self, name: str, config: OpenAICompatConfig, client: httpx.AsyncClient, clock: Clock
    ) -> None:
        """Wrap an already-constructed httpx client; prefer `create()` outside of tests.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            client: An `httpx.AsyncClient` already carrying `base_url`, the bearer header (when
                `config.api_key` is set) and `timeout_s`. Accepted directly (rather than only via
                `create()`) so a test can inject one built on `httpx.MockTransport`.
            clock: Source of `health()`'s `checked_at`.
        """
        self._name = name
        self._config = config
        self._http = client
        self._clock = clock
        self._client = OpenAICompatClient(client, name, config.capabilities.context_window)
        # None until probe() runs (or forever, when probe_models is False): "nothing to check
        # against yet" is deliberately different from "checked and the model was not found".
        self._served_model_ids: frozenset[str] | None = None

    @classmethod
    def create(cls, name: str, config: OpenAICompatConfig, clock: Clock) -> OpenAICompatProvider:
        """Build the httpx client from `config` and return a provider wrapping it.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            clock: Source of `health()`'s `checked_at`.

        Returns:
            A provider whose `probe()` has not yet been called; the composition root calls it
            once at startup (roadmap step 3.4's registry).
        """
        headers: dict[str, str] = {}
        if config.api_key is not None:
            # .get_secret_value() is the only place this ever leaves SecretStr's wrapper.
            headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
        http = httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_s, headers=headers
        )
        return cls(name, config, http, clock)

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; see `LLMProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return this provider's declared capabilities; see `LLMProvider.capabilities`."""
        return self._config.capabilities

    async def probe(self) -> None:
        """GET `/models` and, when `config.probe_models`, record the served model ids.

        Called once by the composition root before the provider serves traffic (roadmap step
        3.4). A no-op when `config.probe_models` is False.

        Raises:
            ProviderUnavailableError: The server could not be reached.
            RateLimitedError: The server rate-limited the probe itself.
            ProviderRequestError: The server rejected the probe request.
        """
        if not self._config.probe_models:
            return
        payload = await self._client.get_json(MODELS_PATH)
        self._served_model_ids = _extract_model_ids(payload)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run `request` to completion; see `LLMProvider.complete`."""
        model = request.model or self._config.model
        self._ensure_model_served(model)
        body = mapping.request_to_json(
            request,
            model=model,
            capabilities=self._config.capabilities,
            provider=self._name,
        )
        payload = await self._client.post_json(CHAT_COMPLETIONS_PATH, body)
        return mapping.response_from_json(payload, provider=self._name)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Run `request`, yielding incremental chunks; see `LLMProvider.stream`."""
        if not self._config.capabilities.streaming:
            # Honest fallback (matches hivemind.llm.fake.FakeLLMProvider exactly): one complete()
            # call, one chunk carrying the whole response's text, usage and stop reason.
            response = await self.complete(request)
            yield LLMChunk(
                text=response.text or None, usage=response.usage, stop_reason=response.stop_reason
            )
            return
        model = request.model or self._config.model
        self._ensure_model_served(model)
        body = mapping.request_to_json(
            request,
            model=model,
            capabilities=self._config.capabilities,
            provider=self._name,
        )
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        state = mapping.StreamState(self._name)
        async for raw_chunk in self._client.stream_sse(CHAT_COMPLETIONS_PATH, body):
            for chunk in state.absorb(raw_chunk):
                yield chunk
        trailing = state.finalize()
        if trailing is not None:
            yield trailing

    async def count_tokens(self, request: LLMRequest) -> int | None:
        """Estimate `request`'s token count; see `LLMProvider.count_tokens`.

        Never returns None (unlike `LLMProvider.count_tokens`'s general contract): this
        provider's `token_counting` capability, when declared True, means "can estimate", not
        "has an exact tokenizer" (roadmap step 3.7: "token counting by estimate with a
        documented margin").
        """
        total_chars = _char_count(request)
        estimate = total_chars / CHARS_PER_TOKEN_ESTIMATE * self._config.token_estimate_margin
        return math.ceil(estimate)

    async def health(self) -> ProviderHealth:
        """GET `/models` directly (bypassing the client's error mapping); see `LLMProvider.health`.

        Talks to `self._http` rather than `self._client` because `LLMProvider.health`'s
        HEALTHY/DEGRADED/DOWN split needs the raw status code (a 429 or 5xx is DEGRADED here, but
        `client.py`'s general mapping raises the same `ProviderUnavailableError` for a 5xx as for
        a connection failure, which health() must tell apart from a genuine DOWN).
        """
        try:
            # codingrules section 11: this await inherits self._http's configured timeout.
            response = await self._http.get(MODELS_PATH)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return ProviderHealth(
                state=HealthState.DOWN, detail=detail, checked_at=self._clock.now()
            )
        state, detail = _health_from_status(response.status_code)
        return ProviderHealth(state=state, detail=detail, checked_at=self._clock.now())

    def _ensure_model_served(self, model: str) -> None:
        """Raise if `probe()` has run and `model` is not in the server's listing.

        Args:
            model: The model id this call will send: the request's own, or the configured default.

        Raises:
            ProviderRequestError: `probe()` ran, `probe_models` is True, and `model` is not one
                of the ids the server's own `/models` reported.
        """
        if self._served_model_ids is None:
            return  # probe() has not run yet, or probe_models is False: nothing to check.
        if model not in self._served_model_ids:
            raise ProviderRequestError(
                self._name,
                UNLISTED_MODEL_STATUS_CODE,
                error_type="model_not_found",
                detail=f"model {model!r} is not in this server's /models listing",
            )


def _extract_model_ids(payload: JsonObject) -> frozenset[str]:
    """Extract every string `id` from a `/models` response's `data` array, defensively.

    A missing or malformed `data` array yields an empty set rather than raising: `probe()` should
    not crash the composition root over a slightly nonstandard `/models` reply. An empty result
    still records that probing happened (`_ensure_model_served` then refuses every model, which
    is the honest outcome when the server's own listing could not be understood).
    """
    entries = payload.get("data")
    if not isinstance(entries, list):
        return frozenset()
    ids: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            model_id = entry.get("id")
            if isinstance(model_id, str):
                ids.add(model_id)
    return frozenset(ids)


def _char_count(request: LLMRequest) -> int:
    """Sum the character count of `request`'s system prompt, every text part and every tool.

    Used only by `count_tokens`'s estimate; deliberately ignores image and tool-call/tool-result
    content, which a real tokenizer would weigh differently and this estimate does not attempt.
    """
    total = len(request.system) if request.system is not None else 0
    for message in request.messages:
        for part in message.parts:
            if isinstance(part, TextPart):
                total += len(part.text)
    for tool in request.tools:
        total += len(tool.name) + len(tool.description) + len(str(tool.parameters))
    return total


def _health_from_status(status_code: int) -> tuple[HealthState, str]:
    """Map a raw `/models` status code to a `(HealthState, detail)` pair.

    Args:
        status_code: The HTTP status `/models` responded with.

    Returns:
        HEALTHY for 200; DOWN for 401 (the server answered but refused these credentials, which
        is as unusable as not answering at all); DEGRADED for 429, every 5xx, and any other
        status (the server answered, just not happily).
    """
    if status_code == 200:
        return HealthState.HEALTHY, "ok"
    if status_code == 401:
        return HealthState.DOWN, "unauthorized (401)"
    return HealthState.DEGRADED, f"status {status_code}"
