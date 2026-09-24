"""Build a Virtual Cell's own `[llm.providers]`/`[llm.slots]` table, Cell-reachable and keyed.

Split out of `hivemind.cli.compose.virtual_cells` for its own line budget (codingrules 5.1): that
module builds everything else a Virtual Cell needs; this one concept -- convert this Hive's own
manifest `[llm]` section into `hivemind.hive.backends.provider_table.CellProviderSpec`/
`CellSlotSpec` rows a Cell can actually use, each provider's seats and rate limits included so
the Cell's own Fanner meters by the operator's own figures -- is one `_cell_providers`/
`_cell_slots`/`_provider_api_keys` call away from `virtual_cells._endpoint_for`, its one caller.
Reuses `hivemind.cli.stores.provider_configs`/`slot_bindings`, the exact manifest ->
`ProviderConfig`/`SlotBinding` conversion the Hive Stand's own `ProviderRegistry` is already built
from (`hivemind.cli.compose.deps.build_provider_registry`), never a second, drifting copy; every
loopback `base_url` is rewritten through `hivemind.cli.in_cell.config.rewrite_loopback_base_url` so
it is reachable from inside a Cell, and every provider's own API key is resolved from this
composition root's own environment through `hivemind.manifest.env.provider_api_key`, the same
function that resolves one for the Hive Stand's own registry.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.virtual_cells._endpoint_for`. Calls into `hivemind.cli.in_cell.config`
    (rewrite_loopback_base_url), `hivemind.cli.stores` (provider_configs, slot_bindings),
    `hivemind.hive.backends.provider_table` (CellProviderSpec, CellSlotSpec), `hivemind.manifest`
    (HiveManifest), `hivemind.manifest.env` (provider_api_key), `hivemind.manifest.schema.llm`
    (ProviderSpec), `hivemind.llm.registry` (ProviderConfig, a type only) and waggle only.

Key invariants:
    - `_cell_providers`/`_cell_slots` never read `environ`: an API key's own VALUE only ever
      travels through `_provider_api_keys`, never the JSON either of the other two feeds
      (`hivemind.hive.backends.provider_table`'s own module docstring).
    - `_provider_api_key_env`'s derivation exactly mirrors `hivemind.manifest.env.
      provider_api_key`'s own (that function returns only the resolved `SecretStr`, never the
      variable name it read); kept as its own function so both `_cell_providers` (the name a Cell
      resolves against) and `_provider_api_keys` (the name this composition root renders under)
      agree, by construction, on the identical variable.

See Also:
    - hivemind.cli.compose.virtual_cells for _endpoint_for, this module's one caller.
    - hivemind.hive.backends.provider_table for CellProviderSpec/CellSlotSpec, this module's own
      output shape.
    - hivemind.cli.stores for provider_configs/slot_bindings, the conversion this module reuses.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import SecretStr

from hivemind.cli.in_cell.config import rewrite_loopback_base_url
from hivemind.cli.stores import provider_configs, slot_bindings
from hivemind.hive.backends.provider_table import CellProviderSpec, CellSlotSpec
from hivemind.llm.registry import ProviderConfig
from hivemind.manifest import HiveManifest
from hivemind.manifest.env import provider_api_key
from hivemind.manifest.schema.llm import ProviderSpec

__all__ = ["cell_providers", "cell_slots", "provider_api_keys"]


def cell_providers(
    manifest: HiveManifest, gateway_host: str | None
) -> tuple[CellProviderSpec, ...]:
    """Build this Hive's own `[llm.providers]` table, decoupled and Cell-reachable.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        gateway_host: The host a Cell reaches the Hive Stand through (`"host.docker.internal"`,
            `hivemind.hive.backends.qemu.network.USER_NET_HOST_ALIAS`), or None to leave every
            base_url unchanged (the "fake" backend: same process, loopback genuinely reachable).
    """
    return tuple(
        _cell_provider(name, config, manifest.llm.providers[name], gateway_host)
        for name, config in provider_configs(manifest).items()
    )


def cell_slots(manifest: HiveManifest) -> tuple[CellSlotSpec, ...]:
    """Build this Hive's own `[llm.slots]` table, decoupled.

    Reuses `hivemind.cli.stores.slot_bindings`, the exact manifest -> SlotBinding conversion the
    Hive Stand's own Fanner and `ProviderRegistry` already share.
    """
    return tuple(
        CellSlotSpec(
            key=row.key,
            provider=row.provider,
            model=row.model,
            fallback=row.fallback,
            effort=row.effort.value,
            max_output_tokens=row.max_output_tokens,
        )
        for row in slot_bindings(manifest)
    )


def provider_api_keys(manifest: HiveManifest, environ: Mapping[str, str]) -> dict[str, SecretStr]:
    """Resolve every configured provider's own API key, keyed by its own variable name.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: The composition root's own environment mapping (codingrules section 13 --
            `hivemind.manifest.env.provider_api_key` is the one function that actually reads it).

    Returns:
        One entry per provider that actually has a key set in `environ`; a provider needing no
        key (a local server with no auth, or one whose operator has not set the variable yet)
        contributes nothing here.
    """
    keys: dict[str, SecretStr] = {}
    for name, spec in manifest.llm.providers.items():
        key = provider_api_key(name, spec, environ)
        if key is not None:
            keys[_provider_api_key_env(name, spec)] = key
    return keys


def _cell_provider(
    name: str, config: ProviderConfig, spec: ProviderSpec, gateway_host: str | None
) -> CellProviderSpec:
    """Build one Cell-reachable provider row: `config`'s wire fields, `spec`'s seats and limits."""
    return CellProviderSpec(
        name=name,
        kind=config.kind,
        base_url=_rewritten_base_url(config.base_url, gateway_host),
        default_model=config.default_model,
        # The operator's own figures, so the Cell's Fanner meters exactly as the Hive Stand's does.
        seats=spec.seats,
        capabilities=config.capability_overrides,
        api_key_env=_provider_api_key_env(name, spec),
        requests_per_minute=spec.requests_per_minute,
        tokens_per_minute=spec.tokens_per_minute,
    )


def _rewritten_base_url(base_url: str, gateway_host: str | None) -> str:
    """Rewrite a loopback `base_url` to `gateway_host`, or leave it unchanged when `None`.

    `gateway_host=None` is the "fake" backend's own case: it runs in the same process as the Hive
    Stand (module docstring), so loopback is already reachable and nothing needs rewriting.
    """
    if gateway_host is None:
        return base_url
    return rewrite_loopback_base_url(base_url, gateway_host)


def _provider_api_key_env(name: str, spec: ProviderSpec) -> str:
    """Return the exact `HIVEMIND_<NAME>_API_KEY`-shaped variable name this provider's key rides.

    Mirrors `hivemind.manifest.env.provider_api_key`'s own derivation (that function returns only
    the resolved `SecretStr`, never the variable name it read, and this composition root needs
    the name too, to render the Cell's own literal `HIVEMIND_<NAME>_API_KEY` variable under the
    identical name `hivemind.llm.registry.ProviderRegistry._resolve_api_key` looks it back up by).
    """
    return spec.api_key_env or f"HIVEMIND_{name.upper()}_API_KEY"
