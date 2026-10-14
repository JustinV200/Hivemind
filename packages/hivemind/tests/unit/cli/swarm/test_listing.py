"""Tests for hivemind.cli.swarm.listing and .render: `hive swarm list`.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/listing.py and render.py (codingrules section 3). Drives the
    real typer app against a real manifest and registry file (`harness`): devices are enrolled
    through the registry, the table and `--json` are read back, and the properties asserted are
    the command's own: every enrolled device is a row (a revoked one too, marked), the two outputs
    carry the same fields, a pending rung request shows, and nothing a device could be
    impersonated with is ever printed.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.listing and hivemind.cli.swarm.render for the modules under test.
"""

from __future__ import annotations

import json
from pathlib import Path

from unit.cli.swarm.harness import enrol, invoke, make_hive

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import FakeClock
from waggle.messages.swarm import RuntimeLevel


def test_list_prints_one_row_per_device_with_its_ids_rung_status_terms_and_last_seen(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(
        manifest,
        label="kitchen-pi",
        access_level=AccessLevel.READ_ONLY,
        comb_shield=CombShieldLevel.PROPOLIS,
        last_seen_at=FakeClock().now(),
    )

    result = invoke(manifest, "list")

    assert result.exit_code == 0, result.stderr
    header, row = result.stdout.splitlines()
    assert header.split() == [
        "DEVICE",
        "LABEL",
        "CELL",
        "LEVEL",
        "WANTS",
        "STATUS",
        "ACCESS",
        "TIER",
        "LAST",
        "SEEN",
    ]
    assert row.split()[:8] == [
        node.device_id,
        "kitchen-pi",
        node.cell_id,
        "GATEWAY",
        "-",
        "ONLINE",
        "READ_ONLY",
        "PROPOLIS",
    ]
    assert row.split()[8].endswith("Z")  # A time, in UTC.


def test_list_json_prints_the_same_fields_as_an_array(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "list", "--json")

    (row,) = json.loads(result.stdout)
    assert set(row) == {
        "device_id",
        "label",
        "cell_id",
        "level",
        "desired_level",
        "status",
        "access_level",
        "comb_shield",
        "last_seen_at",
    }
    assert row["device_id"] == node.device_id and row["label"] == "kitchen-pi"
    assert row["cell_id"] == node.cell_id and row["level"] == "GATEWAY"
    assert row["desired_level"] is None and row["status"] == "ONLINE"


def test_list_keeps_a_revoked_device_in_the_table_marked_revoked(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    live = enrol(manifest, label="attic-pc")
    gone = enrol(
        manifest, label="kitchen-pi", status=NodeStatus.REVOKED, revoked_at=FakeClock().now()
    )

    result = invoke(manifest, "list", "--json")

    rows = {row["device_id"]: row["status"] for row in json.loads(result.stdout)}
    assert rows == {live.device_id: "ONLINE", gone.device_id: "REVOKED"}


def test_list_shows_the_rung_the_operator_asked_for(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", level=RuntimeLevel.COLONIZED)
    invoke(manifest, "promote", "kitchen-pi")

    result = invoke(manifest, "list")

    row = result.stdout.splitlines()[1].split()
    assert row[0] == node.device_id and row[3:5] == ["COLONIZED", "NUC"]


def test_list_of_an_empty_registry_prints_the_header_and_says_how_to_enrol_on_stderr(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)

    table = invoke(manifest, "list")
    as_json = invoke(manifest, "list", "--json")

    assert table.exit_code == 0 and len(table.stdout.splitlines()) == 1
    assert "hive swarm invite" in table.stderr
    assert json.loads(as_json.stdout) == []


def test_list_never_prints_a_key_a_hostname_or_a_capability_string(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", hostname="secret-host")

    for flags in ((), ("--json",)):
        shown = invoke(manifest, "list", *flags).stdout
        assert node.public_key_hex not in shown and "secret-host" not in shown
        assert "exec:" not in shown and "fs:read" not in shown


def test_list_works_while_the_swarm_is_off_because_it_only_reads_the_registry(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path, enabled=False)

    result = invoke(manifest, "list")

    assert result.exit_code == 0
