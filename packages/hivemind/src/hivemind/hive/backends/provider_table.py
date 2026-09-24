"""Define CellProviderSpec and CellSlotSpec: the provider table a Virtual Cell's Warden needs.

A Virtual Cell has no Hive Manifest of its own (`hivemind.manifest.env`'s own module docstring),
and a grant only ever names a slot's provider by its manifest *name* -- never a base URL, kind or
key (`waggle.messages.forage.values.AllowedBinding`/`SourceRef`). So the one composition root that
already holds the full `[llm.providers]`/`[llm.slots]` table (`hivemind.cli.compose.virtual_cells`)
must hand a freshly provisioned Cell that table itself, once, at provision time --
`hivemind.hive.backends.bootstrap.QueenEndpoint` is where it rides along, and `CellBootstrap.
environment()` is where it becomes the `HIVEMIND_PROVIDERS`/`HIVEMIND_SLOTS` JSON this module's own
render functions produce. `CellProviderSpec` mirrors `hivemind.llm.registry.ProviderConfig` plus
the provider's own manifest name (a bare tuple has nowhere else to carry it); `CellSlotSpec` mirrors
`hivemind.forage.map.SlotBinding` the same way. Deliberately NOT those types themselves: this
package's own render step only needs plain, JSON-safe values, never a live registry or pydantic
model, and keeping `hive.backends` free of an `hivemind.llm`/`hivemind.forage` dependency it never
had before keeps this table's one job -- describe it, then render it -- from growing a second one.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends`. Used by `hivemind.hive.backends.
    bootstrap` (`QueenEndpoint`, `CellBootstrap.environment()`) and by `hivemind.cli.compose.
    virtual_cells`, the composition root that builds one of these per configured provider/slot.
    Calls into the standard library only.

Key invariants:
    - Every field here is a plain JSON-safe value (str, bool, int, or a mapping/tuple of those):
      no `SecretStr`, no live object. An API key *value* never appears on `CellProviderSpec`
      (`api_key_env` is only the variable NAME the Cell's own `hivemind.llm.registry.
      ProviderRegistry` resolves against its own environment) -- `QueenEndpoint.provider_api_keys`
      carries the actual `SecretStr`, rendered as that variable directly, never through this
      module's JSON (`hive.backends.bootstrap`'s own module docstring).
    - `render_providers_json`/`render_slots_json` never raise: every field is already a JSON-safe
      primitive, so encoding a well-formed tuple of either dataclass cannot fail.

See Also:
    - hivemind.hive.backends.bootstrap for QueenEndpoint and CellBootstrap.environment(), this
      module's one caller inside hive.backends.
    - hivemind.cli.compose.virtual_cells for the composition root that builds these from a loaded
      HiveManifest's [llm.providers]/[llm.slots].
    - hivemind.cli.in_cell.config for the in-Cell side that parses this JSON back into
      hivemind.llm.registry.ProviderConfig/hivemind.forage.map.SlotBinding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

# Mirrors hivemind.llm.registry.ProviderKind member-for-member; this module may not import
# hivemind.llm (module docstring: deliberately dependency-light), so this is its own copy, the
# same "kept in sync by a dedicated test" arrangement hivemind.llm.registry.ProviderKind already
# documents for its own mirror of hivemind.manifest.schema.llm.ProviderKind.
CellProviderKind = Literal["anthropic", "openai_compat", "fake", "sentence_transformers"]

__all__ = [
    "CellProviderKind",
    "CellProviderSpec",
    "CellSlotSpec",
    "render_providers_json",
    "render_slots_json",
]


@dataclass(frozen=True, slots=True)
class CellProviderSpec:
    """One `[llm.providers.<name>]` row, as a Virtual Cell's Warden needs it to reach a provider.

    Built by `hivemind.cli.compose.virtual_cells` from `hivemind.cli.stores.provider_configs`, the
    same manifest -> `hivemind.llm.registry.ProviderConfig` conversion the Hive Stand's own
    `ProviderRegistry` is built from.
    """

    name: str  # The [llm.providers.<name>] key; ProviderConfig itself carries no name of its own.
    kind: CellProviderKind
    # Already rewritten to be reachable FROM the Cell (docker_gateway_url/USER_NET_HOST_ALIAS),
    # never the Hive Stand's own loopback address.
    base_url: str
    default_model: str | None
    # ProviderConfig.capability_overrides, unchanged.
    capabilities: Mapping[str, bool | int] = field(default_factory=dict)
    # The HIVEMIND_<NAME>_API_KEY-shaped variable name; never the key itself.
    api_key_env: str = ""


@dataclass(frozen=True, slots=True)
class CellSlotSpec:
    """One `[llm.slots.<key>]` row, as a Virtual Cell's Warden needs it to resolve a ModelSlot.

    Built by `hivemind.cli.compose.virtual_cells` from `hivemind.cli.stores.slot_bindings`, the
    same manifest -> `hivemind.forage.map.SlotBinding` conversion the Hive Stand's own Fanner and
    `ProviderRegistry` already share.
    """

    key: str
    provider: str
    model: str
    fallback: str | None
    effort: str  # hivemind.forage.slots.Effort.value ("LOW"/"MEDIUM"/"HIGH"); see module docstring.
    max_output_tokens: int | None


def render_providers_json(providers: tuple[CellProviderSpec, ...]) -> str:
    """Render `providers` as the JSON array `HIVEMIND_PROVIDERS` carries.

    Args:
        providers: Every configured provider, in manifest order.

    Returns:
        A JSON array of objects, one per provider, field names matching `CellProviderSpec`.
    """
    return json.dumps([asdict(spec) for spec in providers])


def render_slots_json(slots: tuple[CellSlotSpec, ...]) -> str:
    """Render `slots` as the JSON array `HIVEMIND_SLOTS` carries.

    Args:
        slots: Every `[llm.slots]` row, in manifest order.

    Returns:
        A JSON array of objects, one per slot, field names matching `CellSlotSpec`.
    """
    return json.dumps([asdict(spec) for spec in slots])
