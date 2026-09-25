"""Define WeightTable and Priority: what score_item weighs, and what it returns.

`WeightTable` is the whole tunable surface of the Attendant's scoring formula (documented in full
in `hivemind.supervision.attendant.scoring`'s module docstring): a base weight per `InboxKind`, a
per-principal multiplier, how much a second of age is worth, a weight per `AlarmSeverity`, how
much a latency budget's urgency counts, a flat bonus for an item that names a task, and (roadmap
step 10.6a) the kinds this supervisor refuses outright: a Guard Bee's request (`GUARD_REQUEST`) is
the Queen's alone to decide (ADR-0043), so a Warden's table refuses it and its Attendant never
orders one. `Priority`
is one item's scored outcome: the number the Attendant orders by, plus the list of factors that
contributed to it, so a supervisor's own logs or the Observation Hive's Attendant view can show
*why* one item outranked another rather than just the bare number.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `queen_default` is read by
    `queen/inbox/` (roadmap step 3.20); `warden_default` by `wardens/inbox/` (roadmap step 3.19).
    Calls into `hivemind.supervision.attendant.items` and `hivemind.supervision.alarm` (for
    `AlarmSeverity`, the same type `InboxItem.severity` carries) only.

Key invariants:
    - WeightTable and Priority are frozen and forbid extras, like every boundary value here.
    - age_weight_per_s, latency_weight and task_link_weight are never negative (Field(ge=0)): a
      factor may contribute nothing, never a penalty, keeping score_item's sum monotonic in age,
      urgency and task linkage.
    - queen_default's weights place a GUARD_REQUEST (a fixed `GUARD_REQUEST_WEIGHT`, the Guard
      principal's multiplier on top) above a CRITICAL Alarm, a CRITICAL Alarm above a
      HUMAN_MESSAGE, and a HUMAN_MESSAGE above a bare WAGGLE_MESSAGE such as a heartbeat
      (tests/unit/supervision/attendant/test_weights.py and test_scoring.py's ordering-invariant
      tests check each).
    - warden_default gives every InboxKind the same base weight (a Warden's inbox is smaller and
      more uniform than the Queen's, codingrules section 8.8) and applies no principal
      favouritism; it refuses GUARD_REQUEST, which never reaches a Warden (ADR-0043).

See Also:
    - .claude/codingrules.md section 8.8 for "the Queen's Attendant weighs human messages heavily
      but not absolutely" and "a Warden's Attendant runs the same scoring over a smaller, more
      uniform inbox".
    - hivemind.supervision.attendant.items for InboxKind and InboxItem, what these weights apply to.
    - hivemind.supervision.attendant.scoring for score_item, the formula documented in full there.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.supervision.alarm import AlarmSeverity
from hivemind.supervision.attendant.items import InboxKind

# ADR-0043: a Guard request outranks every Alarm and every human message. 100 clears the highest
# either reaches on the Queen's table (a CRITICAL Alarm: 10 + 15 + 1.5 for its task link; the
# Queen's items carry no latency budget) by a margin age alone takes most of a day to close.
GUARD_REQUEST_WEIGHT = 100.0
GUARD_PRINCIPAL = "guard"  # The principal every Guard request is scored under (the Guard Bee).
# The Guard principal's own multiplier: neutral by default, named so an operator's override has a
# row to tune (ADR-0043: a noisy Guard could crowd the Queen's attention; this is one lever).
GUARD_PRINCIPAL_WEIGHT = 1.0

__all__ = [
    "GUARD_PRINCIPAL",
    "GUARD_PRINCIPAL_WEIGHT",
    "GUARD_REQUEST_WEIGHT",
    "Priority",
    "WeightTable",
]


class WeightTable(BaseModel):
    """One supervisor's whole scoring configuration: a weight per factor score_item sums.

    Built once per supervisor (`queen_default`/`warden_default`, or a manifest override later) and
    passed to `Attendant.__init__`; never mutated afterwards.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind_weights: dict[InboxKind, float] = Field(
        description="Base weight per InboxKind; a kind missing from this mapping scores 0 for "
        "that factor."
    )
    principal_weights: dict[str, float] = Field(
        description="Multiplier applied to an item's whole score, keyed by InboxItem.principal; "
        "an unlisted principal gets a neutral multiplier of 1.0."
    )
    age_weight_per_s: float = Field(
        ge=0, description="Score added per second of age since InboxItem.received_at."
    )
    severity_weights: dict[AlarmSeverity, float] = Field(
        description="Score added per AlarmSeverity, for an item whose severity is set; a "
        "severity missing from this mapping adds 0."
    )
    latency_weight: float = Field(
        ge=0,
        description="Numerator of the urgency term: latency_weight / latency_budget_s, for an "
        "item that carries a budget. A shorter budget scores higher.",
    )
    task_link_weight: float = Field(
        ge=0, description="Flat score added for an item whose task_id is set."
    )
    refused_kinds: frozenset[InboxKind] = Field(
        default_factory=frozenset,
        description="Kinds this supervisor never scores or acts on: `score_item` refuses one and "
        "`Attendant.order` drops it (a Warden refuses GUARD_REQUEST, ADR-0043).",
    )

    def refuses(self, kind: InboxKind) -> bool:
        """Return whether this supervisor refuses items of `kind` outright.

        Args:
            kind: The kind of an item about to be scored or ordered.

        Returns:
            True for a kind in `refused_kinds`.
        """
        return kind in self.refused_kinds

    @classmethod
    def queen_default(cls) -> WeightTable:
        """Build the Queen's default weights: human messages heavy, but not absolute.

        A HUMAN_MESSAGE's base weight (9.0) sits below an ALARM's (10.0) so that a routine Alarm
        can still edge out a routine human message, and a CRITICAL Alarm's severity bonus (15.0)
        puts a critical Alarm well clear of any human message regardless of age or task linkage --
        "human input carries heavy weight but not absolute priority" (README, "The Queen"). A
        Guard request (roadmap step 10.6a, ADR-0043) sits above all of them at a fixed
        `GUARD_REQUEST_WEIGHT`, with the Guard principal's multiplier on top; its item carries no
        task and no latency budget (`InboxItem`'s own validator), so only age orders two of them.

        Returns:
            A WeightTable tuned for the Queen's inbox: Waggle messages, Alarms, Questions, human
            messages, timers and watch observations, together.
        """
        return cls(
            kind_weights={
                InboxKind.GUARD_REQUEST: GUARD_REQUEST_WEIGHT,  # Above every Alarm and message.
                InboxKind.ALARM: 10.0,
                InboxKind.HUMAN_MESSAGE: 9.0,
                InboxKind.QUESTION: 8.0,
                InboxKind.WAGGLE_MESSAGE: 3.0,  # Heartbeats, task results: routine traffic.
                InboxKind.WATCH_OBSERVATION: 2.0,
                InboxKind.TIMER: 1.0,
            },
            # No favouritism by default, but the Guard's own row is named (a manifest may tune it).
            principal_weights={GUARD_PRINCIPAL: GUARD_PRINCIPAL_WEIGHT},
            age_weight_per_s=0.001,  # ~1 point per 1000s: breaks ties, never dominates kind.
            severity_weights={
                AlarmSeverity.INFO: 0.0,
                AlarmSeverity.WARNING: 4.0,
                AlarmSeverity.CRITICAL: 15.0,  # 10 (ALARM) + 15 = 25, clear of HUMAN_MESSAGE's 9.
            },
            latency_weight=2.0,
            task_link_weight=1.5,
        )

    @classmethod
    def warden_default(cls) -> WeightTable:
        """Build a Warden's default weights: flat, autopilot-only, no principal favouritism.

        A Warden's inbox is smaller and more uniform than the Queen's (codingrules section 8.8):
        it carries no human traffic, so there is no kind hierarchy to justify beyond one rule --
        a sub-bee's Alarm and Question outrank routine traffic (heartbeats, progress, timers),
        and a CRITICAL Alarm outranks everything, because a supervisor that lets an old heartbeat
        queue ahead of a fresh postcondition failure is not supervising. Everything else is flat,
        so age, task linkage and latency urgency do the ordering work. A Guard request is refused
        outright: it is the Queen's alone to decide (ADR-0043), so a Warden's Attendant never
        orders one, whatever reached its inbox.

        Returns:
            A WeightTable with a two-level kind table, graded severities, no principal
            favouritism, and GUARD_REQUEST refused.
        """
        return cls(
            kind_weights={
                InboxKind.ALARM: 3.0,
                InboxKind.QUESTION: 2.0,
                InboxKind.HUMAN_MESSAGE: 1.0,  # Never arrives at a Warden; listed for completeness.
                InboxKind.WAGGLE_MESSAGE: 1.0,
                InboxKind.WATCH_OBSERVATION: 1.0,
                InboxKind.TIMER: 1.0,
            },
            principal_weights={},
            age_weight_per_s=0.001,
            severity_weights={
                AlarmSeverity.INFO: 0.0,
                AlarmSeverity.WARNING: 1.0,
                AlarmSeverity.CRITICAL: 5.0,  # 3 (ALARM) + 5 = 8, clear of any routine item.
            },
            latency_weight=1.0,
            task_link_weight=1.0,
            refused_kinds=frozenset({InboxKind.GUARD_REQUEST}),  # The Queen's alone (ADR-0043).
        )


class Priority(BaseModel):
    """One InboxItem's scored outcome: the number the Attendant orders by, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(description="The item's total score; higher sorts first.")
    reasons: tuple[str, ...] = Field(
        description="One entry per factor that contributed, in the order score_item computed "
        "them, for logs and the Observation Hive's Attendant view."
    )
