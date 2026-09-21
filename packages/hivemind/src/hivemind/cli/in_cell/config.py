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
]


@dataclass(frozen=True, slots=True)
class InCellRuntimeConfig:
    """Everything `hivemind.cli.in_cell.main` needs to build a CellLink and its deps.

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
        queen_waggle_url=_require(env.queen_waggle_url, "HIVEMIND_QUEEN_WAGGLE_URL"),
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
