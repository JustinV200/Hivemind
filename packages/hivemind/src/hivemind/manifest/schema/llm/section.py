"""Define the [llm] section: providers, model slots, grade floors and the servers a Cell hosts.

LlmSection is the root of the [llm] table. Its validators are what keep the slot table
honest: every key lowercase, every ModelSlot bound to something, every binding's provider
declared, every fallback reachable and acyclic, and offline = true refusing any provider that
is not provably local (an in-process kind, IN_PROCESS_KINDS, is local by construction and
needs no base_url). It also refuses binding an EMBEDDING_ONLY_KINDS provider
(sentence_transformers, which has no chat endpoint at all) to any [llm.slots] key except
embedder itself or a key reachable only through the embedder's own fallback chain, so a
manifest can never accidentally send a Worker's chat calls to a provider that cannot answer them.
Roadmap step 8.1 adds [llm.floors] (LlmSection.floors): each slot's minimum model grade,
which routing reads through grade_floors() (hivemind.manifest.schema.llm.floors holds the
checks and the defaults). Roadmap step 8.3 (ADR-0044) adds [llm.servers]
(LlmSection.servers): the model servers a Cell's Warden may start, checked against the
providers by hivemind.manifest.schema.llm.servers.check_servers. The provider, slot-binding
and capability-override rows themselves live in hivemind.manifest.schema.llm.providers.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the llm sub-package of the
    manifest schema. Embedded by hivemind.manifest.schema.manifest.HiveManifest. Calls into
    hivemind.forage (for ModelSlot, never hivemind.llm), its sibling modules
    providers, floors and servers, and waggle only.

Key invariants:
    - Every model here is frozen and forbids unknown fields (codingrules section 8.5).
    - LlmSection.providers, .slots and .servers keys are lowercase snake_case
      (MANIFEST_KEY_PATTERN), enforced by the dict key's own field constraint, not a separate
      validator.
    - Every ModelSlot member's manifest_key is present in LlmSection.slots after
      validation; a manifest that leaves one unbound fails to load, because
      hivemind.llm.slots.resolve must never find a slot with nothing behind it.
    - A fallback chain that revisits a key it has already visited is rejected before it ever
      reaches hivemind.forage.map.ForageMap.for_slot, which would otherwise loop forever.

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names".
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for ``[llm.servers]``.
    - hivemind.manifest.schema.llm.providers for the provider and slot-binding rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.forage import ModelSlot
from hivemind.manifest.schema.llm.floors import (
    FloorGrade,
    complete_floors,
    reject_unknown_floor_keys,
)
from hivemind.manifest.schema.llm.providers import (
    DEFAULT_REQUEST_TIMEOUT_S,
    EMBEDDING_ONLY_KINDS,
    IN_PROCESS_KINDS,
    ManifestKey,
    ProviderSpec,
    SlotBinding,
)
from hivemind.manifest.schema.llm.servers import ServerSpec, check_servers
from waggle.uris import is_loopback_host

__all__ = ["LlmSection"]

# A frozen, extras-forbidding config every model in this module shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class LlmSection(BaseModel):
    """``[llm]``: providers, and the table binding every model slot to one of them."""

    model_config = _MODEL_CONFIG

    offline: bool = Field(
        default=False,
        description="True refuses any provider whose base_url is empty (hosted) or not loopback, "
        "except an in-process kind (IN_PROCESS_KINDS), which is local by construction.",
    )
    default_max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        description="A reply-length cap for every slot that names none of its own. None (the "
        "default) leaves each call site's own budget alone: a planner, a judge and a Drone "
        "need different lengths, and one number for all of them is an operator's choice, "
        "never a silent default.",
    )
    request_timeout_s: float = Field(
        default=DEFAULT_REQUEST_TIMEOUT_S,
        gt=0,
        description="Default per-request timeout, in seconds.",
    )
    providers: dict[ManifestKey, ProviderSpec] = Field(
        default_factory=dict, description="Every model provider this Hive may call, keyed by name."
    )
    slots: dict[ManifestKey, SlotBinding] = Field(
        default_factory=dict,
        description="Every model-slot binding, keyed by a lowercase ModelSlot name or a named "
        "binding used only as a fallback target.",
    )
    floors: dict[ManifestKey, FloorGrade] = Field(
        default_factory=dict,
        description="[llm.floors]: each slot's minimum model grade, 1 to 5, keyed by the slot's "
        "lowercase name. A slot left out keeps its default (grade_floors).",
    )
    servers: dict[ManifestKey, ServerSpec] = Field(
        default_factory=dict,
        description="[llm.servers.<server_id>]: the model servers a Cell's Warden may start on "
        "its own Cell (roadmap step 8.3); empty means this Hive hosts none.",
    )

    def grade_floors(self) -> Mapping[ModelSlot, int]:
        """Return every slot's grade floor: this table's value, else the shipped default."""
        return complete_floors(self.floors)

    @model_validator(mode="after")
    def _every_floor_names_a_model_slot(self) -> LlmSection:
        """Reject an [llm.floors] key that is not a ModelSlot's manifest key."""
        reject_unknown_floor_keys(self.floors)
        return self

    @model_validator(mode="after")
    def _every_server_serves_a_loopback_openai_compat_provider(self) -> LlmSection:
        """Reject a [llm.servers] row whose provider is missing, not openai_compat or off-port."""
        check_servers(self.servers, self.providers)
        return self

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
        """Reject a provider [llm] offline = true cannot prove is local.

        An ``IN_PROCESS_KINDS`` member (``whisper_local``, ``sentence_transformers``) never opens a
        ``base_url`` at all (roadmap steps 6.5a and 7.1), so it is exempt: provably local by
        construction (``ProviderSpec`` refuses it a ``base_url`` in the first place).
        """
        if not self.offline:
            return self
        for name, spec in self.providers.items():
            if spec.kind in IN_PROCESS_KINDS:
                continue  # Its model runs in this process: local by construction, no URL to check.
            if not _is_provably_local(spec.base_url):
                raise ValueError(
                    f"[llm] offline = true but [llm.providers.{name}] is not provably local "
                    f"(base_url={spec.base_url!r}); every provider must have a loopback base_url."
                )
        return self

    @model_validator(mode="after")
    def _embedding_only_kinds_serve_only_the_embedder_slot(self) -> LlmSection:
        """Reject an EMBEDDING_ONLY_KINDS provider bound outside the embedder's own chain.

        Runs after ``_every_fallback_exists_and_never_cycles``/``_every_model_slot_is_bound``
        (definition order; pydantic v2 runs ``mode="after"`` validators in the order they are
        declared), but does not depend on that: ``_reachable_keys`` below stops at a repeated key
        on its own, so a still-cyclic table it might see is walked safely, never looped forever.
        """
        allowed = _keys_only_the_embedder_slot_reaches(self.slots)
        for key, binding in self.slots.items():
            spec = self.providers.get(binding.provider)
            if spec is None:
                continue  # Reported by _every_binding_names_a_declared_provider instead.
            if spec.kind in EMBEDDING_ONLY_KINDS and key not in allowed:
                raise ValueError(
                    f"[llm.slots.{key}] binds provider {binding.provider!r}, an embedding-only "
                    f"kind ({spec.kind!r}), which may serve only 'embedder' or a key reachable "
                    "only through the embedder's own fallback chain."
                )
        return self


def _keys_only_the_embedder_slot_reaches(slots: dict[str, SlotBinding]) -> set[str]:
    """Return every [llm.slots] key that only the embedder slot's own chain ever reaches.

    A key qualifies when the embedder slot's own walk reaches it (embedder itself, or a fallback
    of a fallback of it) AND no *other* ModelSlot's own walk ever reaches the same key -- a key a
    chat slot's chain also passes through is not "only" the embedder's, however it got there.
    """
    reach_counts: dict[str, int] = {}
    embedder_reach: set[str] = set()
    for slot in ModelSlot:
        reached = _reachable_keys(slot.manifest_key, slots)
        for key in reached:
            reach_counts[key] = reach_counts.get(key, 0) + 1
        if slot is ModelSlot.EMBEDDER:
            embedder_reach = reached
    return {key for key in embedder_reach if reach_counts.get(key, 0) == 1}


def _reachable_keys(start: str, slots: dict[str, SlotBinding]) -> set[str]:
    """Return every key visited walking `.fallback` from `start` (`start` itself included).

    Safe against a still-cyclic `slots` table on its own (the `current not in visited` loop
    condition stops the walk the moment a key repeats), independent of whether the cycle
    validator has already run for this model.
    """
    visited: set[str] = set()
    current: str | None = start
    while current is not None and current not in visited:
        visited.add(current)
        binding = slots.get(current)
        current = binding.fallback if binding is not None else None
    return visited


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
