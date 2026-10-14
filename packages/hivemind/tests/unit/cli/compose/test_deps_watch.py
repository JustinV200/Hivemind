"""Tests for hivemind.cli.compose.deps.watch: what each Warden watches its idle Cell with.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/deps/watch.py (codingrules section 3). The Hive Stand's
    Warden comes from a real `build_hive` over a real `builders.cli.fake_manifest`; the settings
    are read from `[supervision.watch]` as the manifest wrote them.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.deps.watch for the module under test.
    - tests/unit/cli/compose/swarm/test_device_warden.py for the device side of the same wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from builders.swarm import make_device_party, make_swarm_node

from hivemind.cell import hive_stand_cell_id
from hivemind.cli.compose import Hive, build_hive
from hivemind.cli.compose.deps import HiveParts
from hivemind.cli.compose.deps.watch import (
    device_id_of_cell,
    device_watch,
    hive_stand_watch,
    watch_settings,
)
from hivemind.manifest import ManifestError, load_manifest
from hivemind.swarm import GatewayHub, PollenWatchProbe
from hivemind.wardens.watch import SessionWatchProbe, WatchDeps, WatchSettings
from waggle.clock import FakeClock, SystemClock
from waggle.ids import IdKind, new_cell_id, parse_id

_TABLE = (
    "interval_s = 0.25\nretention_s = 7200.0\npatrol_interval_s = 2.5\n"
    "cpu_share_alarm = 0.75\ndisk_fill_alarm = 0.9\nroots = ['/var/log', '/srv/app']\n"
)


def _hive(tmp_path: Path, watch_table: str = "") -> Hive:
    """A Hive over a manifest that writes `[supervision.watch]` as given."""
    path = fake_manifest(tmp_path, tuning=ManifestTuning(watch_table=watch_table))
    return build_hive(load_manifest(path, {}), environ={}, clock=SystemClock())


def _parts(hive: Hive) -> HiveParts:
    """The shared parts `hive` was built from."""
    return HiveParts(
        manifest=hive.manifest,
        registry=hive.registry,
        fanner=hive.fanner,
        stores=hive.stores,
        clock=hive.clock,
        enforcer=hive.enforcer,
        royal_jelly=hive.royal_jelly,
    )


def test_the_settings_are_the_manifests_watch_table(tmp_path: Path) -> None:
    hive = _hive(tmp_path, _TABLE)

    settings = watch_settings(hive.manifest.supervision.watch, ("/var/log", "/srv/app"))

    assert settings == WatchSettings(
        interval_s=0.25,
        retention_s=7_200.0,
        patrol_interval_s=2.5,
        cpu_share_alarm=0.75,
        disk_fill_alarm=0.9,
        roots=("/var/log", "/srv/app"),
    )


def test_a_manifest_that_says_nothing_gets_the_documented_defaults(tmp_path: Path) -> None:
    settings = watch_settings(_hive(tmp_path).manifest.supervision.watch, ())

    assert (settings.interval_s, settings.retention_s, settings.patrol_interval_s) == (
        300.0,
        86_400.0,
        3_600.0,
    )
    assert settings.roots == ()


def _device_watch(hive: Hive, **overrides: object) -> WatchDeps:
    """The WatchDeps a device's Warden is built with, for a node with `overrides`."""
    clock = FakeClock()
    node = make_swarm_node(make_device_party(clock), clock, **overrides)
    return device_watch(_parts(hive), node, GatewayHub(clock))


def test_a_devices_settings_never_carry_the_hive_stands_directories(tmp_path: Path) -> None:
    hive = _hive(tmp_path, _TABLE)

    watch = _device_watch(hive)

    # `_TABLE` names /var/log and /srv/app for the Hive Stand's own Warden: those paths are on
    # this machine, not on the device, and the operator has named none for it.
    assert watch.settings.roots == ()
    assert watch.settings.interval_s == 0.25  # The numbers are the Hive's, shared by every Warden.
    assert isinstance(watch.probe, PollenWatchProbe)
    assert watch.bind_session is None


def test_a_devices_settings_carry_the_directories_the_operator_named_for_it(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path, _TABLE)

    watch = _device_watch(hive, watch_roots=("/srv/device-logs", "/var/lib/device-data"))

    assert watch.settings.roots == ("/srv/device-logs", "/var/lib/device-data")


def test_a_directory_the_devices_capabilities_do_not_cover_is_not_asked_for(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path, _TABLE)

    # The packet would refuse a whole look at the device for the second directory.
    watch = _device_watch(
        hive,
        watch_roots=("/srv/allowed/logs", "/srv/other"),
        granted_capabilities=("fs:read:/srv/allowed/**", "watch:cell_whatever"),
    )

    assert watch.settings.roots == ("/srv/allowed/logs",)


def test_the_hive_stands_device_id_is_its_cell_ids_ulid_and_a_valid_device_id() -> None:
    cell_id = new_cell_id(FakeClock())

    device_id = device_id_of_cell(cell_id)

    assert device_id == "device_" + cell_id.removeprefix("cell_")
    assert parse_id(device_id, IdKind.DEVICE) == device_id
    assert device_id_of_cell(cell_id) == device_id  # Pure: the same on every run.


def test_the_hive_stands_warden_watches_through_its_own_session(tmp_path: Path) -> None:
    hive = _hive(tmp_path, _TABLE)

    watch = hive_stand_watch(_parts(hive))

    assert isinstance(watch.probe, SessionWatchProbe)
    assert watch.bind_session == watch.probe.use_session  # The Warden lends its session to it.
    assert watch.settings.roots == ("/var/log", "/srv/app")
    assert watch.schedule.is_seeded is False


def test_every_hive_stand_warden_gets_a_schedule_of_its_own(tmp_path: Path) -> None:
    parts = _parts(_hive(tmp_path))

    first, second = hive_stand_watch(parts), hive_stand_watch(parts)

    assert first.schedule is not second.schedule
    assert first.probe is not second.probe


def test_the_built_hives_warden_carries_the_watch_deps(tmp_path: Path) -> None:
    hive = _hive(tmp_path, _TABLE)

    deps = hive.warden._deps

    assert deps.watch is not None
    assert isinstance(deps.watch.probe, SessionWatchProbe)
    assert deps.watch.settings.patrol_interval_s == 2.5
    assert hive_stand_cell_id(hive.manifest.hive.node_id).startswith("cell_")


def test_a_manifest_with_a_relative_directory_does_not_load(tmp_path: Path) -> None:
    path = fake_manifest(tmp_path, tuning=ManifestTuning(watch_table="roots = ['relative/dir']\n"))

    with pytest.raises(ManifestError, match="absolute"):
        load_manifest(path, {})
