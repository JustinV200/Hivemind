"""Define FloorRefusal and the two rule-id families every floor refuses under.

A floor (ADR-0039, "Floors hold whatever a set says") refuses an action whatever the principal
holds, so its answer is only ever a refusal: a stable rule id for the trail and the end of a
reason sentence for the human. `FloorRefusal` is that answer. Rule ids come in two families:
`guard.tier_floor.<floor>` for what a Comb Shield tier forbids (Night Veil's Virtual-only
placement, local-only slots, clearance, location blindness, control link and initiation, and tier
inheritance) and `guard.state_floor.<floor>` for the Hive's own state, which no bee may touch
(ADR-0041). `Floor` is the one signature every floor function shares, so
`hivemind.guard.policy.floors.chain` can run them as an ordered list.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Built by every floor module; read by `hivemind.guard.policy.evaluate`, which turns a refusal
    into a `PolicyDecision`. Calls into this package's `models` and `table` for types only.

Key invariants:
    - A FloorRefusal is only ever a refusal; nothing here can express an allow.
    - Every rule id is `<family>.<floor>` with a lowercase snake_case floor name, well inside
      `PolicyDecision.rule`'s bound.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Floors hold whatever
      a set says".
    - hivemind.guard.policy.floors.chain for the order the floors run in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hivemind.guard.policy.models import PolicyRequest
from hivemind.guard.policy.table import GuardPolicy

TIER_FLOOR_RULE = "guard.tier_floor"  # Prefix: `.night_veil_location`, ...: what a tier forbids.
STATE_FLOOR_RULE = "guard.state_floor"  # Prefix: `.state_paths`, ...: the Hive's own state.

__all__ = ["STATE_FLOOR_RULE", "TIER_FLOOR_RULE", "Floor", "FloorRefusal", "state", "tier"]


@dataclass(frozen=True, slots=True)
class FloorRefusal:
    """One floor's refusal: the rule id it is recorded under and why, as a sentence's end.

    Attributes:
        rule: The stable rule id, `guard.tier_floor.<floor>` or `guard.state_floor.<floor>`.
        why: The end of the reason sentence `evaluate` builds: why the floor refuses, naming no
            content the action carried.
    """

    rule: str
    why: str


# Every floor: read the request's context and the policy's data, refuse or return None.
Floor = Callable[[PolicyRequest, GuardPolicy], FloorRefusal | None]


def tier(floor: str, why: str) -> FloorRefusal:
    """Build a Comb Shield tier floor's refusal, rule `guard.tier_floor.<floor>`.

    Args:
        floor: The floor's lowercase snake_case name (`night_veil_location`).
        why: Why it refuses, as the end of a sentence.

    Returns:
        The refusal.
    """
    return FloorRefusal(rule=f"{TIER_FLOOR_RULE}.{floor}", why=why)


def state(floor: str, why: str) -> FloorRefusal:
    """Build a Hive-state floor's refusal, rule `guard.state_floor.<floor>`.

    Args:
        floor: The floor's lowercase snake_case name (`loopback`).
        why: Why it refuses, as the end of a sentence.

    Returns:
        The refusal.
    """
    return FloorRefusal(rule=f"{STATE_FLOOR_RULE}.{floor}", why=why)
