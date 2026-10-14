"""Define ask_for_rung: write the operator's `promote` or `demote` as a wish and a ceiling.

The operator's two rung commands are not the same kind of act as the others on the desk. They move
nothing themselves (the running Hive's swarm gate carries the move out, one rung at a time, with a
Guard check and a Handoff for every task on the device), and since the Queen also moves devices on
her own, they must also say what she may do afterwards. So each command writes two things on the
node. The wish (`desired_level`) is the adjacent rung, up or down, which the gate acts on and
then clears. The ceiling is the highest rung the Queen may take the device to by her own
decisions: a `promote` lifts it, since the operator wants the device higher and has no quarrel
with her taking it further, and a `demote` sets it to the rung the device is being lowered to, so
she cannot climb back by herself, which is what pinning a device down means. A `demote` on the
bottom rung has no rung to lower to, so it only pins the device as a gateway and withdraws any
climb that was pending.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.operator`.
    Called by `hivemind.swarm.operator.desk.OperatorDesk.move`. Calls into the registry, the
    ladder's table and the operator errors.

Key invariants:
    - A wish is always one rung from the rung the device is on, up or down, never a jump.
    - The ceiling is written before the wish, so a Queen reading between the two writes finds the
      device held, never free to take back the rung it is being lowered from.
    - A request that is already as asked writes nothing.

See Also:
    - hivemind.swarm.operator.desk for the desk this serves.
    - hivemind.queen.swarm_gate.rungs for the reader of the ceiling.
"""

from __future__ import annotations

from hivemind.swarm.level import TRANSITIONS, is_promotion
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.operator.errors import RungUnavailableError
from hivemind.swarm.registry import SwarmRegistry
from waggle.messages.swarm import RuntimeLevel

__all__ = ["ask_for_rung"]


async def ask_for_rung(
    registry: SwarmRegistry, current: SwarmNode, *, promote: bool
) -> tuple[SwarmNode, bool]:
    """Write the wish and the ceiling a `promote` or a `demote` of `current` stands for.

    Args:
        registry: Where the device lives; the running Hive reads the same rows.
        current: The device as the registry holds it now, not revoked.
        promote: True to climb, False to descend.

    Returns:
        The device carrying the wish and the ceiling, and whether anything was written (False
        when both already were as asked).

    Raises:
        RungUnavailableError: A `promote` of a device already on the top rung.
    """
    if not promote and current.level is RuntimeLevel.GATEWAY:
        return await _pin_at_the_bottom(registry, current)
    target = adjacent_rung(current, promote=promote)
    ceiling = None if promote else target
    if current.desired_level is target and current.ceiling is ceiling:
        return current, False
    # The ceiling first: a Queen reading between the two writes must already find the device
    # held, not free to take back the rung the operator is lowering it from.
    held = await registry.set_ceiling(current.device_id, ceiling)
    return await registry.set_desired_level(held.device_id, target), True


def adjacent_rung(node: SwarmNode, *, promote: bool) -> RuntimeLevel:
    """Return the one rung up (or down) from the rung `node` is on, from the ladder's own table.

    Args:
        node: The device.
        promote: True for the rung above, False for the rung below.

    Returns:
        The adjacent rung.

    Raises:
        RungUnavailableError: There is no such rung: the device is on the end of the ladder.
    """
    # The ladder is a line, so exactly one rung lies in each direction from the middle and at most
    # one from an end; reading it from TRANSITIONS keeps one table deciding which moves exist.
    candidates = [
        rung for rung in TRANSITIONS[node.level] if is_promotion(node.level, rung) == promote
    ]
    if not candidates:
        raise RungUnavailableError(node.device_id, node.level, is_promotion=promote)
    return candidates[0]


async def _pin_at_the_bottom(registry: SwarmRegistry, current: SwarmNode) -> tuple[SwarmNode, bool]:
    """Pin a gateway where it is, so the Queen does not colonize it on her own."""
    if current.ceiling is RuntimeLevel.GATEWAY and current.desired_level is None:
        return current, False
    # A pending climb is withdrawn with the pin: the operator now says the opposite.
    pinned = await registry.set_ceiling(current.device_id, RuntimeLevel.GATEWAY)
    return await registry.set_desired_level(pinned.device_id, None), True
