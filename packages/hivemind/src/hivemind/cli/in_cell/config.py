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

Key invariants:
    - `build_runtime_config` raises `ConfigurationError` naming the missing or malformed variable
      for any of: the Queen's Waggle URL, this Cell's id, the Queen's bee address, the Queen's own
      node id, this Cell's signing key, or the Queen's verify key -- never a bare `KeyError` or a
      cryptography-library exception.
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

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.local.probe import probe_host
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.common.errors import ConfigurationError
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
from waggle.uris import check_waggle_uri, is_loopback_host

# Where this dispatch's Cell keeps every lease's own scratch subdirectory (roadmap step 5.5:
# InCellSpawnSource.lease() creates one under this root per lease). Matches the non-root `hive`
# user's data directory the base-ubuntu image creates (images/base-ubuntu/Dockerfile/README).
DEFAULT_SCRATCH_ROOT = Path("/var/lib/hivemind/scratch")

# How often this Cell sends CellHeartbeat; no manifest exists inside a Virtual Cell image to read
# a configured cadence from (this module's own docstring), so a fixed, generous constant stands in
# until a future step threads one through CellReady/an explicit env var if that proves too coarse.
DEFAULT_HEARTBEAT_INTERVAL_S = 15.0

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
        socks_proxy_url: A SOCKS proxy Waggle should dial through, once Night Veil wires it up
            (roadmap step 5.7a); carried unchanged, never acted on here.
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


def build_runtime_config(env: InCellEnv, clock: Clock) -> InCellRuntimeConfig:
    """Validate and convert `env` into everything the in-Cell Warden entry point needs.

    Args:
        env: The result of `hivemind.manifest.env.read_in_cell_env`.
        clock: Source of this process's own freshly minted node id and Warden id.

    Returns:
        A fully validated InCellRuntimeConfig.

    Raises:
        ConfigurationError: A required variable is missing, or an id or key is malformed.
    """
    cell_id = CellId(_parse_required(env.cell_id, IdKind.CELL, "HIVEMIND_CELL_ID"))
    hive_id = HiveId(_parse_required(env.hive_id, IdKind.HIVE, "HIVEMIND_HIVE_ID"))
    queen_node_id = NodeId(
        _parse_required(env.queen_node_id, IdKind.NODE, "HIVEMIND_QUEEN_NODE_ID")
    )
    signer = Ed25519Signer(_signing_key_bytes(env))
    verifier = Ed25519Verifier({queen_node_id: _verify_key_bytes(env)})
    probed = probe_host(_probe_config())
    spawn_config = InCellSpawnConfig(
        cell_id=cell_id,
        capabilities=probed.capabilities,
        capacity=probed.capacity,
        # New Virtual Cells default to MEADOW (codingrules section 8.7); a higher tier is a Queen
        # provisioning decision (roadmap step 5.7), not something this entry point chooses itself.
        comb_shield=CombShieldLevel.MEADOW,
        scratch_root=DEFAULT_SCRATCH_ROOT,
    )
    return InCellRuntimeConfig(
        queen_waggle_url=_require_queen_waggle_url(env.queen_waggle_url),
        hive_id=hive_id,
        queen_node_id=queen_node_id,
        node_id=new_node_id(clock),
        warden_id=new_warden_id(clock),
        signer=signer,
        verifier=verifier,
        spawn_config=spawn_config,
        heartbeat_interval_s=DEFAULT_HEARTBEAT_INTERVAL_S,
        socks_proxy_url=env.socks_proxy_url,
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


def _probe_config() -> HiveStandConfig:
    """Build a throwaway HiveStandConfig, only to reuse probe_host's own stdlib-only probing.

    `HiveStandConfig` is a Real Cell concept (its own module docstring); nothing here reads its
    `access_level`/`comb_shield` back -- `InCellSpawnConfig` above sets those itself for a Virtual
    Cell. Only `probe_host`'s `capabilities`/`capacity` output is used.
    """
    return HiveStandConfig(
        enabled=True,
        scratch_root=DEFAULT_SCRATCH_ROOT,
        scratch_quota_mb=1,  # Unused: InCellSession carries no scratch-quota watchdog (5.5).
        disk_reserve_mb=0,
        max_sub_bees=None,
        cores=None,
        memory_bytes=None,
        access_level=AccessLevel.FULL,
    )
