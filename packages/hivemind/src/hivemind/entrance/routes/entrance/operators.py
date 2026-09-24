"""Serve operator add: the loopback-only row that refuses a second operator (ADR-0033).

The Hive has one operator: one password, set at the Hive Stand, behind every device's login.
ADR-0033 lists ``operator add`` among the routes that exist only on the loopback listener, beside
approval and reopening, so that the day a Brood supports a second operator it arrives through the
one door no remote session can reach. Until then the row refuses, as ``hive entrance operator
add`` does, whatever ``[entrance] operators`` says: this Brood keeps a single operator credential.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.entrance``.
    Registered in the route table. Calls into nothing: its answer is the refusal.

Key invariants:
    - The row is loopback-only; the remote application never mounts it (a 404 there, not a 409).

See Also:
    - hivemind.cli.entrance.operators for the CLI command that refuses the same way.
"""

from __future__ import annotations

from hivemind.entrance.errors import SingleOperatorError
from hivemind.entrance.gate.spec import LOOPBACK_ONLY, RouteEffect, RouteSpec, session_with

STEWARD = "entrance:steward"  # Administering the door; the console holds it.

__all__ = ["ROUTES"]


async def add_operator() -> None:
    """Refuse a second operator: this Brood keeps a single operator credential.

    Raises:
        SingleOperatorError: Always, until a Brood supports more than one operator.
    """
    raise SingleOperatorError(
        "This Hive keeps a single operator credential; a second operator is not supported."
    )


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/entrance/operators",
        listeners=LOOPBACK_ONLY,
        access=session_with(STEWARD),
        effect=RouteEffect.DOOR,
        endpoint=add_operator,
        summary="Add a second operator (loopback only); refused while the Hive keeps one.",
        status_code=204,
        refusals=((409, "The Hive keeps a single operator credential."),),
    ),
)
