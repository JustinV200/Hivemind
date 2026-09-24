"""Define decide: the pure autopilot leave policy, roadmap step 5.0c.

`decide(request, cell, declared, table) -> ALLOW | ASK | DENY`. Pure autopilot (codingrules section
8.3: decision logic over plain data, no I/O) over already-known facts -- `hivemind.supervision.
capping.apply` is the one effectful caller that gathers them and applies the answer. `table` is the
fourth argument, not folded into a module global: roadmap step 5.0c's own sentence names three
("request, cell, declared"), but `decide` still needs the operator-editable verdicts themselves
(`hivemind.supervision.capping.leave.table.LeavePolicyTable`, loaded once by whichever composition
root builds a `hivemind.wardens.deps.WardenDeps`) to stay pure and testable without any module-level
state (codingrules section 5.5) -- the same reason `hivemind.supervision.capping.tiers.checks_for`
takes a `TierSpec` explicitly rather than reading one off a shared table.

The one hard rule from roadmap step 5.0b this function enforces first, before anything else is
looked at: "a path no plan declared is never persisted, whatever a tool result or a bee says."
`declared` is already the answer to "does this path match the task's `leaves`" (`hivemind.
supervision.capping.leave.matcher.matches_leaving`, called by `apply.py` before this function), so
`decide` need only check it is True.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Called by `hivemind.supervision.capping.apply` once per outside-scratch path, from
    roadmap step 5.0c's own dispatch onward. Calls into `hivemind.cell` (AccessLevel,
    CombShieldLevel) and `hivemind.supervision.capping.leave.model`/`.table` only.

Key invariants:
    - `not declared` is checked first and always returns DENY, before AccessLevel, CombShieldLevel
      or any path class row is even read (roadmap step 5.0b's hard rule; a test asserts this order
      by giving every other input its most permissive value and still getting DENY).
    - READ_ONLY/SCRATCH access and NIGHT_VEIL comb shield are checked next, both always DENY,
      before any path-class row -- an undeclared or unreachable Cell never reaches keep_root's own
      ALLOW default.
    - PathClass.KEEP_ROOT is checked before the executable check: a declared, reachable path under
      keep_root is ALLOW even when it is also executable (roadmap step 5.0c's own suggested
      default table: "keep_root -> ALLOW" is unconditional once the DENY gates above pass).
    - Every other branch reads exactly one `LeavePolicyTable` row; `decide` itself never invents a
      verdict the table does not name.

See Also:
    - .claude/roadmap.md step 5.0c for this function's signature and suggested default table,
      verbatim.
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges."
    - hivemind.supervision.capping.leave.model for LeaveRequest, LeaveCellFacts and LeaveVerdict.
    - hivemind.supervision.capping.leave.table for LeavePolicyTable, this function's fourth input.
    - hivemind.supervision.capping.apply for the one effectful caller.
"""

from __future__ import annotations

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.supervision.capping.leave.model import (
    LeaveCellFacts,
    LeaveRequest,
    LeaveVerdict,
    PathClass,
)
from hivemind.supervision.capping.leave.table import ClassPolicy, LeavePolicyTable

# AccessLevel values below which a leaving is always DENY, whatever else is true (roadmap 5.0c).
_ALWAYS_DENY_ACCESS = frozenset({AccessLevel.READ_ONLY, AccessLevel.SCRATCH})

__all__ = ["decide"]


def decide(
    request: LeaveRequest, cell: LeaveCellFacts, declared: bool, table: LeavePolicyTable
) -> LeaveVerdict:
    """Decide ALLOW, ASK or DENY for one path a proposal wants to persist past its lease.

    Args:
        request: The path's own already-known facts: its class, size and whether it looks
            executable.
        cell: The Cell's own already-known facts: AccessLevel, CombShieldLevel, whether it is the
            Hive Stand.
        declared: Whether the path matches the task's own declared `leaves`
            (`hivemind.supervision.capping.leave.matcher.matches_leaving`, already run by the
            caller). False always means DENY (module docstring's own hard rule), checked first.
        table: The operator-editable verdict table (`hivemind.supervision.capping.leave.table.
            load_leave_policy`).

    Returns:
        ALLOW (persist, approved_by=POLICY), ASK (raise a Question, roadmap step 5.0d) or DENY
        (persist=False, restored on release exactly as before roadmap phase 5).
    """
    # Roadmap 5.0b's hard rule: an undeclared path is never even considered for the checks below.
    if not declared:
        return LeaveVerdict.DENY
    # A Cell the Hive may only read, or may only write inside its own lease's scratch, can never
    # keep anything outside scratch regardless of what the plan declared.
    if cell.access_level in _ALWAYS_DENY_ACCESS:
        return LeaveVerdict.DENY
    # Night Veil Cells are virtual-only and teardown-only (codingrules section 8.7): nothing on
    # one is ever meant to outlive its lease.
    if cell.comb_shield is CombShieldLevel.NIGHT_VEIL:
        return LeaveVerdict.DENY
    # keep_root is the one class the suggested default table allows unconditionally, even for an
    # executable: it exists specifically to hold what a task is meant to leave behind.
    if request.path_class is PathClass.KEEP_ROOT:
        return table.classes.keep_root.verdict
    # An executable anywhere else always asks, whatever its own path class would otherwise say.
    if request.is_executable:
        return table.classes.executable.verdict
    if request.path_class is PathClass.STARTUP:
        return _by_cell(table.classes.startup, cell)
    if request.path_class is PathClass.SYSTEM:
        return _by_cell(table.classes.system, cell)
    if request.path_class is PathClass.OTHER:
        return _by_cell(table.classes.other, cell)
    # PathClass.HOME is what remains: over the size threshold asks regardless of Hive Stand vs
    # borrowed; under it, the ordinary hive-stand/borrowed split applies.
    if request.size > table.general.max_home_bytes:
        return table.classes.home.over_threshold_verdict
    return _by_cell(table.classes.home, cell)


def _by_cell(row: ClassPolicy, cell: LeaveCellFacts) -> LeaveVerdict:
    """Return `row.hive_stand_verdict` or `.borrowed_verdict`, chosen by `cell.is_hive_stand`.

    Args:
        row: A `ClassPolicy` row, or its `HomePolicy` subclass (`.table`), which adds
            `over_threshold_verdict` but keeps these same two fields.
        cell: Supplies `is_hive_stand`.

    Returns:
        The row's own verdict for this Cell.
    """
    return row.hive_stand_verdict if cell.is_hive_stand else row.borrowed_verdict
