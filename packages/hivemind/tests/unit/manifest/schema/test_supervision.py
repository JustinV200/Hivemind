"""Tests for hivemind.manifest.schema.supervision: SupervisionSection, MemorySection and more.

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/supervision.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

    Phase 11 adds [supervision.reconnect_budget] and [supervision.watch], so this module also pins
    their defaults, every bound, the rule that the budget is carved out of max_offline_s, and that
    docs/manifests/full.toml writes every key of both out.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.schema.supervision for the module under test.
    - docs/manifests/full.toml for the written-out example the last tests load.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hivemind.manifest import load_manifest
from hivemind.manifest.schema.supervision import (
    DEFAULT_WAX_TEXT_CAP_CHARS,
    MemorySection,
    ReconnectBudgetSection,
    SupervisionSection,
    WatchSection,
)
from waggle.messages.cell.wax import MAX_WAX_TEXT_CHARS
from waggle.messages.swarm.watch import MAX_OBSERVE_ROOTS

# The repository root, seven parents up from this test file
# (packages/hivemind/tests/unit/manifest/schema/test_supervision.py).
_FULL_TOML = Path(__file__).resolve().parents[6] / "docs" / "manifests" / "full.toml"


def test_supervision_section_has_sensible_defaults() -> None:
    section = SupervisionSection()

    # Both unset by default: a relative path default could only ever be right for a manifest
    # in one particular directory, so unset means the table shipped in
    # hivemind.supervision.defaults instead.
    assert section.policy_file is None
    assert section.capping_tiers_file is None
    assert section.heartbeat_interval_s == 5.0
    assert section.heartbeat_miss_limit == 3
    assert section.max_offline_s == 600.0
    assert section.alarm_attempt_limit == 3


def test_supervision_section_rejects_a_non_positive_heartbeat_interval() -> None:
    with pytest.raises(ValidationError):
        SupervisionSection(heartbeat_interval_s=0)


def test_memory_section_has_sensible_defaults() -> None:
    section = MemorySection()

    assert section.budget_fraction == 0.6
    assert section.handoff_threshold == 0.66
    assert section.hot_window_s == 4.0 * 3600.0
    assert section.pins == ()
    assert section.sweep_interval_s == 3_600.0
    assert section.cell_wax_cap == 20
    assert section.wax_text_cap_chars == DEFAULT_WAX_TEXT_CAP_CHARS


def test_memory_section_wax_text_cap_chars_matches_waggles_own_ceiling() -> None:
    """The manifest's own default never silently drifts from the wire shape's hard ceiling."""
    assert DEFAULT_WAX_TEXT_CAP_CHARS == MAX_WAX_TEXT_CHARS


def test_memory_section_rejects_a_non_positive_wax_text_cap_chars() -> None:
    with pytest.raises(ValidationError):
        MemorySection(wax_text_cap_chars=0)


def test_memory_section_accepts_a_wax_text_cap_chars_lower_than_the_wire_ceiling() -> None:
    assert MemorySection(wax_text_cap_chars=500).wax_text_cap_chars == 500


def test_memory_section_rejects_a_non_positive_sweep_interval() -> None:
    with pytest.raises(ValidationError):
        MemorySection(sweep_interval_s=0)


def test_memory_section_accepts_a_custom_sweep_interval() -> None:
    assert MemorySection(sweep_interval_s=60.0).sweep_interval_s == 60.0


def test_memory_section_rejects_a_non_positive_hot_window() -> None:
    with pytest.raises(ValidationError):
        MemorySection(hot_window_s=0)


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1])
def test_memory_section_rejects_a_budget_fraction_outside_zero_to_one(fraction: float) -> None:
    with pytest.raises(ValidationError):
        MemorySection(budget_fraction=fraction)


def test_memory_section_accepts_a_budget_fraction_of_exactly_one() -> None:
    assert MemorySection(budget_fraction=1.0).budget_fraction == 1.0


def test_memory_section_is_frozen_and_forbids_extras() -> None:
    section = MemorySection()

    with pytest.raises(ValidationError, match="frozen"):
        section.item_cap_chars = 1  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        MemorySection.model_validate({**section.model_dump(), "nope": 1})


# ──────────────────────────────────────────────────────────────────────────────
# [supervision.reconnect_budget] and [supervision.watch] (roadmap phase 11)
# ──────────────────────────────────────────────────────────────────────────────


def test_the_reconnect_budget_defaults_are_two_attempts_in_five_minutes() -> None:
    budget = SupervisionSection().reconnect_budget

    assert (budget.attempts, budget.wall_clock_s) == (2, 300.0)
    assert budget == ReconnectBudgetSection()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"attempts": 0}, "greater than or equal to 1"),
        ({"attempts": -1}, "greater than or equal to 1"),
        ({"wall_clock_s": 0}, "greater than 0"),
        ({"wall_clock_s": -5.0}, "greater than 0"),
        ({"retries": 3}, "extra"),
    ],
)
def test_the_reconnect_budget_rejects_a_bad_figure(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        ReconnectBudgetSection.model_validate(changes)


def test_the_reconnect_budget_accepts_one_attempt_and_a_short_wall_clock() -> None:
    budget = ReconnectBudgetSection(attempts=1, wall_clock_s=0.5)

    assert SupervisionSection(reconnect_budget=budget).reconnect_budget == budget


def test_the_reconnect_budget_must_be_shorter_than_the_offline_limit() -> None:
    # Carved out of max_offline_s, never added to it (ADR-0056): equal is as bad as longer.
    shorter = SupervisionSection(
        max_offline_s=301.0, reconnect_budget=ReconnectBudgetSection(wall_clock_s=300.0)
    )

    assert shorter.max_offline_s == 301.0
    for max_offline_s in (300.0, 120.0):
        with pytest.raises(ValidationError, match=r"must be below \[supervision\] max_offline_s"):
            SupervisionSection(max_offline_s=max_offline_s)
    with pytest.raises(ValidationError, match="wall_clock_s"):
        SupervisionSection(
            max_offline_s=600.0, reconnect_budget=ReconnectBudgetSection(wall_clock_s=600.0)
        )


def test_lowering_the_budget_makes_a_short_offline_limit_valid() -> None:
    section = SupervisionSection(
        max_offline_s=60.0, reconnect_budget=ReconnectBudgetSection(wall_clock_s=30.0)
    )

    assert section.reconnect_budget.wall_clock_s < section.max_offline_s


def test_the_watch_defaults_are_five_minutes_a_day_and_an_hour() -> None:
    watch = SupervisionSection().watch

    assert watch == WatchSection()
    assert (watch.interval_s, watch.retention_s, watch.patrol_interval_s) == (
        300.0,
        86_400.0,
        3_600.0,
    )
    assert (watch.cpu_share_alarm, watch.disk_fill_alarm) == (0.5, 0.95)


@pytest.mark.parametrize("field", ["interval_s", "retention_s", "patrol_interval_s"])
@pytest.mark.parametrize("value", [0, -1.0])
def test_the_watch_durations_must_be_positive(field: str, value: float) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        WatchSection.model_validate({field: value})


@pytest.mark.parametrize("field", ["cpu_share_alarm", "disk_fill_alarm"])
@pytest.mark.parametrize("value", [0, -0.1, 1.01])
def test_the_watch_thresholds_are_fractions_above_zero_and_at_most_one(
    field: str, value: float
) -> None:
    with pytest.raises(ValidationError, match=r"greater than 0|less than or equal to 1"):
        WatchSection.model_validate({field: value})


def test_the_watch_thresholds_accept_exactly_one_and_a_small_fraction() -> None:
    watch = WatchSection(cpu_share_alarm=1.0, disk_fill_alarm=0.01)

    assert (watch.cpu_share_alarm, watch.disk_fill_alarm) == (1.0, 0.01)


def test_the_watched_directories_default_to_none() -> None:
    assert WatchSection().roots == ()


def test_the_watched_directories_accept_absolute_paths_up_to_the_wire_limit() -> None:
    few = WatchSection(roots=("/var/log", "/home/me/projects"))
    most = tuple(f"/watched/{n}" for n in range(MAX_OBSERVE_ROOTS))

    assert few.roots == ("/var/log", "/home/me/projects")
    assert WatchSection(roots=most).roots == most


@pytest.mark.parametrize("root", ["relative/dir", "", "./here", "~/notes", "/has\0nul"])
def test_a_watched_directory_must_be_absolute_and_free_of_nul(root: str) -> None:
    with pytest.raises(ValidationError, match="absolute"):
        WatchSection(roots=(root,))


def test_no_more_directories_are_watched_than_one_observation_can_name() -> None:
    too_many = tuple(f"/watched/{n}" for n in range(MAX_OBSERVE_ROOTS + 1))

    with pytest.raises(ValidationError, match="at most"):
        WatchSection(roots=too_many)


def test_the_new_supervision_tables_are_frozen_and_forbid_extras() -> None:
    budget = ReconnectBudgetSection()
    watch = WatchSection()

    with pytest.raises(ValidationError, match="frozen"):
        budget.attempts = 1  # The assignment is the test.
    with pytest.raises(ValidationError, match="frozen"):
        watch.interval_s = 1.0  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        ReconnectBudgetSection.model_validate({**budget.model_dump(), "nope": 1})
    with pytest.raises(ValidationError, match="extra"):
        WatchSection.model_validate({**watch.model_dump(), "nope": 1})


def test_the_supervision_section_round_trips_with_its_new_tables() -> None:
    section = SupervisionSection(
        reconnect_budget=ReconnectBudgetSection(attempts=3, wall_clock_s=120.0),
        watch=WatchSection(interval_s=60.0, cpu_share_alarm=0.9),
    )

    assert SupervisionSection.model_validate(section.model_dump(mode="json")) == section


def test_full_toml_writes_out_every_key_of_the_new_supervision_tables() -> None:
    supervision = load_manifest(_FULL_TOML).supervision

    assert supervision.reconnect_budget.model_fields_set == set(ReconnectBudgetSection.model_fields)
    assert supervision.watch.model_fields_set == set(WatchSection.model_fields)
    # The written-out example uses the documented defaults, so the docs and the schema agree.
    assert supervision.reconnect_budget == ReconnectBudgetSection()
    assert supervision.watch == WatchSection()
