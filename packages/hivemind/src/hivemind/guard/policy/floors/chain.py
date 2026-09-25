"""Run every floor in order: the first to refuse decides, before any other Guard rule runs.

`hivemind.guard.policy.evaluate` runs the floors first (ADR-0039: "floors, the access-level
ceiling, the deny list, then the held set"), because a floor refuses whatever the principal holds.
`FLOORS` is their one order, hardest and cheapest first: the Hive's own state (ADR-0041: a bee
never touches it, on any Cell, at any tier), then who may start Night Veil work at all, then what
Night Veil work may do, then tier inheritance. `floor_refusal` walks the list and returns the
first refusal, so an action one floor refuses is never asked of another, and the trail records one
rule per denial.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Called by `hivemind.guard.policy.evaluate` (inside `evaluate` and `floor_decision`). Calls
    into this package's floor modules and `refusal`.

Key invariants:
    - Pure and total: the same request and policy always give the same answer.
    - Returns only refusals: nothing here can turn a request into an allow.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Floors hold whatever
      a set says".
"""

from __future__ import annotations

from hivemind.guard.policy.floors.hive_state import hive_state_floor
from hivemind.guard.policy.floors.inheritance import inheritance_floor
from hivemind.guard.policy.floors.initiation import initiation_floor
from hivemind.guard.policy.floors.night_veil import night_veil_floor
from hivemind.guard.policy.floors.refusal import Floor, FloorRefusal
from hivemind.guard.policy.models import PolicyRequest
from hivemind.guard.policy.table import GuardPolicy

__all__ = ["FLOORS", "floor_refusal"]

# The floors, hardest and cheapest first (module docstring): the first to refuse decides.
FLOORS: tuple[Floor, ...] = (
    hive_state_floor,
    initiation_floor,
    night_veil_floor,
    inheritance_floor,
)


def floor_refusal(request: PolicyRequest, policy: GuardPolicy) -> FloorRefusal | None:
    """Return the first floor's refusal of `request`, or None when no floor refuses it.

    Args:
        request: The action being decided.
        policy: The Guard policy; the Hive-state floor reads its `hive_state`.

    Returns:
        The first refusal in `FLOORS` order, or None.
    """
    for floor in FLOORS:
        refusal = floor(request, policy)
        if refusal is not None:
            return refusal
    return None
