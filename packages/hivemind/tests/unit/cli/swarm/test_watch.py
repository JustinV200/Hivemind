"""Tests for hivemind.cli.swarm.watch: `hive swarm watch NODE [DIRECTORY]... [--clear]`.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/watch.py (codingrules section 3). Drives the real typer app
    against a real manifest and registry file (`harness`); each act is read back through the real
    registry and trail the way a running Hive would find it. The properties are the command's:
    naming directories writes them in normal form with a `swarm.watch_changed` event as `human`,
    naming none shows what is set and writes nothing, `--clear` stops the file watching, a repeat
    writes and records nothing, and a refusal exits 1 with its stable code and leaves registry and
    trail as they were.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.watch for the module under test.
    - tests/unit/swarm/operator/test_desk_watch.py for the same rules at the desk.
"""

from __future__ import annotations

from pathlib import Path

from unit.cli.swarm.harness import enrol, invoke, make_hive, stored, trail_events

_KIND = "swarm.watch_changed"


def test_naming_directories_stores_them_in_normal_form_and_records_it(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "watch", "kitchen-pi", "/var/log/pihole/", "/srv//data")

    assert result.exit_code == 0, result.stderr
    assert "watches 2 directories: /var/log/pihole, /srv/data." in result.stdout
    assert "rebuilds the device's Warden" in result.stdout
    assert stored(manifest, node).watch_roots == ("/var/log/pihole", "/srv/data")
    (event,) = trail_events(manifest, _KIND)
    assert event.actor == "human" and event.subject_id == node.device_id
    assert event.payload["roots"] == ["/var/log/pihole", "/srv/data"]


def test_naming_one_directory_reads_in_the_singular(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "watch", "kitchen-pi", "/srv/data")

    assert "watches 1 directory: /srv/data." in result.stdout


def test_naming_none_shows_what_is_set_and_writes_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", watch_roots=("/srv/data",))

    shown = invoke(manifest, "watch", "kitchen-pi")

    assert shown.exit_code == 0, shown.stderr
    assert "watches 1 directory: /srv/data." in shown.stdout
    assert stored(manifest, node).watch_roots == ("/srv/data",)
    assert trail_events(manifest, _KIND) == []


def test_a_device_with_none_named_says_it_reads_processes_and_resources_only(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi")

    shown = invoke(manifest, "watch", "kitchen-pi")

    assert "watches no directory" in shown.stdout and "processes and resource use" in shown.stdout


def test_clear_stops_the_file_watching_and_records_it(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", watch_roots=("/srv/data", "/var/log/app"))

    result = invoke(manifest, "watch", "kitchen-pi", "--clear")

    assert result.exit_code == 0, result.stderr
    assert "watches no directory" in result.stdout
    assert stored(manifest, node).watch_roots == ()
    (event,) = trail_events(manifest, _KIND)
    assert (event.payload["from_roots"], event.payload["roots"]) == (2, [])


def test_naming_directories_together_with_clear_is_bad_input(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", watch_roots=("/srv/data",))

    result = invoke(manifest, "watch", "kitchen-pi", "/srv/other", "--clear")

    assert result.exit_code == 2
    assert stored(manifest, node).watch_roots == ("/srv/data",)


def test_naming_what_it_already_watches_says_so_and_records_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", watch_roots=("/srv/data",))

    again = invoke(manifest, "watch", "kitchen-pi", "/srv/data")

    assert again.exit_code == 0 and "nothing changed" in again.stdout
    assert trail_events(manifest, _KIND) == []


def test_a_directory_that_is_not_absolute_exits_1_with_its_code_and_changes_nothing(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", watch_roots=("/kept",))

    result = invoke(manifest, "watch", "kitchen-pi", "/fine", "logs")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.watch_root_refused")
    assert "directory 2" in result.stderr
    assert stored(manifest, node).watch_roots == ("/kept",)
    assert trail_events(manifest, _KIND) == []


def test_a_revoked_device_is_not_given_directories(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    invoke(manifest, "revoke", "kitchen-pi")

    result = invoke(manifest, "watch", "kitchen-pi", "/srv/data")

    assert result.exit_code == 1, result.stderr
    assert stored(manifest, node).watch_roots == ()
    assert trail_events(manifest, _KIND) == []


def test_a_name_nobody_has_exits_1(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "watch", "nobody", "/srv/data")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.node_unknown")
