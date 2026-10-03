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

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from structlog.testing import capture_logs

from hivemind.cli.compose import virtual_cell_backends
from hivemind.cli.compose.virtual_cell_backends import RegistryContext
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.common.errors import ConfigurationError
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.llm import ProviderSpec
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.listener import CellListener, CellListenerDeps
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

# A private subnet whose gateway this host has (loopback), standing in for the address the real
# daemon's bridge gives the host; `require_local_gateway` binds it.
_SUBNET = "127.0.0.0/29"
_GATEWAY = "127.0.0.1"
# The benchmarking range (private, and never a host's own address): a gateway Docker Desktop's VM
# would have and this host would not.
_FOREIGN_SUBNET = "198.18.7.0/24"
_FOREIGN_GATEWAY = "198.18.7.1"


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


async def test_a_control_gateway_this_host_lacks_is_refused_naming_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real `hive serve` on Windows: Docker Desktop made the network in its own VM, and the
    # listener then failed with a bare "could not bind on any address".
    monkeypatch.setattr(virtual_cell_backends, "SdkDockerClient", FakeDockerClient)
    parts = _parts(
        tmp_path, backend="docker", control_subnet=_FOREIGN_SUBNET, listen_host=_FOREIGN_GATEWAY
    )

    with pytest.raises(ConfigurationError) as refused:
        await parts.prepare()

    message = str(refused.value)
    assert _FOREIGN_GATEWAY in message and "Docker Desktop" in message
    assert "docker network rm hivemind-" in message and "-control" in message


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


_EXPOSED = "virtual_cells.provider_reachable_through_isolation"


async def test_a_model_server_answering_on_the_gateway_is_warned_about(tmp_path: Path) -> None:
    # Arrange: loopback stands in for the control gateway, and a listener for a model server
    # bound to every interface; the provider names the host the way a local server does.
    server = await asyncio.start_server(_hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    manifest = _with_provider(tmp_path, f"http://localhost:{port}/v1")

    async with server:
        with capture_logs() as logs:
            await virtual_cell_backends.warn_exposed_providers(manifest, "127.0.0.1")

    [warning] = [entry for entry in logs if entry["event"] == _EXPOSED]
    assert warning["log_level"] == "warning"
    assert (warning["provider"], warning["port"]) == ("local", port)
    assert "docker0" in warning["hint"]


async def test_a_model_server_bound_elsewhere_draws_no_warning(tmp_path: Path) -> None:
    server = await asyncio.start_server(_hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()  # Bound nowhere the gateway reaches: the connect is refused.
    manifest = _with_provider(tmp_path, f"http://127.0.0.1:{port}/v1")

    with capture_logs() as logs:
        await virtual_cell_backends.warn_exposed_providers(manifest, "127.0.0.1")

    assert [entry for entry in logs if entry["event"] == _EXPOSED] == []


def _with_provider(tmp_path: Path, base_url: str) -> HiveManifest:
    """`fake_manifest`'s manifest with one `openai_compat` provider, "local", at `base_url`."""
    manifest = load_manifest(fake_manifest(tmp_path))
    providers = dict(manifest.llm.providers)
    providers["local"] = ProviderSpec(kind="openai_compat", base_url=base_url)
    llm = manifest.llm.model_copy(update={"providers": providers})
    return manifest.model_copy(update={"llm": llm})


async def _hang_up(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Accept, then close at once: the probe only needs the accept."""
    writer.close()
