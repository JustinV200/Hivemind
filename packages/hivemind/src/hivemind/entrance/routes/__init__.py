"""Hold the Landing Board's HTTP routes: one module per resource, every route declaring its door.

The Landing Board is the Hive Entrance's versioned public API (ADR-0032, ADR-0034): every route
lives under ``/v1/`` in one module per resource, and every route declares its method, path, the
listeners that serve it and the capability a device must hold (``hivemind.entrance.gate.spec``).
Handlers are thin: validate (the models in ``hivemind.entrance.models``), authorise (the gate),
call a subsystem's public API (the Queen for every write into the Hive, the Entrance's own flows
for the door), shape the reply. The resources built so far: ``auth`` (login, logout, step-up),
``enrol`` (redeeming an invite), ``entrance`` (the door: mode, reduce, reopen, invites, approvals,
the steward route, held requests), ``devices``, ``goals``, ``chat``, ``inbox`` and ``push``. The
live streams are views in ``hivemind.entrance.streams``. Later resources (tasks, cells, wardens,
forage, episodes, tools, honey, trail, swarm, llm) join by adding a module and one line to
``registry``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Read by
    ``hivemind.entrance.app`` (both applications) and ``hivemind.entrance.landing_board`` (the
    OpenAPI document). Calls into the gate, the models, the Queen's door and the Entrance flows.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Loopback-only rows (invites, approve, deny, unlock, re-grant, revoke, reopen) are never
      mounted on the remote listener.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the route rules.
    - hivemind.entrance.gate.spec for what a row declares.

Public API:
    - RESOURCE_ROUTES: every resource's rows, in the order the document lists them (registry).
"""

from hivemind.entrance.routes.registry import RESOURCE_ROUTES

__all__ = ["RESOURCE_ROUTES"]
