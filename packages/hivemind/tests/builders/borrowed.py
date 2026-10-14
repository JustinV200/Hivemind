"""Build the environment, keys and manifest a colonized device's Warden is started with (11.6a).

The Pollen Packet starts the Warden of a colonized Swarm device with a handful of environment
variables, the device's node key as a file of raw bytes, and a small device manifest. `make_kit`
writes all three under a temporary directory and returns them with the ids and keys behind them,
so a test can start `run_borrowed_warden` against a scripted listener, check the Warden's frames
against the keys it should have signed them with, and change one variable at a time to see the
configuration refuse it. `DEVICE_MANIFEST` is a complete `[llm]` section with one shared provider,
every slot bound to it, and, on request, a declared model server with the `[forage.map]` row of
the model it loads and a local binding the worker slot falls back to.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/cli/in_cell/borrowed and by the contract test that checks the
    environment pollen sets against the one this Warden reads.

Key invariants:
    - The key file holds exactly what the packet's own writes: the raw 32-byte seed.
    - Every id is a real, validated one, and the listener's `verifier` knows the device's node
      under its real key, so a frame the Warden signs with another key fails to verify.

See Also:
    - hivemind.cli.in_cell.borrowed.config for the environment these values satisfy.
    - docs/manifests/device.toml for the manifest's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hivemind.forage import ModelSlot
from waggle.clock import SystemClock
from waggle.ids import (
    CellId,
    HiveId,
    NodeId,
    WardenId,
    new_cell_id,
    new_hive_id,
    new_node_id,
    new_warden_id,
)
from waggle.signing import Ed25519Signer, Ed25519Verifier

SERVER_ID = "local_server"  # The model server the manifest declares when asked to.
SOURCE_ID = "x"  # The one Forage map entry that server may load.
LOCAL_MODEL = "x-model"  # What that entry serves, and what the worker slot's local binding names.


@dataclass(frozen=True, slots=True)
class BorrowedKit:
    """Everything a device Warden is started with, and the keys and ids to check its frames by."""

    environ: dict[str, str]  # The variables the packet sets; a test may change one.
    state_dir: Path  # Where the Warden's stores, outbox and scratch go.
    manifest_path: Path  # The device manifest the environment names.
    key_path: Path  # The device's node key: the raw 32-byte seed.
    warden_id: WardenId
    node_id: NodeId  # The device's node: what the Warden signs as.
    cell_id: CellId
    hive_id: HiveId
    queen_node_id: NodeId
    queen_signer: Ed25519Signer  # What the scripted listener signs frames to the Warden with.
    device_signer: Ed25519Signer  # What the Warden must sign with.

    @property
    def device_verifier(self) -> Ed25519Verifier:
        """A verifier that accepts frames signed as the device's node, and nothing else."""
        return Ed25519Verifier({self.node_id: self.device_signer.public_key_bytes})


def device_manifest_text(*, with_server: bool = False, supervision: str = "") -> str:
    """Return a complete device manifest's text: one shared provider, every slot bound to it.

    Args:
        with_server: Whether the manifest declares a model server and the model it loads.
        supervision: The body of a ``[supervision]`` table (its keys and sub-tables, with the
            header included); none leaves the Hive's shipped defaults in force.
    """
    lines = ["[llm]", "", "[llm.providers.shared]", 'kind = "openai_compat"']
    lines += ['base_url = "http://hive.example.lan:1234/v1"', "seats = 2", ""]
    for slot in ModelSlot:
        lines += [f"[llm.slots.{slot.manifest_key}]", 'provider = "shared"', 'model = "m"']
        if with_server and slot is ModelSlot.WORKER:
            lines += ['fallback = "local_worker"']  # Where a Nuc's sub-bees go first.
        lines += [""]
    if with_server:
        lines += ["[llm.slots.local_worker]", 'provider = "local"', f'model = "{LOCAL_MODEL}"', ""]
        lines += ["[llm.providers.local]", 'kind = "openai_compat"']
        lines += ['base_url = "http://127.0.0.1:8081/v1"', ""]
        lines += [f"[llm.servers.{SERVER_ID}]", 'provider = "local"', 'argv = ["server"]']
        lines += ["port = 8081", f'sources = ["{SOURCE_ID}"]', "load_on_start = false", ""]
        lines += [f"[forage.map.{SOURCE_ID}]", 'provider = "local"', f'model = "{LOCAL_MODEL}"']
        lines += ["grade = 2", "context_window = 4096", "seats = 2", ""]
    if supervision:
        lines += [supervision, ""]
    return "\n".join(lines)


def make_kit(
    tmp_path: Path,
    url: str,
    *,
    access_level: str = "SCRATCH",
    with_server: bool = False,
    supervision: str = "",
) -> BorrowedKit:
    """Write a device's key and manifest under `tmp_path` and return its environment and keys.

    Args:
        tmp_path: A directory owned by the test.
        url: The swarm listener's URL (a scripted `WebSocketServer`'s `uri`).
        access_level: The level the Queen granted the device.
        with_server: Whether the manifest declares a model server.
        supervision: A ``[supervision]`` table for the manifest (see `device_manifest_text`).

    Returns:
        The kit; `environ` is what the packet would set, ready for `read_in_cell_env`.
    """
    clock = SystemClock()
    device_signer = Ed25519Signer.generate()
    key_path = tmp_path / "node.ed25519"
    key_path.write_bytes(device_signer.private_key_bytes)  # Raw seed, as the packet writes it.
    manifest_path = tmp_path / "warden.toml"
    manifest_path.write_text(
        device_manifest_text(with_server=with_server, supervision=supervision), encoding="utf-8"
    )
    kit = BorrowedKit(
        environ={},
        state_dir=tmp_path / "state",
        manifest_path=manifest_path,
        key_path=key_path,
        warden_id=new_warden_id(clock),
        node_id=new_node_id(clock),
        cell_id=new_cell_id(clock),
        hive_id=new_hive_id(clock),
        queen_node_id=new_node_id(clock),
        queen_signer=Ed25519Signer.generate(),
        device_signer=device_signer,
    )
    kit.environ.update(_environ(kit, url, access_level))
    return kit


def _environ(kit: BorrowedKit, url: str, access_level: str) -> dict[str, str]:
    """The variables the packet sets for the Warden of `kit`'s device."""
    return {
        "HIVEMIND_CELL_LEASE": "borrowed",
        "HIVEMIND_QUEEN_WAGGLE_URL": url,
        "HIVEMIND_CELL_ID": kit.cell_id,
        "HIVEMIND_WARDEN_ID": kit.warden_id,
        "HIVEMIND_NODE_ID": kit.node_id,
        "HIVEMIND_HIVE_ID": kit.hive_id,
        "HIVEMIND_QUEEN_NODE_ID": kit.queen_node_id,
        "HIVEMIND_QUEEN_VERIFY_KEY": kit.queen_signer.public_key_bytes.hex(),
        "HIVEMIND_CELL_SIGNING_KEY_FILE": str(kit.key_path),
        "HIVEMIND_ACCESS_LEVEL": access_level,
        "HIVEMIND_COMB_SHIELD": "MEADOW",
        "HIVEMIND_STATE_DIR": str(kit.state_dir),
        "HIVEMIND_DEVICE_MANIFEST": str(kit.manifest_path),
    }
