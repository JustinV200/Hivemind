"""Tests for hivemind.cli.compose.swarm.build: `[swarm]` turned into the listener, door and gate.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/swarm/build.py (codingrules section 3). Every Hive here is
    built by `build_hive` from a real `builders.swarm.swarm_manifest`, over the Hive's own SQLite
    file and secret store; the ones that run use `run_hive` and a real loopback port.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.swarm.build for the module under test.
    - hivemind.cli.compose.hive for the Hive that holds the result and the run_hive that starts it.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.swarm import free_port, make_tls_material, swarm_manifest

from hivemind.cell import AccessLevel, CellError, CombShieldLevel
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.cli.stores import open_swarm_registry
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.queen.swarm_gate import SwarmGate
from hivemind.swarm import SqliteSwarmRegistry, parse_invite_string
from waggle.clock import SystemClock
from waggle.signing import public_key_hex


def _hive(manifest_path: Path) -> Hive:
    """Build a Hive from a written manifest, outside any event loop."""
    return build_hive(load_manifest(manifest_path, {}), environ={}, clock=SystemClock())


def test_a_hive_with_the_swarm_off_has_no_swarm(tmp_path: Path) -> None:
    assert _hive(fake_manifest(tmp_path)).swarm is None


def test_a_swarm_section_that_is_not_enabled_builds_nothing(tmp_path: Path) -> None:
    hive = _hive(swarm_manifest(tmp_path, enabled=False))

    assert hive.swarm is None


def test_enabling_the_swarm_builds_the_gate_the_listener_the_registry_and_the_door(
    tmp_path: Path,
) -> None:
    hive = _hive(swarm_manifest(tmp_path))

    swarm = hive.swarm
    assert swarm is not None
    assert isinstance(swarm.gate, SwarmGate)
    assert swarm.gate.listener is swarm.listener
    assert isinstance(swarm.registry, SqliteSwarmRegistry)
    assert swarm.door is not None
    assert swarm.listener.hub is swarm.gate.wardens._hub  # One hub: the one the Wardens listen to.


def test_an_invite_carries_the_address_the_listener_advertises_and_the_hives_key(
    tmp_path: Path,
) -> None:
    port = free_port()
    hive = _hive(swarm_manifest(tmp_path, listen_port=port))
    assert hive.swarm is not None
    minted = asyncio.run(hive.swarm.door.mint_invite(AccessLevel.SCRATCH, CombShieldLevel.MEADOW))

    contents = parse_invite_string(minted.invite_string)

    assert contents.endpoint.url == f"ws://127.0.0.1:{port}"
    assert contents.endpoint.hive_id == hive.manifest.hive.id
    assert contents.endpoint.hive_node_id == hive.manifest.hive.node_id
    assert contents.endpoint.tls_sha256 is None
    assert contents.endpoint.hive_key_hex == _hive_key_hex(hive.manifest)


def _hive_key_hex(manifest: HiveManifest) -> str:
    """The public half of the Hive's signing key, as the secret store holds it."""
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    return public_key_hex(asyncio.run(load_or_mint_hive_signer(secrets)).public_key_bytes)


def test_the_registry_is_the_hives_own_database_so_another_process_sees_an_invite(
    tmp_path: Path,
) -> None:
    hive = _hive(swarm_manifest(tmp_path))
    assert hive.swarm is not None
    asyncio.run(hive.swarm.door.mint_invite(AccessLevel.FULL, CombShieldLevel.PROPOLIS, "a laptop"))

    # `hive swarm list` opens the same file on a connection of its own.
    other = open_swarm_registry(hive.manifest.resolve_path(hive.manifest.hive.db))
    [invite] = asyncio.run(other.invites())

    assert invite.label == "a laptop"
    assert invite.comb_shield is CombShieldLevel.PROPOLIS


def test_with_a_certificate_the_invite_pins_it_and_the_listener_advertises_wss(
    tmp_path: Path,
) -> None:
    material = make_tls_material(tmp_path, "swarm")
    port = free_port()
    path = swarm_manifest(
        tmp_path,
        listen_host="0.0.0.0",  # noqa: S104 -- a non-loopback bind is what makes TLS mandatory.
        listen_port=port,
        advertise_url=f"wss://hive.example.test:{port}",
        tls_cert=material.cert_path.name,
        tls_key=material.key_path.name,
    )
    hive = _hive(path)
    assert hive.swarm is not None

    minted = asyncio.run(hive.swarm.door.mint_invite(AccessLevel.SCRATCH, CombShieldLevel.MEADOW))

    endpoint = parse_invite_string(minted.invite_string).endpoint
    assert endpoint.url == f"wss://hive.example.test:{port}"
    assert endpoint.tls_sha256 == material.sha256  # Read from the very file the listener serves.


def test_a_missing_certificate_fails_the_build_not_the_first_device(tmp_path: Path) -> None:
    path = swarm_manifest(
        tmp_path,
        listen_host="0.0.0.0",  # noqa: S104 -- a non-loopback bind is what makes TLS mandatory.
        tls_cert="absent.crt",
        tls_key="absent.key",
    )

    with pytest.raises(FileNotFoundError):
        _hive(path)


def test_run_hive_opens_the_listener_and_closes_it_when_the_block_ends(tmp_path: Path) -> None:
    port = free_port()
    hive = _hive(swarm_manifest(tmp_path, listen_port=port))

    asyncio.run(_open_then_closed(hive, port))


async def _open_then_closed(hive: Hive, port: int) -> None:
    """Assert the swarm listener accepts connections inside `run_hive` and not after it."""
    async with run_hive(hive):
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()

    with pytest.raises(OSError):  # Connection refused, in whatever flavour the OS has.
        await asyncio.open_connection("127.0.0.1", port)


def test_the_gate_starts_after_the_hive_stands_warden_is_attached_and_stops_before_the_queen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive = _hive(swarm_manifest(tmp_path, listen_port=free_port()))
    assert hive.swarm is not None
    seen: dict[str, object] = {}
    gate = hive.swarm.gate
    start, stop = gate.start, gate.stop

    async def spy_start(queen: object, deps: object) -> None:
        # The Hive Stand's own Warden is attached (the Queen has her own Cell) but not started.
        seen["attached_at_start"] = [link.warden_id for link in hive.queen.wardens]
        seen["warden_at_start"] = hive.warden.state.name
        seen["deps"] = deps
        await start(queen, deps)  # type: ignore[arg-type]

    async def spy_stop() -> None:
        # The Queen and the Hive Stand's Warden still run while the devices' Wardens stop.
        seen["queen_stopped_at_stop"] = hive.queen._stop.is_set()
        seen["warden_at_stop"] = hive.warden.state.name
        await stop()

    monkeypatch.setattr(gate, "start", spy_start)
    monkeypatch.setattr(gate, "stop", spy_stop)

    async def scenario() -> None:
        async with run_hive(hive):
            pass

    asyncio.run(scenario())

    assert seen["deps"] is hive.queen_deps  # The gate works with the Queen's own collaborators.
    assert seen["attached_at_start"] == [hive.warden_link.warden_id]
    assert seen["warden_at_start"] == "STARTING"
    assert seen["queen_stopped_at_stop"] is False
    assert seen["warden_at_stop"] in {"ACTIVE", "WATCH"}


def test_a_gate_that_cannot_open_its_port_leaves_nothing_started(tmp_path: Path) -> None:
    port = free_port()
    hive = _hive(swarm_manifest(tmp_path, listen_port=port))

    async def scenario() -> None:
        with pytest.raises(OSError):  # The port is taken: the listener cannot bind.
            async with run_hive(hive):
                pass
        # What had started is unwound: the Hive Stand's Warden is detached, no task is left.
        assert hive.queen.wardens == ()
        others = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        assert others == []

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", port))
        taken.listen()
        asyncio.run(scenario())


def test_a_start_that_fails_after_the_gate_opened_closes_the_gate_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = free_port()
    hive = _hive(swarm_manifest(tmp_path, listen_port=port))

    async def refuse() -> None:
        raise CellError("The Hive Stand's Cell could not be leased.")

    monkeypatch.setattr(hive.warden, "start", refuse)

    async def scenario() -> None:
        with pytest.raises(CellError):
            async with run_hive(hive):
                pass
        with pytest.raises(OSError):  # The gate that had opened the port closed it again.
            await asyncio.open_connection("127.0.0.1", port)
        assert hive.queen.wardens == ()

    asyncio.run(scenario())


_SERVER = """
[llm.providers.local]
kind = "openai_compat"
base_url = "http://127.0.0.1:8081/v1"

[llm.servers.box]
provider = "local"
argv = ["server"]
port = 8081
sources = ["small", "large"]
load_on_start = false

[forage.map.small]
provider = "local"
model = "small-model"
grade = 2
context_window = 4096
seats = 2
vram_bytes_required = 4294967296

[forage.map.large]
provider = "local"
model = "large-model"
grade = 3
context_window = 8192
seats = 2
vram_bytes_required = 12884901888
"""


def test_the_gate_is_built_with_the_servers_the_hive_allows_and_the_nuc_pressure(
    tmp_path: Path,
) -> None:
    path = swarm_manifest(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8") + "\n[swarm.nuc]\nseat_pressure = 0.5\n" + _SERVER,
        encoding="utf-8",
    )

    hive = _hive(path)

    assert hive.swarm is not None
    terms = hive.swarm.gate.terms
    assert terms.nuc_seat_pressure == 0.5
    [server] = terms.servers
    assert server.server_id == "box"
    # Each model the manifest lists for the server, in its order, with no host: the Hive's
    # allowlist for whatever device declares this server.
    assert [(s.source_id, s.spec.vram_bytes_required) for s in server.sources] == [
        ("small", 4 * 1024**3),
        ("large", 12 * 1024**3),
    ]
    assert all(s.spec.host_cell_id is None for s in server.sources)


def test_a_hive_that_declares_no_server_allows_a_device_none(tmp_path: Path) -> None:
    hive = _hive(swarm_manifest(tmp_path))

    assert hive.swarm is not None and hive.swarm.gate.terms.servers == ()
