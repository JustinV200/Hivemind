"""Decide how many fresh Virtual Cells each backend may start now: the Swarming policy (ADR-0063).

Swarming is the Hive scaling its fleet up under load. The dispatcher already provisions one fresh
Virtual Cell for a ready task when placement picks the Virtual side; this module is the cap over
that. `plan_swarm` reads a `SwarmView` (each backend's Cells held and being made, the Hive
Stand's room) and a `SwarmingPolicy` (`[virtual_cells.swarming]`) and returns, per backend, how
many fresh Cells it may start, with the reason when that is none. Every bound is a minimum, and a
bound left unset does not bind, so the default policy never holds a backend back. Queue depth is
no bound here: the dispatcher asks once per ready task with no Cell on the way, so demand is
always the one Cell for the task being placed.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.scheduler`
    sub-package. Called by `hivemind.queen.dispatcher.snapshot` before every placement decision.
    Imports nothing from `hivemind`: the import contract that keeps autopilot off `hivemind.llm`
    names this package too, so Swarming works with every provider down.

Key invariants:
    - Pure (codingrules 8.3): no I/O, no clock, no store; the same inputs give the same plan.
    - An allowance is never negative, never above any bound that is set, and None (unbounded)
      only when no bound is set.
    - A backend priced at zero is never cost-capped; one not marked `draws_on_hive_stand` is
      never capped by the Hive Stand's room.
    - Reasons carry the bound's name and its configured figure only, never a live count, so the
      dispatcher's once-per-cause `queen.decided` is said once per cap, not once per change.

See Also:
    - docs/adr/0063-swarming-policy-and-cost-caps.md for the decision.
    - hivemind.queen.dispatcher.snapshot for where the plan is applied.
    - hivemind.manifest.schema.placement for `[virtual_cells.swarming]`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = ["BackendLimit", "BackendLoad", "SwarmPlan", "SwarmView", "SwarmingPolicy", "plan_swarm"]


@dataclass(frozen=True, slots=True)
class BackendLimit:
    """`[virtual_cells.swarming.backends.<name>]`: one backend's own Swarming bounds.

    Attributes:
        max_cells: The most Cells this backend may hold or be making at once; None for no cap
            beyond the one the backend declares itself.
        cost_per_hour_usd: What one of its Cells costs an hour while held; 0 for a local backend.
        draws_on_hive_stand: Whether its Cells run on the Hive Stand's own host, so the Stand's
            free memory and idle cores bound how many more it may start.
    """

    max_cells: int | None = None
    cost_per_hour_usd: float = 0.0
    draws_on_hive_stand: bool = False


@dataclass(frozen=True, slots=True)
class SwarmingPolicy:
    """`[virtual_cells.swarming]`: the Hive-wide bounds, and each backend's own.

    Attributes:
        max_parallel: The most Virtual Cells held or being made at once, across every backend;
            None for no Hive-wide cap.
        max_cost_per_hour_usd: The most every Virtual Cell held or being made may cost an hour
            together; None for no cost cap.
        backends: Per-backend bounds by registry name; a backend absent here has none.
    """

    max_parallel: int | None = None
    max_cost_per_hour_usd: float | None = None
    backends: Mapping[str, BackendLimit] = field(default_factory=dict)

    def limit_for(self, name: str) -> BackendLimit:
        """Return `name`'s own bounds, or the empty ones when the manifest names none."""
        return self.backends.get(name, BackendLimit())


@dataclass(frozen=True, slots=True)
class BackendLoad:
    """One backend as the dispatcher sees it right now.

    Attributes:
        name: Its registry name.
        held: Cells it holds (running or dormant), as the lifecycle counts them.
        provisioning: Fresh Cells being made on it right now.

    Its own declared cap is not read here: placement already skips a backend with no headroom,
    under its own reason.
    """

    name: str
    held: int
    provisioning: int


@dataclass(frozen=True, slots=True)
class SwarmView:
    """Everything `plan_swarm` reads, measured by its caller.

    Attributes:
        backends: Every registered Virtual backend.
        stand_room: Fresh Cells' worth of free memory and idle cores on the Hive Stand, as
            measured: Cells still being made use none yet, so `plan_swarm` takes those off. None
            when not measured (no Hive Stand attached), which leaves `draws_on_hive_stand` unbound.
    """

    backends: tuple[BackendLoad, ...]
    stand_room: int | None = None


@dataclass(frozen=True, slots=True)
class SwarmPlan:
    """What `plan_swarm` decided: how many fresh Cells each backend may start, and why not.

    Attributes:
        allowance: Fresh Cells each backend may start now, by name: the smallest bound that is
            set, never negative; None when no bound is set on it.
        reasons: Why, for each backend whose allowance is zero.
    """

    allowance: Mapping[str, int | None]
    reasons: Mapping[str, str]


def plan_swarm(view: SwarmView, policy: SwarmingPolicy) -> SwarmPlan:
    """Return how many fresh Virtual Cells each backend in `view` may start now under `policy`.

    Args:
        view: Every backend's load and the Hive Stand's room, measured live.
        policy: The configured bounds.

    Returns:
        A `SwarmPlan` with one allowance per backend in `view`, and a reason for each zero.
    """
    allowance: dict[str, int | None] = {}
    reasons: dict[str, str] = {}
    for load in view.backends:
        # A bound binds only when the manifest sets it; with none set, the backend is unbounded.
        binding = [(room, why) for room, why in _bounds(load, view, policy) if room is not None]
        if not binding:
            allowance[load.name] = None
            continue
        room, why = min(binding, key=lambda pair: pair[0])
        allowance[load.name] = max(0, room)
        if room <= 0:
            reasons[load.name] = f"swarming: {why}"
    return SwarmPlan(allowance=allowance, reasons=reasons)


def _bounds(
    load: BackendLoad, view: SwarmView, policy: SwarmingPolicy
) -> list[tuple[int | None, str]]:
    """Return every bound on `load`'s fresh Cells as (room, reason); None room binds nothing."""
    limit = policy.limit_for(load.name)
    in_use = sum(other.held + other.provisioning for other in view.backends)
    return [
        (
            _left(limit.max_cells, load.held + load.provisioning),
            f"max_cells {limit.max_cells} reached",
        ),
        (_left(policy.max_parallel, in_use), f"max_parallel {policy.max_parallel} reached"),
        (
            _stand_left(view, policy) if limit.draws_on_hive_stand else None,
            "the Hive Stand has no room for another Cell",
        ),
        (
            _cost_room(view, policy, limit),
            f"max_cost_per_hour_usd {policy.max_cost_per_hour_usd} reached",
        ),
    ]


def _left(cap: int | None, used: int) -> int | None:
    """Return what `cap` leaves after `used`, or None when there is no cap."""
    return None if cap is None else cap - used


def _stand_left(view: SwarmView, policy: SwarmingPolicy) -> int | None:
    """Return the Stand's room less the Cells being made on backends that draw on it.

    A Cell still being made uses no memory yet, so the measured room still counts it as free.
    """
    if view.stand_room is None:
        return None
    making = sum(
        load.provisioning
        for load in view.backends
        if policy.limit_for(load.name).draws_on_hive_stand
    )
    return view.stand_room - making


def _cost_room(view: SwarmView, policy: SwarmingPolicy, limit: BackendLimit) -> int | None:
    """Return how many more of this backend's Cells the hourly cost cap still pays for."""
    cap = policy.max_cost_per_hour_usd
    if cap is None or limit.cost_per_hour_usd <= 0:
        return None  # No cap, or a free Cell: money never binds it.
    burn = sum(
        policy.limit_for(other.name).cost_per_hour_usd * (other.held + other.provisioning)
        for other in view.backends
    )
    # A tiny epsilon so a cap that is an exact multiple of the price is not lost to rounding.
    room = (cap - burn) / limit.cost_per_hour_usd + 1e-9
    if not math.isfinite(room):
        # A price tiny next to the cap overflows the quotient: unbounded headroom, or none.
        return None if room > 0 else 0
    return math.floor(room)
