"""Tests for hivemind.cli.in_cell.borrowed.config: what a device Warden watches its machine by.

A colonized device's Warden watches its own machine while no bee works (ADR-0054, roadmap step
11.10). The numbers it watches by come from the device manifest's `[supervision.watch]`, the
Hive's shipped ones when it has none, and the directories it reads logs and file changes from come
from the packet in `HIVEMIND_WATCH_ROOTS`, the operator's list for this device as it was last
told. The variable is the one a device may leave out, and when it is there it must be what the
wire allows, because the Warden reports it back to the Queen exactly as it holds it.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/config.py (codingrules section 3), split by feature
    (14.2) from test_config.py, which covers the rest of the environment.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.config for the module under test.
    - tests/contracts/test_pollen_warden_env.py for the check that pollen sets what this reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.borrowed import BorrowedKit, make_kit, watch

from hivemind.cli.in_cell.borrowed import BorrowedConfig, build_borrowed_config
from hivemind.common.errors import ConfigurationError
from hivemind.manifest import ManifestError, WatchTuning, read_in_cell_env
from waggle.clock import SystemClock

_URL = "ws://127.0.0.1:8720"
_TABLE = (
    "[supervision.watch]\ninterval_s = 7\nretention_s = 600\npatrol_interval_s = 120\n"
    "cpu_share_alarm = 0.8\ndisk_fill_alarm = 0.9\n"
)


def _config(kit: BorrowedKit, **changes: str) -> BorrowedConfig:
    """Build the config for `kit`'s environment with the variables in `changes` set."""
    return build_borrowed_config(read_in_cell_env({**kit.environ, **changes}), SystemClock())


def test_a_manifest_that_says_nothing_of_watching_gets_the_numbers_the_hive_ships(
    tmp_path: Path,
) -> None:
    config = _config(make_kit(tmp_path, _URL))

    assert config.watch.model_dump() == WatchTuning().model_dump()


def test_the_manifests_watch_table_sets_the_numbers_the_warden_watches_by(tmp_path: Path) -> None:
    config = _config(make_kit(tmp_path, _URL, supervision=_TABLE))

    watch_numbers = config.watch
    assert (watch_numbers.interval_s, watch_numbers.retention_s) == (7.0, 600.0)
    assert watch_numbers.patrol_interval_s == 120.0
    assert (watch_numbers.cpu_share_alarm, watch_numbers.disk_fill_alarm) == (0.8, 0.9)


def test_a_manifest_that_names_directories_of_its_own_is_refused_before_the_warden_starts(
    tmp_path: Path,
) -> None:
    table = '[supervision.watch]\nroots = ["/var/log"]\n'

    with pytest.raises(ManifestError, match="hive swarm watch"):
        _config(make_kit(tmp_path, _URL, supervision=table))


def test_a_device_with_no_directory_named_watches_none(tmp_path: Path) -> None:
    config = _config(make_kit(tmp_path, _URL))

    assert config.watch_roots == ()  # Processes and resource use only.


def test_the_directories_the_packet_hands_over_are_kept_as_they_were_given(tmp_path: Path) -> None:
    roots = ("/srv/data", "/var/log/app", "C:\\Logs\\app", '/with "quotes" and spaces')
    kit = watch(make_kit(tmp_path, _URL), *roots)

    config = _config(kit)

    assert config.watch_roots == roots  # In order, untouched: the Warden reports them back.


def test_an_empty_list_is_no_directories(tmp_path: Path) -> None:
    assert _config(watch(make_kit(tmp_path, _URL))).watch_roots == ()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"roots": ["/var/log"]}',  # An object, not an array.
        '"/var/log"',  # A path, not an array of them.
        "[1, 2]",
        '[""]',  # A directory must have a name.
        "[" + ", ".join(f'"/d{i}"' for i in range(17)) + "]",  # One more than the wire carries.
        '["/' + "d" * 256 + '"]',  # One character more than the wire carries.
    ],
)
def test_a_list_the_wire_would_refuse_is_refused_naming_the_variable_and_nothing_else(
    tmp_path: Path, text: str
) -> None:
    kit = make_kit(tmp_path, _URL)

    with pytest.raises(ConfigurationError, match="HIVEMIND_WATCH_ROOTS") as raised:
        _config(kit, HIVEMIND_WATCH_ROOTS=text)

    assert "/var/log" not in str(raised.value)  # The operator's directories are not for a log.
