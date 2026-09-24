"""Own the Capping gate: propose, check, cap, apply, verify -- nothing lands uncapped.

`core.py` defines `CappingGate` itself plus the step-by-step free functions its `propose`/`run`
delegate to (`_check_and_cap`, `_apply_and_verify`, `_roll_back`, `_restore`, `_reject`,
`_record_event`, and others); `model.py` defines its two value types, `GateDeps` (everything one
gate is built with) and `GateOutcome` (a terminal `run()` result). Split into two files -- this
became a package, not a bare module -- purely to stay under the codingrules section 5.1 file-size
limit; `capping/`'s own directory already sits at its ten-module fan-out cap (codingrules 5.6), so
turning `gate.py` into a sub-package cost nothing there (a sub-package counts once against its
parent, never by its own contents).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping`.
    Built by a Warden (roadmap step 3.19) from a `GateDeps`; called by Worker tools through
    `propose`/`run`. Calls into `hivemind.supervision.capping.apply`, `.checks`, `.errors`,
    `.lease_view`, `.leave`, `.postconditions`, `.proposal`, `.state`, `.tiers` and waggle only.

Key invariants:
    - None beyond what `core.py` and `model.py` each state; this face adds no behaviour.

See Also:
    - .claude/codingrules.md section 5.1 for the file-size limit this split satisfies.
    - .claude/codingrules.md section 5.6 for the fan-out rule a sub-package (rather than a second
      flat module) satisfies.
    - hivemind.supervision.capping.gate.core for CappingGate and the step-by-step free functions.
    - hivemind.supervision.capping.gate.model for GateDeps and GateOutcome.

Public API:
    - CappingGate: propose, check, cap, apply, verify (core).
    - GateDeps: everything one CappingGate is built with (model).
    - GateOutcome: a terminal `run()` result (model).
"""

from hivemind.supervision.capping.gate.core import CappingGate
from hivemind.supervision.capping.gate.model import GateDeps, GateOutcome

__all__ = ["CappingGate", "GateDeps", "GateOutcome"]
