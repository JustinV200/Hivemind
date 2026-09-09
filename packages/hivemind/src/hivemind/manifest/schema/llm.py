"""Define the ``[llm]`` section: providers, capability overrides, and the model-slot table.

Codingrules section 8.6 ("model slots, not model names") lives here as schema: a Hive Manifest
names every model provider it may call (``ProviderSpec``, one per ``[llm.providers.<name>]``
table) and binds every ``hivemind.forage.ModelSlot`` (a named place a model call resolves to, such
as ``QUEEN`` or ``WORKER``) to one of them (``SlotBinding``, one per ``[llm.slots.<key>]`` table).
``CapabilityOverrides`` lets a provider's entry correct what its adapter would otherwise assume
(``[llm.providers.<name>.capabilities]``): every field mirrors a ``hivemind.llm.
ProviderCapabilities`` field but stays ``None`` for "let the adapter decide", because
``hivemind.llm`` and ``hivemind.manifest`` are independent siblings in codingrules section 4's
layer table (both Layer 1) and neither may import the other -- so this module never imports
``ProviderCapabilities`` itself; ``as_overrides()`` hands the composition root (which is free to
import both) a plain dict of only the fields actually set, to merge onto whatever defaults that
layer already has in hand. ``LlmSection``'s own validators are what keep the slot table honest:
every key lowercase, every ``ModelSlot`` bound to something, every binding's provider declared,
every fallback reachable and acyclic, and ``offline = true`` refusing any provider that is not
provably local.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``. Calls into ``hivemind.forage`` (for
    ``ModelSlot`` and ``Effort``, never ``hivemind.llm``) and ``waggle`` only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - No field named ``api_key`` exists anywhere in this module: codingrules section 13 forbids a
      secret literal in the manifest file. ``api_key_env`` names an environment variable, and its
      own pattern (``API_KEY_ENV_PATTERN``) rejects a value that is not shaped like one, catching
      the case where someone pastes a key where a variable name belongs.
    - ``LlmSection.providers`` and ``LlmSection.slots`` keys are lowercase snake_case
      (``MANIFEST_KEY_PATTERN``), enforced by the dict key's own field constraint, not a separate
      validator.
    - Every ``ModelSlot`` member's ``manifest_key`` is present in ``LlmSection.slots`` after
      validation; a manifest that leaves one unbound fails to load, because
      ``hivemind.llm.slots.resolve`` (a later roadmap step) must never find a slot with nothing
      behind it.
    - A fallback chain that revisits a key it has already visited is rejected before it ever
      reaches ``hivemind.forage.map.ForageMap.for_slot``, which would otherwise loop forever.

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names" and the capabilities
      rule this module makes into schema.
    - .claude/codingrules.md section 13 for "secrets are never in the manifest file".
    - .claude/codingrules.md section 4 for why this module cannot import ``hivemind.llm``.
    - hivemind.forage.map for the *other* ``SlotBinding``: the forage-side view of one
      ``[llm.slots]`` row, built from this module's ``SlotBinding`` by whatever loads the manifest
      into forage's own types.
    - hivemind.forage.slots for ``ModelSlot`` and ``Effort``.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage import Effort, ModelSlot
from waggle.messages.forage.values import MAX_MODEL_CHARS, MAX_PROVIDER_CHARS
from waggle.uris import is_loopback_host

# A manifest table key: lowercase snake_case, letter first. Shared by [llm.providers.*] and
# [llm.slots.*] keys so both read the same way and neither ever collides with a wire SLOT_PATTERN
# (UPPER_SNAKE) value by accident.
MANIFEST_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
MAX_MANIFEST_KEY_CHARS = (
    64  # A slot name or a short named binding ("local_worker"); never a phrase.
)
# A real secret (an Anthropic key starts "sk-ant-...", an OpenAI one "sk-...") never matches this
# shape, so a value pasted here by mistake instead of an env var name fails validation immediately.
API_KEY_ENV_PATTERN = r"^[A-Z][A-Z0-9_]*$"
DEFAULT_MAX_OUTPUT_TOKENS = 4_096  # A generous default reply length before a caller asks for more.
DEFAULT_REQUEST_TIMEOUT_S = (
    120.0  # Two minutes: enough for a slow model, short enough to fail loud.
)
DEFAULT_PROVIDER_TIMEOUT_S = 120.0  # Per-request timeout at the provider level; mirrors the above.
DEFAULT_PROVIDER_SEATS = (
    4  # A modest concurrency default; the manifest raises it for a busier Hive.
)

# The three adapters codingrules section 8.1 lists today; a fourth needs a codingrules update
# before it needs a manifest change, so this stays a closed Literal rather than a bare str.
ProviderKind = Literal["anthropic", "openai_compat", "fake"]

__all__ = [
    "API_KEY_ENV_PATTERN",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_PROVIDER_SEATS",
    "DEFAULT_PROVIDER_TIMEOUT_S",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "MANIFEST_KEY_PATTERN",
    "MAX_MANIFEST_KEY_CHARS",
    "CapabilityOverrides",
    "LlmSection",
    "ProviderKind",
    "ProviderSpec",
    "SlotBinding",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# The manifest-key alias every dict key in this module uses: lowercase, bounded, and pattern-checked
# by pydantic itself, so "keys are lowercase" needs no separate validator.
_ManifestKey = Annotated[
    str, Field(pattern=MANIFEST_KEY_PATTERN, max_length=MAX_MANIFEST_KEY_CHARS)
]


class CapabilityOverrides(BaseModel):
    """``[llm.providers.<name>.capabilities]``: per-provider corrections to the adapter's defaults.

    Every field mirrors a ``hivemind.llm.ProviderCapabilities`` field but defaults to ``None``,
    meaning "let the adapter decide"; only a field a manifest author actually sets travels any
    further. This module cannot import ``ProviderCapabilities`` itself (see the module docstring),
    so ``as_overrides`` hands back a plain dict instead of a merged capabilities object.
    """

    model_config = _MODEL_CONFIG

    native_tool_calls: bool | None = Field(
        default=None, description="Override whether the provider has its own tool-call protocol."
    )
    schema_output: bool | None = Field(
        default=None, description="Override whether the provider can enforce a JSON schema."
    )
    json_mode: bool | None = Field(
        default=None, description="Override whether the provider can be told to emit valid JSON."
    )
    vision: bool | None = Field(
        default=None, description="Override whether the provider accepts image content."
    )
    streaming: bool | None = Field(
        default=None, description="Override whether the provider streams real deltas."
    )
    reasoning_control: bool | None = Field(
        default=None, description="Override whether the provider exposes a reasoning-effort knob."
    )
    context_window: int | None = Field(
        default=None, gt=0, description="Override the model's context window, in tokens."
    )
    system_role: bool | None = Field(
        default=None, description="Override whether the provider has a dedicated system channel."
    )
    parallel_tool_calls: bool | None = Field(
        default=None, description="Override whether more than one tool call may return per turn."
    )
    token_counting: bool | None = Field(
        default=None,
        description="Override whether the provider can estimate tokens before sending.",
    )

    def as_overrides(self) -> dict[str, bool | int]:
        """Return only the fields this manifest entry actually set.

        Returns:
            A dict of field name to override value, containing nothing for a field left at its
            default ``None``. The composition root merges this onto a
            ``hivemind.llm.ProviderCapabilities`` default (``.full()`` or an adapter's own base)
            with ``model_copy(update=...)``; this module never builds that merged value itself.
        """
        return self.model_dump(exclude_none=True)


class ProviderSpec(BaseModel):
    """``[llm.providers.<name>]``: one model provider a Hive may call, keyed by name."""

    model_config = _MODEL_CONFIG

    kind: ProviderKind = Field(description="Which adapter speaks to this provider.")
    base_url: str = Field(
        default="",
        description="The provider's base URL; empty means a hosted API with a vendor-fixed "
        "endpoint (e.g. Anthropic). Non-empty for a local server (Ollama, vLLM, llama.cpp).",
    )
    api_key_env: Annotated[str, Field(pattern=API_KEY_ENV_PATTERN)] | None = Field(
        default=None,
        description="The environment variable holding this provider's API key; None derives "
        "HIVEMIND_<NAME>_API_KEY from the provider's own manifest key "
        "(hivemind.manifest.env.provider_api_key). Never a literal key: this is a variable name.",
    )
    timeout_s: float = Field(
        default=DEFAULT_PROVIDER_TIMEOUT_S, gt=0, description="Per-request timeout, in seconds."
    )
    seats: int = Field(
        default=DEFAULT_PROVIDER_SEATS,
        gt=0,
        description="Concurrent requests this provider allows.",
    )
    requests_per_minute: int | None = Field(
        default=None, gt=0, description="A rate limit to respect; None when not metered that way."
    )
    tokens_per_minute: int | None = Field(
        default=None, gt=0, description="A token-rate limit to respect; None when not metered."
    )
    capabilities: CapabilityOverrides = Field(
        default_factory=CapabilityOverrides,
        description="Corrections to the adapter's own declared capabilities; every field left "
        "unset means 'trust the adapter'.",
    )


class SlotBinding(BaseModel):
    """``[llm.slots.<key>]``: one model-slot binding: a provider, a model id, and a fallback chain.

    Distinct from ``hivemind.forage.map.SlotBinding``, the lightweight forage-side view of this
    same row (see that module's docstring): this is the manifest's own, fully-validated shape,
    including ``max_output_tokens``, a field forage's view has no need of.
    """

    model_config = _MODEL_CONFIG

    provider: str = Field(
        max_length=MAX_PROVIDER_CHARS,
        description="The [llm.providers.<name>] key this row binds to.",
    )
    model: str = Field(max_length=MAX_MODEL_CHARS, description="The model id this row binds to.")
    fallback: _ManifestKey | None = Field(
        default=None,
        description="Another [llm.slots] key to fall back to when this one's provider is down; "
        "None means no fallback.",
    )
    effort: Effort = Field(
        default=Effort.MEDIUM, description="How hard the model behind this row is asked to think."
    )
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Override [llm] default_max_output_tokens for this row; None keeps it.",
    )


class LlmSection(BaseModel):
    """``[llm]``: providers, and the table binding every model slot to one of them."""

    model_config = _MODEL_CONFIG

    offline: bool = Field(
        default=False,
        description="True refuses any provider whose base_url is empty (hosted) or not loopback.",
    )
    default_max_output_tokens: int = Field(
        default=DEFAULT_MAX_OUTPUT_TOKENS,
        gt=0,
        description="The reply length cap absent a slot override.",
    )
    request_timeout_s: float = Field(
        default=DEFAULT_REQUEST_TIMEOUT_S,
        gt=0,
        description="Default per-request timeout, in seconds.",
    )
    providers: dict[_ManifestKey, ProviderSpec] = Field(
        default_factory=dict, description="Every model provider this Hive may call, keyed by name."
    )
    slots: dict[_ManifestKey, SlotBinding] = Field(
        default_factory=dict,
        description="Every model-slot binding, keyed by a lowercase ModelSlot name or a named "
        "binding used only as a fallback target.",
    )

    @model_validator(mode="after")
    def _every_binding_names_a_declared_provider(self) -> LlmSection:
        """Reject a [llm.slots] row whose provider has no matching [llm.providers] entry."""
        for key, binding in self.slots.items():
            if binding.provider not in self.providers:
                raise ValueError(
                    f"[llm.slots.{key}] names provider {binding.provider!r}, which has no "
                    f"[llm.providers.{binding.provider}] entry."
                )
        return self

    @model_validator(mode="after")
    def _every_fallback_exists_and_never_cycles(self) -> LlmSection:
        """Reject a fallback key that is missing, or a fallback chain that loops."""
        for key, binding in self.slots.items():
            if binding.fallback is not None and binding.fallback not in self.slots:
                raise ValueError(
                    f"[llm.slots.{key}] falls back to {binding.fallback!r}, which is not a "
                    "[llm.slots] key."
                )
            _walk_fallback_chain(key, self.slots)
        return self

    @model_validator(mode="after")
    def _every_model_slot_is_bound(self) -> LlmSection:
        """Reject a manifest that leaves a ModelSlot with nothing behind it."""
        # Every ModelSlot must resolve directly (its own manifest_key must be a [llm.slots] key),
        # not merely be reachable as someone else's fallback target: a caller asks for a slot by
        # name, never by walking a stranger's chain to find it.
        missing = [slot.manifest_key for slot in ModelSlot if slot.manifest_key not in self.slots]
        if missing:
            raise ValueError(f"[llm.slots] is missing a binding for: {', '.join(missing)}.")
        return self

    @model_validator(mode="after")
    def _offline_refuses_hosted_or_non_loopback_providers(self) -> LlmSection:
        """Reject a provider [llm] offline = true cannot prove is local."""
        if not self.offline:
            return self
        for name, spec in self.providers.items():
            if not _is_provably_local(spec.base_url):
                raise ValueError(
                    f"[llm] offline = true but [llm.providers.{name}] is not provably local "
                    f"(base_url={spec.base_url!r}); every provider must have a loopback base_url."
                )
        return self


def _walk_fallback_chain(start: str, table: dict[str, SlotBinding]) -> None:
    """Raise ValueError if following `fallback` from `start` ever revisits a key.

    Args:
        start: The [llm.slots] key to start walking from.
        table: Every [llm.slots] row, keyed by its own key.

    Raises:
        ValueError: The chain starting at `start` visits the same key twice.
    """
    seen: set[str] = set()
    current: str | None = start
    while current is not None:
        if current in seen:
            raise ValueError(
                f"[llm.slots] fallback chain starting at {start!r} cycles back to {current!r}."
            )
        seen.add(current)
        binding = table.get(current)
        # A missing fallback target is reported by _every_fallback_exists_and_never_cycles with a
        # more specific message; stop walking here rather than raising a second, vaguer one.
        current = binding.fallback if binding is not None else None


def _is_provably_local(base_url: str) -> bool:
    """Return True when `base_url` names a loopback host Waggle-style offline mode can trust.

    Args:
        base_url: A provider's ``[llm.providers.<name>] base_url``; empty means a hosted API.

    Returns:
        False for an empty (hosted) URL or one whose host is not loopback; True otherwise.
    """
    if not base_url:
        return False  # Empty means a hosted, vendor-fixed endpoint: never local.
    hostname = urlsplit(base_url).hostname
    return hostname is not None and is_loopback_host(hostname)
