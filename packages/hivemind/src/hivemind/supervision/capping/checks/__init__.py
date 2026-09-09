"""Provide the Capping gate's check-ladder seam and this phase's deterministic checks.

`Check` and `CheckContext` (`base.py`) are the seam every rung of a risk tier's ladder implements
(codingrules section 8.1); `deterministic.py` supplies this phase's autopilot rungs -- schema,
path and command allowlists, diff size cap -- and the registry `deterministic_checks()` a
composition root wires into `hivemind.supervision.capping.gate.GateDeps`. A later phase adds
sandbox-test, judge and human rungs behind the same `Check` Protocol without touching the gate.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping`.
    Built by whichever composition root constructs a `GateDeps` (a Warden, roadmap step 3.19);
    run by `hivemind.supervision.capping.gate.CappingGate`.

Key invariants:
    - Whatever is not re-exported here is private to this sub-package (codingrules section 5.4).

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this sub-package
      follows.
    - hivemind.supervision.capping.checks.base for Check and CheckContext.
    - hivemind.supervision.capping.checks.deterministic for this phase's four concrete checks.

Public API:
    - Check, CheckContext, CheckResultRecord: the check-ladder seam (base).
    - SchemaCheck, PathAllowlistCheck, CommandAllowlistCheck, DiffSizeCapCheck,
      deterministic_checks: this phase's autopilot rungs (deterministic).
"""

from hivemind.supervision.capping.checks.base import Check, CheckContext, CheckResultRecord
from hivemind.supervision.capping.checks.deterministic import (
    CommandAllowlistCheck,
    DiffSizeCapCheck,
    PathAllowlistCheck,
    SchemaCheck,
    deterministic_checks,
)

__all__ = [
    "Check",
    "CheckContext",
    "CheckResultRecord",
    "CommandAllowlistCheck",
    "DiffSizeCapCheck",
    "PathAllowlistCheck",
    "SchemaCheck",
    "deterministic_checks",
]
