"""Turn InCellEnv into InCellRuntimeConfig: everything the in-Cell Warden entry point is built from.

Codingrules section 13: the composition root is the only place configuration becomes deps.
`hivemind.manifest.env.read_in_cell_env` only extracts what `os.environ` holds (`InCellEnv`, every
field optional); this module is where a Virtual Cell image's entry point (roadmap step 5.5)
decides which of those are required, parses them into typed ids and Ed25519 keys, mints this
process's own node id, probes this Cell's own platform and capacity, and assembles the
`hivemind.wardens.spawn.in_cell.InCellSpawnConfig` a real Warden will eventually lease through
(roadmap steps 5.4/5.6). Signing is mandatory across a machine boundary (roadmap step 1.7), so
every key field is required here even though `InCellEnv` itself leaves them optional.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell.local` (HiveStandConfig, probe_host -- reused for this Cell's own probe, since
    every Virtual Cell image is Ubuntu Linux, the same host shape the Hive Stand's own probe
    already handles), `hivemind.cell.tiers`, `hivemind.common.errors`, `hivemind.manifest.env`
    (InCellEnv), `hivemind.wardens.spawn` (InCellSpawnConfig), `waggle.clock`, `waggle.ids` and
    `waggle.signing` only.

Roadmap step 10.3a: this Cell's tier is no longer assumed MEADOW. It comes from the bootstrap
(`HIVEMIND_COMB_SHIELD`, which the provisioning backend always writes; unset reads as MEADOW, the
tier of every Cell minted before it existed), and the link has to match it: a Night Veil Cell must
dial a v3 onion service through a loopback SOCKS proxy (`HIVEMIND_SOCKS_PROXY_URL`), and a Queen
URL that is an onion service is a Night Veil Cell's only, so a missing tier can never pass a Night
Veil link off as MEADOW. PROPOLIS is refused: no in-Cell attestation exists for it yet, and a
Cell that cannot attest its tier must not announce it.

Key invariants:
    - `build_runtime_config` raises `ConfigurationError` naming the missing or malformed variable
      for any of: the Queen's Waggle URL, this Cell's id, the Queen's bee address, the Queen's own
      node id, this Cell's signing key, the Queen's verify key, the Cell's tier or the SOCKS proxy
      -- never a bare `KeyError` or a cryptography-library exception.
    - A NIGHT_VEIL config always names a v3 onion Queen URL and a loopback socks5h/socks4a proxy;
      no other tier's config names an onion Queen URL.
    - A key given as both `..._FILE` and the inline hex variable prefers the file (a mounted
      secret is harder to leak than a bare environment variable, codingrules section 15).
    - `node_id` and `warden_id` are freshly minted every time this process starts: a container is
      disposable (roadmap step 5.5's own key invariant on `hivemind.cell.in_cell`), so there is no
      identity to persist across restarts the way the Hive Stand's own node key is.

See Also:
    - .claude/roadmap.md step 5.5 for the env var list this module reads.
    - .claude/roadmap.md step 1.7 for "signing... mandatory for anything that crosses a machine
      boundary."
    - hivemind.manifest.env for InCellEnv/read_in_cell_env, this module's own input.
    - hivemind.cli.in_cell.link for CellLinkDeps, mostly built from this module's own output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.local.probe import probe_host
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.common.errors import ConfigurationError
from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import Effort
from hivemind.guard.net import IPAddress
from hivemind.llm.registry import ProviderConfig, ProviderKind
from hivemind.manifest.env import InCellEnv
from hivemind.wardens.spawn import InCellSpawnConfig
from waggle.clock import Clock
from waggle.errors import InvalidIdError
from waggle.ids import (
    CellId,
    HiveId,
    IdKind,
    NodeId,
    WardenId,
    new_node_id,
    new_warden_id,
    parse_id,
)
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.transport.socks import SocksProxy
from waggle.uris import check_waggle_uri, is_loopback_host, is_onion_service_host

# Where this dispatch's Cell keeps every lease's own scratch subdirectory (roadmap step 5.5:
# InCellSpawnSource.lease() creates one under this root per lease). Matches the non-root `hive`
# user's data directory the base-ubuntu image creates (images/base-ubuntu/Dockerfile/README).
DEFAULT_SCRATCH_ROOT = Path("/var/lib/hivemind/scratch")

# How often this Cell sends CellHeartbeat; no manifest exists inside a Virtual Cell image to read
# a configured cadence from (this module's own docstring), so a fixed, generous constant stands in
# until a future step threads one through CellReady/an explicit env var if that proves too coarse.
DEFAULT_HEARTBEAT_INTERVAL_S = 15.0

# Mirrors hivemind.llm.registry.ProviderKind's own three members; a HIVEMIND_PROVIDERS row naming
# anything else is malformed input from outside this process (the Queen's own backend), never a
# bare KeyError/ValueError (this module's own "ConfigurationError naming the variable" rule).
_VALID_PROVIDER_KINDS = frozenset(("anthropic", "openai_compat", "fake"))

__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "DEFAULT_SCRATCH_ROOT",
    "InCellRuntimeConfig",
    "build_runtime_config",
    "gateway_host",
    "rewrite_loopback_base_url",
]


@dataclass(frozen=True, slots=True)
class InCellRuntimeConfig:
    """Everything `hivemind.cli.in_cell.main` needs to announce this Cell and build its Warden.

    Attributes:
        queen_waggle_url: Where this Cell dials out to.
        hive_id: The Queen's own bee address.
        queen_node_id: The node id the Queen signs its own frames as.
        node_id: This Cell's own freshly minted node id.
        warden_id: This Warden's own freshly minted id.
        signer: Signs every frame this Cell sends, with its own provisioned private key.
        verifier: Verifies every frame this Cell receives, trusting only the Queen's public key.
        spawn_config: What describes this Cell to `hivemind.wardens.spawn.in_cell.
            InCellSpawnSource`, once a Warden is wired up to use one (roadmap steps 5.4/5.6).
        heartbeat_interval_s: How often `CellHeartbeat` is sent.
        socks_proxy_url: The loopback SOCKS proxy every Waggle dial goes through (a Night Veil
            Cell's Tor SOCKS port, roadmap step 10.3a), already validated; None dials directly.
        providers: This Hive's own `[llm.providers]` table, parsed from `HIVEMIND_PROVIDERS`
            (`hivemind.cli.in_cell.providers.build_in_cell_provider_registry`'s own input); empty
            when the variable is unset, in which case that module keeps building today's fake.
        slots: This Hive's own `[llm.slots]` table, parsed from `HIVEMIND_SLOTS` the same way.
        llm_offline: This Hive's own `[llm] offline` flag, from `HIVEMIND_LLM_OFFLINE`; `False`
            when unset, matching `hivemind.manifest.schema.llm.LlmSection.offline`'s own default.
        environ: The full environment this process was started with, carried through only so
            `build_in_cell_provider_registry` can resolve each provider's own API key by the exact
            variable name `providers`' own `api_key_env` names (`hivemind.manifest.env.InCellEnv.
            environ`'s own docstring explains why this is not a second environment read).
        hive_stand_addresses: The addresses the Queen's host resolved to, once, at start
            (`hivemind.cli.in_cell.hive_stand`); empty until then, and always for a Night Veil
            Cell, whose onion host is never resolved here.
    """

    queen_waggle_url: str
    hive_id: HiveId
    queen_node_id: NodeId
    node_id: NodeId
    warden_id: WardenId
    signer: Ed25519Signer
    verifier: Ed25519Verifier
    spawn_config: InCellSpawnConfig
    heartbeat_interval_s: float
    socks_proxy_url: str | None
    providers: Mapping[str, ProviderConfig]
    slots: tuple[SlotBinding, ...]
    llm_offline: bool
    environ: Mapping[str, str]
    hive_stand_addresses: tuple[IPAddress, ...] = ()


def build_runtime_config(env: InCellEnv, clock: Clock) -> InCellRuntimeConfig:
    """Validate and convert `env` into everything the in-Cell Warden entry point needs.

    Args:
        env: The result of `hivemind.manifest.env.read_in_cell_env`.
        clock: Source of this process's own freshly minted node id and Warden id.

    Returns:
        A fully validated InCellRuntimeConfig.

    Raises:
        ConfigurationError: A required variable is missing, an id or key is malformed, or
            `HIVEMIND_PROVIDERS`/`HIVEMIND_SLOTS` is set but not valid JSON in the expected shape.
    """
    cell_id = CellId(_parse_required(env.cell_id, IdKind.CELL, "HIVEMIND_CELL_ID"))
    hive_id = HiveId(_parse_required(env.hive_id, IdKind.HIVE, "HIVEMIND_HIVE_ID"))
    queen_node_id = NodeId(
        _parse_required(env.queen_node_id, IdKind.NODE, "HIVEMIND_QUEEN_NODE_ID")
    )
    # The tier is the Queen's provisioning decision, read from the bootstrap and held to the
    # link it came with (module docstring), never chosen by this entry point itself.
    comb_shield = _parse_comb_shield(env.comb_shield)
    queen_waggle_url = _require_queen_waggle_url(env.queen_waggle_url)
    _check_link_matches_tier(comb_shield, queen_waggle_url, env.socks_proxy_url)
    return InCellRuntimeConfig(
        queen_waggle_url=queen_waggle_url,
        hive_id=hive_id,
        queen_node_id=queen_node_id,
        node_id=new_node_id(clock),
        warden_id=new_warden_id(clock),
        signer=Ed25519Signer(_signing_key_bytes(env)),
        verifier=Ed25519Verifier({queen_node_id: _verify_key_bytes(env)}),
        spawn_config=_spawn_config(env, cell_id, comb_shield),
        heartbeat_interval_s=DEFAULT_HEARTBEAT_INTERVAL_S,
        socks_proxy_url=env.socks_proxy_url,
        providers=_parse_providers(env.providers_json),
        slots=_parse_slots(env.slots_json),
        llm_offline=env.llm_offline or False,
        environ=env.environ,
    )


def _spawn_config(
    env: InCellEnv, cell_id: CellId, comb_shield: CombShieldLevel
) -> InCellSpawnConfig:
    """Probe this Cell and describe it at the tier its bootstrap named."""
    # HIVEMIND_SCRATCH_ROOT overrides the image's own path: a test or an in-process Cell on a host
    # that cannot create /var/lib/hivemind (Linux CI) sets it; a real container never needs to.
    scratch_root = env.scratch_root if env.scratch_root is not None else DEFAULT_SCRATCH_ROOT
    probed = probe_host(_probe_config(scratch_root))
    return InCellSpawnConfig(
        cell_id=cell_id,
        capabilities=probed.capabilities,
        capacity=probed.capacity,
        comb_shield=comb_shield,
        scratch_root=scratch_root,
    )


def _require(value: str | None, var_name: str) -> str:
    """Return `value`, or raise ConfigurationError naming `var_name` when it is unset."""
    if value is None:
        raise ConfigurationError(f"{var_name} must be set for the in-Cell Warden to start.")
    return value


def _require_queen_waggle_url(value: str | None) -> str:
    """Return `value`, validated as a URI this Cell may dial out to the Queen on.

    `allow_virtual_cell_gateway_host=True` (waggle.uris): a Virtual Cell reaches the Hive Stand
    through a host-gateway alias (`host.docker.internal`) or a private address, never loopback --
    loopback inside the container is the container itself (ADR-0027). Validating with the
    ordinary (loopback-only) rule here would reject exactly the address a Docker/QEMU backend is
    expected to hand this Cell, before the real dial ever gets a chance to fail more usefully.

    Raises:
        ConfigurationError: `value` is unset, or fails `check_waggle_uri`'s widened rule.
    """
    raw = _require(value, "HIVEMIND_QUEEN_WAGGLE_URL")
    try:
        return check_waggle_uri(raw, allow_virtual_cell_gateway_host=True)
    except ValueError as exc:
        raise ConfigurationError(f"HIVEMIND_QUEEN_WAGGLE_URL={raw!r} is invalid: {exc}") from exc


def _parse_comb_shield(value: str | None) -> CombShieldLevel:
    """Return the tier `HIVEMIND_COMB_SHIELD` names; MEADOW when it is unset.

    Raises:
        ConfigurationError: The value names no tier, or names PROPOLIS, which this Cell cannot
            attest (module docstring).
    """
    if value is None:
        return CombShieldLevel.MEADOW  # A bootstrap from before the tier was written.
    try:
        tier = CombShieldLevel[value.strip().upper()]
    except KeyError as exc:
        raise ConfigurationError(
            f"HIVEMIND_COMB_SHIELD={value!r} is not a Comb Shield tier (MEADOW or NIGHT_VEIL)."
        ) from exc
    if tier is CombShieldLevel.PROPOLIS:
        raise ConfigurationError(
            "HIVEMIND_COMB_SHIELD=PROPOLIS: no in-Cell attestation exists for PROPOLIS yet, and a "
            "Cell that cannot attest its tier must not announce it."
        )
    return tier


def _check_link_matches_tier(
    tier: CombShieldLevel, queen_waggle_url: str, socks_proxy_url: str | None
) -> None:
    """Refuse a link that does not fit the tier: Night Veil dials an onion through Tor, only.

    Raises:
        ConfigurationError: The proxy is not a loopback socks5h/socks4a one; a NIGHT_VEIL Cell's
            Queen URL is not a v3 onion service or it names no proxy; or another tier's Queen URL
            is an onion service.
    """
    try:
        proxy = SocksProxy.parse(socks_proxy_url) if socks_proxy_url is not None else None
    except ValueError as exc:
        raise ConfigurationError(f"HIVEMIND_SOCKS_PROXY_URL is invalid: {exc}") from exc
    onion = is_onion_service_host(urlsplit(queen_waggle_url).hostname or "")
    if tier is CombShieldLevel.NIGHT_VEIL and (not onion or proxy is None):
        raise ConfigurationError(
            "A NIGHT_VEIL Cell dials the Queen only at her v3 onion service, through the Tor "
            f"SOCKS proxy on its own loopback; got HIVEMIND_QUEEN_WAGGLE_URL={queen_waggle_url!r} "
            f"and HIVEMIND_SOCKS_PROXY_URL={socks_proxy_url!r}."
        )
    if tier is not CombShieldLevel.NIGHT_VEIL and onion:
        raise ConfigurationError(
            f"HIVEMIND_QUEEN_WAGGLE_URL names an onion service, which only a NIGHT_VEIL Cell "
            f"dials, but HIVEMIND_COMB_SHIELD is {tier.value}."
        )


def rewrite_loopback_base_url(base_url: str, gateway_host: str) -> str:
    """Rewrite a loopback LLM provider base URL to `gateway_host`, port and path unchanged.

    A `ModelSlot` binding a grant names (roadmap step 8.6/8.10) may point at a provider server
    the Hive Stand's own manifest addresses as loopback (`http://127.0.0.1:1234/v1`, a local LM
    Studio, say). Loopback inside a Virtual Cell is the Cell itself, not the Hive Stand, so that
    address is never reachable from in here -- exactly the same reason `HIVEMIND_QUEEN_WAGGLE_URL`
    is a gateway address rather than loopback (ADR-0027). A container reaches the Hive Stand
    through the same host-gateway alias its Waggle control link already dials
    (`HIVEMIND_QUEEN_WAGGLE_URL`'s own host, `waggle.uris.VIRTUAL_CELL_GATEWAY_HOST_NAMES`), and
    Docker/QEMU host-gateway networking preserves the host's own listening port, so only the host
    component changes; the scheme, port, path and query are carried over unchanged.

    TODO(8.x): this is the smallest correct thing for today's wire shape, not the final one.
    `waggle.messages.forage.values.AllowedBinding`/`SourceRef` name a slot's provider and model
    but never a base URL (a grant only ever crosses process boundaries as a manifest provider
    *name*, resolved locally at each end), so nothing yet calls this helper with a real grant's
    own base URL -- see `hivemind.cli.in_cell.providers` for where a future hosting-plan shape
    that carries a Cell-relative URL would wire it in.

    Args:
        base_url: A provider's configured base URL, as `[llm.providers.<name>].base_url` names it.
        gateway_host: The host this Cell reaches the Hive Stand through, e.g.
            `HIVEMIND_QUEEN_WAGGLE_URL`'s own host (`host.docker.internal`, a QEMU SLIRP gateway,
            or an operator's own private address).

    Returns:
        `base_url` unchanged when its host is not loopback (nothing to rewrite: it already names
        something other than "this same machine"); otherwise `base_url` with only its host
        replaced by `gateway_host`.
    """
    parts = urlsplit(base_url)
    if parts.hostname is None or not is_loopback_host(parts.hostname):
        return base_url  # Not a loopback address: already a real, Cell-reachable host (or empty).
    # Replace only the host, keeping any port the original URL named (a loopback provider server
    # binds the same port on the gateway alias, per the module docstring above); userinfo is never
    # present on a manifest provider URL, so it is not carried over.
    port_suffix = f":{parts.port}" if parts.port is not None else ""
    new_netloc = f"{gateway_host}{port_suffix}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


def gateway_host(queen_waggle_url: str) -> str:
    """Return the host this Cell reaches the Queen through, for `rewrite_loopback_base_url`.

    Args:
        queen_waggle_url: `InCellRuntimeConfig.queen_waggle_url`, already validated by
            `build_runtime_config` (`_require_queen_waggle_url`), so it always parses.

    Returns:
        The URL's own host, e.g. `"host.docker.internal"`.
    """
    hostname = urlsplit(queen_waggle_url).hostname
    # build_runtime_config already validated this URL through check_waggle_uri, which requires a
    # parseable host; this branch only guards a caller that skipped that step (e.g. a future test).
    if hostname is None:
        raise ConfigurationError(f"{queen_waggle_url!r} has no host to use as a gateway host.")
    return hostname


def _parse_required(value: str | None, kind: IdKind, var_name: str) -> str:
    """Require `value` to be set and a well-formed id of `kind`."""
    raw = _require(value, var_name)
    try:
        return parse_id(raw, kind)
    except InvalidIdError as exc:
        raise ConfigurationError(
            f"{var_name}={raw!r} is not a valid {kind.name} id: {exc}"
        ) from exc


def _signing_key_bytes(env: InCellEnv) -> bytes:
    """Read this Cell's own private key, preferring a mounted file over the inline variable."""
    if env.signing_key_file is not None:
        hex_text = env.signing_key_file.read_text(encoding="ascii").strip()
    elif env.signing_key_hex is not None:
        hex_text = env.signing_key_hex.get_secret_value().strip()
    else:
        raise ConfigurationError(
            "HIVEMIND_CELL_SIGNING_KEY or HIVEMIND_CELL_SIGNING_KEY_FILE must be set: signing is "
            "mandatory across a machine boundary (roadmap step 1.7)."
        )
    return _decode_hex(hex_text, "HIVEMIND_CELL_SIGNING_KEY")


def _verify_key_bytes(env: InCellEnv) -> bytes:
    """Read the Queen's own public key, preferring a mounted file over the inline variable."""
    if env.queen_verify_key_file is not None:
        hex_text = env.queen_verify_key_file.read_text(encoding="ascii").strip()
    elif env.queen_verify_key_hex is not None:
        hex_text = env.queen_verify_key_hex.strip()
    else:
        raise ConfigurationError(
            "HIVEMIND_QUEEN_VERIFY_KEY or HIVEMIND_QUEEN_VERIFY_KEY_FILE must be set: signing is "
            "mandatory across a machine boundary (roadmap step 1.7)."
        )
    return _decode_hex(hex_text, "HIVEMIND_QUEEN_VERIFY_KEY")


def _decode_hex(hex_text: str, var_name: str) -> bytes:
    """Decode `hex_text` or raise ConfigurationError naming `var_name` (never the key itself)."""
    try:
        return bytes.fromhex(hex_text)
    except ValueError as exc:
        # Never the raw text in the message (codingrules section 15: secrets never in logs).
        raise ConfigurationError(f"{var_name} is not valid hex-encoded key material.") from exc


def _parse_providers(raw: str | None) -> dict[str, ProviderConfig]:
    """Parse `HIVEMIND_PROVIDERS` into ProviderRegistry-facing ProviderConfigs, keyed by name.

    Args:
        raw: The variable's raw JSON text (`hivemind.hive.backends.provider_table.
            render_providers_json`'s own output), or None when this Cell has no provider table.

    Returns:
        An empty dict when `raw` is None (`hivemind.cli.in_cell.providers.
        build_in_cell_provider_registry`'s own cue to keep building today's fake); otherwise one
        ProviderConfig per row, in the JSON array's own order.

    Raises:
        ConfigurationError: `raw` is not a JSON array of well-formed provider rows.
    """
    if raw is None:
        return {}
    providers: dict[str, ProviderConfig] = {}
    for row in _load_json_array(raw, "HIVEMIND_PROVIDERS"):
        if not isinstance(row, dict):
            raise ConfigurationError("HIVEMIND_PROVIDERS contains a row that is not a JSON object.")
        kind = row.get("kind")
        if kind not in _VALID_PROVIDER_KINDS:
            raise ConfigurationError(
                f"HIVEMIND_PROVIDERS names an unknown provider kind: {kind!r}."
            )
        try:
            name = str(row["name"])
            api_key_env = row.get("api_key_env") or None
            providers[name] = ProviderConfig(
                kind=cast(ProviderKind, kind),
                base_url=str(row["base_url"]),
                api_key_env=str(api_key_env) if api_key_env else None,
                capability_overrides=dict(row.get("capabilities") or {}),
                default_model=row.get("default_model"),
            )
        except KeyError as exc:
            raise ConfigurationError(f"HIVEMIND_PROVIDERS row is missing {exc}.") from exc
    return providers


def _parse_slots(raw: str | None) -> tuple[SlotBinding, ...]:
    """Parse `HIVEMIND_SLOTS` into forage-side SlotBindings, in the JSON array's own order.

    Args:
        raw: The variable's raw JSON text (`hivemind.hive.backends.provider_table.
            render_slots_json`'s own output), or None when this Cell has no slot table.

    Returns:
        An empty tuple when `raw` is None; otherwise one SlotBinding per row.

    Raises:
        ConfigurationError: `raw` is not a JSON array of well-formed slot rows.
    """
    if raw is None:
        return ()
    slots: list[SlotBinding] = []
    for row in _load_json_array(raw, "HIVEMIND_SLOTS"):
        if not isinstance(row, dict):
            raise ConfigurationError("HIVEMIND_SLOTS contains a row that is not a JSON object.")
        try:
            slots.append(
                SlotBinding(
                    key=row["key"],
                    provider=row["provider"],
                    model=row["model"],
                    fallback=row.get("fallback"),
                    effort=Effort(row["effort"]),
                    max_output_tokens=row.get("max_output_tokens"),
                )
            )
        except (KeyError, ValueError, ValidationError) as exc:
            raise ConfigurationError(f"HIVEMIND_SLOTS row is malformed: {exc}") from exc
    return tuple(slots)


def _load_json_array(raw: str, var_name: str) -> list[object]:
    """Parse `raw` as a JSON array, or raise ConfigurationError naming `var_name`."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{var_name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ConfigurationError(f"{var_name} must be a JSON array.")
    return parsed


def _probe_config(scratch_root: Path) -> HiveStandConfig:
    """Build a throwaway HiveStandConfig, only to reuse probe_host's own stdlib-only probing.

    `HiveStandConfig` is a Real Cell concept (its own module docstring); nothing here reads its
    `access_level`/`comb_shield` back -- `InCellSpawnConfig` above sets those itself for a Virtual
    Cell. Only `probe_host`'s `capabilities`/`capacity` output is used; `scratch_root` is the one
    the Cell will really use, so the probe measures (and may create) the same directory.
    """
    return HiveStandConfig(
        enabled=True,
        scratch_root=scratch_root,
        scratch_quota_mb=1,  # Unused: InCellSession carries no scratch-quota watchdog (5.5).
        disk_reserve_mb=0,
        max_sub_bees=None,
        cores=None,
        memory_bytes=None,
        access_level=AccessLevel.FULL,
    )
