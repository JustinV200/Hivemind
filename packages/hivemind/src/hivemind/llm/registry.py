"""Define ProviderRegistry: construct, cache and health-check every configured LLM provider.

`hivemind.llm.registry` is roadmap step 3.4's other half: `hivemind.llm.slots.resolve` walks a
`ModelSlot` to a `BoundModel` given a live provider lookup; this module IS that lookup's
production implementation, plus the one place a `kind` string turns into a live `LLMProvider`.
`ProviderRegistry` constructs a provider lazily (on first use, not at startup) and caches it, so
a Hive with nine slots bound to three providers only ever builds three provider objects.
`default_factories()` is the built-in `kind -> ProviderFactory` table for the three adapters this
phase ships (`fake`, `openai_compat`, `anthropic`); `apply_overrides` is the one place a manifest's
per-provider capability override replaces a base `ProviderCapabilities` field. Offline mode
(`[llm] offline = true`, codingrules section 8.6) is enforced a second time here, at
construction, on top of the manifest's own load-time check -- belt and braces, and the only way
to catch a provider kind (`ANTHROPIC`) whose *default* endpoint is hosted even when its
`base_url` is left empty.

This module cannot import `hivemind.manifest`: codingrules section 4 places `llm` and `manifest`
as independent Layer 1 siblings, neither of which may import the other -- the same reason
`hivemind.forage.map.SlotBinding` exists instead of `hivemind.llm.slots.resolve` taking the
manifest's own `LlmSection` directly (see that module's docstring). `ProviderConfig` below is
this package's own, decoupled mirror of one `[llm.providers.<name>]` row
(`hivemind.manifest.schema.llm.ProviderSpec`); the composition root (the CLI, roadmap step 3.21)
builds one per provider from a loaded `HiveManifest` before constructing a `ProviderRegistry`,
the same way it builds `hivemind.forage.map.SlotBinding` rows for `resolve`/`resolve_key`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Constructed once by the composition root
    (`cli/stores.py`, roadmap step 3.21) and read by every Worker, Warden and the Queen for a
    `BoundModel`, and by `hive llm providers` for `health()`. Calls into `hivemind.forage.map`,
    `hivemind.forage.slots`, `hivemind.llm.provider`, `hivemind.llm.capabilities`,
    `hivemind.llm.errors`, `hivemind.llm.fake`, `hivemind.llm.providers.openai_compat`,
    `hivemind.llm.slots` and `waggle` only -- never `hivemind.manifest` (see above).

Key invariants:
    - `provider(name)` constructs at most once per name: a second call for the same name returns
      the cached instance, never a fresh one (codingrules section 8.6's "one door" would be
      pointless if every layer above got its own copy of the same provider).
    - Offline mode is checked before a factory ever runs (`_check_offline`), so a misconfigured
      remote provider never gets as far as opening a client.
    - `PENDING_KINDS` names every `ProviderKind` `default_factories()` does not cover. It is
      empty now that all three adapters are wired, and stays so the completeness test and the
      next adapter have a home.
    - `_resolve_api_key` is the only place this module reads `RegistryDeps.environ`; it mirrors
      `hivemind.manifest.env.provider_api_key`'s own derivation (which this module cannot call
      directly, for the same reason it cannot import `ProviderSpec`) so a provider's secret still
      comes from exactly one environment variable, never the config itself (codingrules 13).

See Also:
    - .claude/codingrules.md section 8.6 for "offline is a first-class mode" and "one door".
    - .claude/codingrules.md section 4 for the Layer 1 "llm | manifest" independent-siblings rule
      this module's `ProviderConfig` exists to respect.
    - .claude/codingrules.md section 13 for the environment-variable and SecretStr rules this
      module's `_resolve_api_key` follows.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.llm.slots for resolve/resolve_key, the functions `bound`/`bound_for_key` call.
    - hivemind.llm.providers.openai_compat for the one non-fake adapter this phase wires up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import SecretStr

from hivemind.common.errors import InvariantViolationError
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.llm.capabilities import ProviderCapabilities, ProviderHealth
from hivemind.llm.errors import OfflineViolationError, UnknownProviderError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.provider import LLMProvider
from hivemind.llm.providers.anthropic import AnthropicConfig, AnthropicProvider
from hivemind.llm.providers.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from hivemind.llm.slots import BoundModel, resolve, resolve_key
from waggle.clock import Clock
from waggle.uris import is_loopback_host

# Mirrors hivemind.manifest.schema.llm.ProviderKind member-for-member; llm may not import manifest
# (codingrules section 4), so this is this module's own copy, kept in sync by a dedicated test.
ProviderKind = Literal["anthropic", "openai_compat", "fake"]

# ANTHROPIC's factory is added to default_factories() once its adapter (roadmap step 3.6) lands;
# the completeness test excludes exactly this set from "every ProviderKind must have a factory".
PENDING_KINDS: frozenset[ProviderKind] = frozenset()

__all__ = [
    "PENDING_KINDS",
    "MissingDefaultModelError",
    "ProviderConfig",
    "ProviderFactory",
    "ProviderKind",
    "ProviderRegistry",
    "RegistryDeps",
    "apply_overrides",
    "default_factories",
]


class MissingDefaultModelError(InvariantViolationError):
    """Raise when an openai_compat ProviderConfig reaches its factory with no default_model.

    The composition root derives `default_model` from the provider's first `[llm.slots]` row
    before building a registry, so reaching this means Hive code skipped that step: a bug in
    the Hive, not bad operator input (codingrules section 10), and named so the trail can say
    which invariant broke.
    """

    code: ClassVar[str] = "hivemind.llm.missing_default_model"

    def __init__(self, name: str) -> None:
        """Build the error for provider `name`.

        Args:
            name: The `[llm.providers.<name>]` key whose config lacks a default model.
        """
        super().__init__(
            f"[llm.providers.{name}] is kind='openai_compat' but no default_model was derived for "
            "it; the composition root must supply one (e.g. from its first [llm.slots] row)."
        )
        self.name = name


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """One `[llm.providers.<name>]` row, decoupled from `hivemind.manifest.schema.llm.ProviderSpec`.

    Mirrors that model's fields (see the module docstring for why this module cannot import it
    directly); the composition root builds one of these per provider from a loaded HiveManifest.
    """

    kind: ProviderKind  # Which factory in RegistryDeps.factories builds this provider.
    base_url: str  # "" means a hosted API with a vendor-fixed endpoint; non-empty for a local one.
    api_key_env: str | None = None  # HIVEMIND_<NAME>_API_KEY is derived when this is None.
    timeout_s: float = 120.0  # Mirrors manifest.schema.llm.DEFAULT_PROVIDER_TIMEOUT_S's default.
    capability_overrides: Mapping[str, bool | int] = field(default_factory=dict)  # From
    # ProviderSpec.capabilities.as_overrides(): only the fields a manifest author actually set.
    default_model: str | None = None  # Required for kind="openai_compat"; unused by every other.


class ProviderFactory(Protocol):
    """Build a live LLMProvider from one provider's config and its already-resolved API key."""

    def __call__(
        self, name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        """Return a new provider instance for `name`.

        Args:
            name: The manifest's `[llm.providers.<name>]` key.
            config: That provider's config.
            api_key: The resolved secret, or None when this provider needs none.
            clock: Passed through to the provider's own health()/count_tokens readings.
        """
        ...


@dataclass(frozen=True, slots=True)
class RegistryDeps:
    """Collaborators a ProviderRegistry needs beyond the manifest-derived config it is given."""

    factories: Mapping[ProviderKind, ProviderFactory]  # kind -> how to build a provider of it.
    environ: Mapping[str, str]  # Read only by _resolve_api_key (codingrules section 13).
    clock: Clock  # Passed to every factory and, through it, to every constructed provider.
    map: ForageMap | None = None  # Prices resolve()'s BoundModels; None leaves them unpriced.


class ProviderRegistry:
    """Construct, cache and health-check every configured LLM provider.

    See the module docstring for why this takes `providers`/`bindings`/`offline` as separate,
    already-manifest-derived pieces rather than a `HiveManifest` (or its `LlmSection`) directly.
    """

    def __init__(
        self,
        providers: Mapping[str, ProviderConfig],
        bindings: Iterable[SlotBinding],
        offline: bool,
        deps: RegistryDeps,
    ) -> None:
        """Store this Hive's provider configs and Forage-side slot bindings; construct nothing yet.

        Args:
            providers: Every `[llm.providers.<name>]` row, decoupled (see ProviderConfig).
            bindings: Every `[llm.slots]` row, forage-side (hivemind.forage.map.SlotBinding).
            offline: This Hive's `[llm] offline` flag.
            deps: Factories, environment, clock and an optional Forage map.
        """
        self._providers = dict(providers)
        self._bindings = tuple(bindings)
        self._offline = offline
        self._deps = deps
        self._cache: dict[str, LLMProvider] = {}

    def names(self) -> tuple[str, ...]:
        """Return every `[llm.providers.*]` name this registry knows, in manifest order."""
        return tuple(self._providers.keys())

    def provider(self, name: str) -> LLMProvider:
        """Return the live provider named `name`, constructing and caching it on first use.

        Args:
            name: A `[llm.providers.<name>]` key.

        Returns:
            The same LLMProvider instance on every call for a given `name`.

        Raises:
            UnknownProviderError: No provider is configured under `name`, or its `kind` has no
                factory in `deps.factories` (a kind listed in `PENDING_KINDS`).
            OfflineViolationError: `[llm] offline = true` and this provider's `base_url` is not
                provably loopback.
        """
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        config = self._providers.get(name)
        if config is None:
            raise UnknownProviderError(name)
        factory = self._deps.factories.get(config.kind)
        if factory is None:
            raise UnknownProviderError(name)
        _check_offline(name, config.base_url, self._offline)
        api_key = _resolve_api_key(name, config.api_key_env, self._deps.environ)
        instance = factory(name, config, api_key, self._deps.clock)
        self._cache[name] = instance
        return instance

    def bound(self, slot: ModelSlot) -> BoundModel:
        """Resolve `slot` to a BoundModel; see `hivemind.llm.slots.resolve`."""
        return resolve(slot, self._bindings, self.provider, self._deps.map)

    def bound_for_key(self, key: str, slot: ModelSlot) -> BoundModel:
        """Resolve a named binding for `slot`; see `hivemind.llm.slots.resolve_key`."""
        return resolve_key(key, slot, self._bindings, self.provider, self._deps.map)

    async def health(self) -> dict[str, ProviderHealth]:
        """Probe every provider this registry has already constructed.

        Never constructs a provider that has not been used yet: `hive llm providers` shows the
        health of what is actually in play, not every name the manifest happens to list.
        """
        return {name: await instance.health() for name, instance in self._cache.items()}


def apply_overrides(
    base: ProviderCapabilities, overrides: Mapping[str, bool | int]
) -> ProviderCapabilities:
    """Return `base` with every field named in `overrides` replaced.

    Args:
        base: The adapter's own default capabilities (usually `ProviderCapabilities.full()`).
        overrides: Field name to value, as `hivemind.manifest.schema.llm.CapabilityOverrides.
            as_overrides()` produces: only the fields a manifest author actually set.

    Returns:
        `base` unchanged when `overrides` is empty, otherwise a copy with those fields replaced.
    """
    return base.model_copy(update=dict(overrides)) if overrides else base


def default_factories() -> Mapping[ProviderKind, ProviderFactory]:
    """Return the built-in `kind -> ProviderFactory` table for every adapter this phase ships.

    Covers every `ProviderKind` except `PENDING_KINDS` (currently none).
    """
    return {
        "fake": _build_fake,
        "openai_compat": _build_openai_compat,
        "anthropic": _build_anthropic,
    }


def _check_offline(name: str, base_url: str, offline: bool) -> None:
    """Raise OfflineViolationError when `offline` and `base_url` is not provably loopback.

    Mirrors hivemind.manifest.schema.llm's own (private) loopback rule from the same public
    primitive, `waggle.uris.is_loopback_host`, rather than importing a name manifest does not
    export -- belt and braces on top of the manifest's own load-time check (codingrules 8.6), and
    the only place that also catches a provider kind (ANTHROPIC) whose *default* endpoint is
    remote even when `base_url` is left empty.
    """
    if not offline:
        return
    if not _is_provably_local(base_url):
        raise OfflineViolationError(name, base_url)


def _is_provably_local(base_url: str) -> bool:
    """Return True when `base_url` names a loopback host; False for empty or any other host."""
    if not base_url:
        return False  # Empty means a hosted, vendor-fixed endpoint (e.g. Anthropic): never local.
    hostname = urlsplit(base_url).hostname
    return hostname is not None and is_loopback_host(hostname)


def _resolve_api_key(
    name: str, api_key_env: str | None, environ: Mapping[str, str]
) -> SecretStr | None:
    """Read one provider's API key from `environ`, mirroring hivemind.manifest.env.provider_api_key.

    That function cannot be called directly here: it takes a ProviderSpec, and llm may not
    import manifest (see the module docstring). This is the same short derivation, kept here so
    `RegistryDeps.environ` stays the only environment read this module performs (codingrules
    section 13: "environment variables are read in exactly one place").
    """
    var_name = api_key_env or f"HIVEMIND_{name.upper()}_API_KEY"
    raw = environ.get(var_name)
    return SecretStr(raw) if raw is not None else None


def _build_fake(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """Build a FakeLLMProvider from `config`; `api_key` is accepted (Protocol shape) and unused."""
    capabilities = apply_overrides(ProviderCapabilities.full(), config.capability_overrides)
    return FakeLLMProvider(name=name, capabilities=capabilities, clock=clock)


def _build_anthropic(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """Build an AnthropicProvider from `config` via its own SDK-client-building classmethod.

    An empty `base_url` means the SDK's default hosted endpoint (None to the adapter);
    `_check_offline` has already refused that case when the Hive is offline, so this never
    opens a client an air-gapped Hive would not want.
    """
    base = AnthropicConfig(
        api_key=api_key, base_url=config.base_url or None, timeout_s=config.timeout_s
    )
    capabilities = apply_overrides(base.capabilities, config.capability_overrides)
    adapter_config = base.model_copy(update={"capabilities": capabilities})
    return AnthropicProvider.from_config(name, adapter_config, clock)


def _build_openai_compat(
    name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
) -> LLMProvider:
    """Build an OpenAICompatProvider from `config`, via its own httpx-client-building classmethod.

    Raises:
        MissingDefaultModelError: `config.default_model` is None; an openai_compat provider's
            wire body needs a model id and has no manifest-level default (see
            hivemind.llm.providers.openai_compat's README, "Why model is on the config").
    """
    if config.default_model is None:
        raise MissingDefaultModelError(name)
    capabilities = apply_overrides(ProviderCapabilities.full(), config.capability_overrides)
    oc_config = OpenAICompatConfig(
        base_url=config.base_url,
        model=config.default_model,
        api_key=api_key,
        timeout_s=config.timeout_s,
        capabilities=capabilities,
    )
    return OpenAICompatProvider.create(name, oc_config, clock)
