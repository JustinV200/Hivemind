"""Persist what the Pollen Packet must remember: its key, its PacketState and the data layout.

The Pollen Packet (the thin gateway program on a device the Hive borrows) survives restarts, so
everything it was told at enrolment and since lives in its data directory: the node's Ed25519 key
in a file of its own (mode 0600, never sent anywhere), and ``PacketState``, one JSON document
holding the device's identity, the Queen's (the Hive's central orchestrator) address and pins, the
Cell's (a unit of compute) grants and the highest QueenMoved relocation notice honoured. Every
write is write-new, flush, replace-old, so a crash leaves the old document or the new one and
never a torn one (gateway-contract section 4); ``write_atomic`` is the one routine that does it
and the lease table and the status file reuse it. ``StateStore`` is the one owner of the mutable
copy: an update is validated, written to disk first and only then becomes the value tasks read.
``DataLayout`` names every file and directory under the data directory in one place, the folder the
Hive runtime and its Warden live in included. The state also says which rung of the runtime ladder
the device is on and, above the first, records the Warden that lives on it (``WardenRecord``: its
pid and creation time, so a restarted packet adopts it and a recycled pid is never mistaken for
it); the level and the record are validated as a pair because the dead-man switch stands down
exactly while a Warden lives on the device.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Written by the enrolment
    ceremony and by the gateway when a swarm.node_update or a QueenMoved arrives; read by every
    other pollen module; calls into waggle (signing, message value types) and the standard
    library only.

Key invariants:
    - A file is replaced atomically, with mode 0600, and flushed before the old one goes.
    - The key file holds the raw 32-byte seed and nothing else; no state document, log line or
      error message ever carries it.
    - ``StateStore.current`` only ever changes to a value that validated and reached the disk.
    - A Warden is recorded exactly when the level is above GATEWAY, so the two are always written
      in one update.

See Also:
    - docs/waggle/gateway-contract.md section 4 for what a gateway persists.
    - docs/waggle/enrolment.md section 7.3 for what the accept leaves on the device.
    - pollen.agent.moved for the pure functions that derive new states from a QueenMoved.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pollen.errors import NotEnrolledError, StateError
from waggle.messages.base import (
    CellIdField,
    DeviceIdField,
    HiveIdField,
    LeaseIdField,
    NodeIdField,
    UtcDatetime,
    WardenIdField,
)
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm import NodeKey, RuntimeLevel
from waggle.messages.swarm.enrolment import (
    MAX_CAPABILITY_CHARS,
    MAX_GRANTED_CAPABILITIES,
    MAX_TRUSTED_NODES,
    PUBLIC_KEY_HEX_PATTERN,
)
from waggle.signing import Ed25519Signer
from waggle.uris import check_waggle_uri

STATE_SCHEMA_VERSION = 1  # Bumped when the document's shape changes incompatibly.
PRIVATE_FILE_MODE = 0o600  # Key, state and tables: readable by the packet's own account only.
PRIVATE_DIR_MODE = 0o700  # The data directory and every lease's scratch directory.
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"  # A certificate pin: lowercase hex of a SHA-256 digest.

__all__ = [
    "PRIVATE_DIR_MODE",
    "PRIVATE_FILE_MODE",
    "DataLayout",
    "InstallSettings",
    "PacketState",
    "QueenMove",
    "StateStore",
    "WardenRecord",
    "create_key",
    "load_signer",
    "load_state",
    "save_state",
    "write_atomic",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # The same stance as every boundary model.
_KeyHex = Annotated[str, Field(pattern=PUBLIC_KEY_HEX_PATTERN)]  # A raw Ed25519 public key, hex.


@dataclass(frozen=True, slots=True)
class DataLayout:
    """Where every file the packet keeps lives, under one data directory."""

    root: Path  # The data directory: from --data-dir, POLLEN_DATA_DIR or the platform default.

    @property
    def state(self) -> Path:
        """The PacketState document."""
        return self.root / "state.json"

    @property
    def key(self) -> Path:
        """The node's private key file (mode 0600, never sent anywhere)."""
        return self.root / "node.ed25519"

    @property
    def outbox(self) -> Path:
        """The waggle outbox log of frames that must reach the Queen after a reconnect."""
        return self.root / "outbox.jsonl"

    @property
    def lease_table(self) -> Path:
        """The persisted lease table."""
        return self.root / "lease-table.json"

    @property
    def status(self) -> Path:
        """The diagnostic status document ``pollen status`` reads."""
        return self.root / "status.json"

    @property
    def runtime(self) -> Path:
        """Where the Hive runtime lives: the venv, the Warden's state and its logs."""
        return self.root / "runtime"

    @property
    def venv(self) -> Path:
        """The virtual environment ``pollen.bootstrap`` installs the pinned runtime into."""
        return self.runtime / "venv"

    @property
    def warden_state(self) -> Path:
        """The on-device Warden's state directory: its stores, outbox and scratch."""
        return self.runtime / "state"

    @property
    def warden_log(self) -> Path:
        """Where the on-device Warden's output goes; the previous run's is kept beside it."""
        return self.runtime / "warden.log"

    @property
    def prepare_log(self) -> Path:
        """Where the output of the last runtime installation goes."""
        return self.runtime / "prepare.log"

    def scratch(self, lease_id: LeaseIdField) -> Path:
        """Return the scratch directory of ``lease_id``: ``<data_dir>/leases/<lease_id>``."""
        return self.root / "leases" / lease_id

    def backups(self, lease_id: LeaseIdField) -> Path:
        """Return where the prior bytes of paths a lease changed outside scratch are kept."""
        return self.root / "restore" / lease_id

    def ensure(self) -> None:
        """Create the data directory (owner-only) if it does not exist yet."""
        self.root.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# The document
# ──────────────────────────────────────────────────────────────────────────────


class QueenMove(BaseModel):
    """A QueenMoved the packet accepted and still honours both ends of (its grace window)."""

    model_config = _MODEL_CONFIG

    sequence: int = Field(ge=1, description="The notice's sequence, the highest accepted so far.")
    previous_url: str = Field(description="The address the packet dialled before the notice.")
    previous_node_id: NodeIdField = Field(description="The Hive Stand node that stepped down.")
    previous_key_hex: _KeyHex = Field(description="The key of that node, dropped at grace_until.")
    new_url: str = Field(description="The new Queen's address, dialled from effective_at.")
    new_node_id: NodeIdField = Field(description="The new Hive Stand's node.")
    new_key_hex: _KeyHex = Field(description="The new node's key, the Hive key from grace_until.")
    effective_at: UtcDatetime = Field(
        description="When the packet starts dialling the new address."
    )
    grace_until: UtcDatetime = Field(description="Until when either address and key are honoured.")


class InstallSettings(BaseModel):
    """What ``pollen install`` records for the colonization step (wave 3) to read later."""

    model_config = _MODEL_CONFIG

    runtime_wheelhouse: str | None = Field(
        default=None, description="A local directory of wheels the hivemind runtime installs from."
    )
    runtime_index: str | None = Field(
        default=None, description="A package index URL the hivemind runtime installs from."
    )
    warden_manifest: str | None = Field(
        default=None, description="The device's local manifest for an on-device Warden."
    )
    runtime_python: str | None = Field(
        default=None, description="A Python 3.12 or newer the runtime's environment is made with."
    )

    @model_validator(mode="after")
    def _one_runtime_source(self) -> InstallSettings:
        """Refuse a wheelhouse and an index together."""
        # Two sources would leave the installer to pick one; the operator names exactly one.
        if self.runtime_wheelhouse is not None and self.runtime_index is not None:
            raise ValueError("Name a runtime wheelhouse or a runtime index, not both.")
        return self


class WardenRecord(BaseModel):
    """The on-device Warden the packet started, so a restarted packet finds it again."""

    model_config = _MODEL_CONFIG

    pid: int = Field(ge=1, description="The Warden's process id, also its process-group id.")
    created: float = Field(
        ge=0, description="The process's creation time, so a recycled pid is not taken for it."
    )
    runtime_version: str = Field(min_length=1, description="The hivemind version that is running.")
    started_at: UtcDatetime = Field(description="When the packet started it.")


class PacketState(BaseModel):
    """Everything the packet persists in state.json (gateway-contract section 4)."""

    model_config = _MODEL_CONFIG

    schema_version: int = Field(
        default=STATE_SCHEMA_VERSION, description="The document's shape version."
    )
    device_id: DeviceIdField = Field(description="This device's bee address, minted at enrolment.")
    node_id: NodeIdField = Field(description="The node whose key (node.ed25519) signs every frame.")
    queen_url: str = Field(
        description="The only address the packet dials (ws:// only on loopback)."
    )
    tls_pin_sha256: Annotated[str, Field(pattern=SHA256_HEX_PATTERN)] | None = Field(
        description="SHA-256 of the listener's certificate pinned from the invite; None for ws://."
    )
    hive_id: HiveIdField = Field(description="The Queen's bee address, recipient of what goes up.")
    hive_node_id: NodeIdField = Field(description="The Hive Stand node pinned from the invite.")
    hive_key_hex: _KeyHex = Field(description="The Hive key: the pinned node's public key, hex.")
    trusted_nodes: tuple[NodeKey, ...] = Field(
        min_length=1,
        max_length=MAX_TRUSTED_NODES,
        description="Node ids and keys whose signatures the packet accepts; seeds its verifier.",
    )
    cell_id: CellIdField = Field(description="The Real Cell this device is.")
    warden_id: WardenIdField = Field(description="The Cell's Warden; the other admitted sender.")
    level: RuntimeLevel = Field(
        default=RuntimeLevel.GATEWAY, description="The rung of the runtime ladder the device is on."
    )
    access_level: AccessLevel = Field(description="How much of the device the Hive may touch.")
    comb_shield: CombShieldLevel = Field(description="The Cell's security tier, never NIGHT_VEIL.")
    granted_capabilities: tuple[Annotated[str, Field(max_length=MAX_CAPABILITY_CHARS)], ...] = (
        Field(
            max_length=MAX_GRANTED_CAPABILITIES,
            description="Capability strings checked locally on every request.",
        )
    )
    offline_limit_s: float = Field(gt=0, description="The dead-man limit for a Level 0 device.")
    heartbeat_interval_s: float = Field(gt=0, description="Seconds between device heartbeats.")
    moved_sequence: int = Field(
        default=0, ge=0, description="The highest QueenMoved sequence accepted; 0 means none."
    )
    queen_move: QueenMove | None = Field(
        default=None, description="An accepted relocation still inside its grace window."
    )
    last_heard_at: UtcDatetime | None = Field(
        default=None, description="When the Queen was last heard from, persisted coarsely."
    )
    enrolled_at: UtcDatetime = Field(description="When the accept was persisted.")
    install: InstallSettings = Field(
        default_factory=InstallSettings, description="What pollen install recorded."
    )
    warden: WardenRecord | None = Field(
        default=None, description="The on-device Warden, set exactly when level is above GATEWAY."
    )

    @model_validator(mode="after")
    def _link_rules(self) -> PacketState:
        """Hold the link rules: a valid address, a pin exactly for wss://, no Night Veil."""
        # A state file edited by hand must not talk the packet into a plaintext or unpinned
        # link, or into a tier a Real Cell cannot hold (gateway-contract sections 5 and 4).
        check_waggle_uri(self.queen_url)
        if self.queen_url.startswith("wss://") != (self.tls_pin_sha256 is not None):
            raise ValueError("A wss:// address needs a certificate pin and a ws:// one has none.")
        if self.comb_shield is CombShieldLevel.NIGHT_VEIL:
            raise ValueError("A Real Cell never holds the NIGHT_VEIL tier.")
        # The dead-man switch stands down exactly while a Warden lives on the device, so the
        # level and the record that says one does may never disagree (ADR-0052).
        if (self.level is RuntimeLevel.GATEWAY) != (self.warden is None):
            raise ValueError("A Warden is recorded exactly when the level is above GATEWAY.")
        return self


# ──────────────────────────────────────────────────────────────────────────────
# Files
# ──────────────────────────────────────────────────────────────────────────────


def write_atomic(path: Path, data: bytes, mode: int = PRIVATE_FILE_MODE) -> None:
    """Replace ``path`` with ``data`` so a crash leaves the old file or the new, never a torn one.

    Args:
        path: The file to write; its directory is created if it is missing.
        data: The new content.
        mode: Permission bits of the new file; owner-only by default.

    Raises:
        OSError: The file could not be written, flushed or replaced.
    """
    path.parent.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    # Write-new, flush to disk, then rename over the old file: the rename is the only moment the
    # visible content changes, and it is atomic on every filesystem the packet runs on.
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    """Flush the directory entry so the rename itself survives a power cut (best effort)."""
    # Windows cannot open a directory this way and some filesystems refuse fsync on one; the
    # rename has already happened, so failing here costs durability of the name, nothing else.
    with contextlib.suppress(OSError):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def create_key(path: Path, *, replace: bool = False) -> Ed25519Signer:
    """Mint the node's Ed25519 key and persist it (mode 0600) before anything is dialled.

    Args:
        path: Where the key file goes (``DataLayout.key``).
        replace: Overwrite an existing key; only a re-enrolment with a new invite does.

    Returns:
        The signer for the new key.

    Raises:
        StateError: A key already exists and ``replace`` is false, or the file cannot be written.
    """
    if path.exists() and not replace:
        raise StateError("A node key already exists here; enrol with --force to replace it.")
    signer = Ed25519Signer.generate()
    try:
        write_atomic(path, signer.private_key_bytes)
    except OSError as exc:
        raise StateError("The node key could not be written to the data directory.") from exc
    return signer


def load_signer(path: Path) -> Ed25519Signer:
    """Read the node key back.

    Args:
        path: The key file.

    Returns:
        The signer for the stored key.

    Raises:
        NotEnrolledError: There is no key file.
        StateError: The file cannot be read or is not a 32-byte key.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise NotEnrolledError("There is no node key here; run pollen enrol first.") from exc
    except OSError as exc:
        raise StateError("The node key file cannot be read.") from exc
    try:
        return Ed25519Signer(raw)
    except ValueError as exc:
        raise StateError("The node key file is not a 32-byte Ed25519 seed.") from exc


def load_state(path: Path) -> PacketState:
    """Read and validate the PacketState document.

    Args:
        path: The state file (``DataLayout.state``).

    Returns:
        The validated state.

    Raises:
        NotEnrolledError: The file does not exist.
        StateError: The file cannot be read or fails validation.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise NotEnrolledError("This device is not enrolled; run pollen enrol first.") from exc
    except OSError as exc:
        raise StateError("The state file cannot be read.") from exc
    try:
        return PacketState.model_validate_json(raw)
    except ValidationError as exc:
        # Locations only: a hand-edited file's values are not worth echoing into an error.
        where = ", ".join(".".join(str(part) for part in e["loc"]) for e in exc.errors())
        raise StateError(f"The state file is not a valid PacketState (see: {where}).") from exc


def save_state(path: Path, state: PacketState) -> None:
    """Write ``state`` atomically.

    Args:
        path: The state file.
        state: The document to persist.

    Raises:
        StateError: The file could not be written.
    """
    try:
        write_atomic(path, state.model_dump_json(indent=2).encode("utf-8"))
    except OSError as exc:
        raise StateError("The state file could not be written.") from exc


class StateStore:
    """The one owner of the mutable PacketState: validated, written, then visible.

    Owns mutable state (codingrules 8.5, documented): ``_state`` is replaced, never edited, and
    only after the new document reached the disk. An ``asyncio.Lock`` serialises updates so two
    tasks cannot write each other's stale copy.
    """

    def __init__(self, path: Path, state: PacketState) -> None:
        """Wrap an already persisted state.

        Args:
            path: The state file updates are written to.
            state: The value currently on disk.
        """
        self._path = path
        self._state = state
        self._lock = asyncio.Lock()

    @classmethod
    def load(cls, path: Path) -> StateStore:
        """Open the store on an existing state file.

        Args:
            path: The state file.

        Returns:
            A store holding what the file says.

        Raises:
            NotEnrolledError: There is no state file.
            StateError: The file cannot be read or is invalid.
        """
        return cls(path, load_state(path))

    @property
    def current(self) -> PacketState:
        """The state as of the last successful update; read it afresh for every request."""
        return self._state

    async def update(self, **changes: object) -> PacketState:
        """Apply ``changes`` to the state, persist the result, and return it.

        Args:
            **changes: PacketState field names and their new values.

        Returns:
            The new state, now also the value of ``current``.

        Raises:
            StateError: The result fails validation or cannot be written; ``current`` is then
                unchanged.
        """
        async with self._lock:
            try:
                merged = {**self._state.model_dump(), **changes}
                updated = PacketState.model_validate(merged)
            except ValidationError as exc:
                raise StateError("A state update failed validation and was not applied.") from exc
            # Disk first, memory second: a failed write leaves the old document authoritative.
            await asyncio.to_thread(save_state, self._path, updated)
            self._state = updated
            return updated

    async def transform(
        self, change: Callable[[PacketState], PacketState | None]
    ) -> PacketState | None:
        """Replace the state with ``change(current)``, deciding from the value under the lock.

        Args:
            change: A pure function from the current state to its replacement, or None to leave
                the state as it is (a stale or refused relocation notice, nothing to settle).

        Returns:
            The new state, now also ``current``; None if nothing changed.

        Raises:
            StateError: The replacement fails validation or cannot be written; ``current`` is
                then unchanged.
        """
        async with self._lock:
            replacement = change(self._state)
            if replacement is None or replacement == self._state:
                return None
            try:
                # A copy made with model_copy skips validation, so the document is checked
                # again here: the link rules must hold for what reaches the disk.
                updated = PacketState.model_validate(replacement.model_dump())
            except ValidationError as exc:
                raise StateError("A state change failed validation and was not applied.") from exc
            await asyncio.to_thread(save_state, self._path, updated)
            self._state = updated
            return updated
