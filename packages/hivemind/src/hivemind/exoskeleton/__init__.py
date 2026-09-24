"""Provide the Hive's optional Cell peripherals: the Exoskeleton.

A task that needs a desktop, a browser or audio gets them attached to its Cell for its own lease,
and only then (codingrules section 8.7, ADR-0031): compound_eye (vision: capture a frame, digest a
region), antennae (touch: pointer and keyboard), buzz (hearing and voice), and a browser (the fast
path, driven through the accessibility tree rather than pixels). Every peripheral reaches its Cell
only through that Cell's `CellSession`, so one backend serves a Virtual Cell and a Linux Real Cell
alike. `attach` plans from capabilities, starts only what the plan names (a private display, a
private sound server, a browser), and returns a handle whose `detach` stops exactly that. Nothing
in the core assumes any of this is attached.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Called by the Warden's spawn path
    (attach and detach), the Worker's Exoskeleton tools and the Capping gate's GUI surface. Calls
    into hivemind.cell, hivemind.guard, hivemind.pheromone and hivemind.common.

Key invariants:
    - Nothing here runs a process except through a CellSession.
    - No frame, recording or typed text ever reaches a log or the trail.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the design.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for how
      GUI actions are gated.

Public API:
    - attach, AttachDeps, ExoskeletonConfig, ExoskeletonHandle, Peripherals, DetachReport,
      AttachPlan, DisplaySource, plan_attach (attach): equip and unequip a Cell.
    - ExoskeletonError, AttachError, PeripheralError, ElementNotFoundError (errors).
    - Point, Region, ScreenSize (geometry); Frame, PNG_MEDIA_TYPE (frames); ScratchLayout
      (scratch); X11Display (x11).
    - The peripheral protocols CompoundEye, Antennae, Buzz, Recording, Browser (their packages).
"""

from hivemind.exoskeleton.antennae import Antennae
from hivemind.exoskeleton.attach import (
    AttachDeps,
    AttachPlan,
    DetachReport,
    DisplaySource,
    ExoskeletonConfig,
    ExoskeletonHandle,
    Peripherals,
    attach,
    plan_attach,
)
from hivemind.exoskeleton.browser import Browser
from hivemind.exoskeleton.buzz import Buzz, Recording
from hivemind.exoskeleton.compound_eye import CompoundEye
from hivemind.exoskeleton.errors import (
    AttachError,
    ElementNotFoundError,
    ExoskeletonError,
    PeripheralError,
)
from hivemind.exoskeleton.frames import PNG_MEDIA_TYPE, Frame
from hivemind.exoskeleton.geometry import Point, Region, ScreenSize
from hivemind.exoskeleton.scratch import ScratchLayout
from hivemind.exoskeleton.x11 import X11Display

__all__ = [
    "PNG_MEDIA_TYPE",
    "Antennae",
    "AttachDeps",
    "AttachError",
    "AttachPlan",
    "Browser",
    "Buzz",
    "CompoundEye",
    "DetachReport",
    "DisplaySource",
    "ElementNotFoundError",
    "ExoskeletonConfig",
    "ExoskeletonError",
    "ExoskeletonHandle",
    "Frame",
    "PeripheralError",
    "Peripherals",
    "Point",
    "Recording",
    "Region",
    "ScratchLayout",
    "ScreenSize",
    "X11Display",
    "attach",
    "plan_attach",
]
