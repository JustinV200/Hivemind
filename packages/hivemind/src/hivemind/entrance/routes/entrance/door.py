"""Serve the Entrance's mode: read it, reduce the door, and reopen it from loopback.

The Hive Entrance can be narrowed at once (ADR-0041): reducing drops it to loopback only, ends every
remote session, stops the remote listener and closes every remote socket within a second. Narrowing
is always safe, so a steward's session may reduce from either listener without step-up. Reopening
exists only on the loopback listener and needs a step-up, so a compromised remote session can
never reopen what the operator closed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.entrance``.
    Registered in the route table. Calls into the Entrance Reducer and the persisted mode.

Key invariants:
    - The reopen row is loopback-only; the remote application never mounts it.

See Also:
    - hivemind.entrance.reducer for what reducing and reopening do.
"""

from __future__ import annotations

from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    LOOPBACK_ONLY,
    RouteEffect,
    RouteSpec,
    session_with,
)
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.models import ChangedView, ModeView
from hivemind.entrance.reducer import ReduceReason

OBSERVE = "observe"  # Reading the mode is a view.
STEWARD = "entrance:steward"  # Narrowing or reopening the door is administering it.

__all__ = ["ROUTES"]


async def read_mode(services: Services) -> ModeView:
    """Read the Entrance's mode and whether the remote listener serves.

    Args:
        services: The Entrance's services.

    Returns:
        The mode.
    """
    # Latency: one local read of a one-row table.
    mode = await services.enrolment.records.store.entrance_mode.get()
    door = services.door
    return ModeView(mode=mode, exposed=door.exposed, remote_listening=door.remote_listening)


async def reduce_entrance(caller: CallerParam, services: Services) -> ChangedView:
    """Drop the Entrance to loopback only; reducing a reduced Entrance changes nothing.

    Args:
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        Whether this call moved the mode, and the mode now.
    """
    changed = await services.reducer.reduce(ReduceReason.OPERATOR, caller.device.id)
    mode = await services.enrolment.records.store.entrance_mode.get()
    return ChangedView(changed=changed, mode=mode)


async def reopen_entrance(caller: CallerParam, services: Services) -> ChangedView:
    """Reopen a reduced Entrance (loopback only, after step-up).

    Args:
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        Whether this call moved the mode, and the mode now.
    """
    await require_step_up(services, caller, ActionKind.REOPEN)
    changed = await services.reducer.reopen(caller.session)
    mode = await services.enrolment.records.store.entrance_mode.get()
    return ChangedView(changed=changed, mode=mode)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/entrance/mode",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=read_mode,
        summary="Read the Entrance's mode.",
        response_model=ModeView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/entrance/reduce",
        listeners=BOTH_LISTENERS,
        access=session_with(STEWARD),
        effect=RouteEffect.DOOR,
        endpoint=reduce_entrance,
        summary="Reduce the Entrance to loopback only, ending every remote session.",
        response_model=ChangedView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/entrance/open",
        listeners=LOOPBACK_ONLY,
        access=session_with(STEWARD),
        effect=RouteEffect.DOOR,
        endpoint=reopen_entrance,
        summary="Reopen a reduced Entrance (loopback only, after step-up).",
        response_model=ChangedView,
    ),
)
