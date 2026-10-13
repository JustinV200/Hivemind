"""Contract suite for CombRegistry: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of
    hivemind.royal_jelly.registry.protocol.CombRegistry's contract and runs against three
    harnesses: `SqliteCombRegistry` on `:memory:`, on a temp file, and the
    `InMemoryCombRegistry` fake, so the fake is proven to refuse and record exactly what the real
    store does before any other test relies on it (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.protocol for the contract under test.
    - tests/unit/royal_jelly/registry/test_no_bypass.py for the no-bypass proof on the SQLite store.
    - packages/hivemind/tests/contracts/test_honey_store_contract.py for the pattern this mirrors.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.royal_jelly import make_failing_report, make_offer, make_tool_spec

from hivemind.common.sqlite import connect
from hivemind.royal_jelly.errors import (
    PromotionRefusedError,
    RollbackRefusedError,
    ToolNotFoundError,
    ToolTransitionError,
    VersionExistsError,
    WrongCellError,
    WrongPrincipalError,
)
from hivemind.royal_jelly.registry import (
    ChangeKind,
    CombRegistry,
    InMemoryCombRegistry,
    Principal,
    PromotionOffer,
    QueenPrincipal,
    SqliteCombRegistry,
    ToolKey,
    WardenPrincipal,
)
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from hivemind.royal_jelly.tool_state import ToolState
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_warden_id
from waggle.messages.tool.authoring import ToolScope

_QUEEN = QueenPrincipal()
_HARNESS_KINDS = ("sqlite_memory", "sqlite_file", "in_memory")


@pytest.fixture(params=_HARNESS_KINDS)
async def registry(request: pytest.FixtureRequest, tmp_path: Path) -> CombRegistry:
    """Build the parametrised registry: a fresh, empty one of each implementation."""
    clock = FakeClock()
    if request.param == "in_memory":
        return InMemoryCombRegistry(clock)
    path = ":memory:" if request.param == "sqlite_memory" else tmp_path / "hive.db"
    return await SqliteCombRegistry.create(connect(path), clock)


def _warden_for(cell_id: CellId | None) -> WardenPrincipal:
    assert cell_id is not None
    return WardenPrincipal(new_warden_id(FakeClock()), cell_id)


async def _promote(
    registry: CombRegistry, spec: ToolSpec | None = None, principal: Principal | None = None
) -> ToolSpec:
    """Promote `spec` (a fresh HIVE echo tool by default) with a real package and report."""
    spec = spec or make_tool_spec()
    report, offer = make_offer(spec)
    if principal is None:
        principal = _QUEEN if spec.scope is ToolScope.HIVE else _warden_for(spec.cell_id)
    await registry.promote(spec, report, offer, principal, "initial")
    return spec


def _key(spec: ToolSpec) -> ToolKey:
    return ToolKey(spec.name, spec.scope, spec.cell_id)


# ─────────────────────────────── promote and read ───────────────────────────────


async def test_promote_stores_a_current_row_and_describes_the_change(
    registry: CombRegistry,
) -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)

    change = await registry.promote(spec, report, offer, _QUEEN, "first version")

    assert change.kind is ChangeKind.PROMOTED
    assert change.by == "queen"
    assert change.reason == "first version"
    assert change.supersedes_version is None
    row = change.row
    assert row.is_current
    assert row.state is ToolState.PROMOTED
    assert row.package_sha256 == offer.package_sha256
    assert row.package_size == offer.package_size_bytes
    assert row.spec == spec
    assert row.report == report
    assert await registry.get(spec.name, ToolScope.HIVE, None) == row


async def test_a_new_version_becomes_current_and_names_the_one_it_displaced(
    registry: CombRegistry,
) -> None:
    first = await _promote(registry, make_tool_spec(version="1.0.0"))
    spec = make_tool_spec(version="1.1.0")
    report, offer = make_offer(spec)

    change = await registry.promote(spec, report, offer, _QUEEN, "second")

    assert change.supersedes_version == "1.0.0"
    assert (await registry.get(first.name, ToolScope.HIVE, None)).version == "1.1.0"
    assert not (await registry.get(first.name, ToolScope.HIVE, None, "1.0.0")).is_current
    assert [r.is_current for r in await registry.list_tools()] == [False, True]


async def test_get_by_version_returns_that_version_and_missing_ones_raise(
    registry: CombRegistry,
) -> None:
    spec = await _promote(registry)

    assert (await registry.get(spec.name, ToolScope.HIVE, None, "1.0.0")).version == "1.0.0"
    with pytest.raises(ToolNotFoundError):
        await registry.get(spec.name, ToolScope.HIVE, None, "9.9.9")
    with pytest.raises(ToolNotFoundError):
        await registry.get("never_promoted", ToolScope.HIVE, None)


async def test_get_rejects_a_cell_that_disagrees_with_the_scope(registry: CombRegistry) -> None:
    with pytest.raises(ValueError, match="cell_id"):
        await registry.get("echo_tool", ToolScope.CELL, None)
    with pytest.raises(ValueError, match="cell_id"):
        await registry.get("echo_tool", ToolScope.HIVE, new_cell_id(FakeClock()))


async def test_list_tools_filters_by_scope_cell_and_currency_in_numeric_version_order(
    registry: CombRegistry,
) -> None:
    for version in ("1.2.0", "1.10.0"):
        await _promote(registry, make_tool_spec(version=version))
    cell_spec = make_tool_spec(name="cell_tool", scope=ToolScope.CELL)
    await _promote(registry, cell_spec)

    everything = await registry.list_tools()
    hive_only = await registry.list_tools(ToolScope.HIVE)
    cell_only = await registry.list_tools(cell_id=cell_spec.cell_id)
    current = await registry.list_tools(current_only=True)

    assert [(r.name, r.version) for r in everything] == [
        ("cell_tool", "1.0.0"),
        ("echo_tool", "1.2.0"),
        ("echo_tool", "1.10.0"),
    ]
    assert [r.version for r in hive_only] == ["1.2.0", "1.10.0"]
    assert [r.name for r in cell_only] == ["cell_tool"]
    assert [(r.name, r.version) for r in current] == [
        ("cell_tool", "1.0.0"),
        ("echo_tool", "1.10.0"),
    ]


async def test_visible_tools_are_hive_current_plus_that_cells_current(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec(name="hive_tool", version="1.0.0"))
    await _promote(registry, make_tool_spec(name="hive_tool", version="2.0.0"))
    mine = make_tool_spec(name="my_tool", scope=ToolScope.CELL)
    await _promote(registry, mine)
    await _promote(registry, make_tool_spec(name="their_tool", scope=ToolScope.CELL))

    visible = await registry.visible_tools(mine.cell_id)  # type: ignore[arg-type]

    assert [(r.name, r.version) for r in visible] == [("hive_tool", "2.0.0"), ("my_tool", "1.0.0")]
    assert await registry.visible_tools(new_cell_id(FakeClock())) == tuple(
        r for r in visible if r.name == "hive_tool"
    )


async def test_a_hive_tool_shadows_a_cell_tool_of_the_same_name(registry: CombRegistry) -> None:
    cell_spec = make_tool_spec(name="shared_tool", scope=ToolScope.CELL)
    await _promote(registry, cell_spec)
    await _promote(registry, make_tool_spec(name="shared_tool"))

    visible = await registry.visible_tools(cell_spec.cell_id)  # type: ignore[arg-type]

    assert [(r.name, r.scope) for r in visible] == [("shared_tool", ToolScope.HIVE)]


async def test_the_same_name_may_exist_at_hive_scope_and_in_two_cells(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec())
    first = make_tool_spec(scope=ToolScope.CELL)
    second = make_tool_spec(scope=ToolScope.CELL)
    await _promote(registry, first)
    await _promote(registry, second)

    assert len(await registry.list_tools()) == 3
    assert (await registry.get(first.name, ToolScope.CELL, first.cell_id)).cell_id == first.cell_id


# ─────────────────────────────── refusals ───────────────────────────────


async def test_promote_refuses_each_unproven_or_unauthorised_offer_and_stores_nothing(
    registry: CombRegistry,
) -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    failing = make_failing_report(spec, offer.package_sha256, offer.package_size_bytes)
    other_cell = _warden_for(new_cell_id(FakeClock()))
    cell_spec = make_tool_spec(scope=ToolScope.CELL)
    cell_report, cell_offer = make_offer(cell_spec)
    attempts: list[tuple[str, ToolSpec, QuarantineReport, PromotionOffer, Principal]] = [
        ("report_failed", spec, failing, offer, _QUEEN),
        (
            "report_mismatch",
            spec,
            report.model_copy(update={"package_sha256": "a" * 64}),
            offer,
            _QUEEN,
        ),
        ("wrong_principal", spec, report, offer, other_cell),
        ("wrong_principal", cell_spec, cell_report, cell_offer, _QUEEN),
        ("wrong_cell", cell_spec, cell_report, cell_offer, other_cell),
    ]

    for refusal, attempt_spec, attempt_report, attempt_offer, principal in attempts:
        with pytest.raises(PromotionRefusedError) as raised:
            await registry.promote(attempt_spec, attempt_report, attempt_offer, principal, "try")
        assert raised.value.refusal == refusal

    assert await registry.list_tools() == ()


async def test_promote_refuses_a_version_that_exists_even_when_retired(
    registry: CombRegistry,
) -> None:
    spec = await _promote(registry)
    report, offer = make_offer(spec)

    with pytest.raises(VersionExistsError):
        await registry.promote(spec, report, offer, _QUEEN, "again")
    await registry.retire(_key(spec), spec.version, _QUEEN, "withdrawn")
    with pytest.raises(VersionExistsError):
        await registry.promote(spec, report, offer, _QUEEN, "again")


# ─────────────────────────────── retire ───────────────────────────────


async def test_retire_marks_the_row_retired_keeps_it_and_leaves_no_current(
    registry: CombRegistry,
) -> None:
    spec = await _promote(registry)

    change = await registry.retire(_key(spec), spec.version, _QUEEN, "no longer wanted")

    assert change.kind is ChangeKind.RETIRED
    assert change.row.state is ToolState.RETIRED
    assert not change.row.is_current
    assert change.row.retired_reason == "no longer wanted"
    assert change.row.retired_at is not None
    with pytest.raises(ToolNotFoundError):
        await registry.get(spec.name, ToolScope.HIVE, None)
    assert (await registry.get(spec.name, ToolScope.HIVE, None, spec.version)).state is (
        ToolState.RETIRED
    )
    assert await registry.visible_tools(new_cell_id(FakeClock())) == ()


async def test_retiring_the_current_version_does_not_fall_back_to_an_older_one(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec(version="1.0.0"))
    newest = await _promote(registry, make_tool_spec(version="2.0.0"))

    await registry.retire(_key(newest), "2.0.0", _QUEEN, "bad release")

    with pytest.raises(ToolNotFoundError):
        await registry.get(newest.name, ToolScope.HIVE, None)


async def test_retire_refuses_unknown_versions_and_already_retired_ones(
    registry: CombRegistry,
) -> None:
    spec = await _promote(registry)
    await registry.retire(_key(spec), spec.version, _QUEEN, "first")

    with pytest.raises(ToolNotFoundError):
        await registry.retire(_key(spec), "9.9.9", _QUEEN, "no such version")
    with pytest.raises(ToolTransitionError):
        await registry.retire(_key(spec), spec.version, _QUEEN, "twice")


async def test_a_warden_retires_only_its_own_cells_tool_and_the_queen_may_too(
    registry: CombRegistry,
) -> None:
    spec = make_tool_spec(scope=ToolScope.CELL)
    await _promote(registry, spec)

    with pytest.raises(WrongCellError):
        await registry.retire(_key(spec), spec.version, _warden_for(new_cell_id(FakeClock())), "x")
    await registry.retire(_key(spec), spec.version, _warden_for(spec.cell_id), "mine")

    other = make_tool_spec(scope=ToolScope.CELL)
    await _promote(registry, other)
    await registry.retire(_key(other), other.version, _QUEEN, "operator")


async def test_a_warden_cannot_retire_a_hive_tool(registry: CombRegistry) -> None:
    spec = await _promote(registry)

    with pytest.raises(WrongPrincipalError):
        await registry.retire(_key(spec), spec.version, _warden_for(new_cell_id(FakeClock())), "x")


# ─────────────────────────────── rollback ───────────────────────────────


async def test_rollback_makes_the_older_version_current_and_names_the_displaced_one(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec(version="1.0.0"))
    newest = await _promote(registry, make_tool_spec(version="2.0.0"))

    change = await registry.rollback(_key(newest), "1.0.0", _QUEEN, "2.0.0 misbehaves")

    assert change.kind is ChangeKind.ROLLED_BACK
    assert change.supersedes_version == "2.0.0"
    assert change.row.version == "1.0.0"
    assert change.row.is_current
    current = await registry.get(newest.name, ToolScope.HIVE, None)
    assert (current.version, current.supersedes_version) == ("1.0.0", "2.0.0")
    newer = await registry.get(newest.name, ToolScope.HIVE, None, "2.0.0")
    assert newer.state is ToolState.PROMOTED
    assert not newer.is_current


async def test_rollback_after_the_current_version_was_retired_restores_an_older_one(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec(version="1.0.0"))
    newest = await _promote(registry, make_tool_spec(version="2.0.0"))
    await registry.retire(_key(newest), "2.0.0", _QUEEN, "bad")

    change = await registry.rollback(_key(newest), "1.0.0", _QUEEN, "restore")

    assert change.supersedes_version is None
    assert (await registry.get(newest.name, ToolScope.HIVE, None)).version == "1.0.0"


async def test_rollback_refuses_illegal_targets_and_changes_nothing(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec(version="1.0.0"))
    await _promote(registry, make_tool_spec(version="2.0.0"))
    retired = await _promote(registry, make_tool_spec(version="3.0.0"))
    await registry.retire(_key(retired), "3.0.0", _QUEEN, "bad")
    await registry.rollback(_key(retired), "2.0.0", _QUEEN, "restore")
    before = await registry.list_tools()

    with pytest.raises(ToolNotFoundError):
        await registry.rollback(_key(retired), "0.1.0", _QUEEN, "no such version")
    with pytest.raises(RollbackRefusedError, match="RETIRED"):
        await registry.rollback(_key(retired), "3.0.0", _QUEEN, "retired")
    with pytest.raises(RollbackRefusedError, match="already the current"):
        await registry.rollback(_key(retired), "2.0.0", _QUEEN, "already")

    assert await registry.list_tools() == before


async def test_rollback_refuses_a_version_that_is_not_older_than_the_current(
    registry: CombRegistry,
) -> None:
    await _promote(registry, make_tool_spec(version="1.0.0"))
    newest = await _promote(registry, make_tool_spec(version="2.0.0"))
    await registry.rollback(_key(newest), "1.0.0", _QUEEN, "back")

    with pytest.raises(RollbackRefusedError, match="not older"):
        await registry.rollback(_key(newest), "2.0.0", _QUEEN, "forward")


async def test_rollback_obeys_scope_authority(registry: CombRegistry) -> None:
    spec = make_tool_spec(scope=ToolScope.CELL)
    await _promote(registry, spec)
    await _promote(
        registry,
        make_tool_spec(name=spec.name, scope=ToolScope.CELL, version="2.0.0", cell_id=spec.cell_id),
    )

    with pytest.raises(WrongCellError):
        await registry.rollback(_key(spec), "1.0.0", _warden_for(new_cell_id(FakeClock())), "x")
    change = await registry.rollback(_key(spec), "1.0.0", _warden_for(spec.cell_id), "mine")
    assert change.row.version == "1.0.0"


async def test_a_blank_reason_is_refused_before_anything_is_written(
    registry: CombRegistry,
) -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)

    with pytest.raises(ValueError, match="reason"):
        await registry.promote(spec, report, offer, _QUEEN, "  ")

    assert await registry.list_tools() == ()
