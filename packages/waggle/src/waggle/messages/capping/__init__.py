"""Re-export the capping family: a proposal, its check ladder, the verdict and the rollback.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Capping is
the gate every side effect passes: a Worker (the bee that does the work) proposes an action,
checkers report each rung of the ladder, and the Warden (the always-on supervisor of one Cell)
gives the verdict. ``proposals`` holds the proposal and the check result; ``action`` the proposed
action itself (a diff, a command or a step sequence); ``verdict`` the gate's word, each
postcondition result and a rollback. This package is the family's face: a caller imports any of its
messages, enums or value models from here without knowing which module defines them. The bounds
each module names stay in that module, because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a capping payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``capping.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.9 for the family's normative fields and rules.
    - waggle.messages.capping.proposals, waggle.messages.capping.action and
      waggle.messages.capping.verdict for the definitions.

Public API:
    - Proposals (proposals): CheckKind, CheckOutcome, CheckResult, ProposalSubmitted, RiskTier.
    - Action (action): ActionKind, ProposedAction.
    - Verdict (verdict): PostconditionResult, RollbackDone, RollbackMethod, Verdict,
      VerdictOutcome.
"""

from waggle.messages.capping.action import ActionKind, ProposedAction
from waggle.messages.capping.proposals import (
    CheckKind,
    CheckOutcome,
    CheckResult,
    ProposalSubmitted,
    RiskTier,
)
from waggle.messages.capping.verdict import (
    PostconditionResult,
    RollbackDone,
    RollbackMethod,
    Verdict,
    VerdictOutcome,
)

__all__ = [
    "ActionKind",
    "CheckKind",
    "CheckOutcome",
    "CheckResult",
    "PostconditionResult",
    "ProposalSubmitted",
    "ProposedAction",
    "RiskTier",
    "RollbackDone",
    "RollbackMethod",
    "Verdict",
    "VerdictOutcome",
]
