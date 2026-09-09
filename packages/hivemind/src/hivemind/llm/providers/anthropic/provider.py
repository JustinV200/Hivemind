"""Define AnthropicProvider: the hosted-Claude adapter, using the `anthropic` SDK.

Anthropic (the vendor behind the Claude family of models) is the Hive's first LLM provider (the
README's own bet: "the Hive starts on Claude"); this module owns the `LLMProvider` implementation
itself (`hivemind.llm.provider.LLMProvider`, codingrules section 8.1), while `mapping.py` and its
`streaming.py` sibling are the only places an Anthropic wire field name appears, and `client.py` is
the only place the SDK's own methods are actually called. `AnthropicConfig` is the validated form
of one `[llm.providers.<name>]` manifest section of kind `"anthropic"`; the registry (roadmap step
3.4, a later dispatch) applies any `[llm.providers.<name>.capabilities]` override before
construction, so `AnthropicConfig.capabilities` is always this binding's final, effective set by
the time `AnthropicProvider` reads it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.providers.anthropic`.
    Constructed by the registry (`hivemind.llm.registry`, roadmap step 3.4) via `from_config`;
    implements `hivemind.llm.provider.LLMProvider`, so every layer above `hivemind.llm` calls it
    only through that Protocol. Calls into `hivemind.llm.providers.anthropic.{mapping,streaming,
    client}`, `hivemind.llm.capabilities`, `hivemind.llm.models` and `waggle.clock` only.

Key invariants:
    - The API key, when set, is held in `AnthropicConfig.api_key: SecretStr` and never appears in
      a `repr`, a log line, or an error message (codingrules section 13); only
      `.get_secret_value()` in `from_config()` ever reads it.
    - `from_config()` never raises for a missing key: an unset `api_key` becomes an explicit empty
      string on the SDK client (never `None`, which would let the SDK search environment
      variables or a local credential profile the Hive Manifest never authorized), so
      construction always succeeds and the first real call fails loudly and typed instead.
    - `health()` never raises: every failure mode becomes a `ProviderHealth` reading instead, per
      `LLMProvider.health`'s contract (delegated to `AnthropicClient.probe_health`).

See Also:
    - .claude/codingrules.md section 8.6 for the LLM provider independence rules this implements.
    - .claude/codingrules.md section 13 for the SecretStr rule `api_key` follows.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision behind the
      LLMProvider Protocol this class implements.
    - hivemind.llm.providers.anthropic.mapping for the wire translation this class calls.
    - hivemind.llm.providers.anthropic.client for the SDK layer this class calls.
    - hivemind.llm.provider for the LLMProvider Protocol this class implements.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
from hivemind.llm.models import LLMChunk, LLMRequest, LLMResponse
from hivemind.llm.providers.anthropic import mapping, streaming
from hivemind.llm.providers.anthropic.client import AnthropicClient
from waggle.clock import Clock

DEFAULT_CONTEXT_WINDOW = (
    200_000  # Roadmap step 3.6: the vendor's declared default window, verified against the SDK.
)
DEFAULT_TIMEOUT_S = 120.0  # Matches [llm] request_timeout_s's own manifest default (section 2.4).

__all__ = ["AnthropicConfig", "AnthropicProvider"]


class AnthropicConfig(BaseModel):
    """One `[llm.providers.<name>]` manifest section of kind `"anthropic"`, validated.

    Every field is optional or defaulted because the registry (roadmap step 3.4) constructs this
    from a manifest section whose own fields are all optional at that layer too; validation here
    only rejects an unrecognised field (`extra="forbid"`) or a malformed value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr | None = Field(
        default=None,
        description="The Anthropic API key, normally resolved from HIVEMIND_<PROVIDER>_API_KEY "
        "by manifest/env.py. None means no key is configured: every call then fails with "
        "ProviderRequestError and health() reports DOWN, but construction never raises. Never "
        "logged, repr'd, or included in any error message (SecretStr).",
    )
    base_url: str | None = Field(
        default=None,
        description="None uses the anthropic SDK's own built-in endpoint. That endpoint is "
        "deliberately never written as a literal anywhere in this package (codingrules section "
        "8.6): scripts/check_no_model_ids.py greps source for provider URL fragments.",
    )
    timeout_s: float = Field(
        default=DEFAULT_TIMEOUT_S,
        gt=0,
        description="The SDK client's per-request timeout, in seconds (codingrules section 11: "
        "every external await has a timeout).",
    )
    capabilities: ProviderCapabilities = Field(
        default_factory=lambda: ProviderCapabilities.full(context_window=DEFAULT_CONTEXT_WINDOW),
        description="This binding's declared capabilities. The registry applies any manifest "
        "[llm.providers.<name>.capabilities] override before constructing this config, so this "
        "is always the final, effective set by the time AnthropicProvider reads it.",
    )


class AnthropicProvider:
    """Talk to the Anthropic Messages API: the `LLMProvider` for `kind = "anthropic"`.

    Never branch on `self.name` (codingrules section 8.6): every difference in behaviour here
    comes from `self._config.capabilities`, not from the fact that this provider is Anthropic.
    """

    def __init__(
        self, name: str, config: AnthropicConfig, client: anthropic.AsyncAnthropic, clock: Clock
    ) -> None:
        """Wrap an already-constructed SDK client; prefer `from_config()` outside of tests.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            client: An `anthropic.AsyncAnthropic` already carrying its api key, base url and
                timeout. Accepted directly (rather than only via `from_config()`) so a test can
                inject one built on the SDK's own mock transport.
            clock: Source of `health()`'s `checked_at`.
        """
        self._name = name
        self._config = config
        self._clock = clock
        self._client = AnthropicClient(client, name, config.capabilities.context_window)

    @classmethod
    def from_config(cls, name: str, config: AnthropicConfig, clock: Clock) -> AnthropicProvider:
        """Build the SDK client from `config` and return a provider wrapping it.

        This is the one production construction site (codingrules 8.6); every other caller
        (tests included) should use the plain constructor with an already-built client instead.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: This provider's configuration.
            clock: Source of `health()`'s `checked_at`.

        Returns:
            A provider ready to serve calls immediately: nothing here does I/O (codingrules
            section 5.5), so a missing or invalid api_key surfaces only on the first real call.
        """
        # .get_secret_value() is the only place this ever leaves SecretStr's wrapper. An empty
        # string (never None) keeps the SDK from falling back to an ambient credential source
        # (an environment variable, an `ant auth login` profile, ...) the Hive Manifest never
        # authorized -- see this module's docstring's "Key invariants".
        api_key = config.api_key.get_secret_value() if config.api_key is not None else ""
        sdk = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout_s,
            max_retries=0,  # The Fanner and the ladders own retry policy, not the SDK's own.
        )
        return cls(name, config, sdk, clock)

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key; see `LLMProvider.name`."""
        return self._name

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return this provider's declared capabilities; see `LLMProvider.capabilities`."""
        return self._config.capabilities

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run `request` to completion; see `LLMProvider.complete`."""
        params = mapping.to_create_params(request, self._config.capabilities, provider=self._name)
        message = await self._client.create(params)
        return mapping.from_message(message, provider=self._name)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Run `request`, yielding incremental chunks; see `LLMProvider.stream`."""
        if not self._config.capabilities.streaming:
            # Honest fallback (matches hivemind.llm.fake.FakeLLMProvider exactly): one complete()
            # call, one chunk carrying the whole response's text, usage and stop reason.
            response = await self.complete(request)
            yield LLMChunk(
                text=response.text or None,
                usage=response.usage,
                stop_reason=response.stop_reason,
            )
            return
        params = mapping.to_create_params(request, self._config.capabilities, provider=self._name)
        events = self._client.stream(params)
        async for chunk in streaming.accumulate(events, provider=self._name):
            yield chunk

    async def count_tokens(self, request: LLMRequest) -> int | None:
        """Estimate `request`'s token count via the API; see `LLMProvider.count_tokens`.

        Returns:
            None when `capabilities.token_counting` is False, honouring the same
            "never pretend to count when it declared it cannot" rule `FakeLLMProvider` follows;
            otherwise the API's own exact count.
        """
        if not self._config.capabilities.token_counting:
            return None
        params = mapping.to_count_params(request, self._config.capabilities, provider=self._name)
        return await self._client.count_tokens(params)

    async def health(self) -> ProviderHealth:
        """Return this provider's current health; see `LLMProvider.health`."""
        return await self._client.probe_health(self._clock)
