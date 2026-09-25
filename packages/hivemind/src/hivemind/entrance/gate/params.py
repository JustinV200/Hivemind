"""Name the parameters every Landing Board route declares: its services, listener and caller.

A route's signature is its contract (FastAPI builds the OpenAPI document from it), so what a route
is handed is spelled once, here, as annotated types: ``Services`` (the Entrance's services),
``Here`` (this listener's own dependencies), ``CallerParam`` (the caller the route's gate
admitted) and ``ArrivalParam`` (how a public route's request arrived). None of them appears in the
published document; each is resolved by the application the route is mounted on.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Used by every
    module in ``hivemind.entrance.routes``. Calls into the gate's own dependencies only.

Key invariants:
    - A route reads its caller only through ``CallerParam``, which exists only behind a gate.

See Also:
    - hivemind.entrance.gate.admit for the gate and the caller.
    - hivemind.entrance.gate.services for the services and the listener dependencies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from hivemind.entrance.auth.session import Arrival
from hivemind.entrance.gate.admit import Caller, current_arrival, current_caller
from hivemind.entrance.gate.services import (
    EntranceServices,
    ListenerDeps,
    get_listener,
    get_services,
)

__all__ = ["ArrivalParam", "CallerParam", "Here", "Services"]

Services = Annotated[EntranceServices, Depends(get_services)]  # The Entrance's services.
Here = Annotated[ListenerDeps, Depends(get_listener)]  # This listener's own dependencies.
CallerParam = Annotated[Caller, Depends(current_caller)]  # The caller the gate admitted.
ArrivalParam = Annotated[Arrival, Depends(current_arrival)]  # How a public request arrived.
