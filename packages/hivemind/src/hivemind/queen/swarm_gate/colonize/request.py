"""Define decide_request: what a device's pending rung asks of the Queen, or why she refuses it.

The operator climbs or lowers a device with `hive swarm promote` and `demote`, which only record
the rung wanted (`desired_level`); the Queen carries it out one rung at a time, because the ladder
has no edge that skips one (ADR-0052). This is the rule that reads that request, as a pure
function so every refusal is a row of a table, and the flow that acts on it holds no judgement of
its own. From the first rung the step is a promotion if `decide_colonize` agrees the device can
carry a Warden; from the second it is a withdrawal, which only an idle Warden may make, since its
Handoffs live on the device. A rung that touches a Nuc is refused with its own word until the
Nuc promotion exists (wave 3D); a refusal always names one stable word, and the operator reads it
on the trail.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Called by `.flow.Colonizer.request`. Calls into
    `hivemind.forage` (the footprint type), `hivemind.queen.swarm_gate.terms` and
    `hivemind.swarm` (the node, the ladder and the pure colonization decision) only.

Key invariants:
    - Pure: no I/O, no clock; the same node, terms and facts give the same answer.
    - One rung at a time: whatever rung is wanted, the step returned is the next one toward it.
    - A request that cannot be carried out is a refusal with one stable word, never an exception.
    - A withdrawal is never ordered of a Warden that is not idle.

See Also:
    - hivemind.swarm.level for `decide_colonize`, which the first rung defers to.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for withdrawal and the ladder.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.forage import RoleFootprint
from hivemind.queen.swarm_gate.terms import GateTerms
from hivemind.swarm import ColonizeFacts, RuntimeLevel, SwarmNode, decide_colonize

NUC_RUNG_WORD = "nuc_rung_unavailable"  # The Nuc promotion is wave 3D's; refused until it lands.
NOT_ASKED_WORD = "not_asked"  # The node asks for nothing, or for the rung it is already on.
BUSY_WORD = "warden_busy"  # A withdrawal while the Warden holds work: its Handoffs are on-device.

# The rungs, lowest first: how "the next one toward" the wanted rung is found without a table of
# every pair.
_LADDER = (RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED, RuntimeLevel.NUC)

__all__ = [
    "BUSY_WORD",
    "NOT_ASKED_WORD",
    "NUC_RUNG_WORD",
    "Ask",
    "Step",
    "decide_request",
    "next_rung",
]


class Step(Enum):
    """The one thing the Queen does about a request."""

    PROMOTE = "promote"  # Move the Warden onto the device (GATEWAY -> COLONIZED).
    WITHDRAW = "withdraw"  # Move it back to the Hive Stand (COLONIZED -> GATEWAY).


@dataclass(frozen=True, slots=True)
class Ask:
    """The Queen's answer to a request: a step and why, or no step and why not.

    Attributes:
        step: What to do; None when the request is refused.
        word: The reason to go ahead (a promotion's `ColonizeReason`) or the one word for the
            refusal. Never free text.
    """

    step: Step | None
    word: str


def next_rung(level: RuntimeLevel, wanted: RuntimeLevel) -> RuntimeLevel:
    """Return the rung one step from `level` toward `wanted`; `level` itself when already there.

    Args:
        level: The rung the device is on.
        wanted: The rung the operator asked for.

    Returns:
        The adjacent rung on the way.
    """
    here, there = _LADDER.index(level), _LADDER.index(wanted)
    if there == here:
        return level
    return _LADDER[here + 1] if there > here else _LADDER[here - 1]


def decide_request(
    node: SwarmNode, terms: GateTerms, footprint: RoleFootprint, *, is_idle: bool
) -> Ask:
    """Decide what `node`'s pending `desired_level` asks of the Queen right now.

    Args:
        node: The device as the registry holds it, with its `desired_level`.
        terms: The gate's numbers; the relief pressure and the free memory a device must keep.
        footprint: What one bee costs a Cell, for the device's Forage test.
        is_idle: Whether the device's Warden holds no work and reports WATCH; read by a
            withdrawal and by nothing else.

    Returns:
        A step and its reason, or a refusal and its word.
    """
    wanted = node.desired_level
    if wanted is None or wanted is node.level:
        return Ask(None, NOT_ASKED_WORD)
    rung = next_rung(node.level, wanted)
    if RuntimeLevel.NUC in (node.level, rung):
        return Ask(None, NUC_RUNG_WORD)
    if rung is RuntimeLevel.COLONIZED:
        return _promotion(node, terms, footprint)
    return Ask(Step.WITHDRAW, "operator") if is_idle else Ask(None, BUSY_WORD)


def _promotion(node: SwarmNode, terms: GateTerms, footprint: RoleFootprint) -> Ask:
    """Whether `decide_colonize` lets the device carry a Warden, and its reason either way."""
    facts = ColonizeFacts(
        level=node.level,
        python_version=node.capabilities.python_version,
        access_level=node.access_level,
        capacity=node.capacity,
        footprint=footprint,
        min_free_memory_bytes=terms.min_free_memory_bytes,
        desired_level=node.desired_level,
        hive_pressure=0.0,  # Pressure-driven relief is the Queen's own trigger, not wired yet.
        relief_pressure=terms.relief_pressure,
    )
    decision = decide_colonize(facts)
    if decision.reason is None:
        return Ask(None, decision.refusal or NOT_ASKED_WORD)
    return Ask(Step.PROMOTE, decision.reason.value)
