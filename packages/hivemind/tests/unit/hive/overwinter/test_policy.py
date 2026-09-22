"""Tests for hivemind.hive.overwinter.policy: decide_release and every ADR-0029 rule.

Fits into the Hive:
    Mirrors src/hivemind/hive/overwinter/policy.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.overwinter.policy for the module under test.
    - .claude/codingrules.md section 14.3 for "property-based tests (hypothesis) for codecs...".
"""

from __future__ import annotations

import dataclasses

from builders.cells import make_cell
from builders.forage import make_capacity
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cell import CellKind, CombShieldLevel
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import (
    OverwinterConfig,
    OverwinterDecision,
    PoolView,
    ReleaseOutcome,
    decide_release,
)
from waggle.clock import FakeClock
from waggle.ids import new_hive_id


def _spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec: a modest base-ubuntu image, overridable per test."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1 * 1024**3,
        "disk_bytes": 1 * 1024**2,  # 1 MB, small enough to fit every disk_budget_mb below.
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _eligible_outcome(**overrides: object) -> ReleaseOutcome:
    """Build a ReleaseOutcome that clears every rule, overridable per test."""
    fields: dict[str, object] = {
        "rolled_back_whole_cell": False,
        "has_block_wax": False,
        "single_use": False,
        "backend_can_pause": True,
    }
    fields.update(overrides)
    return ReleaseOutcome(**fields)  # type: ignore[arg-type]


def _eligible_pool(**overrides: object) -> PoolView:
    """Build a PoolView with plenty of room, overridable per test."""
    fields: dict[str, object] = {"total": 0, "per_image": {}, "disk_used_mb": 0}
    fields.update(overrides)
    return PoolView(**fields)  # type: ignore[arg-type]


def _eligible_config(**overrides: object) -> OverwinterConfig:
    """Build an OverwinterConfig with generous bounds, overridable per test."""
    fields: dict[str, object] = {
        "enabled": True,
        "max_cells": 4,
        "max_per_image": 2,
        "max_dormant_s": 3600.0,
        "disk_budget_mb": 8192,
    }
    fields.update(overrides)
    return OverwinterConfig(**fields)  # type: ignore[arg-type]


def test_decide_release_overwinters_when_every_rule_clears() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)

    decision = decide_release(
        cell, _spec(), _eligible_outcome(), _eligible_pool(), _eligible_config()
    )

    assert decision.decision is OverwinterDecision.OVERWINTER


def test_decide_release_tears_down_when_the_pool_is_disabled() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    config = _eligible_config(enabled=False)

    decision = decide_release(cell, _spec(), _eligible_outcome(), _eligible_pool(), config)

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "disabled" in decision.reason


def test_decide_release_tears_down_a_night_veil_cell() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.NIGHT_VEIL)

    decision = decide_release(
        cell, _spec(), _eligible_outcome(), _eligible_pool(), _eligible_config()
    )

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "Night Veil" in decision.reason


def test_decide_release_tears_down_a_single_use_task() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    outcome = _eligible_outcome(single_use=True)

    decision = decide_release(cell, _spec(), outcome, _eligible_pool(), _eligible_config())

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "single-use" in decision.reason


def test_decide_release_tears_down_after_a_whole_cell_rollback() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    outcome = _eligible_outcome(rolled_back_whole_cell=True)

    decision = decide_release(cell, _spec(), outcome, _eligible_pool(), _eligible_config())

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "rolled the whole Cell back" in decision.reason


def test_decide_release_tears_down_with_an_open_block_wax_note() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    outcome = _eligible_outcome(has_block_wax=True)

    decision = decide_release(cell, _spec(), outcome, _eligible_pool(), _eligible_config())

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "BLOCK" in decision.reason


def test_decide_release_tears_down_when_the_backend_cannot_pause() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    outcome = _eligible_outcome(backend_can_pause=False)

    decision = decide_release(cell, _spec(), outcome, _eligible_pool(), _eligible_config())

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "pause" in decision.reason


def test_decide_release_tears_down_when_the_image_is_at_its_own_cap() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    pool = _eligible_pool(per_image={"base-ubuntu": 2})
    config = _eligible_config(max_per_image=2)

    decision = decide_release(cell, _spec(), _eligible_outcome(), pool, config)

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "max_per_image" in decision.reason


def test_decide_release_tears_down_when_the_pool_is_at_its_total_cap() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    pool = _eligible_pool(total=4)
    config = _eligible_config(max_cells=4)

    decision = decide_release(cell, _spec(), _eligible_outcome(), pool, config)

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "max_cells" in decision.reason


def test_decide_release_tears_down_when_the_disk_budget_would_be_exceeded() -> None:
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.MEADOW)
    pool = _eligible_pool(disk_used_mb=10)
    config = _eligible_config(disk_budget_mb=10)
    spec = _spec(disk_bytes=1 * 1024**2)  # 1 more MB tips 10 MB used over a 10 MB budget.

    decision = decide_release(cell, spec, _eligible_outcome(), pool, config)

    assert decision.decision is OverwinterDecision.TEARDOWN
    assert "disk_budget_mb" in decision.reason


def test_decide_release_names_the_first_veto_when_several_apply() -> None:
    # NIGHT_VEIL is checked before single_use in _RULES; both hold here, so the reason must be
    # the Night Veil one, never the single-use one.
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.NIGHT_VEIL)
    outcome = _eligible_outcome(single_use=True)

    decision = decide_release(cell, _spec(), outcome, _eligible_pool(), _eligible_config())

    assert "Night Veil" in decision.reason


@st.composite
def _any_outcome(draw: st.DrawFn) -> ReleaseOutcome:
    """Draw a ReleaseOutcome with every field randomised, so no combination is left unchecked."""
    return _eligible_outcome(
        single_use=draw(st.booleans()),
        rolled_back_whole_cell=draw(st.booleans()),
        has_block_wax=draw(st.booleans()),
        backend_can_pause=draw(st.booleans()),
    )


@st.composite
def _any_pool_and_config(draw: st.DrawFn) -> tuple[PoolView, OverwinterConfig]:
    """Draw a (PoolView, OverwinterConfig) pair with occupancy and the pool switch randomised."""
    total = draw(st.integers(min_value=0, max_value=10))
    max_cells = draw(st.integers(min_value=1, max_value=10))
    pool_enabled = draw(st.booleans())
    return _eligible_pool(total=total), _eligible_config(enabled=pool_enabled, max_cells=max_cells)


@given(outcome=_any_outcome(), pool_and_config=_any_pool_and_config())
def test_night_veil_is_never_overwinter_whatever_else_holds(
    outcome: ReleaseOutcome, pool_and_config: tuple[PoolView, OverwinterConfig]
) -> None:
    pool, config = pool_and_config
    cell = make_cell(kind=CellKind.VIRTUAL, comb_shield=CombShieldLevel.NIGHT_VEIL)

    decision = decide_release(cell, _spec(), outcome, pool, config)

    assert decision.decision is OverwinterDecision.TEARDOWN


def test_overwinter_config_from_section_mirrors_the_manifest_section() -> None:
    from hivemind.manifest.schema.placement import VirtualCellsOverwinterSection

    section = VirtualCellsOverwinterSection(
        enabled=False, max_cells=7, max_per_image=3, max_dormant_s=120.0, disk_budget_mb=4096
    )

    config = OverwinterConfig.from_section(section)

    assert config == dataclasses.replace(
        _eligible_config(),
        enabled=False,
        max_cells=7,
        max_per_image=3,
        max_dormant_s=120.0,
        disk_budget_mb=4096,
    )
