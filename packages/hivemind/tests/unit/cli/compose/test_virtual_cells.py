"""Unit tests for hivemind.cli.compose.virtual_cells: build_virtual_cells, docker_gateway_url.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/cli/compose/virtual_cells.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.virtual_cells for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.cli import fake_manifest

from hivemind.cell import CombShieldLevel
from hivemind.cli.compose import virtual_cell_backends
from hivemind.cli.compose.virtual_cell_backends import night_veil_socks_proxy_url
from hivemind.cli.compose.virtual_cells import (
    _fail_closed_night_veil_probe,
    build_virtual_cells,
    docker_gateway_url,
)
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.manifest.schema.security import SecuritySection, TierProfile
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.messages import CombShieldLevel as WireCombShieldLevel


def _load_with_virtual_cells(tmp_path: Path, **overrides: object) -> HiveManifest:
    """Load `fake_manifest`'s own manifest, with `[virtual_cells]` overridden."""
    manifest_path = fake_manifest(tmp_path)
    manifest = load_manifest(manifest_path)
    fields: dict[str, object] = {"backend": "fake"}
    fields.update(overrides)
    return manifest.model_copy(update={"virtual_cells": VirtualCellsSection(**fields)})


async def test_build_virtual_cells_returns_none_when_backend_unset(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = load_manifest(manifest_path)  # [virtual_cells] backend defaults to None.
    clock = FakeClock()

    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)

    assert parts is None


async def test_build_virtual_cells_registers_only_fake_when_selected(tmp_path: Path) -> None:
    manifest = _load_with_virtual_cells(tmp_path)  # backend = "fake"
    clock = FakeClock()

    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)

    assert parts is not None
    # Only "fake" here, never alongside "docker"/"qemu" (see hivemind.cli.compose.
    # virtual_cell_backends.build_registry's own docstring: a real Docker run found placement
    # picking "fake" first by registry.names() order when both were registered).
    assert set(parts.registry.names()) == {"fake"}


async def test_build_virtual_cells_registers_only_the_selected_backend(tmp_path: Path) -> None:
    manifest = _load_with_virtual_cells(tmp_path, backend="qemu")
    clock = FakeClock()

    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)

    assert parts is not None
    assert set(parts.registry.names()) == {"qemu"}


async def test_build_virtual_cells_docker_registry_constructs_without_a_started_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`registry.get("docker")` must not raise before `CellListener.start()` has ever run.

    Offline commands (`hive cells inspect`/`destroy`/`abscond`, `cli/readback/virtual*.py`) build
    this same registry just to construct a backend and read its already-provisioned Cells back,
    never starting the listener -- a real Docker run found this raising the listener's own "no
    port until start()" RuntimeError before hivemind.cli.compose.virtual_cell_backends.
    _listener_url's fallback. SdkDockerClient is swapped for FakeDockerClient so this stays a unit
    test: no real Docker daemon is ever touched.
    """
    monkeypatch.setattr(virtual_cell_backends, "SdkDockerClient", FakeDockerClient)
    manifest = _load_with_virtual_cells(tmp_path, backend="docker")
    clock = FakeClock()

    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)

    assert parts is not None
    assert set(parts.registry.names()) == {"docker"}
    backend = parts.registry.get("docker")  # Must not raise.
    assert backend.name == "docker"


async def test_build_virtual_cells_fake_backend_is_constructible() -> None:
    """`.get("fake")` never needs the docker SDK or qemu_base_image/qemu_vm_root at all."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        manifest = _load_with_virtual_cells(Path(tmp))
        clock = FakeClock()
        parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
        assert parts is not None

        backend = parts.registry.get("fake")

        assert backend.name == "fake"


async def test_virtual_backend_source_reports_the_fake_backend_with_a_default_spec(
    tmp_path: Path,
) -> None:
    manifest = _load_with_virtual_cells(tmp_path)
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None

    candidates = await parts.virtual_backend_source()

    assert len(candidates) == 1
    assert candidates[0].name == "fake"
    assert len(candidates[0].specs) == 1
    assert candidates[0].specs[0].image == manifest.virtual_cells.default_image


async def test_dormant_cell_source_starts_empty(tmp_path: Path) -> None:
    manifest = _load_with_virtual_cells(tmp_path)
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None

    assert await parts.dormant_cell_source() == ()


async def test_qemu_backend_raises_configuration_error_without_base_image_or_vm_root(
    tmp_path: Path,
) -> None:
    manifest = _load_with_virtual_cells(tmp_path, backend="qemu")
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None

    from hivemind.common.errors import ConfigurationError

    try:
        parts.registry.get("qemu")
    except ConfigurationError as exc:
        assert "qemu_base_image" in str(exc)
    else:
        raise AssertionError("expected a ConfigurationError")


def test_docker_gateway_url_rewrites_loopback_host() -> None:
    assert docker_gateway_url("ws://127.0.0.1:54321") == "ws://host.docker.internal:54321"
    assert docker_gateway_url("ws://localhost:9000") == "ws://host.docker.internal:9000"


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.7a/5.7b: the Night Veil socks-proxy wiring and the fail-closed probe.
# ──────────────────────────────────────────────────────────────────────────────


def _manifest_with_tor_socks(tmp_path: Path, tor_socks: str) -> HiveManifest:
    manifest = load_manifest(fake_manifest(tmp_path))
    tiers = dict(manifest.security.tiers)
    tiers[WireCombShieldLevel.NIGHT_VEIL] = TierProfile(
        egress_profile="vpn_tor", control_channel="tor_hidden_service", tor_socks=tor_socks
    )
    return manifest.model_copy(
        update={
            "security": SecuritySection(tiers=tiers, default_comb_shield=WireCombShieldLevel.MEADOW)
        }
    )


def test_night_veil_socks_proxy_url_is_none_for_meadow(tmp_path: Path) -> None:
    manifest = _manifest_with_tor_socks(tmp_path, "socks5://127.0.0.1:9050")

    assert night_veil_socks_proxy_url(manifest, CombShieldLevel.MEADOW) is None


def test_night_veil_socks_proxy_url_is_none_with_no_tor_socks_configured(tmp_path: Path) -> None:
    manifest = load_manifest(fake_manifest(tmp_path))  # Default tiers: tor_socks="".

    assert night_veil_socks_proxy_url(manifest, CombShieldLevel.NIGHT_VEIL) is None


def test_night_veil_socks_proxy_url_reads_the_configured_tor_socks(tmp_path: Path) -> None:
    manifest = _manifest_with_tor_socks(tmp_path, "socks5://127.0.0.1:9050")

    assert (
        night_veil_socks_proxy_url(manifest, CombShieldLevel.NIGHT_VEIL)
        == "socks5://127.0.0.1:9050"
    )


def test_fail_closed_night_veil_probe_raises_a_clear_error() -> None:
    with pytest.raises(NotImplementedError, match="no real probe wired up"):
        _fail_closed_night_veil_probe(make_cell())
