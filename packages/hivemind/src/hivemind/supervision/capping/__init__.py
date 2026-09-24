"""Provide the Capping gate: nothing with a side effect outside scratch lands uncapped.

Codingrules section 8.12 (the beekeeping term this package is named for: a colony caps a honey
cell only once the honey is ripe): a bee's work is provisional until the Capping gate has checked
it, applied it and verified its declared postconditions. `proposal.py` defines what a bee wants to
do (`Proposal`); `state.py` its one state machine (`PROPOSED -> CHECKING -> CAPPED -> APPLIED ->
VERIFIED`, `CHECKING -> REJECTED`, `APPLIED -> ROLLED_BACK`); `tiers.py` the risk-tier vocabulary,
the loader for `supervision/defaults/capping-tiers.toml`, and `checks_for`, how a task's tempo may
shorten or lengthen a tier's check ladder within its floor (codingrules section 8.14); `lease_view.
py` the Protocol seam to a Real Cell's lease; `checks/` the check-ladder seam, this phase's
deterministic rungs and the independent judge rung (`JudgeCheck`, `JudgeReviewer`, rubrics as
data); `diff.py` and `apply.py` how a DIFF or COMMAND action is actually applied; `postconditions.
py` how one assertion is checked after applying; `gate.py` the `CappingGate` that ties every module
here into one propose-check-cap-apply-verify walk, writing a `capping.*` trail event on every
transition; and `audit.py` the after-the-fact half (roadmap step 4.10): sampling a tier's
completed proposals for judge review when nothing gates them in real time. The proposer never
verifies its own work (codingrules section 8.12): `CappingGate.run` is called by the proposing
bee's Warden, never by the bee itself, and neither is `JudgeCheck` or `audit_completed`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Built
    by a Warden (roadmap step 3.19) from a `hivemind.supervision.capping.GateDeps`; proposed into
    by Worker tools (`hivemind.workers.tools`, roadmap step 3.16). Calls into `hivemind.cell`,
    `hivemind.forage`, `hivemind.guard`, `hivemind.pheromone`, `hivemind.supervision` and waggle
    only; never `hivemind.llm` (codingrules section 4: nothing under either autopilot package may
    import it, and this package sits under both) and never `hivemind.memory` or `hivemind.
    honey_store` (`audit.FindingsSink` is the seam a later phase implements against those).

Key invariants:
    - Whatever is not re-exported here is private to this package (codingrules section 5.4).
    - `RiskTier`'s member names and values mirror `waggle.messages.capping.RiskTier`'s exactly
      (codingrules section 6.1); every other waggle capping/label type this package touches
      (`ActionKind`, `ProposedAction`, `CheckKind`, `CheckOutcome`, `Postcondition`,
      `PostconditionKind`, `RollbackMethod`) is carried directly, not mirrored, because it names
      no behaviour of its own.

See Also:
    - .claude/codingrules.md section 8.12 for the Capping gate's whole shape.
    - .claude/codingrules.md section 8.14 for the tempo-reads-capping rule checks_for implements.
    - .claude/codingrules.md Appendix C, "Proposal" row, for this package's state machine.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the decisions behind tiers
      as data, fail-closed checks, `LeaseView` as a Protocol seam, and REVERSE_DIFF vs snapshot
      rollback.
    - supervision/defaults/capping-tiers.toml and judge-rubrics.toml for the Hive's shipped v0
      tables.
    - hivemind.supervision for the Supervisor/Alarm/Attendant shape this package's caller
      (a Warden) is built on.

Public API:
    - Proposal, MAX_POSTCONDITIONS: what a bee wants to do (proposal).
    - ProposalState, TRANSITIONS, assert_transition, can_transition, is_terminal: the state
      machine (state).
    - RiskTier, TierSpec, TierTable, load_tiers: risk tiers as data (tiers).
    - checks_for, SHORTEN_LATENCY_BUDGET_S: tempo-driven ladder shortening/lengthening (tiers).
    - LeaseView: the Protocol seam to a Real Cell's lease (lease_view).
    - Check, CheckContext, CheckResultRecord, SchemaCheck, PathAllowlistCheck,
      CommandAllowlistCheck, DiffSizeCapCheck, deterministic_checks: the check ladder (checks).
    - JudgeCheck, JudgeOutcome, JudgeRequest, JudgeReviewer, JudgeVerdict, judge_checks,
      JudgeRubric, load_judge_rubrics, FakeJudgeReviewer: the independent-review rung and
      deterministic_checks()'s sibling registry (checks).
    - ApplyResult, TouchedPath, apply_action: applying a CAPPED proposal (apply).
    - apply_unified_diff: the pure diff applier behind a DIFF action (diff).
    - PostconditionOutcome, check_postcondition: checking one assertion after applying
      (postconditions).
    - CappingGate, GateDeps, GateOutcome: the gate itself (gate).
    - AuditDeps, AuditFinding, AuditRates, AuditSampler, FindingsSink, InMemoryFindingsSink,
      audit_completed: the after-the-fact sampled audit (audit).
    - AuditRateRaise, raised_audit_rate, live_audit_raises, CarriedAuditRaises,
      AUDIT_RATE_RAISED_KIND, MAX_RAISES_READ, MAX_CARRIED_PER_TIER: a Guard Bee raise of a
      tier's sampled-audit rate, read back from the trail by a gate on it, carried on a grant to
      every other Warden (roadmap step 10.6, Waggle 1.8) (raises).
    - CappingError, UnknownProposalError, InvalidProposalTransitionError, DiffApplyError,
      JudgeUnavailableError, JudgeAnswerError: this package's error tree (errors).
"""

from hivemind.supervision.capping.apply import ApplyResult, TouchedPath, apply_action
from hivemind.supervision.capping.audit import (
    AuditDeps,
    AuditFinding,
    AuditRates,
    AuditSampler,
    FindingsSink,
    InMemoryFindingsSink,
    audit_completed,
)
from hivemind.supervision.capping.checks import (
    Check,
    CheckContext,
    CheckResultRecord,
    CommandAllowlistCheck,
    DiffSizeCapCheck,
    FakeJudgeReviewer,
    JudgeCheck,
    JudgeOutcome,
    JudgeRequest,
    JudgeReviewer,
    JudgeRubric,
    JudgeVerdict,
    PathAllowlistCheck,
    SchemaCheck,
    deterministic_checks,
    judge_checks,
    load_judge_rubrics,
)
from hivemind.supervision.capping.diff import apply_unified_diff
from hivemind.supervision.capping.errors import (
    CappingError,
    DiffApplyError,
    InvalidProposalTransitionError,
    JudgeAnswerError,
    JudgeUnavailableError,
    UnknownProposalError,
)
from hivemind.supervision.capping.gate import CappingGate, GateDeps, GateOutcome
from hivemind.supervision.capping.lease_view import LeaseView
from hivemind.supervision.capping.postconditions import (
    CHECKABLE_KINDS,
    PostconditionOutcome,
    check_postcondition,
)
from hivemind.supervision.capping.proposal import MAX_POSTCONDITIONS, Proposal
from hivemind.supervision.capping.raises import (
    AUDIT_RATE_RAISED_KIND,
    MAX_CARRIED_PER_TIER,
    MAX_RAISES_READ,
    AuditRateRaise,
    CarriedAuditRaises,
    live_audit_raises,
    raised_audit_rate,
)
from hivemind.supervision.capping.state import (
    TRANSITIONS,
    ProposalState,
    assert_transition,
    can_transition,
    is_terminal,
)
from hivemind.supervision.capping.tiers import (
    SHORTEN_LATENCY_BUDGET_S,
    RiskTier,
    TierSpec,
    TierTable,
    checks_for,
    load_tiers,
)

__all__ = [
    "AUDIT_RATE_RAISED_KIND",
    "CHECKABLE_KINDS",
    "MAX_CARRIED_PER_TIER",
    "MAX_POSTCONDITIONS",
    "MAX_RAISES_READ",
    "SHORTEN_LATENCY_BUDGET_S",
    "TRANSITIONS",
    "ApplyResult",
    "AuditDeps",
    "AuditFinding",
    "AuditRateRaise",
    "AuditRates",
    "AuditSampler",
    "CappingError",
    "CappingGate",
    "CarriedAuditRaises",
    "Check",
    "CheckContext",
    "CheckResultRecord",
    "CommandAllowlistCheck",
    "DiffApplyError",
    "DiffSizeCapCheck",
    "FakeJudgeReviewer",
    "FindingsSink",
    "GateDeps",
    "GateOutcome",
    "InMemoryFindingsSink",
    "InvalidProposalTransitionError",
    "JudgeAnswerError",
    "JudgeCheck",
    "JudgeOutcome",
    "JudgeRequest",
    "JudgeReviewer",
    "JudgeRubric",
    "JudgeUnavailableError",
    "JudgeVerdict",
    "LeaseView",
    "PathAllowlistCheck",
    "PostconditionOutcome",
    "Proposal",
    "ProposalState",
    "RiskTier",
    "SchemaCheck",
    "TierSpec",
    "TierTable",
    "TouchedPath",
    "UnknownProposalError",
    "apply_action",
    "apply_unified_diff",
    "assert_transition",
    "audit_completed",
    "can_transition",
    "check_postcondition",
    "checks_for",
    "deterministic_checks",
    "is_terminal",
    "judge_checks",
    "live_audit_raises",
    "load_judge_rubrics",
    "load_tiers",
    "raised_audit_rate",
]
