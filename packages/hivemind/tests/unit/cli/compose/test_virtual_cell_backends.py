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
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.manifest import load_manifest
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock

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


async def test_a_fake_backend_hive_prepares_nothing(tmp_path: Path) -> None:
    parts = _parts(tmp_path, backend="fake")

    await parts.prepare()  # No Docker client is ever built for a fake-backend Hive.

    assert set(parts.registry.names()) == {"fake"}
