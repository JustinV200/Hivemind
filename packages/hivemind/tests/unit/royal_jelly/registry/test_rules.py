"""Tests for hivemind.royal_jelly.registry.rules: the pure policy every registry shares.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry/rules.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.registry.rules for the module under test.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from builders.royal_jelly import make_failing_report, make_offer, make_tool_spec

from hivemind.royal_jelly.errors import (
    NeedsReviewError,
    ReportFailedError,
    ReportMismatchError,
    RollbackRefusedError,
    ToolNotFoundError,
    VersionExistsError,
    WrongCellError,
    WrongPrincipalError,
)
from hivemind.royal_jelly.registry import (
    CombToolRow,
    InMemoryCombRegistry,
    QueenPrincipal,
    ToolKey,
    WardenPrincipal,
)
from hivemind.royal_jelly.registry.principals import Principal
from hivemind.royal_jelly.registry.rules import (
    WriteContext,
    build_promoted_row,
    check_authority,
    check_duplicate,
    check_promotion,
    check_rollback,
    pick_visible,
    version_key,
)
from hivemind.royal_jelly.tool_state import ToolState
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

_QUEEN = QueenPrincipal()
_CLOCK = FakeClock()


def _warden(cell_id: object) -> WardenPrincipal:
    return WardenPrincipal(new_warden_id(_CLOCK), cell_id)  # type: ignore[arg-type]


async def _row(
    version: str = "1.0.0", *, name: str = "echo_tool", scope: ToolScope = ToolScope.HIVE
) -> CombToolRow:
    """Promote one real tool into a fake registry and return its row."""
    spec = make_tool_spec(name=name, version=version, scope=scope)
    report, offer = make_offer(spec)
    principal: Principal = _QUEEN if scope is ToolScope.HIVE else _warden(spec.cell_id)
    return (await InMemoryCombRegistry(_CLOCK).promote(spec, report, offer, principal, "r")).row


# ─────────────────────────────── authority ───────────────────────────────


def test_the_queen_acts_at_hive_scope_and_a_warden_does_not() -> None:
    key = ToolKey("echo_tool", ToolScope.HIVE, None)

    check_authority(key, _QUEEN, queen_may_act_on_cell=False)
    with pytest.raises(WrongPrincipalError):
        check_authority(key, _warden(new_cell_id(_CLOCK)), queen_may_act_on_cell=True)


def test_the_owning_warden_acts_at_cell_scope_and_another_warden_does_not() -> None:
    cell = new_cell_id(_CLOCK)
    key = ToolKey("echo_tool", ToolScope.CELL, cell)

    check_authority(key, _warden(cell), queen_may_act_on_cell=False)
    with pytest.raises(WrongCellError):
        check_authority(key, _warden(new_cell_id(_CLOCK)), queen_may_act_on_cell=False)


def test_the_queen_may_retire_or_roll_back_cell_tools_but_never_promote_them() -> None:
    key = ToolKey("echo_tool", ToolScope.CELL, new_cell_id(_CLOCK))

    check_authority(key, _QUEEN, queen_may_act_on_cell=True)
    with pytest.raises(WrongPrincipalError):
        check_authority(key, _QUEEN, queen_may_act_on_cell=False)


# ─────────────────────────────── promotion ───────────────────────────────


def test_a_good_promotion_passes_every_check() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)

    check_promotion(spec, report, offer, _QUEEN)


def test_a_failing_report_is_refused() -> None:
    spec = make_tool_spec()
    _report, offer = make_offer(spec)
    failing = make_failing_report(spec, offer.package_sha256, offer.package_size_bytes)

    with pytest.raises(ReportFailedError, match="tests"):
        check_promotion(spec, failing, offer, _QUEEN)


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ("spec", "spec hash"),
        ("package", "package hash"),
        ("size", "package size"),
        ("name", "name"),
        ("version", "version"),
        ("tool_id", "tool id"),
    ],
)
def test_a_report_for_something_else_is_refused(change: str, fragment: str) -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    if change == "spec":
        report = report.model_copy(update={"spec_sha256": "b" * 64})
    elif change == "package":
        offer = replace(offer, package_sha256="c" * 64)
    elif change == "size":
        offer = replace(offer, package_size_bytes=offer.package_size_bytes + 1)
    else:
        report = report.model_copy(update={change: "other" if change != "version" else "9.9.9"})
        if change == "tool_id":
            report = report.model_copy(update={"tool_id": make_tool_spec().tool_id})

    with pytest.raises(ReportMismatchError, match=fragment):
        check_promotion(spec, report, offer, _QUEEN)


def test_a_real_cell_report_at_hive_scope_needs_the_queens_review() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec, path=QuarantinePath.REAL_CELL)

    with pytest.raises(NeedsReviewError):
        check_promotion(spec, report, offer, _QUEEN)
    check_promotion(spec, report, replace(offer, reviewed=True), _QUEEN)


def test_a_real_cell_report_at_cell_scope_needs_no_review() -> None:
    spec = make_tool_spec(scope=ToolScope.CELL)
    report, offer = make_offer(spec, path=QuarantinePath.REAL_CELL)

    check_promotion(spec, report, offer, _warden(spec.cell_id))


def test_a_review_flag_in_the_report_alone_does_not_stand_in_for_the_promotions() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec, path=QuarantinePath.REAL_CELL, reviewed=True)
    claimed = report.model_copy(update={"reviewed": True})

    with pytest.raises(NeedsReviewError):
        check_promotion(spec, claimed, replace(offer, reviewed=False), _QUEEN)


async def test_check_duplicate_refuses_an_existing_version_in_any_state() -> None:
    row = await _row()

    check_duplicate(None, row.spec)
    with pytest.raises(VersionExistsError):
        check_duplicate(row, row.spec)
    with pytest.raises(VersionExistsError):
        check_duplicate(replace(row, state=ToolState.RETIRED), row.spec)


async def test_build_promoted_row_stamps_who_why_and_when() -> None:
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    context = WriteContext(_QUEEN, "because", _CLOCK.now())

    row = build_promoted_row(spec, report, offer, context, "0.9.0")

    assert row.is_current
    assert row.state is ToolState.PROMOTED
    assert (row.promoted_by, row.reason, row.promoted_at) == ("queen", "because", _CLOCK.now())
    assert row.supersedes_version == "0.9.0"
    assert row.package_sha256 == offer.package_sha256
    assert row.spec_json == spec.to_json()


@pytest.mark.parametrize("reason", ["", "   ", "x" * 1001])
def test_a_write_context_refuses_an_empty_or_oversized_reason(reason: str) -> None:
    with pytest.raises(ValueError, match="reason"):
        WriteContext(_QUEEN, reason, _CLOCK.now())


# ─────────────────────────────── rollback ───────────────────────────────


async def test_check_rollback_accepts_an_older_promoted_non_current_version() -> None:
    older = replace(await _row("1.0.0"), is_current=False)
    current = await _row("2.0.0")

    assert check_rollback(older.key, older, current, "1.0.0") is older
    assert check_rollback(older.key, older, None, "1.0.0") is older  # After the current retired.


async def test_check_rollback_refuses_each_illegal_target() -> None:
    older = replace(await _row("1.0.0"), is_current=False)
    current = await _row("2.0.0")
    key = older.key

    with pytest.raises(ToolNotFoundError):
        check_rollback(key, None, current, "0.1.0")
    with pytest.raises(RollbackRefusedError, match="RETIRED"):
        check_rollback(key, replace(older, state=ToolState.RETIRED), current, "1.0.0")
    with pytest.raises(RollbackRefusedError, match="already the current"):
        check_rollback(key, replace(older, is_current=True), current, "1.0.0")
    with pytest.raises(RollbackRefusedError, match="not older"):
        check_rollback(key, replace(current, is_current=False), current, "2.0.0")


async def test_check_rollback_refuses_a_row_whose_report_no_longer_proves_it() -> None:
    older = replace(await _row("1.0.0"), is_current=False)
    current = await _row("2.0.0")
    forged_package = replace(older, package_sha256="d" * 64)
    unreadable = replace(older, report_json="{not json")
    failing = make_failing_report(older.spec, older.package_sha256)
    failed_report = replace(older, report_json=failing.to_json())

    for bad in (forged_package, unreadable, failed_report):
        with pytest.raises(RollbackRefusedError):
            check_rollback(bad.key, bad, current, "1.0.0")


# ─────────────────────────────── helpers ───────────────────────────────


def test_version_key_orders_numerically_not_lexically() -> None:
    assert version_key("1.10.0") > version_key("1.9.0")
    assert version_key("2.0.0") > version_key("1.99.99")
    assert version_key("1.0.0") == (1, 0, 0)


async def test_pick_visible_prefers_a_hive_row_over_a_cell_row_of_the_same_name() -> None:
    hive = await _row(name="shared_tool")
    cell = await _row(name="shared_tool", scope=ToolScope.CELL)
    other = await _row(name="another_tool", scope=ToolScope.CELL)

    assert pick_visible([cell, hive, other]) == (other, hive)
    assert pick_visible([hive, cell, other]) == (other, hive)
    assert pick_visible([]) == ()
