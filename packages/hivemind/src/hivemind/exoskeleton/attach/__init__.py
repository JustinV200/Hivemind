"""Equip a Cell with the Exoskeleton one task needs, and take it off again: the attach package.

Attach (roadmap step 6.4, ADR-0031) turns a task's `ExoskeletonNeed` into running peripherals on
its Cell: it plans from the Cell's capability report and the bee's grant (`plan`), starts a private
display (`display`), a private sound server (`audio`) and a browser (through an injected launcher)
through the Cell's own session, waits for each to be usable (`ready`), and hands back an
`ExoskeletonHandle` whose `detach` stops exactly what was started (`handle`). `core.attach` is the
entry point.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the exoskeleton package.
    Called by the Warden's spawn path for a task whose needs carry an Exoskeleton, and by tests
    and demos driving a Cell directly. Calls into `hivemind.cell`, `hivemind.guard`,
    `hivemind.pheromone` and the exoskeleton's own peripheral packages.

Key invariants:
    - Plans are pure and branch on capabilities, never on the kind of Cell.
    - Nothing attach started outlives a failed attach or a detach.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the design.
    - hivemind.exoskeleton.scratch for where every started process keeps its files.

Public API:
    - attach, AttachDeps, ExoskeletonConfig, DEFAULT_SCREEN, DEFAULT_READY_TIMEOUT_S (core).
    - ExoskeletonHandle, Peripherals, DetachReport, ExoskeletonTrail (handle).
    - plan_attach, AttachPlan, DisplaySource (plan).
    - AUDIO_PROGRAMS (audio), X11_PROGRAMS, WINDOW_MANAGER (display): what attach runs on a Cell.
"""

from hivemind.exoskeleton.attach.audio import AUDIO_PROGRAMS
from hivemind.exoskeleton.attach.core import (
    DEFAULT_READY_TIMEOUT_S,
    DEFAULT_SCREEN,
    AttachDeps,
    ExoskeletonConfig,
    attach,
)
from hivemind.exoskeleton.attach.display import WINDOW_MANAGER, X11_PROGRAMS
from hivemind.exoskeleton.attach.handle import (
    DetachReport,
    ExoskeletonHandle,
    ExoskeletonTrail,
    Peripherals,
)
from hivemind.exoskeleton.attach.plan import AttachPlan, DisplaySource, plan_attach

__all__ = [
    "AUDIO_PROGRAMS",
    "DEFAULT_READY_TIMEOUT_S",
    "DEFAULT_SCREEN",
    "WINDOW_MANAGER",
    "X11_PROGRAMS",
    "AttachDeps",
    "AttachPlan",
    "DetachReport",
    "DisplaySource",
    "ExoskeletonConfig",
    "ExoskeletonHandle",
    "ExoskeletonTrail",
    "Peripherals",
    "attach",
    "plan_attach",
]
