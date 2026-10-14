"""Tests for hivemind.cli.compose.swarm.door: the door `hive swarm invite` mints with.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/swarm/door.py (codingrules section 3). The property that
    matters is that the command's door and the running Hive's door cannot disagree: both are built
    from the same manifest and the same Hive key, and an invite from either names the same
    address, the same Hive and the same pins. It is shown by minting from both over one manifest
    (a built Hive's own door, and `build_invite_door` over a registry and recorder of its own) and
    comparing what a device would read.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.swarm.door for the module under test.
    - tests/unit/cli/compose/swarm/test_build.py for the running Hive's door on its own.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cells import make_identity
from builders.swarm import free_port, make_tls_material, swarm_manifest

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cli.compose import build_hive
from hivemind.cli.compose.swarm import build_invite_door
from hivemind.cli.stores import open_swarm_registry, open_trail
from hivemind.manifest import load_manifest
from hivemind.swarm import SwarmRecorder, parse_invite_string
from waggle.clock import SystemClock


def _mint(manifest_path: Path) -> str:
    """Mint through the command's door, as `hive swarm invite` does, and return the string."""
    manifest = load_manifest(manifest_path, {})
    db = manifest.resolve_path(manifest.hive.db)
    identity = make_identity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="human")
    recorder = SwarmRecorder(open_trail(db), SystemClock(), identity)
    door = build_invite_door(manifest, open_swarm_registry(db), recorder)
    minted = asyncio.run(door.mint_invite(AccessLevel.SCRATCH, CombShieldLevel.MEADOW, "attic-pc"))
    return minted.invite_string


def test_the_commands_door_and_the_running_hives_door_put_the_same_address_in_an_invite(
    tmp_path: Path,
) -> None:
    manifest_path = swarm_manifest(tmp_path, listen_port=free_port())
    hive = build_hive(load_manifest(manifest_path, {}), environ={}, clock=SystemClock())
    assert hive.swarm is not None

    from_the_hive = asyncio.run(
        hive.swarm.door.mint_invite(AccessLevel.SCRATCH, CombShieldLevel.MEADOW, "attic-pc")
    )
    from_the_command = _mint(manifest_path)

    assert (
        parse_invite_string(from_the_hive.invite_string).endpoint
        == parse_invite_string(from_the_command).endpoint
    )


def test_the_commands_door_pins_the_certificate_the_listener_will_serve(tmp_path: Path) -> None:
    material = make_tls_material(tmp_path, "hive")
    manifest_path = swarm_manifest(
        tmp_path,
        listen_host="0.0.0.0",  # noqa: S104  # The schema's rule: a non-loopback bind needs TLS.
        tls_cert=material.cert_path.name,
        tls_key=material.key_path.name,
        advertise_url="wss://hive.example.net:8720",
    )

    endpoint = parse_invite_string(_mint(manifest_path)).endpoint

    assert endpoint.url == "wss://hive.example.net:8720"
    assert endpoint.tls_sha256 == material.sha256


def test_minting_before_the_hive_has_ever_run_makes_the_key_the_hive_will_sign_with(
    tmp_path: Path,
) -> None:
    manifest_path = swarm_manifest(tmp_path, listen_port=free_port())

    early = parse_invite_string(_mint(manifest_path)).endpoint.hive_key_hex
    hive = build_hive(load_manifest(manifest_path, {}), environ={}, clock=SystemClock())
    assert hive.swarm is not None
    later = asyncio.run(hive.swarm.door.mint_invite(AccessLevel.SCRATCH, CombShieldLevel.MEADOW))

    # The first invite created the key in the secret store; the Hive then loaded the same one.
    assert parse_invite_string(later.invite_string).endpoint.hive_key_hex == early
