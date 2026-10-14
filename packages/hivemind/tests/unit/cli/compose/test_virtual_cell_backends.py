"""Unit tests for hivemind.cli.compose.virtual_cell_backends: the Docker control network wiring.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/cli/compose/virtual_cell_backends.py (codingrules section 3); roadmap step
    10.6a's half of it: `prepare_backend` makes the control network before the listener binds, and
    the Docker backend shares that one client and is handed the network. `SdkDockerClient` is
    swapped for `FakeDockerClient`, so no daemon is touched.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.virtual_cell_backends for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cli import fake_manifest

from hivemind.cli.compose import virtual_cell_backends
from hivemind.cli.compose.virtual_cell_backends import RegistryContext
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.manifest import load_manifest
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.listener import CellListener, CellListenerDeps
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

_SUBNET = "10.213.7.0/24"
_GATEWAY = "10.213.7.1"


def _parts(tmp_path: Path, **fields: object) -> VirtualCellsParts:
    """Build the Virtual side of `fake_manifest`'s Hive with `[virtual_cells]` set to `fields`."""
    manifest = load_manifest(fake_manifest(tmp_path))
    section = VirtualCellsSection.model_validate(fields)
    manifest = manifest.model_copy(update={"virtual_cells": section})
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None
    return parts


async def test_prepare_makes_the_control_network_on_the_client_the_backend_then_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(virtual_cell_backends, "SdkDockerClient", FakeDockerClient)
    parts = _parts(tmp_path, backend="docker", control_subnet=_SUBNET, listen_host=_GATEWAY)

    await parts.prepare()
    backend = parts.registry.get("docker")

    assert isinstance(backend, DockerCellBackend)
    client = backend.client
    assert isinstance(client, FakeDockerClient)
    # One client per Hive: the network the listener binds the gateway of is on the same daemon
    # connection the backend provisions and cuts through.
    [control] = client.ensure_network_calls
    assert (control.subnet, control.gateway, control.internal) == (_SUBNET, _GATEWAY, True)
    assert backend.capabilities.can_cut_egress


async def test_without_a_control_subnet_there_is_nothing_to_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(virtual_cell_backends, "SdkDockerClient", FakeDockerClient)
    parts = _parts(tmp_path, backend="docker")

    await parts.prepare()
    backend = parts.registry.get("docker")

    assert isinstance(backend, DockerCellBackend)
    assert isinstance(backend.client, FakeDockerClient)
    assert backend.client.ensure_network_calls == []
    assert not backend.capabilities.can_cut_egress


def test_the_hives_offline_limit_is_told_to_every_cell_it_provisions(tmp_path: Path) -> None:
    # A Night Veil Cell's Tor link can drop under it; its Warden clusters fully past this limit,
    # and a Cell holds no manifest to read it from (roadmap step 10.3a).
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path))
    supervision = manifest.supervision.model_copy(update={"max_offline_s": 75.0})
    manifest = manifest.model_copy(update={"supervision": supervision})
    gate, signer, node_id = QueenReadinessGate(), Ed25519Signer.generate(), new_node_id(clock)
    listener = CellListener(CellListenerDeps(gate, signer, node_id, new_hive_id(clock)), clock)
    section = VirtualCellsSection.model_validate(
        {"backend": "fake", "advertise_url": "ws://localhost:8710"}
    )
    ctx = RegistryContext(manifest, section, gate, listener, signer, node_id, {})

    endpoint = virtual_cell_backends._endpoint_for(ctx, docker=False)

    assert endpoint.max_offline_s == 75.0


async def test_a_fake_backend_hive_prepares_nothing(tmp_path: Path) -> None:
    parts = _parts(tmp_path, backend="fake")

    await parts.prepare()  # No Docker client is ever built for a fake-backend Hive.

    assert set(parts.registry.names()) == {"fake"}
