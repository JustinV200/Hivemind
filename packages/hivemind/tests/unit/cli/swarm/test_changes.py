"""Tests for hivemind.cli.swarm.changes: `hive swarm revoke|access|shield|promote|demote`.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/changes.py (codingrules section 3). Drives the real typer app
    against a real manifest and registry file (`harness`); each act is read back through the real
    registry and trail the way a running Hive would find it. The properties are the commands': an
    act is written with its trail event as `human`, a repeat writes and records nothing, a refusal
    (a revoked device, Night Veil, the top of the ladder, a label that names two devices) exits 1
    with its stable code and leaves registry and trail exactly as they were, and a rung request
    records nothing until the Queen acts.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.changes for the module under test.
    - tests/unit/swarm/operator/test_desk.py for the same rules at the desk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.cli.swarm.harness import enrol, invoke, make_hive, stored, trail_events

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.swarm.node_state import NodeStatus
from waggle.messages.swarm import RuntimeLevel

_ACTS = ("swarm.revoked", "swarm.access_changed", "swarm.shield_changed")


def _acts(manifest: Path) -> list[str]:
    """The kinds of the operator's three recorded acts on the trail, oldest first."""
    return [e.kind for kind in _ACTS for e in trail_events(manifest, kind)]


@pytest.mark.parametrize("reference", ["device", "cell", "label"])
def test_revoke_names_a_device_by_its_id_its_cell_or_its_label(
    tmp_path: Path, reference: str
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    enrol(manifest, label="attic-pc")
    named = {"device": node.device_id, "cell": node.cell_id, "label": "kitchen-pi"}[reference]

    result = invoke(manifest, "revoke", named)

    assert result.exit_code == 0, result.stderr
    assert f"revoked {node.device_id} (kitchen-pi)" in result.stdout
    assert stored(manifest, node).status is NodeStatus.REVOKED
    (event,) = trail_events(manifest, "swarm.revoked")
    assert event.actor == "human" and event.subject_id == node.device_id


def test_revoke_again_says_so_and_records_nothing_more(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    invoke(manifest, "revoke", "kitchen-pi")

    again = invoke(manifest, "revoke", "kitchen-pi")

    assert again.exit_code == 0 and "already revoked" in again.stdout
    assert len(trail_events(manifest, "swarm.revoked")) == 1
    assert stored(manifest, node).status is NodeStatus.REVOKED


def test_revoke_refuses_a_label_two_devices_carry_and_changes_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    first, second = enrol(manifest, label="pi"), enrol(manifest, label="pi")

    result = invoke(manifest, "revoke", "pi")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.node_ambiguous")
    assert first.device_id in result.stderr and second.device_id in result.stderr
    assert stored(manifest, first).status is NodeStatus.ONLINE and _acts(manifest) == []


def test_revoke_of_a_name_nobody_has_exits_1_with_its_code(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "revoke", "nobody")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.node_unknown")
    assert _acts(manifest) == []


def test_access_sets_the_level_and_the_capability_strings_and_records_it(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", access_level=AccessLevel.FULL)

    result = invoke(manifest, "access", "kitchen-pi", "READ_ONLY")

    assert result.exit_code == 0, result.stderr
    assert "access level FULL -> READ_ONLY" in result.stdout
    after = stored(manifest, node)
    assert after.access_level is AccessLevel.READ_ONLY
    assert after.granted_capabilities == ("fs:read:**", f"watch:{node.cell_id}")
    (event,) = trail_events(manifest, "swarm.access_changed")
    assert event.actor == "human" and event.payload["to_level"] == "READ_ONLY"


def test_access_to_the_level_it_has_changes_and_records_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", access_level=AccessLevel.SCRATCH)

    result = invoke(manifest, "access", "kitchen-pi", "scratch")

    assert result.exit_code == 0 and "nothing changed" in result.stdout
    assert _acts(manifest) == []


def test_access_with_a_level_that_does_not_exist_is_bad_input(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "access", "kitchen-pi", "root")

    assert result.exit_code == 2
    assert stored(manifest, node).access_level is node.access_level


def test_shield_moves_a_device_to_propolis_and_records_it(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "shield", "kitchen-pi", "Propolis")

    assert result.exit_code == 0, result.stderr
    assert stored(manifest, node).comb_shield is CombShieldLevel.PROPOLIS
    (event,) = trail_events(manifest, "swarm.shield_changed")
    assert (event.payload["from_tier"], event.payload["to_tier"]) == ("MEADOW", "PROPOLIS")


def test_shield_refuses_night_veil_with_the_reason_and_changes_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "shield", "kitchen-pi", "night_veil")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.tier_refused")
    assert "only for Virtual Cells" in result.stderr and "never torn down" in result.stderr
    assert stored(manifest, node).comb_shield is CombShieldLevel.MEADOW
    assert _acts(manifest) == []


def test_shield_with_a_tier_that_does_not_exist_is_bad_input(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi")

    assert invoke(manifest, "shield", "kitchen-pi", "granite").exit_code == 2


@pytest.mark.parametrize(
    ("command", "level", "expected"),
    [
        ("promote", RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED),
        ("promote", RuntimeLevel.COLONIZED, RuntimeLevel.NUC),
        ("demote", RuntimeLevel.COLONIZED, RuntimeLevel.GATEWAY),
        ("demote", RuntimeLevel.NUC, RuntimeLevel.COLONIZED),
    ],
)
def test_promote_and_demote_write_the_adjacent_rung_as_the_wish_and_record_nothing(
    tmp_path: Path, command: str, level: RuntimeLevel, expected: RuntimeLevel
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", level=level)

    result = invoke(manifest, command, "kitchen-pi")

    assert result.exit_code == 0, result.stderr
    assert f"is on {level.name}; asked for {expected.name}" in result.stdout
    after = stored(manifest, node)
    assert (after.level, after.desired_level) == (level, expected)
    # A descent pins the device on the rung it lands on; a climb leaves the Queen free.
    assert after.ceiling is (expected if command == "demote" else None)
    assert trail_events(manifest, "swarm.promoted") == []
    assert trail_events(manifest, "swarm.demoted") == []


def test_promote_asked_twice_is_one_wish(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi")
    invoke(manifest, "promote", "kitchen-pi")

    again = invoke(manifest, "promote", "kitchen-pi")

    assert again.exit_code == 0 and "nothing changed" in again.stdout


def test_promote_past_the_top_of_the_ladder_is_refused_with_the_reason(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", level=RuntimeLevel.NUC)

    result = invoke(manifest, "promote", "kitchen-pi")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.rung_unavailable")
    assert "the top of the ladder" in result.stderr
    assert stored(manifest, node).desired_level is None


def test_demote_of_a_gateway_pins_it_so_the_queen_does_not_colonize_it_herself(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "demote", "kitchen-pi")

    assert result.exit_code == 0, result.stderr
    assert "pinned on GATEWAY, the bottom rung" in result.stdout
    after = stored(manifest, node)
    assert after.ceiling is RuntimeLevel.GATEWAY and after.desired_level is None


def test_demote_of_a_pinned_gateway_says_so_and_changes_nothing(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    invoke(manifest, "demote", "kitchen-pi")

    again = invoke(manifest, "demote", "kitchen-pi")

    assert again.exit_code == 0 and "already pinned on GATEWAY" in again.stdout
    assert stored(manifest, node).ceiling is RuntimeLevel.GATEWAY


def test_promote_lifts_a_pin_and_says_the_queen_may_climb_again(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    invoke(manifest, "demote", "kitchen-pi")

    result = invoke(manifest, "promote", "kitchen-pi")

    assert result.exit_code == 0 and "Its pin is lifted" in result.stdout
    after = stored(manifest, node)
    assert after.ceiling is None and after.desired_level is RuntimeLevel.COLONIZED


def test_demote_of_a_colonized_device_says_it_is_pinned_where_it_lands(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", level=RuntimeLevel.COLONIZED)

    result = invoke(manifest, "demote", "kitchen-pi")

    assert "It is pinned there: the Queen will not take it higher on her own." in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("access", "REF", "full"),
        ("shield", "REF", "propolis"),
        ("promote", "REF"),
        ("demote", "REF"),
    ],
)
def test_a_revoked_device_accepts_no_change_and_the_refusal_names_its_code(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi", level=RuntimeLevel.COLONIZED)
    invoke(manifest, "revoke", "kitchen-pi")
    before = stored(manifest, node)

    result = invoke(manifest, *(node.device_id if a == "REF" else a for a in args))

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.node_revoked")
    assert stored(manifest, node) == before
    assert _acts(manifest) == ["swarm.revoked"]


def test_every_change_works_with_the_swarm_off_because_it_only_writes_the_registry(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path, enabled=False)
    enrol(manifest, label="kitchen-pi")

    assert invoke(manifest, "access", "kitchen-pi", "read_only").exit_code == 0
    assert invoke(manifest, "revoke", "kitchen-pi").exit_code == 0
