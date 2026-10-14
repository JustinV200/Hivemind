"""Define decide_rung: the Queen's own decision to raise a device a rung, or to take one back.

Until now only the operator's `desired_level` moved a device. The roadmap has the Queen decide too:
colonize a device whose Forage suffices when the Hive Stand needs relief or a task on it must
survive short outages, and give a colonized one a model server when the shared seats are under
pressure or a task's autonomy asks for it. This is that decision for one device, as a pure function
of the facts, so each case (a reason holds, the device cannot carry it, the operator pinned the
device, a failed attempt is backing off, a reason has been gone long enough) is a row of a table.
The reasons themselves are not re-judged here: `decide_colonize` and `decide_nuc` give them, the
same ones the operator's request goes through, and `colonize_reason` and `nuc_reason` say whether a
reason still holds for a rung she already raised.

The rules, in the order they apply:

1. The operator's pin wins. A device whose `ceiling` is below its rung is taken down one rung, when
   it is idle; the Queen never takes a device above its ceiling, and a pending `desired_level` is
   not this module's to see (the caller leaves such a device to the operator's request).
2. A reason to climb, from a device that can carry the next rung, climbs it, unless the last
   attempt at that move failed and its back-off has not run out. A reason from a device that
   cannot carry it is a refusal word, returned so it is recorded once rather than every pass.
3. A rung the Queen raised herself (`Memory.seen`) is kept while a reason for it holds, and the
   time it was last seen to hold is refreshed. Once every reason has been gone for the configured
   time (`lower_after_s`), and the device is idle, it is taken back one rung. A rung the operator
   raised has no mark and is never taken back by her.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.rungs`. Called by `.evaluate`; its memory is kept by `.keeper`.
    Calls into `hivemind.queen.swarm_gate.colonize` (the facts and the step), `hivemind.forage`,
    `hivemind.swarm` (the decisions) and waggle only.

Key invariants:
    - Pure: no I/O and no clock; the time is a fact handed in. The same facts give the same verdict.
    - One rung at a time, up or down, and never above the operator's ceiling.
    - A rung is lowered only while the device is idle, and only after its reasons have been gone for
      the whole of `lower_after_s`; any reason that holds again restarts that time.
    - A failed move is not attempted again until its back-off has run out.
    - A rung nobody recorded the Queen raising is never lowered by this rule (only by a ceiling).

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the Queen's own rung decisions.
    - hivemind.swarm.level and hivemind.swarm.nuc for the reasons and the refusals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

from hivemind.forage import RoleFootprint
from hivemind.queen.swarm_gate.colonize.nuc.facts import nuc_facts
from hivemind.queen.swarm_gate.colonize.request import NOT_ASKED_WORD, Ask, Step, colonize_facts
from hivemind.queen.swarm_gate.terms import GateTerms
from hivemind.swarm import (
    RuntimeLevel,
    SwarmNode,
    colonize_reason,
    decide_colonize,
    decide_nuc,
    nuc_reason,
)

# The rungs, lowest first: how "the one above" and "the one below" are found without a table.
_RUNGS = (RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED, RuntimeLevel.NUC)
REASONS_GONE = "reasons_gone"  # Why the Queen takes a rung back: nothing asks for it any more.
PINNED = "pinned"  # Why she takes one back otherwise: the operator's ceiling is below it.

# One move on the ladder: (the rung the device is on, the rung the Queen moves it to).
Move = tuple[RuntimeLevel, RuntimeLevel]

__all__ = [
    "PINNED",
    "REASONS_GONE",
    "Memory",
    "Move",
    "RungFacts",
    "Verdict",
    "decide_rung",
    "target_rung",
]


@dataclass(frozen=True, slots=True)
class Memory:
    """What the Queen remembers of the moves she made on one device herself.

    Attributes:
        seen: For each rung she raised the device to, when a reason for it was last seen to hold.
        retry_at: For each move that failed, the earliest time she may try it again.
    """

    seen: Mapping[RuntimeLevel, datetime] = field(default_factory=dict)
    retry_at: Mapping[Move, datetime] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RungFacts:
    """Everything `decide_rung` weighs about one device with no pending request.

    Attributes:
        node: The device, with the capacity the hub last had and no `desired_level`.
        terms: The gate's numbers: the thresholds, the servers and the two back-off times.
        footprint: What one bee costs a Cell, for the device's Forage test.
        now: The time, from the Queen's clock.
        relief: The Hive Stand's relief pressure, with this device's own bees counted in when
            the device is off the Hive Stand (`rungs.pressure.relief_pressure`); 0 for a
            device with no bee running, which has no part in it.
        seats: The shared pool's seat pressure (`rungs.pressure.seat_pressure`); 0 for a device
            with no bee running, for the same reason.
        survives: Whether a task placed on or pinned to the device must survive a lost link.
        declared: The model server ids the device's Warden declared.
        idle: Whether the device's Warden reports `WATCH` and holds no task.
        memory: What the Queen remembers of her own moves on this device.
    """

    node: SwarmNode
    terms: GateTerms
    footprint: RoleFootprint
    now: datetime
    relief: float
    seats: float
    survives: bool
    declared: tuple[str, ...]
    idle: bool
    memory: Memory


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the Queen does about a device this pass.

    Attributes:
        ask: The step to take and the reason for it; None to do nothing.
        seen: The rung whose mark is refreshed, because a reason for it still holds; or None.
        declined: The word for why a reason that holds cannot be acted on (the device cannot
            carry the rung); or None.
    """

    ask: Ask | None = None
    seen: RuntimeLevel | None = None
    declined: str | None = None


def target_rung(step: Step) -> RuntimeLevel:
    """Return the rung a step of the ladder moves the device to."""
    return {
        Step.PROMOTE: RuntimeLevel.COLONIZED,
        Step.NUC_PROMOTE: RuntimeLevel.NUC,
        Step.WITHDRAW: RuntimeLevel.GATEWAY,
        Step.NUC_DEMOTE: RuntimeLevel.COLONIZED,
    }[step]


def decide_rung(facts: RungFacts) -> Verdict:
    """Decide whether to raise `facts.node` a rung, take one back, or leave it.

    Args:
        facts: The device, the Hive Stand's pressure, the thresholds and what she remembers.

    Returns:
        The verdict: a step, the rung whose mark to refresh, and/or a refusal word to record.
    """
    node = facts.node
    seen = node.level if _raised_and_holds(facts) else None  # Refreshed even when climbing on.
    if _above(node.level, node.ceiling):
        verdict = _lower(facts, PINNED) if facts.idle else Verdict()
    else:
        verdict = _climb(facts) or _lowering(facts)
    return replace(verdict, seen=seen)


def _climb(facts: RungFacts) -> Verdict | None:
    """Return the verdict for a reason to climb, or None when nothing asks for the next rung."""
    node = facts.node
    if node.level is RuntimeLevel.NUC:
        return None
    target = _RUNGS[_RUNGS.index(node.level) + 1]
    if _above(target, node.ceiling):
        return None  # The operator pinned the device below it.
    judged = _judge(facts)
    if judged is None:
        return None
    if isinstance(judged, str):
        return Verdict(declined=judged)
    return Verdict() if _backing_off(facts, target) else Verdict(ask=judged)


def _judge(facts: RungFacts) -> Ask | str | None:
    """Return the step the rung above is worth, the refusal word, or None when nothing asks."""
    node, terms = facts.node, facts.terms
    refusal: str | None
    if node.level is RuntimeLevel.GATEWAY:
        first = decide_colonize(
            colonize_facts(
                node, terms, facts.footprint, pressure=facts.relief, survives=facts.survives
            )
        )
        if first.reason is not None:
            return Ask(Step.PROMOTE, first.reason.value)
        refusal = first.refusal
    else:
        second = decide_nuc(
            nuc_facts(node, facts.declared, terms, pressure=facts.seats, survives=facts.survives)
        )
        if second.reason is not None:
            return Ask(Step.NUC_PROMOTE, second.reason.value, second)
        refusal = second.refusal
    return None if refusal in (None, NOT_ASKED_WORD) else refusal


def _lowering(facts: RungFacts) -> Verdict:
    """Return the verdict for a rung the Queen raised: kept while a reason holds, else lowered."""
    node, memory = facts.node, facts.memory
    mark = memory.seen.get(node.level)
    if mark is None or _reason_holds(facts):
        return Verdict()  # Not hers to take back, or still wanted.
    gone_for = (facts.now - mark).total_seconds()
    if gone_for < _lower_after(facts.terms, node.level) or not facts.idle:
        return Verdict()
    return _lower(facts, REASONS_GONE)


def _lower(facts: RungFacts, why: str) -> Verdict:
    """Return the step that takes the device one rung down, unless that move is backing off."""
    level = facts.node.level
    target = _RUNGS[_RUNGS.index(level) - 1]
    if _backing_off(facts, target):
        return Verdict()
    return Verdict(ask=Ask(Step.NUC_DEMOTE if level is RuntimeLevel.NUC else Step.WITHDRAW, why))


def _raised_and_holds(facts: RungFacts) -> bool:
    """Whether the rung the device is on is one the Queen raised and a reason for it still holds."""
    return facts.node.level in facts.memory.seen and _reason_holds(facts)


def _reason_holds(facts: RungFacts) -> bool:
    """Whether any reason for the rung the device is on holds now, by the rule that raised it."""
    node, terms = facts.node, facts.terms
    if node.level is RuntimeLevel.COLONIZED:
        colonize = colonize_facts(
            node, terms, facts.footprint, pressure=facts.relief, survives=facts.survives
        )
        return colonize_reason(colonize) is not None
    if node.level is RuntimeLevel.NUC:
        nuc = nuc_facts(node, facts.declared, terms, pressure=facts.seats, survives=facts.survives)
        return nuc_reason(nuc) is not None
    return False


def _backing_off(facts: RungFacts, target: RuntimeLevel) -> bool:
    """Whether the last attempt at the move to `target` failed and its wait has not run out."""
    retry = facts.memory.retry_at.get((facts.node.level, target))
    return retry is not None and facts.now < retry


def _lower_after(terms: GateTerms, level: RuntimeLevel) -> float:
    """Seconds the reasons for `level` must have been gone before the Queen takes it back."""
    return terms.nuc_lower_after_s if level is RuntimeLevel.NUC else terms.lower_after_s


def _above(level: RuntimeLevel, ceiling: RuntimeLevel | None) -> bool:
    """Whether `level` is higher on the ladder than the operator's `ceiling` (None: no ceiling)."""
    return ceiling is not None and _RUNGS.index(level) > _RUNGS.index(ceiling)
