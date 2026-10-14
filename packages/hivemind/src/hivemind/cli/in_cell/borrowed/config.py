"""Turn the environment a Pollen Packet gives a colonized device's Warden into BorrowedConfig.

`hivemind.manifest.env.read_in_cell_env` only extracts what the process was started with. This
module decides what a Warden that borrows the machine it runs on cannot start without and parses
it: the ids it keeps across the move from the Hive Stand (its Warden id, the device's node id and
Cell id), the device's own key (the packet's key file, which holds the raw 32-byte seed and not hex
text), the level the Queen granted the device, the swarm listener's URL and certificate pin, the
directory its stores live in, and the device manifest. It reuses `build_runtime_config`, which
every Virtual Cell's Warden is validated by, for everything the two share (the Queen's ids and key,
the URL, the tier), then replaces what is the device's alone: the ids, the model tables, which come
from the device manifest and not from a bootstrap, and a source configuration for the machine
(`HiveStandConfig` named for a Swarm device, the same lease machinery the Hive Stand uses).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.borrowed.run`. Calls into `hivemind.cell` (the tiers, `HiveStandConfig`),
    `hivemind.cli.in_cell.config`, `hivemind.cli.in_cell.borrowed.llm`, `hivemind.manifest`
    (InCellEnv, the device manifest) and waggle only.

Key invariants:
    - Every value a device needs is required here; a missing or malformed one is a
      `ConfigurationError` naming the variable, never a `KeyError` and never a message that
      carries key material.
    - The node key is read as the packet writes it (raw 32 bytes), and is never put in a message.
    - A device holds the MEADOW tier only: PROPOLIS is verified from the Hive Stand, which a Warden
      on the device cannot do, and NIGHT_VEIL is a Virtual Cell's.
    - A `wss://` listener needs the certificate pin the packet holds and a `ws://` one has none, as
      the packet's own state requires; the URL is checked by the strict rule, not the Virtual
      Cell's gateway-host carve-out.
    - The scratch root and every store live under `HIVEMIND_STATE_DIR`, so the machine keeps
      nothing of the Warden's outside the directory the packet made for it.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the move this configures.
    - hivemind.cli.in_cell.config for `build_runtime_config`, the validation this builds on.
    - pollen.bootstrap.warden for the environment the packet sets (contract-tested in
      tests/contracts/test_pollen_warden_env.py).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from hivemind.cell.local import HiveStandConfig
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.cli.in_cell.borrowed.llm import DeviceLlm, device_llm
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.common.errors import ConfigurationError
from hivemind.manifest import InCellEnv, load_device_manifest
from hivemind.manifest.schema.core import DEFAULT_DISK_RESERVE_MB, DEFAULT_SCRATCH_QUOTA_MB
from hivemind.manifest.schema.supervision import DEFAULT_MAX_OFFLINE_S
from waggle.clock import Clock
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, NodeId, WardenId, parse_id
from waggle.transport.tls import is_valid_pin
from waggle.uris import check_waggle_uri

BORROWED = "borrowed"  # HIVEMIND_CELL_LEASE for a Warden that borrows its machine.
OWNED = "owned"  # HIVEMIND_CELL_LEASE for a Virtual Cell's Warden; what an unset variable means.
SOURCE_NAME = (
    "swarm"  # The `source` of a device's Cell: how the Queen tells it from the Hive Stand.
)
CELL_NAME = "swarm-device"  # Its human-readable label; the Queen names the node herself.
SCRATCH_DIR = "scratch"  # Under the state directory: every lease's own scratch subdirectory.
NODE_KEY_BYTES = 32  # An Ed25519 seed, which is what the packet's key file holds.
DEVICE_MAX_OFFLINE_S = DEFAULT_MAX_OFFLINE_S  # `[supervision] max_offline_s`, which a device lacks.

__all__ = [
    "BORROWED",
    "CELL_NAME",
    "DEVICE_MAX_OFFLINE_S",
    "OWNED",
    "SCRATCH_DIR",
    "SOURCE_NAME",
    "BorrowedConfig",
    "build_borrowed_config",
    "is_borrowed",
]


@dataclass(frozen=True, slots=True)
class BorrowedConfig:
    """Everything a colonized device's Warden is composed from.

    Attributes:
        runtime: The Warden's runtime configuration: ids, keys, link URL, model tables, probed
            platform. Shared in shape with a Virtual Cell's, so the same builders consume it.
        cell: The configuration of the machine as a Real Cell the Warden leases, named for the
            device (`SOURCE_NAME`) and carrying the access level and tier the Queen granted.
        state_dir: Where the Warden keeps its SQLite stores, outbox and scratch.
        tls_pin: The swarm listener's certificate pin, for a `wss://` URL; None for `ws://`.
        llm: The device manifest's model tables and the servers it declares.
        max_offline_s: How long the link may stay down before the Warden clusters fully.
    """

    runtime: InCellRuntimeConfig
    cell: HiveStandConfig
    state_dir: Path
    tls_pin: str | None
    llm: DeviceLlm
    max_offline_s: float


def is_borrowed(env: InCellEnv) -> bool:
    """Say whether `env` asks for a Warden that borrows its machine.

    Args:
        env: The result of `hivemind.manifest.env.read_in_cell_env`.

    Returns:
        True for `HIVEMIND_CELL_LEASE=borrowed`; False when it is unset or `owned`.

    Raises:
        ConfigurationError: The variable names a mode this entry point does not know, which would
            otherwise be run as a Virtual Cell's Warden by mistake.
    """
    if env.cell_lease is None or env.cell_lease == OWNED:
        return False
    if env.cell_lease == BORROWED:
        return True
    raise ConfigurationError(
        f"HIVEMIND_CELL_LEASE={env.cell_lease!r} is not {OWNED!r} or {BORROWED!r}."
    )


def build_borrowed_config(env: InCellEnv, clock: Clock) -> BorrowedConfig:
    """Validate and convert `env` into everything a colonized device's Warden is built from.

    Args:
        env: The result of `read_in_cell_env` for a process started with `HIVEMIND_CELL_LEASE`
            set to `borrowed`.
        clock: Source of the ids `build_runtime_config` mints before this replaces them.

    Returns:
        A fully validated BorrowedConfig.

    Raises:
        ConfigurationError: A required variable is missing or malformed, the tier or the listener
            does not suit a device, the node key file is unreadable, or the device manifest is
            missing or invalid (`ManifestError` is one).
    """
    state_dir = _required_path(env.state_dir, "HIVEMIND_STATE_DIR")
    manifest = load_device_manifest(_required_path(env.device_manifest, "HIVEMIND_DEVICE_MANIFEST"))
    llm = device_llm(manifest.llm, manifest.forage.map)
    seed = _node_key_seed(_required_path(env.signing_key_file, "HIVEMIND_CELL_SIGNING_KEY_FILE"))
    # The shared validation reads the key as hex text and the scratch root from the environment, so
    # it is handed the device's own in those forms; it never sees the file the packet wrote.
    scratch_root = state_dir / SCRATCH_DIR
    shared = env.model_copy(
        update={
            "signing_key_file": None,
            "signing_key_hex": SecretStr(seed.hex()),
            "scratch_root": scratch_root,
        }
    )
    base = build_runtime_config(shared, clock)
    _check_listener(base.queen_waggle_url, env.tls_pin)
    _check_tier(base.spawn_config.comb_shield)
    return BorrowedConfig(
        runtime=_device_runtime(base, env, llm),
        cell=_device_cell(base, env, llm),
        state_dir=state_dir,
        tls_pin=env.tls_pin,
        llm=llm,
        max_offline_s=DEVICE_MAX_OFFLINE_S,
    )


def _device_runtime(
    base: InCellRuntimeConfig, env: InCellEnv, llm: DeviceLlm
) -> InCellRuntimeConfig:
    """Replace what a bootstrap minted with what the device keeps: its ids and its model tables."""
    return dataclasses.replace(
        base,
        node_id=NodeId(_parse_id(env.node_id, IdKind.NODE, "HIVEMIND_NODE_ID")),
        warden_id=WardenId(_parse_id(env.warden_id, IdKind.WARDEN, "HIVEMIND_WARDEN_ID")),
        providers=llm.providers,
        provider_seats=llm.seats,
        provider_limits=llm.limits,
        slots=llm.slots,
        llm_offline=llm.offline,
    )


def _device_cell(base: InCellRuntimeConfig, env: InCellEnv, llm: DeviceLlm) -> HiveStandConfig:
    """Describe the machine as the Real Cell the Warden leases: the Queen's id, source `swarm`."""
    spawn = base.spawn_config
    return HiveStandConfig(
        enabled=True,
        scratch_root=spawn.scratch_root,
        scratch_quota_mb=DEFAULT_SCRATCH_QUOTA_MB,
        disk_reserve_mb=DEFAULT_DISK_RESERVE_MB,
        max_sub_bees=None,
        cores=None,
        memory_bytes=None,
        access_level=_access_level(env.access_level),
        comb_shield=spawn.comb_shield,
        cell_id=spawn.cell_id,
        source_name=SOURCE_NAME,
        cell_name=CELL_NAME,
        # Whether the machine could host a model server: only if the operator declared one.
        can_host_model=bool(llm.servers),
    )


def _check_tier(tier: CombShieldLevel) -> None:
    """Refuse every tier but MEADOW (module docstring's Key invariants)."""
    if tier is not CombShieldLevel.MEADOW:
        raise ConfigurationError(
            f"A Swarm device's Warden holds the MEADOW tier only, not {tier.value}: PROPOLIS is "
            "verified from the Hive Stand and NIGHT_VEIL is a Virtual Cell's."
        )


def _required_path(value: Path | None, var_name: str) -> Path:
    """Return `value`, or raise ConfigurationError naming `var_name` when it is unset."""
    if value is None:
        raise ConfigurationError(f"{var_name} must be set for a Warden that borrows its machine.")
    return value


def _parse_id(value: str | None, kind: IdKind, var_name: str) -> str:
    """Require `value` to be set and a well-formed id of `kind`."""
    if value is None:
        raise ConfigurationError(f"{var_name} must be set for a Warden that borrows its machine.")
    try:
        return parse_id(value, kind)
    except InvalidIdError as exc:
        raise ConfigurationError(f"{var_name}={value!r} is not a valid {kind.name} id.") from exc


def _access_level(value: str | None) -> AccessLevel:
    """Return the level `HIVEMIND_ACCESS_LEVEL` names; a device has one, so it is required."""
    if value is None:
        raise ConfigurationError("HIVEMIND_ACCESS_LEVEL must be set for a Warden that borrows.")
    try:
        return AccessLevel[value.strip().upper()]
    except KeyError as exc:
        names = ", ".join(level.name for level in AccessLevel)
        raise ConfigurationError(
            f"HIVEMIND_ACCESS_LEVEL={value!r} is not an access level ({names})."
        ) from exc


def _node_key_seed(path: Path) -> bytes:
    """Read the device's node key as the packet stores it: the raw seed, nothing else.

    Raises:
        ConfigurationError: The file cannot be read or is not a 32-byte seed. The message names
            the variable and never a byte of the file.
    """
    try:
        seed = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError("HIVEMIND_CELL_SIGNING_KEY_FILE cannot be read.") from exc
    if len(seed) != NODE_KEY_BYTES:
        raise ConfigurationError(
            f"HIVEMIND_CELL_SIGNING_KEY_FILE does not hold a {NODE_KEY_BYTES}-byte Ed25519 seed."
        )
    return seed


def _check_listener(queen_waggle_url: str, tls_pin: str | None) -> None:
    """Hold the link rules of the packet's own state: a strict URL, and a pin exactly for wss://.

    Raises:
        ConfigurationError: The URL is not one a device may dial, or the pin does not fit it.
    """
    try:
        # Strict: the Virtual Cell's gateway-host carve-out (build_runtime_config's) is not a
        # device's, whose listener is a wss:// address anywhere or ws:// on loopback.
        check_waggle_uri(queen_waggle_url)
    except ValueError as exc:
        raise ConfigurationError(
            f"HIVEMIND_QUEEN_WAGGLE_URL={queen_waggle_url!r} is invalid for a device: {exc}"
        ) from exc
    if queen_waggle_url.startswith("wss://") != (tls_pin is not None):
        raise ConfigurationError(
            "HIVEMIND_TLS_PIN is required exactly for a wss:// listener: a pinned certificate "
            "is what a device trusts, and a ws:// loopback address has none."
        )
    if tls_pin is not None and not is_valid_pin(tls_pin):
        raise ConfigurationError("HIVEMIND_TLS_PIN is not a SHA-256 fingerprint in lowercase hex.")
