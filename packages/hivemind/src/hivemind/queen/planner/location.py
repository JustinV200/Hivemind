"""Define the Night Veil location asks a goal is refused for at submission (roadmap step 10.3d).

A Night Veil Cell is location-blind (ADR-0030): the Guard's Night Veil floor refuses the
location families (`geo`, `wifi:scan`, `host:metadata`) and `net` to a cloud metadata endpoint
on it, at every point, whatever a set holds. A Night Veil goal that asks for one of them anyway
could never run as asked, so it is refused before any of its tasks exists: `ceiling_location_asks`
names every such ask in the goal's own ceiling (the capability set its submitter held) and
`needs_location_asks` every one in the planned tasks' needs (a network scope that is a metadata
endpoint). The Queen records each through the Guard and refuses the goal with
`NightVeilLocationError`, which her intake settles as a REFUSED request carrying its reason and
`hive run` prints as a refusal.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    planner sub-package. Called by `hivemind.queen.goal_submission.plan_goal_graph`. Calls into
    `hivemind.cell` (CombShieldLevel, TaskNeeds), `hivemind.common.errors`, `hivemind.guard`
    (Capability, CapabilitySet, the location families and the metadata host names) and
    `hivemind.queen.errors` only.

Key invariants:
    - Pure: both functions only read, and name asks in a stable, sorted order.
    - A ceiling that holds `net:*` asks for nothing here: only a location family, or a `net`
      scope that names a metadata endpoint, is an ask; the floor still refuses the endpoint
      itself if a task ever tries to reach it.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the location-blind rule.
    - hivemind.guard.policy.floors.night_veil for the floor that refuses each ask.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from hivemind.cell import CombShieldLevel, TaskNeeds
from hivemind.common.errors import PermissionDeniedError
from hivemind.guard import Capability, CapabilityFamily, CapabilitySet
from hivemind.guard.net import is_metadata_host
from hivemind.guard.policy.floors import LOCATION_FAMILIES
from hivemind.queen.errors import QueenError

__all__ = ["NightVeilLocationError", "ceiling_location_asks", "needs_location_asks"]


class NightVeilLocationError(PermissionDeniedError, QueenError):
    """Raise when a Night Veil goal asks where its Cell is: refused before it is ever planned."""

    code: ClassVar[str] = "hivemind.queen.night_veil_location"

    def __init__(self, asks: tuple[Capability, ...]) -> None:
        """Build the refusal naming every location ask.

        Args:
            asks: Each capability the goal's ceiling or planned needs asked for; never empty.
        """
        named = ", ".join(str(ask) for ask in asks)
        super().__init__(
            "A Night Veil goal is location-blind (ADR-0030), and this one asks for "
            f"{named}; it was refused before any task was created."
        )
        self.asks = asks


def ceiling_location_asks(capabilities: tuple[str, ...] | None) -> tuple[Capability, ...]:
    """Return every location ask in a goal's ceiling, sorted.

    Args:
        capabilities: The goal's capability set as canonical strings; None for the operator's
            own path, which carries no ceiling and so asks for nothing.

    Returns:
        Each capability in a location family, or a `net` scope naming a metadata endpoint.
    """
    if capabilities is None:
        return ()
    held = CapabilitySet.parse(*capabilities).capabilities
    return tuple(sorted((c for c in held if _asks_location(c)), key=str))


def needs_location_asks(needs: Iterable[TaskNeeds]) -> tuple[Capability, ...]:
    """Return every location ask in the needs of the Night Veil tasks among `needs`, sorted.

    Args:
        needs: Each planned task's needs; a task at any other tier asks for nothing here.

    Returns:
        `net:<scope>` for each network scope a Night Veil task needs that names a metadata
        endpoint, once each.
    """
    asks = {
        Capability(family=CapabilityFamily.NET, scope=scope)
        for need in needs
        if need.comb_shield is CombShieldLevel.NIGHT_VEIL
        for scope in need.network_scopes
        if is_metadata_host(scope)
    }
    return tuple(sorted(asks, key=str))


def _asks_location(capability: Capability) -> bool:
    """Return whether one capability asks where a Cell is."""
    if capability.family in LOCATION_FAMILIES:
        return True
    return capability.family is CapabilityFamily.NET and is_metadata_host(capability.scope)
