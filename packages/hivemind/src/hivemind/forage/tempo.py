"""Define Tempo and AccuracyBar: a task's speed-against-accuracy setting.

Every task carries a tempo: how fast it must be done and how right it must be (codingrules
section 8.14). ``AccuracyBar`` is the accuracy half of that setting -- LOW, NORMAL, HIGH or
CRITICAL -- and sets a floor on the model grade a call may use, how much parallelism and spend
Forage (the Hive's capacity, modelled as data in this package) grants, and how long a check
ladder Capping (the quality gate that verifies every side effect before it lands) runs. ``Tempo``
pairs that bar with an optional latency budget in seconds. Tempo lives here, in ``forage``, and
not in ``llm`` (the package that talks to model providers), because codingrules section 4 fixes
the dependency direction within Layer 1 as ``llm`` importing ``forage`` and never the reverse: a
grant, a routing decision or an autopilot rule needs to read a task's tempo without pulling in
provider machinery, and ``cell.needs.TaskNeeds`` needs to carry one without importing ``llm``
either. This module mirrors ``waggle.messages.labels``'s ``AccuracyBar`` and ``Tempo`` member for
member and field for field, because the same setting travels on task and Forage messages over the
wire; a sync test in ``tests/unit/forage/test_tempo.py`` keeps the two from drifting apart.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by the Attendant (inbox
    triage, supervision.attendant) when ordering a supervisor's inbox, by llm.routing when it
    picks a slot's provider and effort, by forage.allocate when it grants parallelism and spend,
    and by Capping when it lengthens or, within floors, shortens a proposal's check ladder.
    Carried by hivemind.cell.needs.TaskNeeds, one per task. Calls into waggle.messages only, for
    the from_wire/to_wire conversions.

Key invariants:
    - AccuracyBar's member names and values are identical to waggle.messages.labels.AccuracyBar's
      (tests/unit/forage/test_tempo.py checks it member for member).
    - Tempo.latency_budget_s is either None (no budget) or a number strictly greater than zero;
      Field(gt=0) rejects zero and negative values before any other code sees them.
    - Tempo is frozen and forbids unknown keys, like every boundary value in this repository.
    - Tempo never overrides safety: access levels, capability attenuation and the left-as-found
      rule for Real Cells are unaffected by how urgent a task is (codingrules section 8.14). This
      module holds no enforcement of that rule; it is a property of how routing and Capping read
      the value, not of the value itself.
    - grade_floor is total: every AccuracyBar member has an entry in GRADE_FLOORS, and every
      floor it returns is a legal Forage map grade (1 to 5).

See Also:
    - .claude/codingrules.md section 8.14 for Tempo's role in routing, Forage and Capping.
    - .claude/codingrules.md section 4 for why forage never imports llm.
    - waggle.messages.labels for the wire form this module mirrors.
    - hivemind.cell.needs for TaskNeeds.tempo, the field that carries this value per task.
    - hivemind.forage.allocate for grant(), grade_floor's caller when it filters map sources.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages import AccuracyBar as WireAccuracyBar
from waggle.messages import Tempo as WireTempo

# The Forage map grades sources 1 (weakest) to 5 (strongest, waggle.messages.forage.values.
# MIN_MODEL_GRADE/MAX_MODEL_GRADE); these four floors are hand-set, not derived, so the reasoning
# for each lives beside it rather than in one shared comment.
LOW_GRADE_FLOOR = 1  # LOW tolerates the map's weakest usable grade: speed and cost win outright.
NORMAL_GRADE_FLOOR = 2  # The ordinary bar most work clears without asking for anything special.
HIGH_GRADE_FLOOR = (
    3  # Worth the extra cost: the upper half of the scale, not just "better than average".
)
CRITICAL_GRADE_FLOOR = 4  # Reserves the top two grades for work that must not be wrong; grade 5
# itself stays headroom above the floor rather than the floor, since the Queen's own slot always
# reaches for the strongest available regardless of any task's bar (codingrules section 8.10).

__all__ = ["GRADE_FLOORS", "AccuracyBar", "Tempo", "grade_floor"]


class AccuracyBar(Enum):
    """How right a task's answer must be: a model grade floor, Forage spend, Capping's ladder.

    Read by llm.routing (a minimum model grade the bound source must clear), forage.allocate
    (more parallelism for an urgent task, more spend for a thorough one) and Capping, whose check
    ladder (docs/supervision/capping-tiers.toml) may shorten at LOW/NORMAL and lengthen at
    HIGH/CRITICAL but never drop below the floor a risk tier fixes (codingrules section 8.14).
    """

    LOW = "LOW"  # Weakest allowed grade; least Forage spend; the shortest ladder a tier permits.
    NORMAL = "NORMAL"  # The role's own floor; ordinary Forage spend; the tier's usual ladder.
    HIGH = "HIGH"  # Raises the floor above NORMAL; more Forage spend; a longer ladder.
    CRITICAL = "CRITICAL"  # Strongest grade; most spend and parallelism; the fullest ladder.


class Tempo(BaseModel):
    """A task's speed-against-accuracy setting: how fast it must be done and how right it must be.

    The planner sets both fields per subtask (brood_chamber.task, phase 2 step 2.4); the human
    may set them on a goal. Carried on hivemind.cell.needs.TaskNeeds, one per task, and mirrored
    from waggle.messages.Tempo, the wire form the same setting travels on across task and Forage
    messages.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    latency_budget_s: Annotated[float, Field(gt=0)] | None = Field(
        default=None,
        description="The latency the task can tolerate, in seconds; None means no budget. "
        "Strictly greater than 0 when set.",
    )
    accuracy: AccuracyBar = Field(
        default=AccuracyBar.NORMAL,
        description="The accuracy bar: sets the minimum model grade, Forage's parallelism and "
        "spend, and how long a Capping check ladder gets.",
    )

    @classmethod
    def from_wire(cls, wire: WireTempo) -> Tempo:
        """Build a Tempo from the wire form waggle.messages carries on task and Forage messages.

        Args:
            wire: The waggle.messages.Tempo value read off an Envelope.

        Returns:
            The equivalent hivemind Tempo.
        """
        # Both sides share field names and value strings, so the conversion is a straight
        # field-by-field copy; the accuracy bar is mapped through its own enum by value.
        return cls(
            latency_budget_s=wire.latency_budget_s,
            accuracy=AccuracyBar(wire.accuracy.value),
        )

    def to_wire(self) -> WireTempo:
        """Build the wire form waggle.messages carries on task and Forage messages.

        Returns:
            The equivalent waggle.messages.Tempo.
        """
        return WireTempo(
            latency_budget_s=self.latency_budget_s,
            accuracy=WireAccuracyBar(self.accuracy.value),
        )


# The single table behind grade_floor (codingrules section 5.2: one small table, not a chain of
# ifs); roadmap step 3.12 names these exact floors for forage.allocate.grant to filter map sources
# by, alongside a task's latency budget (which grade_floor does not touch: distance, not grade, is
# the latency-budget-facing half of tempo, and that comparison lives in llm.routing, Layer 2).
GRADE_FLOORS: Mapping[AccuracyBar, int] = {
    AccuracyBar.LOW: LOW_GRADE_FLOOR,
    AccuracyBar.NORMAL: NORMAL_GRADE_FLOOR,
    AccuracyBar.HIGH: HIGH_GRADE_FLOOR,
    AccuracyBar.CRITICAL: CRITICAL_GRADE_FLOOR,
}


def grade_floor(bar: AccuracyBar) -> int:
    """Return the minimum Forage map grade a source must clear for `bar`.

    Args:
        bar: The accuracy half of a task's tempo.

    Returns:
        A grade from 1 to 5 (`GRADE_FLOORS`); `hivemind.forage.allocate.grant` keeps only map
        sources whose `ModelSourceSpec.grade` is at least this floor.
    """
    return GRADE_FLOORS[bar]
