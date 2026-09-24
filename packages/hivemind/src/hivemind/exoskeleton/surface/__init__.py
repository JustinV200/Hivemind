"""Apply, verify and undo GUI steps for the Capping gate: the surface package.

The Capping gate applies a GUI proposal only through the `GuiSurface` seam (Layer 2, ADR-0032);
`ExoskeletonSurface` (`core`) is that seam over one attached Exoskeleton: it runs each typed step on
the right peripheral (`steps`), observes the GUI postconditions until they hold or settle
(`verify`), keeps the browser's undo point for rollback, and feeds the flight recorder.

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
    - run_step (steps); observe_until, url_matches, Observation (verify).
"""

from hivemind.exoskeleton.surface.core import DEFAULT_SETTLE_S, SETTLE_POLL_S, ExoskeletonSurface
from hivemind.exoskeleton.surface.steps import run_step
from hivemind.exoskeleton.surface.verify import Observation, observe_until, url_matches

__all__ = [
    "DEFAULT_SETTLE_S",
    "SETTLE_POLL_S",
    "ExoskeletonSurface",
    "Observation",
    "observe_until",
    "run_step",
    "url_matches",
]
