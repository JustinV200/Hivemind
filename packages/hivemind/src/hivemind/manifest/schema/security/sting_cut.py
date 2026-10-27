"""Define the ``[guard.sting_cut]`` section: when the Queen may cut one Cell off on her own.

A Sting Cut (roadmap step 13.4a) disconnects one Cell at once: its lease is released, its
Hive-started processes killed, its scratch removed and its key forgotten. The human may cut any
Cell (``hive cells sting-cut``, after a step-up and the typed phrase). The Queen may cut one only
for the **dire patterns** listed here as data: Guard Bee rule keys whose finding means nothing short
of cutting the Cell off would contain it. Every cut of hers follows an isolation, preserves the
Cell's evidence first, never touches the Hive Stand's own lease, and is capped by
``max_autonomous_sting_cuts_per_hour`` (0 switches her cuts off). Rule keys are spelled as
strings because the manifest sits below the Guard Bee that ships them (codingrules section 4); a
key naming no rule never fires.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). Embedded by
    ``hivemind.manifest.schema.security.guard.GuardSection``; read by
    ``hivemind.cli.compose.guard.build_guard_deps``. Calls into pydantic only.

Key invariants:
    - Frozen and forbids unknown fields (codingrules section 8.5).
    - Nothing here can widen anything: a pattern only lets the Queen cut what she already
      isolated, and the cap only ever bounds her.

See Also:
    - .claude/roadmap.md step 13.4a for the dire patterns.
    - docs/guard/sting-cut.md for the operator's view.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# The shipped rule keys that name the roadmap's dire patterns: a frame or a trail segment that
# failed its signature on the Cell's own link, a burst of replayed envelope ids from its node, and
# repeated frame-ceiling closes from it (11.3a). The others the roadmap lists (egress attempts
# outside a bee's scopes, an outside-scratch touch after a rejection, an injection then a denial,
# a changed capability report) are rules about one bee, or not built: quarantine answers those.
DEFAULT_STING_CUT_PATTERNS = (
    "envelope_forgery",
    "segment_forgery",
    "replay_refusal_burst",
    "frame_ceiling_repeats",
)
DEFAULT_MAX_AUTONOMOUS_STING_CUTS_PER_HOUR = 2  # Two Cells an hour: an attack, not a habit.
MAX_STING_CUTS_PER_HOUR = 60  # Past one a minute a cap no longer caps anything.
MAX_STING_CUT_PATTERNS = 64  # A list is data, never unbounded.

_RuleKey = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")]

__all__ = [
    "DEFAULT_MAX_AUTONOMOUS_STING_CUTS_PER_HOUR",
    "DEFAULT_STING_CUT_PATTERNS",
    "MAX_STING_CUTS_PER_HOUR",
    "StingCutSection",
]


class StingCutSection(BaseModel):
    """``[guard.sting_cut]``: the dire patterns the Queen cuts on, and her hourly cap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dire_patterns: tuple[_RuleKey, ...] = Field(
        default=DEFAULT_STING_CUT_PATTERNS,
        max_length=MAX_STING_CUT_PATTERNS,
        description="Guard Bee rule keys whose requests the Queen answers by isolating the Cell "
        "and then Sting Cutting it, by autopilot rule; every other request never reaches a cut "
        "unless her awake episode decides one.",
    )
    max_autonomous_sting_cuts_per_hour: int = Field(
        default=DEFAULT_MAX_AUTONOMOUS_STING_CUTS_PER_HOUR,
        ge=0,
        le=MAX_STING_CUTS_PER_HOUR,
        description="The most Cells the Queen cuts on her own in any hour; one more stays isolated "
        "and goes to the human. 0 leaves every cut to the human.",
    )
