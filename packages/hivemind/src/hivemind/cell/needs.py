"""Define TaskNeeds: what a task requires from the Cell (Real or Virtual) that runs it.

TaskNeeds is pure data a planner, or the human, attaches to a task's spec
(brood_chamber.task.TaskSpec, phase 2 step 2.4). queen.placement.decide reads it alongside the
current Cell inventory, each candidate's Cell Wax (a Queen-written caution about one Cell) and the
manifest's ``[placement]`` section to choose a Cell: whether isolation is required, preferred or
unnecessary; whether the task needs an Exoskeleton (a display, input or audio attachment on top of
a Cell's plain terminal session); which operating-system family it needs; which network scopes its
capability set must allow reaching; whether its Cell may be destroyed the moment the task ends
(disposability); the minimum CombShieldLevel (hivemind.cell.tiers) its Cell must carry; and its
Tempo (hivemind.forage.tempo), the speed-against-accuracy setting routing, Forage allocation and
Capping all read. This module also defines the two small enums TaskNeeds is built from:
``Isolation`` (the exclusivity a task demands from its Cell) and ``OsFamily``, which mirrors
``waggle.messages.OsFamily`` member for member because the same value travels on the wire in a
device's capability report.

Fits into the Hive:
    Layer 2 (the Cell abstraction). Read by queen.placement.decide (Layer 6) when it maps a
    task's needs plus the Cell inventory to a Placement, and carried on brood_chamber.TaskSpec
    (also Layer 2) so a task's needs travel with it from submission onward. Calls into
    hivemind.cell.tiers and hivemind.forage.tempo only.

Key invariants:
    - Every field is defaulted, so TaskNeeds() is the plain case: preferred isolation, no
      Exoskeleton, any OS, no network scopes, a disposable Cell, the MEADOW shield, and
      NORMAL-accuracy Tempo with no latency budget.
    - comb_shield == CombShieldLevel.NIGHT_VEIL requires isolation == Isolation.REQUIRED: Night
      Veil is virtual-only (codingrules section 8.7), so a task that would accept a Real Cell can
      never demand it.
    - OsFamily's member names and values are identical to waggle.messages.OsFamily's
      (tests/unit/cell/test_needs.py checks it member for member).
    - network_scopes holds at most MAX_NETWORK_SCOPES entries, each at most MAX_SCOPE_CHARS long
      and non-empty.

See Also:
    - .claude/codingrules.md section 8.7 for placement's inputs and the Night Veil constraint.
    - .claude/roadmap.md phase 2 step 2.3a for why this module is built ahead of the rest of cell.
    - hivemind.cell.tiers for CombShieldLevel.
    - hivemind.forage.tempo for Tempo.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.cell.tiers import CombShieldLevel
from hivemind.forage.tempo import Tempo

MAX_NETWORK_SCOPES = 32  # Generous for one task; broader network policy belongs in the manifest.
MAX_SCOPE_CHARS = 253  # RFC 1035's hostname length limit; the longest one scope entry can be.

__all__ = ["MAX_NETWORK_SCOPES", "MAX_SCOPE_CHARS", "Isolation", "OsFamily", "TaskNeeds"]


class Isolation(Enum):
    """Whether a task's Cell must, should, or need not be exclusive to just that task.

    Read by queen.placement.decide to decide whether a Real Cell (an existing, borrowed device)
    is even a candidate for the task.
    """

    REQUIRED = "REQUIRED"  # Only a Virtual Cell qualifies; never placed on a Real Cell.
    PREFERRED = "PREFERRED"  # Worth choosing when cheap; a fitting Real Cell may still be reused.
    NONE = "NONE"  # Any Cell will do; placement weighs other factors only.


class OsFamily(Enum):
    """The operating-system family the task's Cell must run; mirrors waggle.messages.OsFamily."""

    LINUX = "LINUX"  # Linux hosts and Cell images (images/ is Ubuntu-based by default).
    WINDOWS = "WINDOWS"  # Windows hosts, e.g. an enrolled Windows device or the Hive Stand itself.
    MACOS = "MACOS"  # macOS hosts; best-effort per codingrules section 2.


class TaskNeeds(BaseModel):
    """What a task requires from the Cell (Real or Virtual) that runs it.

    Attached to a TaskSpec (brood_chamber.task) and read by queen.placement.decide. Every field is
    defaulted so TaskNeeds() is the plain case: no isolation demand beyond a mild preference, a
    terminal-only Cell, any OS, no extra network reach, a disposable Cell, the MEADOW security
    tier, and NORMAL-accuracy Tempo with no latency budget.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    isolation: Isolation = Field(
        default=Isolation.PREFERRED,
        description="Whether the task's Cell must, should, or need not be exclusive to it. "
        "REQUIRED excludes every Real Cell (codingrules section 15).",
    )
    exoskeleton: bool = Field(
        default=False,
        description="Whether the task needs an Exoskeleton (a display, input or audio "
        "attachment) rather than the Cell's plain terminal session.",
    )
    os: OsFamily | None = Field(
        default=None,
        description="The operating-system family the Cell must run, or None when any family "
        "will do.",
    )
    network_scopes: tuple[Annotated[str, Field(min_length=1, max_length=MAX_SCOPE_CHARS)], ...] = (
        Field(
            default=(),
            max_length=MAX_NETWORK_SCOPES,
            description="Outbound network destinations (hostnames) the task's capability set "
            "must allow reaching. Empty means the task needs no network beyond what its Cell "
            "already grants.",
        )
    )
    disposable: bool = Field(
        default=True,
        description="Whether the Cell may be destroyed as soon as the task ends, rather than "
        "kept (Overwintered) for reuse.",
    )
    comb_shield: CombShieldLevel = Field(
        default=CombShieldLevel.MEADOW,
        description="The minimum CombShieldLevel the Cell running this task must carry.",
    )
    tempo: Tempo = Field(
        default_factory=Tempo,
        description="The task's speed-against-accuracy setting; read by routing, Forage "
        "allocation and Capping.",
    )

    @model_validator(mode="after")
    def _night_veil_requires_required_isolation(self) -> TaskNeeds:
        """Reject a NIGHT_VEIL comb_shield paired with anything but REQUIRED isolation.

        Night Veil is virtual-only (codingrules section 8.7): PREFERRED or NONE isolation could
        still place the task on a Real Cell, which can never carry that tier, so the combination
        would promise a control the Hive cannot deliver wherever the task might land.

        Returns:
            This TaskNeeds unchanged, once the combination is confirmed valid.

        Raises:
            ValueError: comb_shield is NIGHT_VEIL and isolation is not REQUIRED.
        """
        # Night Veil demands a fresh Virtual Cell every time; anything less than REQUIRED
        # isolation leaves the door open to a Real Cell, which the tier forbids outright.
        if (
            self.comb_shield is CombShieldLevel.NIGHT_VEIL
            and self.isolation is not Isolation.REQUIRED
        ):
            raise ValueError(
                "TaskNeeds with comb_shield=NIGHT_VEIL requires isolation=REQUIRED: Night Veil "
                "is virtual-only, so a task that would accept a Real Cell cannot demand it "
                f"(got isolation={self.isolation.value})."
            )
        return self
