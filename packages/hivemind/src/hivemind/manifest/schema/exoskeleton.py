"""Define the ``[exoskeleton]`` section: the display attach starts, and recording retention.

The Exoskeleton is a Cell's optional set of peripherals, attached for one task that asks for them:
a display, input, audio and a browser (codingrules 8.7, ADR-0031). ``ExoskeletonSection`` holds
what the Hive Stand's operator may tune about it: the size of a display attach starts (and of the
browser window inside it), how long attach waits for everything it started to become usable, and
how many days the flight recorder's recordings (the frames and evidence of every GUI action,
ADR-0032) are kept before the retention sweep removes them. Whether Chromium keeps its own sandbox
is deliberately not here: the composition root decides it from where the browser runs (off inside a
Virtual Cell, which is itself the sandbox, and on the Hive Stand only when the Hive runs as root),
never from a switch an operator could flip by mistake.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.manifest.HiveManifest``; converted by the composition root
    (``hivemind.cli.compose.exoskeleton``) into ``hivemind.exoskeleton.ExoskeletonConfig`` and the
    recording store's retention cutoff. Calls into nothing beyond pydantic.

Key invariants:
    - Frozen and forbids unknown fields (codingrules section 8.5).
    - The defaults mirror ``hivemind.exoskeleton.attach.core.DEFAULT_SCREEN`` and
      ``DEFAULT_READY_TIMEOUT_S``; this module cannot import Layer 3, so a composition-root test
      holds the two in step.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for attach.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for "retention
      is a manifest setting".
    - docs/manifests/full.toml for the annotated example.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SCREEN_WIDTH = 1280  # Mirrors attach.core.DEFAULT_SCREEN: a laptop-sized desktop.
DEFAULT_SCREEN_HEIGHT = 800  # Every site lays out for 1280x800.
DEFAULT_READY_TIMEOUT_S = 30.0  # Mirrors attach.core.DEFAULT_READY_TIMEOUT_S, for a slow Cell.
# A month of recordings: long enough for sampled audit and an operator's look back after the fact,
# short enough that frames (tens of kilobytes each) do not pile up without bound.
DEFAULT_RECORDING_RETENTION_DAYS = 30
MIN_SCREEN_SIDE = 320  # The narrowest layout sites still design for; smaller shows nothing useful.
MAX_SCREEN_SIDE = 8_192  # Past 8K every recorded frame costs megabytes and no task needs it.

__all__ = [
    "DEFAULT_READY_TIMEOUT_S",
    "DEFAULT_RECORDING_RETENTION_DAYS",
    "DEFAULT_SCREEN_HEIGHT",
    "DEFAULT_SCREEN_WIDTH",
    "MAX_SCREEN_SIDE",
    "MIN_SCREEN_SIDE",
    "ExoskeletonSection",
]


class ExoskeletonSection(BaseModel):
    """``[exoskeleton]``: the display attach starts, its readiness budget, recording retention."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    screen_width: int = Field(
        default=DEFAULT_SCREEN_WIDTH,
        ge=MIN_SCREEN_SIDE,
        le=MAX_SCREEN_SIDE,
        description="Pixel width of a display attach starts, and of the browser window in it.",
    )
    screen_height: int = Field(
        default=DEFAULT_SCREEN_HEIGHT,
        ge=MIN_SCREEN_SIDE,
        le=MAX_SCREEN_SIDE,
        description="Pixel height of a display attach starts, and of the browser window in it.",
    )
    ready_timeout_s: float = Field(
        default=DEFAULT_READY_TIMEOUT_S,
        gt=0,
        description="Seconds attach waits for the display, sound server and browser together "
        "to become usable before it stops them and refuses the task.",
    )
    recording_retention_days: int = Field(
        default=DEFAULT_RECORDING_RETENTION_DAYS,
        gt=0,
        description="Days a flight recording is kept after its last action before the retention "
        "sweep (run as the Hive starts) deletes it, frames included.",
    )
