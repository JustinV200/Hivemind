"""Apply, verify and undo GUI steps for the Capping gate: the surface package.

The Capping gate applies a GUI proposal only through the `GuiSurface` seam (Layer 2, ADR-0032);
`ExoskeletonSurface` (`core`) is that seam over one attached Exoskeleton: it runs each typed step on
the right peripheral (`steps`), observes the GUI postconditions until they hold or settle
(`verify`), keeps the browser's undo point for rollback, and feeds the flight recorder, whose
record of an action it renders for a judge (`evidence`). `verify` also checks a task's structural
acceptance on the attached browser for the Warden (roadmap step 6.7).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Built by the Warden's `equip` and injected into a sub-bee's `GateDeps.gui`. Calls into
    `hivemind.supervision.capping`, and the exoskeleton's `attach`, `browser`, `recorder`.

Key invariants:
    - The surface never decides whether a step may run; the gate already has.

See Also:
    - hivemind.supervision.capping.gui for the protocol.

Public API:
    - ExoskeletonSurface, DEFAULT_SETTLE_S, SETTLE_POLL_S (core).
    - run_step (steps); observe_until, url_matches, Observation, check_structural,
      ACCEPTANCE_SETTLE_S (verify); judge_evidence (evidence).
"""

from hivemind.exoskeleton.surface.core import DEFAULT_SETTLE_S, SETTLE_POLL_S, ExoskeletonSurface
from hivemind.exoskeleton.surface.evidence import judge_evidence
from hivemind.exoskeleton.surface.steps import run_step
from hivemind.exoskeleton.surface.verify import (
    ACCEPTANCE_SETTLE_S,
    Observation,
    check_structural,
    observe_until,
    url_matches,
)

__all__ = [
    "ACCEPTANCE_SETTLE_S",
    "DEFAULT_SETTLE_S",
    "SETTLE_POLL_S",
    "ExoskeletonSurface",
    "Observation",
    "check_structural",
    "judge_evidence",
    "observe_until",
    "run_step",
    "url_matches",
]
