"""Unit tests for hivemind.cli.compose.exoskeleton: a Warden's Exoskeleton wiring.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/cli/compose/exoskeleton.py
    (codingrules section 3). Covers the three composition roots' wiring (the Hive Stand's, through
    a real `build_hive` on a real SQLite file, a Virtual Cell's in-Cell one and a colonized
    device's), the browser launcher with and without the browser extra, ears with and without a
    transcriber that can hear, the retention sweep, and the Night Veil purge of a torn-down Cell's
    recordings.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.exoskeleton for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.llm import make_fanner_deps
from builders.recordings import RECORDING_START, make_recorded_action, make_recording_info
from builders.wardens import make_warden_deps

import hivemind.cli.compose.exoskeleton as wiring_module
from hivemind.cli.compose import build_hive
from hivemind.cli.compose.exoskeleton import (
    DEVICE_RECORDING_KEEP,
    ExoskeletonWiring,
    build_browser_launcher,
    device_exoskeleton,
    exoskeleton_config,
    in_cell_exoskeleton,
    open_hive_recordings,
    resolve_ears,
    running_as_root,
)
from hivemind.cli.stores import build_registry
from hivemind.common.sqlite import connect
from hivemind.exoskeleton import ExoskeletonConfig, ScreenSize
from hivemind.exoskeleton.attach import DEFAULT_READY_TIMEOUT_S, DEFAULT_SCREEN
from hivemind.exoskeleton.browser import ChromiumLauncher
from hivemind.exoskeleton.recorder import InMemoryRecordingStore, SqliteRecordingStore
from hivemind.forage import ModelSlot
from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import Effort
from hivemind.llm import (
    DirectTranscriptionGate,
    Fanner,
    FannerTranscriptionGate,
    ProviderConfig,
    ProviderRegistry,
    RegistryDeps,
    default_factories,
)
from hivemind.manifest import load_manifest
from hivemind.manifest.schema import ExoskeletonSection
from waggle.clock import FakeClock

_MANIFESTS_DIR = Path(__file__).resolve().parents[6] / "docs" / "manifests"
_HAS_BROWSER_EXTRA = importlib.util.find_spec("playwright") is not None


def _registry_without_a_transcriber(clock: FakeClock) -> ProviderRegistry:
    """A registry binding only WARDEN, like the in-Cell fallback given no slot table."""
    providers = {"fake": ProviderConfig(kind="fake", base_url="", default_model=None)}
    warden = SlotBinding(
        key="warden", provider="fake", model="m", fallback=None, effort=Effort.MEDIUM
    )
    deps = RegistryDeps(factories=default_factories(), environ={}, clock=clock)
    return ProviderRegistry(providers, (warden,), offline=False, deps=deps)


def test_the_manifest_defaults_mirror_attachs_own() -> None:
    section = ExoskeletonSection()

    assert ScreenSize(section.screen_width, section.screen_height) == DEFAULT_SCREEN
    assert section.ready_timeout_s == DEFAULT_READY_TIMEOUT_S
    assert exoskeleton_config(section, browser_sandbox=True) == ExoskeletonConfig()


def test_exoskeleton_config_converts_the_section_and_takes_the_sandbox_from_the_caller() -> None:
    section = ExoskeletonSection(screen_width=1920, screen_height=1080, ready_timeout_s=45.0)

    config = exoskeleton_config(section, browser_sandbox=False)

    assert config == ExoskeletonConfig(
        screen=ScreenSize(1920, 1080), ready_timeout_s=45.0, browser_sandbox=False
    )


def test_running_as_root_follows_the_effective_uid() -> None:
    if sys.platform == "win32":
        assert running_as_root() is False
    else:
        assert running_as_root() == (os.geteuid() == 0)


def test_the_launcher_is_chromium_where_the_browser_extra_is_installed() -> None:
    if not _HAS_BROWSER_EXTRA:
        pytest.skip("the hivemind[browser] extra (Playwright) is not installed here")

    launcher = build_browser_launcher(FakeClock(), ScreenSize(1600, 900))

    assert isinstance(launcher, ChromiumLauncher)


def test_there_is_no_launcher_without_the_browser_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stand-in for a Hive installed without hivemind[browser]: the module it looks for is absent.
    monkeypatch.setattr(wiring_module, "BROWSER_EXTRA_MODULE", "hivemind_no_such_browser_extra")

    assert build_browser_launcher(FakeClock(), DEFAULT_SCREEN) is None


def test_ears_put_the_transcriber_chain_behind_the_given_gate(tmp_path: Path) -> None:
    clock = FakeClock()
    registry = build_registry(load_manifest(fake_manifest(tmp_path), {}), {}, clock)
    gate = DirectTranscriptionGate()

    ears = resolve_ears(registry, gate)

    assert ears is not None and ears.gate is gate
    assert ears.bound.slot is ModelSlot.TRANSCRIBER


def test_there_are_no_ears_when_the_transcriber_cannot_hear() -> None:
    # minimal.toml binds the transcriber to anthropic, a chat-only kind.
    registry = build_registry(load_manifest(_MANIFESTS_DIR / "minimal.toml"), {}, FakeClock())

    assert resolve_ears(registry, DirectTranscriptionGate()) is None


def test_there_are_no_ears_when_no_transcriber_is_bound() -> None:
    registry = _registry_without_a_transcriber(FakeClock())

    assert resolve_ears(registry, DirectTranscriptionGate()) is None


def test_the_hive_stands_warden_is_equipped_by_build_hive(tmp_path: Path) -> None:
    # Arrange: a real build over the manifest's own SQLite file, so the durable store is opened.
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock), {})

    hive = build_hive(manifest, environ={}, clock=clock)

    deps = hive.warden._deps
    assert isinstance(deps.recording_store, SqliteRecordingStore)
    assert deps.recording_store is hive.stores.recordings
    assert deps.ears is not None and isinstance(deps.ears.gate, FannerTranscriptionGate)
    assert deps.exoskeleton_config == ExoskeletonConfig(browser_sandbox=not running_as_root())
    assert isinstance(deps.browser_launcher, ChromiumLauncher) is _HAS_BROWSER_EXTRA


def test_the_in_cell_wiring_keeps_recordings_in_memory_and_drops_chromiums_sandbox() -> None:
    clock = FakeClock()

    wiring = in_cell_exoskeleton(
        _registry_without_a_transcriber(clock), Fanner(make_fanner_deps(clock)), clock
    )

    assert isinstance(wiring.recordings, InMemoryRecordingStore)
    # The Cell is the sandbox, and its loopback is its own container's (ADR-0059).
    assert wiring.config == ExoskeletonConfig(browser_sandbox=False, private_loopback=True)
    assert wiring.ears is None
    assert isinstance(wiring.launcher, ChromiumLauncher) is _HAS_BROWSER_EXTRA


def test_the_in_cell_wirings_ears_hear_through_the_cells_own_fanner(tmp_path: Path) -> None:
    # Phase 6 handoff 4.5.3: a Cell's transcriptions are metered and recorded like its chat calls.
    clock = FakeClock()
    registry = build_registry(load_manifest(fake_manifest(tmp_path), {}), {}, clock)
    fanner = Fanner(make_fanner_deps(clock))

    wiring = in_cell_exoskeleton(registry, fanner, clock)

    assert wiring.ears is not None and isinstance(wiring.ears.gate, FannerTranscriptionGate)
    assert wiring.ears.gate._fanner is fanner


def test_the_device_wiring_is_the_in_cell_one_but_for_chromiums_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    registry = _registry_without_a_transcriber(clock)
    monkeypatch.setattr(wiring_module, "running_as_root", lambda: False)

    device = device_exoskeleton(registry, clock)
    in_cell = in_cell_exoskeleton(registry, Fanner(make_fanner_deps(clock)), clock)

    # A Virtual Cell is its own sandbox; a machine the Hive borrows is not, so Chromium keeps its.
    assert device.config.browser_sandbox is True and in_cell.config.browser_sandbox is False
    assert dataclasses.replace(device.config, browser_sandbox=False, private_loopback=True) == (
        in_cell.config
    )
    assert type(device.launcher) is type(in_cell.launcher)
    assert device.ears is None and in_cell.ears is None  # Nothing bound to the transcriber hears.


def test_a_device_whose_warden_runs_as_root_cannot_keep_chromiums_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(wiring_module, "running_as_root", lambda: True)

    wiring = device_exoskeleton(_registry_without_a_transcriber(clock), clock)

    # Chromium cannot start with its sandbox as root, on the Hive Stand or on a device.
    assert wiring.config.browser_sandbox is False


def test_the_device_wiring_keeps_its_recordings_in_the_wardens_memory_and_hears_directly(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    registry = build_registry(load_manifest(fake_manifest(tmp_path), {}), {}, clock)

    wiring = device_exoskeleton(registry, clock)

    # No frame of a borrowed machine's display is written to its disk: the store is a process's own.
    assert isinstance(wiring.recordings, InMemoryRecordingStore)
    assert wiring.ears is not None and isinstance(wiring.ears.gate, DirectTranscriptionGate)
    assert wiring.ears.bound.slot is ModelSlot.TRANSCRIBER
    assert wiring.config.screen == DEFAULT_SCREEN
    assert wiring.config.ready_timeout_s == DEFAULT_READY_TIMEOUT_S


async def test_a_device_wardens_store_forgets_a_recording_a_day_after_its_last_action() -> None:
    clock = FakeClock(RECORDING_START)
    wiring = device_exoskeleton(_registry_without_a_transcriber(clock), clock)
    store = wiring.recordings
    assert store is not None
    await store.open(make_recording_info("rec_old", started_at=clock.now()))
    await store.add("rec_old", make_recorded_action(at=clock.now()))

    clock.advance((DEVICE_RECORDING_KEEP + timedelta(hours=1)).total_seconds())
    await store.open(make_recording_info("rec_new", started_at=clock.now()))

    # A Warden that runs for months holds the last day's recordings, not every one it ever made.
    assert [info.recording_id for info in await store.recordings()] == ["rec_new"]


async def test_a_virtual_cells_store_forgets_nothing_because_it_lives_as_long_as_its_cell() -> None:
    clock = FakeClock(RECORDING_START)
    wiring = in_cell_exoskeleton(
        _registry_without_a_transcriber(clock), Fanner(make_fanner_deps(clock)), clock
    )
    store = wiring.recordings
    assert store is not None
    await store.open(make_recording_info("rec_old", started_at=clock.now()))

    clock.advance((DEVICE_RECORDING_KEEP + timedelta(hours=1)).total_seconds())
    await store.open(make_recording_info("rec_new", started_at=clock.now()))

    assert {info.recording_id for info in await store.recordings()} == {"rec_old", "rec_new"}


def test_applying_the_wiring_sets_the_four_fields_and_nothing_else() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    store = InMemoryRecordingStore()
    wiring = ExoskeletonWiring(
        config=ExoskeletonConfig(browser_sandbox=False), launcher=None, recordings=store, ears=None
    )

    applied = wiring.apply(deps)

    assert applied.recording_store is store
    assert applied.exoskeleton_config.browser_sandbox is False
    assert (applied.trail, applied.memory, applied.source) == (deps.trail, deps.memory, deps.source)


def test_opening_the_hives_recordings_applies_the_retention_window(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"

    async def _seed() -> None:
        store = await SqliteRecordingStore.create(connect(db), FakeClock())
        for recording_id, days in (("rec_old", 0), ("rec_new", 20)):
            at = RECORDING_START + timedelta(days=days)
            await store.open(make_recording_info(recording_id, started_at=at))
            await store.add(recording_id, make_recorded_action(at=at))

    asyncio.run(_seed())
    now = FakeClock(RECORDING_START + timedelta(days=31))

    store = open_hive_recordings(db, ExoskeletonSection(recording_retention_days=30), now)

    assert [info.recording_id for info in asyncio.run(store.recordings())] == ["rec_new"]
