"""Serve the entrance resource: the Hive Entrance administering its own door.

``/v1/entrance`` is the door itself (ADR-0033), split by responsibility: ``door`` (the mode,
reducing, and reopening from loopback after step-up), ``enrolments`` (invites, the devices asking
to join, approval and denial on loopback, and the steward route), ``held`` (the requests held for
a person's confirmation) and ``operators`` (operator add, loopback only, refused while the Hive
keeps one operator). Every row declares its listeners: the loopback-only ones are never
mounted on the remote listener.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Its four row
    groups join the route table through ``hivemind.entrance.routes.registry``. Calls into the
    Reducer, enrolment and the confirmation flow.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the door's rules.

Public API:
    - DOOR_ROUTES: the mode, reduce and reopen rows (door).
    - ENROLMENT_ROUTES: invites, pending requests, approval, denial and the steward row
      (enrolments).
    - HELD_ROUTES: the held requests' rows (held).
    - OPERATOR_ROUTES: operator add (operators).
"""

from hivemind.entrance.routes.entrance.door import ROUTES as DOOR_ROUTES
from hivemind.entrance.routes.entrance.enrolments import ROUTES as ENROLMENT_ROUTES
from hivemind.entrance.routes.entrance.held import ROUTES as HELD_ROUTES
from hivemind.entrance.routes.entrance.operators import ROUTES as OPERATOR_ROUTES

__all__ = ["DOOR_ROUTES", "ENROLMENT_ROUTES", "HELD_ROUTES", "OPERATOR_ROUTES"]
