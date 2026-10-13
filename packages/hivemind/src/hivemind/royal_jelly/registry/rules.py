"""Decide what the Comb Registry refuses: the pure checks every implementation shares.

The Comb Registry (the catalogue of promoted tools) is the source of truth for executable tools,
so what it will and will not record is policy, not storage. This module holds that policy as pure
functions over values (no I/O, no clock): who may act at which scope, whether a Quarantine Comb
report really proves the spec and package offered, whether a rollback target is legal, and how a
promoted row is built. `SqliteCombRegistry` and `InMemoryCombRegistry` both call these inside
their own transaction, so the two cannot drift, and the contract suite holds them to it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.registry. Called
    by the SQLite store and the in-memory fake; calls into the spec package, the error tree and
    the row values. Never imports a store.

Key invariants:
    - HIVE scope is the Queen's alone; CELL promotion is the owning Warden's alone. The Queen may
      additionally retire or roll back a CELL-scope tool (operator control, via `hive tools`).
    - There is no force flag: a promotion needs a passing report whose spec and package hashes and
      size equal what is offered, and a REAL_CELL-path report at HIVE scope needs `reviewed=True`.
    - A rollback target must be a PROMOTED, non-current, strictly older version whose own stored
      report still passes and still names its own spec and package.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the refusal list.
    - hivemind.royal_jelly.errors for the refusals raised here.
    - tests/unit/royal_jelly/registry/test_no_bypass.py for the proof each refusal holds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

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
from hivemind.royal_jelly.registry.principals import Principal, QueenPrincipal, WardenPrincipal
from hivemind.royal_jelly.registry.rows import CombToolRow, PromotionOffer, ToolKey
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec, spec_sha256
from hivemind.royal_jelly.tool_state import ToolState
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

_VERSION_PART = re.compile(r"\d+")  # The numeric parts of a three-number semantic version.

__all__ = [
    "WriteContext",
    "build_promoted_row",
    "check_authority",
    "check_duplicate",
    "check_promotion",
    "check_rollback",
    "pick_visible",
    "version_key",
]


@dataclass(frozen=True, slots=True)
class WriteContext:
    """Who is writing, why, and when: the facts every write stamps on its row."""

    principal: Principal  # The acting principal.
    reason: str  # The reason the principal gave.
    at: datetime  # The registry clock's instant.

    def __post_init__(self) -> None:
        """Refuse an empty or oversized reason: it is stamped on a row and sent over the wire."""
        # The trail records why every registry change happened, so a blank reason is a caller bug;
        # the bound is waggle's own, the one ToolPromoted.reason carries.
        if not self.reason.strip() or len(self.reason) > MAX_REASON_CHARS:
            raise ValueError(
                f"A registry write needs a reason of 1 to {MAX_REASON_CHARS} characters."
            )


def version_key(version: str) -> tuple[int, ...]:
    """Return a version's numeric parts, so "1.10.0" sorts after "1.9.0".

    Args:
        version: Semantic version text (`VERSION_PATTERN`).

    Returns:
        The integer parts, comparable as a tuple.
    """
    return tuple(int(part) for part in _VERSION_PART.findall(version))


def check_authority(key: ToolKey, principal: Principal, *, queen_may_act_on_cell: bool) -> None:
    """Refuse a principal that may not act on `key`'s scope.

    Args:
        key: The tool being written.
        principal: The acting principal.
        queen_may_act_on_cell: True for retire and rollback (operator control through the Queen);
            False for promotion, which only the owning Warden makes at CELL scope.

    Raises:
        WrongPrincipalError: HIVE scope from a non-Queen, or CELL promotion from a non-Warden.
        WrongCellError: A Warden acting on another Cell's tool.
    """
    match (key.scope, principal):
        case (ToolScope.HIVE, QueenPrincipal()):
            return  # The Queen alone acts at HIVE scope.
        case (ToolScope.HIVE, WardenPrincipal()):
            raise WrongPrincipalError(
                f"A Warden cannot act on HIVE-scope tool {key.name!r}; only the Queen may."
            )
        case (ToolScope.CELL, QueenPrincipal()) if queen_may_act_on_cell:
            return  # Retire and rollback are operator controls the Queen carries out.
        case (ToolScope.CELL, QueenPrincipal()):
            raise WrongPrincipalError(
                f"The Queen cannot promote CELL-scope tool {key.name!r}; its Warden does."
            )
        case (ToolScope.CELL, WardenPrincipal(cell_id=cell_id)) if cell_id == key.cell_id:
            return  # A Warden acts for its own Cell.
        case (ToolScope.CELL, WardenPrincipal(cell_id=cell_id)):
            raise WrongCellError(
                f"A Warden of Cell {cell_id} cannot act on tool {key.name!r} of Cell {key.cell_id}."
            )
    raise WrongPrincipalError(
        f"{principal!r} may not act on tool {key.name!r}."
    )  # pragma: no cover


def check_promotion(
    spec: ToolSpec, report: QuarantineReport, offer: PromotionOffer, principal: Principal
) -> None:
    """Refuse a promotion unless authority, evidence and review all hold.

    Args:
        spec: The spec offered.
        report: The Quarantine Comb's report offered as evidence.
        offer: The package hash, size and review flag the promotion claims.
        principal: The acting principal.

    Raises:
        WrongPrincipalError: Wrong kind of principal for the scope.
        WrongCellError: A Warden promoting for another Cell.
        ReportFailedError: The report did not pass.
        ReportMismatchError: The report proves a different spec or package.
        NeedsReviewError: A REAL_CELL-path report at HIVE scope without `offer.reviewed`.
    """
    # Authority first: it is the cheapest check and decides whether the rest is anyone's business.
    check_authority(
        ToolKey(spec.name, spec.scope, spec.cell_id), principal, queen_may_act_on_cell=False
    )
    # There is no force flag (codingrules section 15): no passing report, no promotion.
    if not report.passed:
        raise ReportFailedError(
            f"The Quarantine Comb report for {spec.name} {spec.version} failed gate "
            f"{report.failing_gate!r}; a failing report cannot promote."
        )
    _check_report_matches(spec, report, offer)
    # The Queen reads a Real-Cell-path report herself before a tool reaches every Cell (9.3a).
    if (
        report.path is QuarantinePath.REAL_CELL
        and spec.scope is ToolScope.HIVE
        and not offer.reviewed
    ):
        raise NeedsReviewError(
            f"{spec.name} {spec.version} was proven on a Real Cell; HIVE scope needs the Queen's "
            "review."
        )


def _check_report_matches(spec: ToolSpec, report: QuarantineReport, offer: PromotionOffer) -> None:
    """Refuse a report that proves a different tool, spec, package or size than offered."""
    mismatches = [
        label
        for label, same in (
            ("tool id", report.tool_id == spec.tool_id),
            ("name", report.name == spec.name),
            ("version", report.version == spec.version),
            ("spec hash", report.spec_sha256 == spec_sha256(spec)),
            ("package hash", report.package_sha256 == offer.package_sha256),
            ("package size", report.package_size_bytes == offer.package_size_bytes),
        )
        if not same
    ]
    if mismatches:
        raise ReportMismatchError(
            f"The report for {spec.name} {spec.version} does not match the offer: "
            f"{', '.join(mismatches)} differ."
        )


def build_promoted_row(
    spec: ToolSpec,
    report: QuarantineReport,
    offer: PromotionOffer,
    context: WriteContext,
    supersedes_version: str | None,
) -> CombToolRow:
    """Build the PROMOTED, current row a checked promotion stores.

    Args:
        spec: The spec offered (already checked).
        report: The passing report (already checked).
        offer: The package facts (already checked).
        context: Who promoted, why and when.
        supersedes_version: The version it displaces as current, if any.

    Returns:
        The row, with `is_current` True.
    """
    return CombToolRow(
        tool_id=spec.tool_id,
        name=spec.name,
        version=spec.version,
        scope=spec.scope,
        cell_id=spec.cell_id,
        spec_json=spec.to_json(),
        package_sha256=offer.package_sha256,
        package_size=offer.package_size_bytes,
        report_json=report.to_json(),
        quarantine_path=report.path,
        reviewed=offer.reviewed,
        promoted_by=context.principal.label,
        promoted_at=context.at,
        state=ToolState.PROMOTED,
        is_current=True,
        supersedes_version=supersedes_version,
        retired_at=None,
        retired_reason=None,
        reason=context.reason,
    )


def check_duplicate(existing: CombToolRow | None, spec: ToolSpec) -> None:
    """Refuse a version that already exists for that (name, scope, Cell).

    Args:
        existing: The row already stored at the spec's version, if any.
        spec: The spec being promoted.

    Raises:
        VersionExistsError: A row exists, whatever its state: versions are immutable.
    """
    if existing is not None:
        raise VersionExistsError(
            f"{spec.name} {spec.version} already exists at {spec.scope.value} scope "
            f"(state {existing.state.value}); versions are immutable, promote a new version."
        )


def check_rollback(
    key: ToolKey, target: CombToolRow | None, current: CombToolRow | None, to_version: str
) -> CombToolRow:
    """Refuse a rollback target that is not a legal, evidenced, older PROMOTED version.

    Args:
        key: The tool being rolled back.
        target: The row at `to_version`, if any.
        current: The tool's current row, if any (none after the current one was retired).
        to_version: The version asked for.

    Returns:
        `target`, once every check passes.

    Raises:
        ToolNotFoundError: No such version exists.
        RollbackRefusedError: Retired, already current, not older than current, or its own stored
            report no longer proves it.
    """
    if target is None:
        raise ToolNotFoundError(f"Tool {key.name!r} has no version {to_version} to roll back to.")
    if target.state is not ToolState.PROMOTED:
        raise RollbackRefusedError(
            f"{key.name} {to_version} is {target.state.value}, not PROMOTED."
        )
    if target.is_current:
        raise RollbackRefusedError(f"{key.name} {to_version} is already the current version.")
    if current is not None and version_key(target.version) >= version_key(current.version):
        raise RollbackRefusedError(
            f"{key.name} {to_version} is not older than the current {current.version}."
        )
    _check_own_evidence(target)
    return target


def _check_own_evidence(row: CombToolRow) -> None:
    """Refuse a row whose stored report no longer passes or names a different spec or package."""
    try:
        report = row.report
        spec = row.spec
    except ValidationError as exc:
        raise RollbackRefusedError(
            f"{row.name} {row.version} has an unreadable stored record."
        ) from exc
    # The row's own passing report is its evidence (no new quarantine on rollback); the hashes
    # tie that report to the spec and package this row actually holds.
    if not (
        report.passed
        and report.spec_sha256 == spec_sha256(spec)
        and report.package_sha256 == row.package_sha256
    ):
        raise RollbackRefusedError(f"{row.name} {row.version}'s stored report does not prove it.")


def pick_visible(current_rows: Iterable[CombToolRow]) -> tuple[CombToolRow, ...]:
    """Choose the rows a Cell's bees may see from its candidate current rows.

    Args:
        current_rows: Current PROMOTED rows at HIVE scope and at the Cell's own CELL scope.

    Returns:
        One row per tool name, ordered by name. When a HIVE and a CELL tool share a name, the HIVE
        row wins: a Warden-promoted tool must never shadow a Queen-promoted one.
    """
    chosen: dict[str, CombToolRow] = {}
    for row in current_rows:
        held = chosen.get(row.name)
        # Keep the first row seen for a name unless a HIVE row arrives to displace a CELL one.
        if held is None or (row.scope is ToolScope.HIVE and held.scope is ToolScope.CELL):
            chosen[row.name] = row
    return tuple(chosen[name] for name in sorted(chosen))
