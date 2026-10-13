"""Prove the Comb Registry has no bypass: every refusal holds and no other door makes a tool.

Roadmap 9.2 asks for "a test asserts there is no bypass". Two halves. First, each refusal in the
design (ADR-0046) is exercised against `SqliteCombRegistry` and the table is shown unchanged
afterwards, so a refused promotion leaves no trace. Second, the store's public surface is checked
by reflection to be exactly the protocol's methods, and every method other than `promote` and
`rollback` is shown unable to leave a PROMOTED, current row behind.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/registry (codingrules section 3 and 15: tools never skip the
    Quarantine Comb; there is no `force=True`).

Key invariants:
    - A refused write never changes a row.
    - The only methods that can create or flip a current PROMOTED row are promote and rollback.

See Also:
    - hivemind.royal_jelly.registry.sqlite for the store whose surface is checked.
    - hivemind.royal_jelly.registry.protocol for the methods that surface must equal.
"""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import dataclass, replace

import pytest
from builders.royal_jelly import make_failing_report, make_offer, make_tool_spec

from hivemind.common.sqlite import connect
from hivemind.royal_jelly.errors import (
    NeedsReviewError,
    PromotionRefusedError,
    ReportFailedError,
    ReportMismatchError,
    VersionExistsError,
    WrongCellError,
    WrongPrincipalError,
)
from hivemind.royal_jelly.registry import (
    CombRegistry,
    Principal,
    QueenPrincipal,
    SqliteCombRegistry,
    ToolKey,
    WardenPrincipal,
)
from hivemind.royal_jelly.spec import ToolSpec
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_warden_id
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

_QUEEN = QueenPrincipal()
_SNAPSHOT_SQL = "SELECT * FROM comb_tools ORDER BY row_id"
_CONSTRUCTORS = {"create"}  # Classmethods that build the store; they write no tool row.


async def _open() -> tuple[SqliteCombRegistry, sqlite3.Connection]:
    connection = connect(":memory:")
    return await SqliteCombRegistry.create(connection, FakeClock()), connection


def _table(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [tuple(record) for record in connection.execute(_SNAPSHOT_SQL).fetchall()]


def _current_promoted(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(record)
        for record in connection.execute(
            "SELECT name, scope, cell_id, version FROM comb_tools "
            "WHERE state = 'PROMOTED' AND is_current = 1 ORDER BY row_id"
        ).fetchall()
    ]


def _warden(cell_id: CellId | None) -> WardenPrincipal:
    assert cell_id is not None
    return WardenPrincipal(new_warden_id(FakeClock()), cell_id)


@dataclass(frozen=True, slots=True)
class _Attempt:
    """How a refused promotion is set up: who asks, and what is wrong with the offer."""

    principal: Principal = _QUEEN
    failing: bool = False  # The report failed a gate.
    mismatch: str | None = None  # "package" or "spec": the report proves something else.
    path: QuarantinePath = QuarantinePath.SANDBOX_CELL
    reviewed: bool = False  # The offer's review flag.
    seed: bool = False  # Promote the same spec first, so the second try is a duplicate.


async def _refused(spec: ToolSpec, attempt: _Attempt | None = None) -> PromotionRefusedError:
    """Promote `spec` expecting a refusal; return it after proving the table did not change."""
    attempt = attempt or _Attempt()
    registry, connection = await _open()
    report, offer = make_offer(spec, reviewed=attempt.reviewed, path=attempt.path)
    if attempt.seed:
        await registry.promote(spec, report, offer, _QUEEN, "first")
    if attempt.failing:
        report = make_failing_report(spec, offer.package_sha256, offer.package_size_bytes)
    if attempt.mismatch == "package":
        offer = replace(offer, package_sha256="e" * 64)
    if attempt.mismatch == "spec":
        report = report.model_copy(update={"spec_sha256": "f" * 64})
    before = _table(connection)

    with pytest.raises(PromotionRefusedError) as raised:
        await registry.promote(spec, report, offer, attempt.principal, "try")

    assert _table(connection) == before  # A refused promotion leaves no trace.
    return raised.value


async def test_a_report_that_did_not_pass_cannot_promote() -> None:
    error = await _refused(make_tool_spec(), _Attempt(failing=True))

    assert isinstance(error, ReportFailedError)
    assert error.refusal == "report_failed"


@pytest.mark.parametrize("mismatch", ["package", "spec"])
async def test_a_report_for_another_package_or_spec_cannot_promote(mismatch: str) -> None:
    error = await _refused(make_tool_spec(), _Attempt(mismatch=mismatch))

    assert isinstance(error, ReportMismatchError)
    assert error.refusal == "report_mismatch"


async def test_hive_scope_refuses_a_warden() -> None:
    error = await _refused(make_tool_spec(), _Attempt(principal=_warden(new_cell_id(FakeClock()))))

    assert isinstance(error, WrongPrincipalError)
    assert error.refusal == "wrong_principal"


async def test_cell_scope_refuses_the_queen() -> None:
    error = await _refused(make_tool_spec(scope=ToolScope.CELL), _Attempt(principal=_QUEEN))

    assert isinstance(error, WrongPrincipalError)


async def test_cell_scope_refuses_a_warden_of_another_cell() -> None:
    spec = make_tool_spec(scope=ToolScope.CELL)

    error = await _refused(spec, _Attempt(principal=_warden(new_cell_id(FakeClock()))))

    assert isinstance(error, WrongCellError)
    assert error.refusal == "wrong_cell"


async def test_an_existing_version_cannot_be_promoted_again() -> None:
    error = await _refused(make_tool_spec(), _Attempt(seed=True))

    assert isinstance(error, VersionExistsError)
    assert error.refusal == "version_exists"


async def test_a_real_cell_path_report_at_hive_scope_needs_review() -> None:
    error = await _refused(make_tool_spec(), _Attempt(path=QuarantinePath.REAL_CELL))

    assert isinstance(error, NeedsReviewError)
    assert error.refusal == "needs_review"


async def test_a_reviewed_real_cell_path_report_at_hive_scope_promotes() -> None:
    registry, _ = await _open()
    spec = make_tool_spec()
    report, offer = make_offer(spec, path=QuarantinePath.REAL_CELL, reviewed=True)

    change = await registry.promote(spec, report, offer, _QUEEN, "reviewed")

    assert change.row.is_current
    assert change.row.reviewed


async def test_the_owning_warden_promotes_its_cell_tool() -> None:
    registry, _ = await _open()
    spec = make_tool_spec(scope=ToolScope.CELL)
    report, offer = make_offer(spec)

    change = await registry.promote(spec, report, offer, _warden(spec.cell_id), "mine")

    assert change.row.cell_id == spec.cell_id


def test_promote_has_no_force_or_bypass_parameter() -> None:
    parameters = set(inspect.signature(SqliteCombRegistry.promote).parameters) - {"self"}

    assert parameters == {"spec", "report", "offer", "principal", "reason"}
    assert set(inspect.signature(CombRegistry.promote).parameters) - {"self"} == parameters


def test_the_stores_public_surface_is_exactly_the_protocols_methods() -> None:
    def public(cls: type) -> set[str]:
        return {
            name
            for name, member in inspect.getmembers(cls)
            if not name.startswith("_") and callable(member)
        }

    protocol_methods = public(CombRegistry)
    store_methods = public(SqliteCombRegistry) - _CONSTRUCTORS

    assert protocol_methods == {
        "list_tools",
        "get",
        "visible_tools",
        "promote",
        "retire",
        "rollback",
    }
    assert store_methods == protocol_methods
    assert not [
        name
        for name in vars(SqliteCombRegistry)
        if not name.startswith("_") and name not in protocol_methods | _CONSTRUCTORS
    ]


async def test_only_promote_and_rollback_can_leave_a_current_promoted_row() -> None:
    registry, connection = await _open()
    first, second = make_tool_spec(version="1.0.0"), make_tool_spec(version="2.0.0")
    key = ToolKey(first.name, ToolScope.HIVE, None)
    for spec in (first, second):
        report, offer = make_offer(spec)
        await registry.promote(spec, report, offer, _QUEEN, "promote")
    after_promotes = _current_promoted(connection)
    assert after_promotes == [(first.name, "HIVE", "", "2.0.0")]

    # Every read, and retire, leaves the set of current PROMOTED rows no larger than before.
    await registry.list_tools()
    await registry.get(first.name, ToolScope.HIVE, None)
    await registry.visible_tools(new_cell_id(FakeClock()))
    assert _current_promoted(connection) == after_promotes
    await registry.retire(key, "2.0.0", _QUEEN, "withdraw")
    assert _current_promoted(connection) == []

    # Retire can only clear the flag; it cannot make 1.0.0 current, only rollback can.
    await registry.rollback(key, "1.0.0", _QUEEN, "restore")
    assert _current_promoted(connection) == [(first.name, "HIVE", "", "1.0.0")]


async def test_retire_and_rollback_obey_the_same_scope_authority() -> None:
    registry, connection = await _open()
    spec = make_tool_spec()
    report, offer = make_offer(spec)
    await registry.promote(spec, report, offer, _QUEEN, "first")
    before = _table(connection)
    key = ToolKey(spec.name, ToolScope.HIVE, None)
    intruder = _warden(new_cell_id(FakeClock()))

    with pytest.raises(WrongPrincipalError):
        await registry.retire(key, spec.version, intruder, "nope")
    with pytest.raises(WrongPrincipalError):
        await registry.rollback(key, spec.version, intruder, "nope")

    assert _table(connection) == before
