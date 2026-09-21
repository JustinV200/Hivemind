"""Provide the leave policy: pure autopilot deciding whether a Leaving persists, ALLOW/ASK/DENY.

Roadmap step 5.0c. `model.py` defines the policy's own data (`PathClass`, `LeaveVerdict`,
`LeaveRequest`, `LeaveCellFacts`); `classify.py` classifies a resolved path into a `PathClass`;
`executable.py` guesses whether a path is an executable; `matcher.py` matches a resolved path
against a task's declared `leaves` (roadmap step 5.0b); `table.py` loads `leave-policy.toml` as
data (codingrules section 13); `policy.py` is `decide`, the pure function that ties the rest
together. `hivemind.supervision.capping.apply` is the one effectful caller: it gathers each
outside-scratch path's already-known facts (reading the session, applying the diff), calls
`matches_leaving` then `decide`, and turns ALLOW/DENY into `persist=True`/`False` on
`LeaseView.note_restore_path` (ASK is roadmap step 5.0d's own `HumanCheck`, in `.checks.human`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping`.
    Built by whichever composition root builds a `hivemind.wardens.deps.WardenDeps` (production:
    `cli/compose/deps.py`; tests: `tests/builders/wardens.make_warden_deps`); read by `hivemind.
    supervision.capping.apply` through `hivemind.supervision.capping.gate.GateDeps`. Calls into
    `hivemind.cell` (AccessLevel, CombShieldLevel, OsFamily), `hivemind.supervision.capping.errors`
    and waggle only; never `hivemind.wardens` or `hivemind.queen` (codingrules section 4: Capping
    sits below both in the layer table).

Key invariants:
    - `decide` is pure (codingrules section 8.3): no I/O, no clock, no randomness. Every
      already-known input is gathered by `hivemind.supervision.capping.apply` before calling it.
    - A path no plan declared is never persisted (roadmap step 5.0b's hard rule): `matches_leaving`
      returning None is what makes `decide`'s own `declared` argument False, and `decide` checks
      that before anything else.

See Also:
    - .claude/roadmap.md step 5.0c for this package's own deliverable, verbatim.
    - .claude/roadmap.md phase 5 preamble for "declared by the plan, decided by policy, asked of
      the human only when the policy says so."
    - hivemind.supervision.capping.apply for the one effectful caller.
    - hivemind.supervision.capping.checks.human for HumanCheck, roadmap step 5.0d's own ASK rung.

Public API:
    - PathClass, LeaveVerdict, LeaveRequest, LeaveCellFacts: the policy's own data (model).
    - classify_path: pure path classification (classify).
    - looks_executable: a pure, conservative executable guess (executable).
    - matches_leaving: match a resolved path against declared leaves (matcher).
    - LeavePolicyTable, LeavePolicyClasses, ClassPolicy, HomePolicy, SingleVerdict,
      GeneralSettings, load_leave_policy, DEFAULT_LEAVE_POLICY_FILENAME: leave-policy.toml, as
      data (table).
    - decide: the pure policy function (policy).
    - Asker, LeaveApplyContext, LeaveDecisionRecord, LeavePersistDecision, HIVE_STAND_SOURCE,
      DEFAULT_HUMAN_TIMEOUT_S, build_leave_context, with_asker, decide_persist: turning a verdict
      into persist/approved_by for apply.py, and the seam HumanCheck (roadmap step 5.0d) asks
      through (persist).
    - LeaveHumanVerdict: the human's own closed answer to an ASK verdict (model).
    - leave_decided_payload: the capping.leave_decided trail payload for one decision (trail).
    - ScannedFile, MAX_SCAN_FILES, MAX_SCAN_FILE_BYTES, declared_leaving_root,
      scan_declared_leaves: the `run_command` before/after scan, roadmap step 5.0e (scan).
"""

from hivemind.supervision.capping.leave.classify import classify_path
from hivemind.supervision.capping.leave.executable import looks_executable
from hivemind.supervision.capping.leave.matcher import matches_leaving
from hivemind.supervision.capping.leave.model import (
    LeaveCellFacts,
    LeaveHumanVerdict,
    LeaveRequest,
    LeaveVerdict,
    PathClass,
)
from hivemind.supervision.capping.leave.persist import (
    DEFAULT_HUMAN_TIMEOUT_S,
    HIVE_STAND_SOURCE,
    Asker,
    LeaveApplyContext,
    LeaveDecisionRecord,
    LeavePersistDecision,
    build_leave_context,
    decide_persist,
    with_asker,
)
from hivemind.supervision.capping.leave.policy import decide
from hivemind.supervision.capping.leave.scan import (
    MAX_SCAN_FILE_BYTES,
    MAX_SCAN_FILES,
    ScannedFile,
    declared_leaving_root,
    scan_declared_leaves,
)
from hivemind.supervision.capping.leave.table import (
    DEFAULT_LEAVE_POLICY_FILENAME,
    ClassPolicy,
    GeneralSettings,
    HomePolicy,
    LeavePolicyClasses,
    LeavePolicyTable,
    SingleVerdict,
    load_leave_policy,
)
from hivemind.supervision.capping.leave.trail import leave_decided_payload

__all__ = [
    "DEFAULT_HUMAN_TIMEOUT_S",
    "DEFAULT_LEAVE_POLICY_FILENAME",
    "HIVE_STAND_SOURCE",
    "MAX_SCAN_FILES",
    "MAX_SCAN_FILE_BYTES",
    "Asker",
    "ClassPolicy",
    "GeneralSettings",
    "HomePolicy",
    "LeaveApplyContext",
    "LeaveCellFacts",
    "LeaveDecisionRecord",
    "LeaveHumanVerdict",
    "LeavePersistDecision",
    "LeavePolicyClasses",
    "LeavePolicyTable",
    "LeaveRequest",
    "LeaveVerdict",
    "PathClass",
    "ScannedFile",
    "SingleVerdict",
    "build_leave_context",
    "classify_path",
    "decide",
    "decide_persist",
    "declared_leaving_root",
    "leave_decided_payload",
    "load_leave_policy",
    "looks_executable",
    "matches_leaving",
    "scan_declared_leaves",
    "with_asker",
]
