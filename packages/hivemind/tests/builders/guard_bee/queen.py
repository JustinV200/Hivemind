"""Build the Guard Bee a composition root would build for a running Queen, from her own parts.

`guard_bee_for_queen` builds what the composition root builds (`hivemind.cli.compose.guard.
with_guard`, for `hive run` and `hive serve`) from a test's Queen: her trail, clock and node, her
Guard policy (the enforcer's), her slot resolver and her call gate (the Royal Reserve's seats), the
shipped tier table, a `[guard]` section, and a door for her Guard requests: a `RecordingDoor`, or
the running Queen herself, whose door files durably and decides on her next tick.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through `builders.guard_bee`
    by the tests that run a Guard Bee over a real Queen, Entrance or Hive.

Key invariants:
    - The Guard Bee's events carry the Queen's own Hive and node, acting as "system".
"""

from __future__ import annotations

from hivemind.cell import CellIdentity
from hivemind.guard import GuardRequestDoor
from hivemind.manifest.schema.guard import GuardSection
from hivemind.queen.deps import QueenDeps
from hivemind.supervision.capping import load_tiers
from hivemind.workers.roles.guard_bee import GuardBee, GuardBeeInputs, build_guard_bee

__all__ = ["guard_bee_for_queen"]


def guard_bee_for_queen(
    deps: QueenDeps, door: GuardRequestDoor, guard: GuardSection | None = None
) -> GuardBee:
    """Build the Guard Bee for the Queen whose parts are `deps`.

    Args:
        deps: The Queen's dependencies: trail, clock, identity, enforcer, slots and call gate.
        door: Where its Guard requests go: the Queen's door, or a recording one in a test.
        guard: The manifest's `[guard]` section; the defaults when omitted.

    Returns:
        A Guard Bee whose first round is due at once.
    """
    identity = CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
    )
    inputs = GuardBeeInputs(
        trail=deps.trail,
        clock=deps.clock,
        identity=identity,
        door=door,
        guard=guard if guard is not None else GuardSection(),
        tiers=load_tiers(),
        policy=deps.enforcer.policy,
        bound_for=deps.bound_for,
        call_gate=deps.call_gate,
    )
    return build_guard_bee(inputs)
