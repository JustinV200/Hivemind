"""Build a Pollen Packet's side of a colonized device without a Queen: enrolment and runtime.

The contract tests between pollen and the Warden it starts (`contracts/test_pollen_warden_env.py`
and `contracts/test_pollen_warden_process.py`) need what the packet holds on disk once it has been
enrolled and its runtime prepared, but not a Hive to enrol with. `make_packet` writes the node key
with the packet's own routine, builds the `PacketState` an enrolment leaves (the ids, the keys of
both ends, the level and tier the Queen granted, the operator's device manifest recorded as the
install settings) and returns it with the signers behind it. `install_runtime` makes the packet's
runtime environment the way a finished PREPARE leaves it, except that the environment is this
workspace's own: a `python` that is this interpreter and a `hivemind-in-cell` that is the console
script the workspace installed, so the process a test starts is the real Warden of this checkout.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the two pollen contract
    tests; depends on `pollen`'s state and platform seam, on `builders.borrowed` for the manifest
    text, and on waggle.

Key invariants:
    - The key file is written by `pollen.state.create_key`, so it is what the packet writes and
      the Warden is to read: the raw 32-byte seed, mode 0600.
    - The state passes the packet's own validation; nothing here bypasses it.
    - The runtime's programs are this workspace's, never a copy: a Warden started from them runs
      the code under test.

See Also:
    - pollen.bootstrap for the code that reads this layout and starts the programs.
    - builders.borrowed for the unit tests' counterpart (the Warden's side, without a packet).
"""

from __future__ import annotations

import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from builders.borrowed import device_manifest_text

from pollen.platform import PlatformShim
from pollen.state import DataLayout, InstallSettings, PacketState, create_key
from waggle.clock import SystemClock
from waggle.ids import (
    new_cell_id,
    new_device_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm import NodeKey
from waggle.signing import Ed25519Signer, public_key_hex

LOOPBACK = "ws://127.0.0.1:8720"  # A Queen the state can name; the Warden is given its own URL.
HOSTED = "wss://hive.example.net:8720"  # A Queen over TLS: the state then needs a pin.
PIN = "ab" * 32  # A SHA-256 certificate fingerprint in lowercase hex.

__all__ = [
    "HOSTED",
    "LOOPBACK",
    "PIN",
    "Packet",
    "install_runtime",
    "make_packet",
    "warden_script",
]


@dataclass(frozen=True, slots=True)
class Packet:
    """A packet's enrolment as it stands on disk, and the keys behind it."""

    state: PacketState  # What the packet persists; also what it starts the Warden from.
    layout: DataLayout  # The packet's data directory, created.
    device_signer: Ed25519Signer  # The device's node key: what the Warden must sign with.
    queen_signer: Ed25519Signer  # The Queen's: what a scripted listener signs frames with.
    manifest: Path  # The device manifest the operator recorded at install time.


def make_packet(
    tmp_path: Path,
    *,
    url: str = LOOPBACK,
    pin: str | None = None,
    level: AccessLevel = AccessLevel.FULL,
) -> Packet:
    """Write what an enrolment leaves under `tmp_path` and return it.

    Args:
        tmp_path: A directory owned by the test.
        url: The Queen's address the state records; a `wss://` one needs `pin`.
        pin: The certificate pin for a `wss://` Queen; None for `ws://`.
        level: The access level the Queen granted the device.

    Returns:
        The packet: its state, layout, both ends' signers and the device manifest.
    """
    clock = SystemClock()
    layout = DataLayout(tmp_path / "pollen")
    layout.ensure()
    device_signer = create_key(layout.key)
    queen_signer = Ed25519Signer.generate()
    queen_key = public_key_hex(queen_signer.public_key_bytes)
    queen_node = new_node_id(clock)
    manifest = tmp_path / "warden.toml"
    manifest.write_text(device_manifest_text(), encoding="utf-8")
    state = PacketState(
        device_id=new_device_id(clock),
        node_id=new_node_id(clock),
        queen_url=url,
        tls_pin_sha256=pin,
        hive_id=new_hive_id(clock),
        hive_node_id=queen_node,
        hive_key_hex=queen_key,
        trusted_nodes=(NodeKey(node_id=queen_node, public_key_hex=queen_key),),
        cell_id=new_cell_id(clock),
        warden_id=new_warden_id(clock),
        access_level=level,
        comb_shield=CombShieldLevel.MEADOW,
        granted_capabilities=("fs:read:**",),
        offline_limit_s=120.0,
        heartbeat_interval_s=15.0,
        enrolled_at=clock.now(),
        install=InstallSettings(warden_manifest=str(manifest)),
    )
    return Packet(state, layout, device_signer, queen_signer, manifest)


def warden_script() -> Path | None:
    """Return this workspace's installed `hivemind-in-cell` console script, if it has one."""
    script = Path(sys.executable).with_name("hivemind-in-cell")
    return script if script.is_file() else None


def install_runtime(layout: DataLayout, shim: PlatformShim, script: Path) -> None:
    """Make the packet's runtime environment out of this workspace's interpreter and `script`.

    Args:
        layout: The packet's data directory; the environment goes where PREPARE puts it.
        shim: The platform shim, which knows where an environment keeps its programs.
        script: The console script to run as the Warden (`warden_script()`'s answer).
    """
    python = shim.venv_executable(layout.venv, "python")
    warden = shim.venv_executable(layout.venv, "hivemind-in-cell")
    python.parent.mkdir(parents=True)
    # A wrapper, not a symlink: a virtual environment finds its packages by the directory its
    # interpreter is started from, and this one has none of its own.
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    warden.symlink_to(script)  # The script's own shebang names the workspace interpreter.
