"""Tests for hivemind.honey_store.scope: scope builders, folders, scope_for_nectar, capabilities.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/scope.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.scope for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.guard import CapabilityFamily
from hivemind.honey_store.errors import InvalidScopeError
from hivemind.honey_store.models import NectarOrigin
from hivemind.honey_store.scope import (
    HIVE_SCOPE,
    NectarProvenance,
    bee_scope,
    cell_scope,
    folder_for_scope,
    honey_ref,
    is_readable,
    parse_honey_ref,
    queen_read_capabilities,
    readable_globs,
    scope_for_folder,
    scope_for_nectar,
    task_scope,
    warden_read_capabilities,
    worker_read_capabilities,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_honey_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages.honey import NectarKind

_CLOCK = FakeClock()
_CELL_ID = new_cell_id(_CLOCK)
_TASK_ID = new_task_id(_CLOCK)
_WORKER_ID = new_worker_id(_CLOCK)
_WARDEN_ID = new_warden_id(_CLOCK)


# ──────────────────────────────────────────────────────────────────────────────
# Scope builders and folder round trip
# ──────────────────────────────────────────────────────────────────────────────


def test_cell_scope_task_scope_bee_scope_build_expected_strings() -> None:
    assert cell_scope(_CELL_ID) == f"cell:{_CELL_ID}"
    assert task_scope(_TASK_ID) == f"task:{_TASK_ID}"
    assert bee_scope(_WORKER_ID) == f"bee:{_WORKER_ID}"
    assert bee_scope(_WARDEN_ID) == f"bee:{_WARDEN_ID}"


@pytest.mark.parametrize(
    "scope",
    [HIVE_SCOPE, f"cell:{_CELL_ID}", f"bee:{_WORKER_ID}", f"task:{_TASK_ID}"],
)
def test_folder_for_scope_and_scope_for_folder_round_trip(scope: str) -> None:
    folder = folder_for_scope(scope)

    assert scope_for_folder(folder) == scope


def test_folder_for_scope_maps_every_kind_to_its_own_prefix() -> None:
    assert folder_for_scope(HIVE_SCOPE) == "/hive"
    assert folder_for_scope(cell_scope(_CELL_ID)) == f"/cells/{_CELL_ID}"
    assert folder_for_scope(bee_scope(_WORKER_ID)) == f"/bees/{_WORKER_ID}"
    assert folder_for_scope(task_scope(_TASK_ID)) == f"/tasks/{_TASK_ID}"


def test_scope_for_folder_returns_none_for_an_unknown_folder() -> None:
    assert scope_for_folder("/bee-bread") is None
    assert scope_for_folder("/cells/") is None  # Prefix with no id after it.


def test_scope_for_folder_returns_none_for_a_path_deeper_than_a_scope_folder() -> None:
    # A Cell's live wax folder sits under its Cell folder but is not a scope of its own.
    assert scope_for_folder(f"/cells/{_CELL_ID}/wax") is None


def test_parse_honey_ref_rejects_a_live_wax_path() -> None:
    with pytest.raises(InvalidScopeError):
        parse_honey_ref(f"/cells/{_CELL_ID}/wax/wax_x")


def test_folder_for_scope_rejects_a_malformed_scope() -> None:
    with pytest.raises(InvalidScopeError):
        folder_for_scope("not-a-scope")


# ──────────────────────────────────────────────────────────────────────────────
# honey_ref / parse_honey_ref
# ──────────────────────────────────────────────────────────────────────────────


def test_honey_ref_and_parse_honey_ref_round_trip() -> None:
    honey_id = new_honey_id(_CLOCK)
    scope = cell_scope(_CELL_ID)

    ref = honey_ref(scope, honey_id)
    parsed_scope, parsed_id = parse_honey_ref(ref)

    assert ref == f"/cells/{_CELL_ID}/{honey_id}"
    assert parsed_scope == scope
    assert parsed_id == honey_id


def test_parse_honey_ref_rejects_an_unknown_folder() -> None:
    with pytest.raises(InvalidScopeError):
        parse_honey_ref("/bee-bread/honey_x")


# ──────────────────────────────────────────────────────────────────────────────
# scope_for_nectar: every ADR-0031 branch, both ways
# ──────────────────────────────────────────────────────────────────────────────


def _provenance(**overrides: object) -> NectarProvenance:
    """Build a NectarProvenance for scope_for_nectar's own tests, BEE/FINDING by default."""
    fields: dict[str, object] = {
        "kind": NectarKind.FINDING,
        "origin": NectarOrigin.BEE,
        "task_id": None,
        "cell_id": _CELL_ID,
        "bee": None,
    }
    fields.update(overrides)
    return NectarProvenance(**fields)  # type: ignore[arg-type]  # overrides is loosely typed.


def test_scope_for_nectar_human_uses_its_proposed_scope() -> None:
    proposed = task_scope(_TASK_ID)

    result = scope_for_nectar(_provenance(origin=NectarOrigin.HUMAN), proposed)

    assert result == proposed


def test_scope_for_nectar_human_defaults_to_hive_with_no_proposal() -> None:
    result = scope_for_nectar(_provenance(origin=NectarOrigin.HUMAN), None)

    assert result == HIVE_SCOPE


def test_scope_for_nectar_human_rejects_a_malformed_proposed_scope() -> None:
    with pytest.raises(InvalidScopeError):
        scope_for_nectar(_provenance(origin=NectarOrigin.HUMAN), "nonsense")


@pytest.mark.parametrize("origin", [NectarOrigin.WATCH, NectarOrigin.CELL_WAX])
def test_scope_for_nectar_watch_and_cell_wax_are_always_cell_scoped(origin: NectarOrigin) -> None:
    result = scope_for_nectar(_provenance(origin=origin, kind=NectarKind.PATROL_SUMMARY), None)

    assert result == cell_scope(_CELL_ID)


def test_scope_for_nectar_bee_bread_keeps_its_task_scope_when_it_has_one() -> None:
    result = scope_for_nectar(_provenance(origin=NectarOrigin.BEE_BREAD, task_id=_TASK_ID), None)

    assert result == task_scope(_TASK_ID)


def test_scope_for_nectar_bee_bread_falls_back_to_hive_with_no_task() -> None:
    result = scope_for_nectar(_provenance(origin=NectarOrigin.BEE_BREAD), None)

    assert result == HIVE_SCOPE


def test_scope_for_nectar_task_outcome_is_always_hive() -> None:
    result = scope_for_nectar(_provenance(origin=NectarOrigin.TASK_OUTCOME, task_id=_TASK_ID), None)

    assert result == HIVE_SCOPE


@pytest.mark.parametrize(
    "kind", [NectarKind.FINDING, NectarKind.AUDIT_FINDING, NectarKind.RIPENED_HONEY]
)
def test_scope_for_nectar_bee_shared_knowledge_kinds_are_hive(kind: NectarKind) -> None:
    result = scope_for_nectar(_provenance(kind=kind, task_id=_TASK_ID, bee=_WORKER_ID), None)

    assert result == HIVE_SCOPE


def test_scope_for_nectar_bee_patrol_summary_is_cell_scoped() -> None:
    result = scope_for_nectar(_provenance(kind=NectarKind.PATROL_SUMMARY), None)

    assert result == cell_scope(_CELL_ID)


@pytest.mark.parametrize(
    "kind",
    [
        NectarKind.TRANSCRIPT,
        NectarKind.TOOL_RESULT,
        NectarKind.FLIGHT_RECORDING,
    ],
)
def test_scope_for_nectar_bee_working_material_prefers_its_task(kind: NectarKind) -> None:
    result = scope_for_nectar(_provenance(kind=kind, task_id=_TASK_ID, bee=_WORKER_ID), None)

    assert result == task_scope(_TASK_ID)


def test_scope_for_nectar_bee_working_material_falls_back_to_bee_with_no_task() -> None:
    result = scope_for_nectar(_provenance(kind=NectarKind.TOOL_RESULT, bee=_WORKER_ID), None)

    assert result == bee_scope(_WORKER_ID)


def test_scope_for_nectar_bee_working_material_falls_back_to_cell_with_neither() -> None:
    result = scope_for_nectar(_provenance(kind=NectarKind.TOOL_RESULT), None)

    assert result == cell_scope(_CELL_ID)


# ──────────────────────────────────────────────────────────────────────────────
# Default capability sets and the read side (readable_globs / is_readable)
# ──────────────────────────────────────────────────────────────────────────────


def test_queen_read_capabilities_grants_every_scope() -> None:
    caps = queen_read_capabilities()

    assert is_readable(HIVE_SCOPE, caps)
    assert is_readable(cell_scope(_CELL_ID), caps)
    assert is_readable(task_scope(_TASK_ID), caps)


def test_warden_read_capabilities_grants_hive_its_cell_and_itself_only() -> None:
    caps = warden_read_capabilities(_CELL_ID, _WARDEN_ID)

    assert is_readable(HIVE_SCOPE, caps)
    assert is_readable(cell_scope(_CELL_ID), caps)
    assert is_readable(bee_scope(_WARDEN_ID), caps)
    assert not is_readable(task_scope(_TASK_ID), caps)
    assert not is_readable(cell_scope(new_cell_id(_CLOCK)), caps)


def test_worker_read_capabilities_grants_hive_cell_task_goal_and_itself() -> None:
    goal_id = new_task_id(_CLOCK)

    caps = worker_read_capabilities(_TASK_ID, goal_id, _CELL_ID, _WORKER_ID)

    assert is_readable(HIVE_SCOPE, caps)
    assert is_readable(cell_scope(_CELL_ID), caps)
    assert is_readable(task_scope(_TASK_ID), caps)
    assert is_readable(task_scope(goal_id), caps)
    assert is_readable(bee_scope(_WORKER_ID), caps)
    assert not is_readable(cell_scope(new_cell_id(_CLOCK)), caps)


def test_readable_globs_returns_only_honey_read_scopes_sorted() -> None:
    caps = warden_read_capabilities(_CELL_ID, _WARDEN_ID).attenuate(
        warden_read_capabilities(_CELL_ID, _WARDEN_ID)
    )

    globs = readable_globs(caps)

    assert globs == tuple(sorted(globs))
    assert all(capability.family is CapabilityFamily.HONEY_READ for capability in caps)


def test_is_readable_matches_a_wildcard_glob() -> None:
    caps = queen_read_capabilities()

    assert is_readable(cell_scope(_CELL_ID), caps)
