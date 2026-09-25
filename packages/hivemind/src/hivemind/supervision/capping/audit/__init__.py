"""Provide Capping's after-the-fact half: sampled audits, and the Guard Bee's raises of their rate.

Codingrules section 8.12: "What cannot be gated is sampled ... and the Guard Bee raises a tier's
rate when its failure rate climbs." `sampler` is the sampling itself (`AuditSampler` picks which
terminal proposals to audit at their tier's rate, `audit_completed` has the judge review one and
deposits its finding); `raises` is what a Guard Bee raise of that rate carries and every way a
gate reads the raises in force (roadmap step 10.6). One concern, two files: grouped here once
phases 6 and 10 together took `hivemind.supervision.capping` past codingrules 5.6's ten modules.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping`.
    Imported by `hivemind.supervision.capping`'s own face. Calls into this package's `sampler`
    and `raises` only.

Key invariants:
    - This file holds re-exports and __all__ only (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 8.12 for the sampled audit and its raises.
    - hivemind.supervision.capping for the whole Capping face.

Public API:
    - Sampling (sampler): AuditSampler, AuditRates, AuditDeps, AuditFinding, FindingsSink,
      InMemoryFindingsSink, audit_completed, review_applied.
    - Raises (raises): AuditRateRaise, CarriedAuditRaises, live_audit_raises, raised_audit_rate,
      AUDIT_RATE_RAISED_KIND, MAX_CARRIED_PER_TIER, MAX_RAISES_READ.
"""

from hivemind.supervision.capping.audit.raises import (
    AUDIT_RATE_RAISED_KIND,
    MAX_CARRIED_PER_TIER,
    MAX_RAISES_READ,
    AuditRateRaise,
    CarriedAuditRaises,
    live_audit_raises,
    raised_audit_rate,
)
from hivemind.supervision.capping.audit.sampler import (
    AuditDeps,
    AuditFinding,
    AuditRates,
    AuditSampler,
    FindingsSink,
    InMemoryFindingsSink,
    audit_completed,
    review_applied,
)

__all__ = [
    "AUDIT_RATE_RAISED_KIND",
    "MAX_CARRIED_PER_TIER",
    "MAX_RAISES_READ",
    "AuditDeps",
    "AuditFinding",
    "AuditRateRaise",
    "AuditRates",
    "AuditSampler",
    "CarriedAuditRaises",
    "FindingsSink",
    "InMemoryFindingsSink",
    "audit_completed",
    "live_audit_raises",
    "raised_audit_rate",
    "review_applied",
]
