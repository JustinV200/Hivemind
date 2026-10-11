"""Define the provider and slot-binding tables of [llm]: what a Hive may call, and as which slot.

Codingrules section 8.6 ("model slots, not model names") lives here as schema: a Hive Manifest
names every model provider it may call (ProviderSpec, one per [llm.providers.<name>]
table) and binds every hivemind.forage.ModelSlot (a named place a model call resolves to, such
as QUEEN or WORKER) to one of them (SlotBinding, one per [llm.slots.<key>] table).
CapabilityOverrides lets a provider's entry correct what its adapter would otherwise assume
([llm.providers.<name>.capabilities]): every field mirrors a hivemind.llm.
ProviderCapabilities field but stays None for "let the adapter decide", because
hivemind.llm and hivemind.manifest are independent siblings in codingrules section 4's
layer table (both Layer 1) and neither may import the other -- so this module never imports
ProviderCapabilities itself; as_overrides() hands the composition root (which is free to
import both) a plain dict of only the fields actually set, to merge onto whatever defaults that
layer already has in hand. A provider of an in-process kind (IN_PROCESS_KINDS:
whisper_local, roadmap step 6.5a, and sentence_transformers, roadmap step 7.1, ADR-0036)
runs its model inside the Hive's own process, so it is local by construction: it names no
base_url at all. Roadmap step 7.1 adds the embedder's own boundary: ProviderSpec.embedding
is an EmbeddingOptions (batch size, device, whether to ever reach a model hub) that applies
only when a provider is asked to embed. ProviderKind, EMBEDDING_ONLY_KINDS and
IN_PROCESS_KINDS mirror hivemind.llm.registry's own copies member-for-member (that module
cannot import this one, for the same Layer 1 sibling reason above); a dedicated test keeps the two
sides in sync. The cross-row rules (every binding's provider declared, fallbacks reachable and
acyclic) live in hivemind.manifest.schema.llm.section.LlmSection, which embeds these rows.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the llm sub-package of the
    manifest schema. Embedded by hivemind.manifest.schema.llm.section.LlmSection and read by
    hivemind.manifest.schema.llm.servers. Calls into hivemind.forage (for Effort,
    never hivemind.llm) and waggle only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - No field named api_key exists anywhere in this module: codingrules section 13 forbids a
      secret literal in the manifest file. api_key_env names an environment variable, and its
      own pattern (API_KEY_ENV_PATTERN) rejects a value that is not shaped like one, catching
      the case where someone pastes a key where a variable name belongs.
    - A provider whose kind is in IN_PROCESS_KINDS has an empty base_url: there is no
      server to reach, so a URL there would only ever be a typo for another kind.

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names" and the capabilities
      rule this module makes into schema.
    - .claude/codingrules.md section 13 for "secrets are never in the manifest file".
    - .claude/codingrules.md section 4 for why this module cannot import hivemind.llm.
    - hivemind.forage.map for the *other* SlotBinding: the forage-side view of one
      [llm.slots] row, built from this module's SlotBinding by whatever loads the manifest
      into forage's own types.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage import Effort
from waggle.messages.forage.values import MAX_MODEL_CHARS, MAX_PROVIDER_CHARS

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
DEFAULT_REQUEST_TIMEOUT_S = (
    120.0  # Two minutes: enough for a slow model, short enough to fail loud.
)
DEFAULT_PROVIDER_TIMEOUT_S = 120.0  # Per-request timeout at the provider level; mirrors the above.
DEFAULT_PROVIDER_SEATS = (
    4  # A modest concurrency default; the manifest raises it for a busier Hive.
)

# The adapters codingrules section 8.1 lists: three chat kinds, plus "whisper_local" (roadmap step
# 6.5a, ADR-0033), a transcription-only kind, and "sentence_transformers" (roadmap step 7.1,
# ADR-0036), an embedding-only kind; a new one needs a codingrules update before it needs a
# manifest change, so this stays a closed Literal rather than a bare str.
ProviderKind = Literal[
    "anthropic", "openai_compat", "fake", "whisper_local", "sentence_transformers"
]

# Kinds with no chat endpoint at all (roadmap 7.1): mirrors hivemind.llm.registry.
# EMBEDDING_ONLY_KINDS member-for-member (llm may not import manifest, and vice versa; see the
# module docstring). The LlmSection validator below is what enforces this at load time.
EMBEDDING_ONLY_KINDS: frozenset[ProviderKind] = frozenset({"sentence_transformers"})

# Kinds whose model runs inside the Hive's own process (faster-whisper for "whisper_local",
# sentence-transformers for "sentence_transformers"): local by construction, so offline mode needs
# no base_url to prove it, and a base_url is refused. Mirrors hivemind.llm.registry.
# IN_PROCESS_KINDS member-for-member, for the same reason.
IN_PROCESS_KINDS: frozenset[ProviderKind] = frozenset({"whisper_local", "sentence_transformers"})


__all__ = [
    "API_KEY_ENV_PATTERN",
    "DEFAULT_PROVIDER_SEATS",
    "DEFAULT_PROVIDER_TIMEOUT_S",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "EMBEDDING_ONLY_KINDS",
    "IN_PROCESS_KINDS",
    "MANIFEST_KEY_PATTERN",
    "MAX_MANIFEST_KEY_CHARS",
    "CapabilityOverrides",
    "EmbeddingOptions",
    "ManifestKey",
    "ProviderKind",
    "ProviderSpec",
    "SlotBinding",
]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
# The manifest-key alias every dict key in this module uses: lowercase, bounded, and pattern-checked
# by pydantic itself, so "keys are lowercase" needs no separate validator.
ManifestKey = Annotated[str, Field(pattern=MANIFEST_KEY_PATTERN, max_length=MAX_MANIFEST_KEY_CHARS)]


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
    audio: bool | None = Field(
        default=None,
        description="Override whether the provider accepts audio content parts directly.",
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


class EmbeddingOptions(BaseModel):
    """``[llm.providers.<name>.embedding]``: options that apply only when this provider embeds.

    Every field is optional or defaulted because most providers never serve the ``EMBEDDER`` slot
    at all; a provider that does reads these instead of a family of new top-level ``ProviderSpec``
    fields that would apply to its chat calls too.
    """

    model_config = _MODEL_CONFIG

    batch_size: int | None = Field(
        default=None,
        gt=0,
        description="How many texts to send per embed call; None lets the adapter's own "
        "default decide.",
    )
    device: str | None = Field(
        default=None,
        description="The in-process device to load the model on ('cpu', 'cuda', 'mps'); None "
        "lets the library choose. Ignored by a kind that is not in-process.",
    )
    local_files_only: bool = Field(
        default=False,
        description="Never reach a model hub; only load from an already-cached or local path. "
        "The composition root forces this True whenever [llm] offline = true, regardless of "
        "what this field says.",
    )


class ProviderSpec(BaseModel):
    """``[llm.providers.<name>]``: one model provider a Hive may call, keyed by name."""

    model_config = _MODEL_CONFIG

    kind: ProviderKind = Field(description="Which adapter speaks to this provider.")
    base_url: str = Field(
        default="",
        description="The provider's base URL; empty means a hosted API with a vendor-fixed "
        "endpoint (e.g. Anthropic). Non-empty for a local server (Ollama, vLLM, llama.cpp). "
        "Always empty for an in-process kind (IN_PROCESS_KINDS), which reaches no server.",
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
    embedding: EmbeddingOptions = Field(
        default_factory=EmbeddingOptions,
        description="Options that apply only when this provider is asked to embed (roadmap 7.1); "
        "ignored by a provider no [llm.slots] row ever binds to the EMBEDDER slot.",
    )
    prompt_overlay: ManifestKey | None = Field(
        default=None,
        description="The name of a directory under hivemind/llm/prompts/overlays/ whose files are "
        "appended to every shipped prompt this provider is sent (roadmap 8.4, ADR-0044): the one "
        "place a weaker or quirkier model's tweaks live, so the shared prompts stay portable. "
        "None (the default) sends every prompt as shipped. The name is checked when the provider "
        "is built, since the manifest cannot see the overlays the llm package ships.",
    )

    @model_validator(mode="after")
    def _in_process_kinds_name_no_base_url(self) -> ProviderSpec:
        """Reject a base_url on a provider whose model runs inside the Hive's own process."""
        # An in-process adapter never opens a connection, so a URL here is not a setting it
        # would honour but a sign the author meant another kind (openai_compat, most likely).
        if self.kind in IN_PROCESS_KINDS and self.base_url:
            raise ValueError(
                f"kind={self.kind!r} runs in process and takes no base_url "
                f"(got {self.base_url!r}); use kind='openai_compat' for a server."
            )
        return self


class SlotBinding(BaseModel):
    """``[llm.slots.<key>]``: one model-slot binding: a provider, a model id, and a fallback chain.

    Distinct from ``hivemind.forage.map.SlotBinding``, the lightweight forage-side view of this
    same row (see that module's docstring): this is the manifest's own, fully-validated shape;
    ``hivemind.cli.stores.slot_bindings`` copies the fields the forage view carries, and
    ``max_output_tokens`` rides along so ``hivemind.llm.slots.BoundModel.stamp`` can apply it.
    """

    model_config = _MODEL_CONFIG

    provider: str = Field(
        max_length=MAX_PROVIDER_CHARS,
        description="The [llm.providers.<name>] key this row binds to.",
    )
    model: str = Field(max_length=MAX_MODEL_CHARS, description="The model id this row binds to.")
    fallback: ManifestKey | None = Field(
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
