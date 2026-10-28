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
from builders.attested_cells import GatedFakeCellBackend, StubQueen, make_tier_spec
from builders.cells import make_cell
from builders.cli import fake_manifest
from builders.forage import make_footprint, make_reserve, make_source

from hivemind.cell import Cell, CombShieldLevel, TaskNeeds
from hivemind.cli.compose import virtual_cell_backends
from hivemind.cli.compose.virtual_cell_backends import night_veil_socks_proxy_url
from hivemind.cli.compose.virtual_cells import (
    _fail_closed_night_veil_probe,
    _fail_closed_propolis_probe,
    build_virtual_cells,
    docker_gateway_url,
)
from hivemind.forage.allocate import GoalBudgets, GrantInputs, grant
from hivemind.forage.map import ForageMap
from hivemind.forage.models import Abundance
from hivemind.forage.tempo import Tempo
from hivemind.hive import CellProvisionError
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import ReleaseOutcome
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.manifest.schema.placement import VirtualCellsOverwinterSection, VirtualCellsSection
from hivemind.manifest.schema.security.tiers import SecuritySection, TierProfile
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import (
    ForageView,
    Inventory,
    PlacementPolicy,
    ProvisionVirtual,
    decide,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_warden_id
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages.task import WorkerRole


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


async def test_the_composed_cell_gate_records_what_it_refuses_on_the_hives_trail(
    tmp_path: Path,
) -> None:
    manifest = _load_with_virtual_cells(tmp_path)
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)

    parts = build_virtual_cells(manifest, trail, clock)

    # Roadmap step 10.6: a forged frame or segment is the Queen's own record, for the Guard Bee,
    # made through the Night Veil boundary over the Hive's trail (a Night Veil Cell's stays veiled).
    assert parts is not None
    recorder = parts.listener._deps.recorder
    assert recorder is not None and recorder.trail is parts.night_veil.veiled
    assert parts.night_veil.veiled.durable is trail
    assert (recorder.hive_id, recorder.node_id) == (manifest.hive.id, manifest.hive.node_id)


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
    terminal, desktop = candidates[0].specs  # Roadmap step 6.12: terminal first, desktop second.
    assert (terminal.image, terminal.exoskeleton) == (manifest.virtual_cells.default_image, False)
    assert (desktop.image, desktop.exoskeleton) == ("desktop-ubuntu", True)
    # Identical resources: neither spec ever fits Forage where the other would not.
    assert desktop.model_copy(update={"image": terminal.image, "exoskeleton": False}) == terminal


async def test_every_composed_spec_carries_the_manifests_own_sub_bee_cap(tmp_path: Path) -> None:
    """Phase 5 open item 6: `[virtual_cells] max_sub_bees` is each template's cap, no constant."""
    manifest = _load_with_virtual_cells(tmp_path, max_sub_bees=7)
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None

    candidates = await parts.virtual_backend_source()

    assert [spec.capacity.max_sub_bees for spec in candidates[0].specs] == [7, 7]


async def test_a_cap_above_the_reserved_cores_is_bounded_by_forage_at_grant_time(
    tmp_path: Path,
) -> None:
    """Forage sizes a Virtual Cell's grant by its reservation, as a Real Cell's by its probe."""
    manifest = _load_with_virtual_cells(tmp_path, max_sub_bees=32, cpu_cores=2.0)
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None
    spec = (await parts.virtual_backend_source())[0].specs[0]
    # One whole core a bee, and nothing else binding: memory, seats and margin left out.
    source = make_source(
        seats=64,
        abundance=Abundance(
            seats_free=64, requests_per_minute_left=None, tokens_per_minute_left=None
        ),
    )
    inputs = GrantInputs(
        cell_capacity=spec.capacity,
        role=WorkerRole.DRONE,
        footprint=make_footprint(cpu_cores=1.0, memory_bytes=0),
        tempo=Tempo(),
        map=ForageMap([source], clock=clock),
        reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.0),
        budgets=GoalBudgets(spend_cap_usd=100.0, token_budget=1_000_000, max_sub_bees=64),
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        task_id=None,
        grant_id=new_grant_id(clock),
        now=clock.now(),
        ttl_s=300.0,
    )

    granted = grant(inputs)

    assert spec.capacity.max_sub_bees == 32
    assert granted.max_sub_bees == 2  # The two reserved cores, never the manifest's 32.


async def test_virtual_backend_source_threads_the_manifests_own_exoskeleton_image(
    tmp_path: Path,
) -> None:
    manifest = _load_with_virtual_cells(tmp_path, exoskeleton_image="hivemind/desktop-ubuntu:dev")
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None

    candidates = await parts.virtual_backend_source()

    assert candidates[0].specs[1].image == "hivemind/desktop-ubuntu:dev"


@pytest.mark.parametrize(
    ("needs", "image"),
    [(TaskNeeds(), "base-ubuntu"), (TaskNeeds(exoskeleton=True, audio=True), "desktop-ubuntu")],
)
async def test_placement_over_the_composed_specs_boots_the_desktop_image_only_when_needed(
    tmp_path: Path, needs: TaskNeeds, image: str
) -> None:
    """The composed feed and the real decide together: roadmap step 6.12 end to end, minus I/O."""
    manifest = _load_with_virtual_cells(tmp_path)
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None
    inventory = Inventory(virtual_backends=await parts.virtual_backend_source())

    placement = decide(
        needs, inventory, ForageView(footprint=make_footprint()), PlacementPolicy(prefer="virtual")
    )

    assert isinstance(placement, ProvisionVirtual)
    assert placement.spec.image == image
    assert placement.spec.exoskeleton is needs.exoskeleton


async def test_dormant_cell_source_starts_empty(tmp_path: Path) -> None:
    manifest = _load_with_virtual_cells(tmp_path)
    clock = FakeClock()
    parts = build_virtual_cells(manifest, MemoryPheromoneTrail(clock), clock)
    assert parts is not None

    assert await parts.dormant_cell_source() == ()


@pytest.mark.parametrize("trigger", ["placement", "release"])
async def test_a_dormant_cell_past_its_deadline_is_evicted_while_the_hive_runs(
    tmp_path: Path, trigger: str
) -> None:
    """Roadmap 13.5: expiry is enforced on every placement pass and release, not only at start."""
    overwinter = VirtualCellsOverwinterSection(enabled=True, max_dormant_s=60.0)
    manifest = _load_with_virtual_cells(tmp_path, overwinter=overwinter)
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    parts = build_virtual_cells(manifest, trail, clock)
    assert parts is not None
    [backend] = await parts.virtual_backend_source()
    lifecycle, spec = parts.lifecycle, backend.specs[0]
    stale = await _granted(lifecycle, spec)
    await lifecycle.release(stale.id, _REUSABLE)
    await lifecycle.overwinter(stale.id, scrub=_no_scrub)
    clock.advance(61.0)  # Past its own dormant_until.

    if trigger == "release":  # Another Cell's release looks at the pool first.
        await lifecycle.release((await _granted(lifecycle, spec)).id, _REUSABLE)
    else:  # The Queen's dormant feed, read on every placement pass and liveness tick.
        await parts.dormant_cell_source()

    [evicted] = await trail.query(TrailQuery(subject_id=stale.id, kind="cell.evicted"))
    assert evicted.payload["reason"] == "dormant_until expired"
    assert stale.id not in [cell.cell_id for cell in await parts.dormant_cell_source()]


_REUSABLE = ReleaseOutcome(
    rolled_back_whole_cell=False, has_block_wax=False, single_use=False, backend_can_pause=True
)


async def _granted(lifecycle: CellLifecycle, spec: VirtualCellSpec) -> Cell:
    """Provision a Cell from `spec` on the fake backend and take it to GRANTED."""
    cell = await lifecycle.provision(spec, "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    return cell


async def _no_scrub(cell: object) -> None:
    """A Scrubber that does nothing: what scrubbing does is not this test's concern."""


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
    # A wildcard bind is what a reachable listener uses; a Cell must never be handed it as a host.
    assert docker_gateway_url("ws://0.0.0.0:58753") == "ws://host.docker.internal:58753"
    assert docker_gateway_url("ws://[::]:58753") == "ws://host.docker.internal:58753"
    assert docker_gateway_url("wss://hive.example:8710") == "wss://hive.example:8710"


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


def test_fail_closed_propolis_probe_raises_a_clear_error() -> None:
    with pytest.raises(NotImplementedError, match="no real probe wired up"):
        _fail_closed_propolis_probe(make_cell())


@pytest.mark.parametrize("tier", [CombShieldLevel.PROPOLIS, CombShieldLevel.NIGHT_VEIL])
async def test_a_hive_built_here_readies_no_cell_of_a_tier_it_cannot_attest(
    tmp_path: Path, tier: CombShieldLevel
) -> None:
    # The composition root's own default: no session path to a Virtual Cell exists, so each
    # attested tier's probe factory fails closed and no Cell of that tier becomes ready.
    manifest = _load_with_virtual_cells(tmp_path)
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    parts = build_virtual_cells(manifest, trail, clock)
    assert parts is not None
    wardens: list[WardenLink] = []
    parts.registry.register("gated", lambda: GatedFakeCellBackend(clock, parts.gate, wardens))
    parts.provider.bind_queen(StubQueen(wardens))
    placement = ProvisionVirtual(spec=make_tier_spec(tier), backend="gated", reason="test")

    with pytest.raises(CellProvisionError, match="no real probe wired up"):
        await parts.provider.acquire(placement, None)  # type: ignore[arg-type]

    kinds = [event.kind for event in await trail.query(TrailQuery())]
    assert "cell.ready" not in kinds
    assert "cell.destroyed" in kinds
