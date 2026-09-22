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

from builders.cli import fake_manifest

from hivemind.cli.compose.virtual_cells import build_virtual_cells, docker_gateway_url
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.placement import VirtualCellsSection
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock


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


async def test_build_virtual_cells_registers_fake_always(tmp_path: Path) -> None:
    manifest = _load_with_virtual_cells(tmp_path)  # backend = "fake"
    clock = FakeClock()

    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)

    assert parts is not None
    # Only "fake" here: docker/qemu are never registered unless actually selected (see
    # hivemind.cli.compose.virtual_cells._build_registry's own docstring for why).
    assert set(parts.registry.names()) == {"fake"}


async def test_build_virtual_cells_registers_only_the_selected_backend_plus_fake(
    tmp_path: Path,
) -> None:
    manifest = _load_with_virtual_cells(tmp_path, backend="qemu")
    clock = FakeClock()

    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)

    assert parts is not None
    assert set(parts.registry.names()) == {"fake", "qemu"}


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
