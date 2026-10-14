"""Convert a device manifest's `[llm]` section into the tables a colonized Warden is built with.

The Warden on a colonized Swarm device resolves a model binding the way every Warden does, through
a provider registry built from provider rows, a slot table and a seat meter, but its rows come from
the device manifest rather than the Hive Manifest. `device_llm` is the one conversion, written to
give the same answer `hivemind.cli.stores.provider_configs` and `slot_bindings` give for a Hive
Manifest's `[llm]` (a test compares them), so a binding means the same on the device as on the Hive
Stand. It also lists the model servers the manifest declares, as the Queen's capacity report names
them: by id, with nothing loaded, because a Level 1 Warden starts no server (that is a promotion to
Level 2, which the Queen asks for and the operator has allowed by declaring the server).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.borrowed.config`. Calls into `hivemind.forage`, `hivemind.llm`,
    `hivemind.manifest.schema` and waggle only.

Key invariants:
    - A provider's `ProviderConfig`, seats and rate limits, and a slot's `SlotBinding`, are exactly
      what the Hive Stand's composition root builds from the same rows, including the rule that a
      slot with no `max_output_tokens` of its own takes `[llm] default_max_output_tokens`.
    - A declared server is reported with no sources: nothing is loaded on a device that has not
      been promoted, and a source it reported would read as a model the Queen could route to.
    - More servers than the wire carries is a configuration error found at start, not a capacity
      report that fails to build on the first reconnection.

See Also:
    - hivemind.cli.stores for `provider_configs` and `slot_bindings`, the Hive Stand's conversions.
    - hivemind.manifest.device for DeviceManifest, whose `[llm]` this converts.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for "the device carries its own
      configuration".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.common.errors import ConfigurationError
from hivemind.forage.map import SlotBinding
from hivemind.llm import ProviderConfig, RateLimit
from hivemind.manifest.schema import LlmSection
from waggle.messages.forage import ModelServerReport
from waggle.messages.forage.hosting import MAX_MODEL_SERVERS

__all__ = ["DeviceLlm", "device_llm"]


@dataclass(frozen=True, slots=True)
class DeviceLlm:
    """A device manifest's `[llm]`, in the shapes the Warden's composition consumes.

    Attributes:
        providers: Each provider's registry configuration, by its manifest name.
        seats: Each provider's concurrency budget, which the Warden's Fanner meters calls by.
        limits: Each provider's request and token rate limits.
        slots: Every slot binding, in manifest order.
        servers: The declared model servers as the capacity report names them: id only.
        offline: Whether the manifest asks for offline mode.
    """

    providers: Mapping[str, ProviderConfig]
    seats: Mapping[str, int]
    limits: Mapping[str, RateLimit]
    slots: tuple[SlotBinding, ...]
    servers: tuple[ModelServerReport, ...]
    offline: bool


def device_llm(section: LlmSection) -> DeviceLlm:
    """Convert `section` into the tables the Warden is built with.

    Args:
        section: The validated `[llm]` section of a device manifest.

    Returns:
        The provider, seat, rate-limit, slot and server tables.

    Raises:
        ConfigurationError: The manifest declares more model servers than one capacity report can
            carry.
    """
    if len(section.servers) > MAX_MODEL_SERVERS:
        raise ConfigurationError(
            f"The device manifest declares {len(section.servers)} model servers; a Cell reports "
            f"at most {MAX_MODEL_SERVERS}."
        )
    return DeviceLlm(
        providers=_provider_configs(section),
        seats={name: spec.seats for name, spec in section.providers.items()},
        limits={
            name: RateLimit(
                requests_per_minute=spec.requests_per_minute,
                tokens_per_minute=spec.tokens_per_minute,
            )
            for name, spec in section.providers.items()
        },
        slots=_slot_bindings(section),
        servers=tuple(ModelServerReport(server_id=name, sources=()) for name in section.servers),
        offline=section.offline,
    )


def _provider_configs(section: LlmSection) -> dict[str, ProviderConfig]:
    """Build each provider's registry row, with the default model its first slot binding names."""
    # An openai_compat provider has no model of its own to default to, so the first slot that
    # binds it supplies one, exactly as the Hive Stand's composition root does.
    defaults: dict[str, str] = {}
    for row in section.slots.values():
        defaults.setdefault(row.provider, row.model)
    return {
        name: ProviderConfig(
            kind=spec.kind,
            base_url=spec.base_url,
            api_key_env=spec.api_key_env,
            timeout_s=spec.timeout_s,
            capability_overrides=spec.capabilities.as_overrides(),
            default_model=defaults.get(name),
            embedding_batch_size=spec.embedding.batch_size,
            embedding_device=spec.embedding.device,
            # An in-process embedder must never reach a model hub on a device proven offline.
            embedding_local_files_only=spec.embedding.local_files_only or section.offline,
            prompt_overlay=spec.prompt_overlay,
        )
        for name, spec in section.providers.items()
    }


def _slot_bindings(section: LlmSection) -> tuple[SlotBinding, ...]:
    """Build every slot binding, a row with no output cap of its own taking the section's."""
    return tuple(
        SlotBinding(
            key=key,
            provider=row.provider,
            model=row.model,
            fallback=row.fallback,
            effort=row.effort,
            max_output_tokens=row.max_output_tokens
            if row.max_output_tokens is not None
            else section.default_max_output_tokens,
        )
        for key, row in section.slots.items()
    )
