"""Tests for hivemind.cli.in_cell.borrowed.watch: a device Warden watching its own machine.

The Warden that lives on a colonized device watches the machine it runs on while no bee works
(ADR-0054, roadmap step 11.10), the way every Real Cell's Warden does, but through a session that
belongs to no lease, because it gives its lease back whenever it is idle. These tests build the
watch mode from a real device configuration and look at the machine the test runs on: the
processes and resource use it can see, the log lines of a directory the packet named, and the one
thing it must never do, which is write anything to the device.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/watch.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.watch for the module under test.
    - tests/e2e/test_phase11_exit_watch_colonized.py for the same on a real device in a container.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from builders.borrowed import BorrowedKit, make_kit, watch
from builders.swarm import make_device_party, make_swarm_node

from hivemind.cell.tiers import AccessLevel
from hivemind.cli.in_cell.borrowed import (
    BorrowedConfig,
    build_borrowed_config,
    device_watch,
    readable_watch_roots,
)
from hivemind.cli.in_cell.borrowed import watch as borrowed_watch
from hivemind.manifest import read_in_cell_env
from hivemind.swarm.enrolment import node_update_for
from hivemind.wardens.watch import SessionWatchProbe
from waggle.clock import FakeClock, SystemClock
from waggle.messages.swarm import ObservationKind

_URL = "ws://127.0.0.1:8720"
_CLOCK = SystemClock()
_LINUX = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="The collectors are Linux's."
)


def _config(kit: BorrowedKit) -> BorrowedConfig:
    """The device configuration for `kit`, with its state directory made, as the stores would."""
    config = build_borrowed_config(read_in_cell_env(kit.environ), _CLOCK)
    config.state_dir.mkdir(parents=True)
    return config


def _names(directory: Path) -> set[str]:
    """Every file and directory under `directory`, as paths relative to it."""
    return {str(path.relative_to(directory)) for path in directory.rglob("*")}


def _write(path: Path, text: str) -> None:
    """Write a file for a watched directory to hold, making the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_warden_looks_through_a_probe_that_is_lent_no_lease(tmp_path: Path) -> None:
    deps = device_watch(_config(make_kit(tmp_path, _URL)), _CLOCK)

    assert isinstance(deps.probe, SessionWatchProbe)
    # The Warden's own session comes and goes with each task: nothing is lent to the probe.
    assert deps.bind_session is None


def test_the_settings_are_the_device_manifests_numbers_and_the_directories_it_may_read(
    tmp_path: Path,
) -> None:
    table = "[supervision.watch]\ninterval_s = 7\nretention_s = 600\npatrol_interval_s = 120\n"
    kit = watch(make_kit(tmp_path, _URL, supervision=table), "/srv/data", "/var/log/app")

    settings = device_watch(_config(kit), _CLOCK).settings

    assert (settings.interval_s, settings.retention_s, settings.patrol_interval_s) == (
        7.0,
        600.0,
        120.0,
    )
    assert settings.roots == ("/srv/data", "/var/log/app")


def test_with_no_directory_named_only_the_machine_itself_is_watched(tmp_path: Path) -> None:
    assert device_watch(_config(make_kit(tmp_path, _URL)), _CLOCK).settings.roots == ()


@_LINUX
async def test_it_sees_the_processes_and_resources_of_its_machine_and_writes_nothing(
    tmp_path: Path,
) -> None:
    config = _config(make_kit(tmp_path, _URL))
    probe = device_watch(config, _CLOCK).probe
    before = _names(tmp_path)

    observation = await probe.observe(
        (ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE), (), None
    )

    assert observation.reason == "Observed."
    assert observation.processes and observation.resources is not None
    assert _names(tmp_path) == before  # No scratch, no lease directory, not a file.


@_LINUX
async def test_it_reads_the_log_lines_and_file_changes_of_a_directory_the_packet_named(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "app-logs"
    _write(logs / "app.log", "starting up\nthe database went away\n")
    config = _config(watch(make_kit(tmp_path, _URL), str(logs)))
    deps = device_watch(config, _CLOCK)
    before = _names(tmp_path)

    observation = await deps.probe.observe(
        (ObservationKind.LOGS, ObservationKind.FILE_CHANGES), deps.settings.roots, None
    )

    assert [line.line for line in observation.log_lines] == [
        "starting up",
        "the database went away",
    ]
    assert [change.path for change in observation.file_changes] == [str(logs / "app.log")]
    assert _names(tmp_path) == before


def test_the_warden_holds_itself_to_what_its_access_level_lets_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = watch(make_kit(tmp_path, _URL), "/srv/data", "/var/log")
    config = _config(kit)
    # Every level grants `fs:read:**` today; a narrower grant must shorten the list, not fail it.
    monkeypatch.setattr(
        borrowed_watch, "granted_capabilities", lambda level, cell_id: ("fs:read:/srv/**",)
    )

    assert readable_watch_roots(config) == ("/srv/data",)
    assert device_watch(config, _CLOCK).settings.roots == ("/srv/data",)
    assert config.watch_roots == ("/srv/data", "/var/log")  # What it reports back is unchanged.


@pytest.mark.parametrize("level", list(AccessLevel))
def test_the_warden_drops_nothing_the_queen_sends_at_any_level(
    tmp_path: Path, level: AccessLevel
) -> None:
    # Dropping a directory the Queen sent would make the Warden's report differ from hers for
    # ever, and the device would be restarted until it fell back to Level 0.
    clock = FakeClock()
    named = ("/srv/data", "/var/log/app", "C:\\Logs")
    node = make_swarm_node(make_device_party(clock), clock, access_level=level, watch_roots=named)
    sent = node_update_for(node, "connected").watch_roots
    kit = watch(make_kit(tmp_path, _URL, access_level=level.name), *sent)

    assert readable_watch_roots(_config(kit)) == sent == named
