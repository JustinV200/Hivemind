"""Build a Virtual Cell's own `[llm.providers]`/`[llm.slots]` table, Cell-reachable and keyed.

Split out of `hivemind.cli.compose.virtual_cells` for its own line budget (codingrules 5.1): that
module builds everything else a Virtual Cell needs; this one concept -- convert this Hive's own
manifest `[llm]` section into `hivemind.hive.backends.provider_table.CellProviderSpec`/
`CellSlotSpec` rows a Cell can actually use, each provider's seats and rate limits included so
the Cell's own Fanner meters by the operator's own figures -- is one `_cell_providers`/
`_cell_slots`/`_provider_api_keys` call away from `virtual_cells._endpoint_for`, its one caller.
Reuses `hivemind.cli.stores.provider_configs`/`slot_bindings`, the exact manifest ->
`ProviderConfig`/`SlotBinding` conversion the Hive Stand's own `ProviderRegistry` is already built
from (`hivemind.cli.stores.build_provider_registry`), never a second, drifting copy; every
loopback `base_url` is rewritten through `hivemind.cli.in_cell.config.rewrite_loopback_base_url` so
it is reachable from inside a Cell, and every provider's own API key is resolved from this
composition root's own environment through `hivemind.manifest.env.provider_api_key`, the same
function that resolves one for the Hive Stand's own registry.

`providers_answering_on` asks the opposite question of the same table: which host-side providers
an isolated Cell can still reach. With `[virtual_cells] control_subnet` set, a Docker Cell keeps
its link to the control gateway through an egress cut, so a model server bound to every interface
(`0.0.0.0`) answers it there too; the operator decided on a warning and docs, not a host firewall
rule (`.claude/phase-10-handoff.md`), so the probe is one bounded TCP connect per provider and
`hivemind.cli.compose.virtual_cell_backends.prepare_backend` logs what answered.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.virtual_cells._endpoint_for`, and by `virtual_cell_backends.
    prepare_backend` for the exposure probe. Calls into asyncio (one TCP connect per probe),
    `hivemind.cli.in_cell.config` (rewrite_loopback_base_url), `hivemind.cli.stores`
    (provider_configs, slot_bindings),
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
    - `providers_answering_on` never sends a byte and never waits past its timeout per probe; a
      provider whose base_url names a remote host is never probed.

See Also:
    - hivemind.cli.compose.virtual_cells for _endpoint_for, this module's one caller.
    - hivemind.hive.backends.provider_table for CellProviderSpec/CellSlotSpec, this module's own
      output shape.
    - hivemind.cli.stores for provider_configs/slot_bindings, the conversion this module reuses.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import SecretStr

from hivemind.cli.in_cell.config import rewrite_loopback_base_url
from hivemind.cli.stores import provider_configs, slot_bindings
from hivemind.hive.backends.provider_table import CellProviderSpec, CellSlotSpec
from hivemind.llm.registry import ProviderConfig
from hivemind.manifest import HiveManifest
from hivemind.manifest.env import provider_api_key
from hivemind.manifest.schema.llm import ProviderSpec

__all__ = [
    "EXPOSURE_PROBE_TIMEOUT_S",
    "cell_providers",
    "cell_slots",
    "provider_api_keys",
    "providers_answering_on",
]

# One TCP connect to a local address: a listener answers in well under a millisecond, so this only
# bounds a connect nothing answers (a filtered port, an address Docker Desktop keeps in its VM).
EXPOSURE_PROBE_TIMEOUT_S = 0.5
# Hosts a provider's base_url names when the model server runs on the Hive Stand itself; a Cell
# reaches every one of them through the host (rewrite_loopback_base_url), so each is worth probing.
_HOST_SIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "host.docker.internal"})
_DEFAULT_PORTS = {"http": 80, "https": 443}  # A base_url that names no port uses its scheme's.


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


async def providers_answering_on(
    manifest: HiveManifest, address: str, timeout_s: float = EXPOSURE_PROBE_TIMEOUT_S
) -> tuple[tuple[str, int], ...]:
    """Return every host-side provider whose port also answers on `address`, with that port.

    A model server bound to every interface answers on the Docker control gateway too, so an
    isolated Cell still reaches it there: the egress cut cannot stop that service. One bounded TCP
    connect per host-side provider, all at once; a provider named by a remote host is never
    probed, and a connect refused, filtered or timed out counts as not answering.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        address: The host's own non-loopback address to probe, the control gateway.
        timeout_s: The bound on each connect.

    Returns:
        `(provider name, port)` for each provider that answered, in manifest order.
    """
    candidates = [
        (name, port)
        for name, config in provider_configs(manifest).items()
        if (port := _host_side_port(config.base_url)) is not None
    ]
    answers = await asyncio.gather(
        *(_answers(address, port, timeout_s) for _name, port in candidates)
    )
    return tuple(pair for pair, answered in zip(candidates, answers, strict=True) if answered)


def _host_side_port(base_url: str) -> int | None:
    """Return the port of a base_url naming the Hive Stand itself; None for any other URL."""
    parts = urlsplit(base_url)
    if parts.hostname not in _HOST_SIDE_HOSTS:
        return None  # A hosted API, or an in-process kind's empty base_url: nothing local.
    try:
        return parts.port or _DEFAULT_PORTS.get(parts.scheme)
    except ValueError:
        return None  # A malformed port: the provider fails on its own, nothing to probe here.


async def _answers(address: str, port: int, timeout_s: float) -> bool:
    """Return whether a TCP listener accepts a connection on `address:port` within `timeout_s`."""
    try:
        # External wait: one local connect, bounded so a filtered port never holds hive run.
        async with asyncio.timeout(timeout_s):
            _reader, writer = await asyncio.open_connection(address, port)
    except (OSError, TimeoutError):
        return False
    writer.close()  # Nothing is sent: the accept alone proves the bind.
    # A reset or a slow close changes nothing: it accepted. Bounded all the same.
    with contextlib.suppress(OSError, TimeoutError):
        async with asyncio.timeout(timeout_s):
            await writer.wait_closed()
    return True


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
        prompt_overlay=config.prompt_overlay,  # Roadmap 8.4: the same overlay inside the Cell.
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
