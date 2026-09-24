"""Run the Hive Entrance inside ``hive serve``: its parts, its wiring, its listeners, its life.

The Hive Entrance runs in the Queen's own process and event loop (ADR-0032). ``parts`` defines what
the ``hive serve`` composition root hands it (the tables, the Hive, the keys, what the manifest
decides); ``build`` wires every collaborator once over those parts and the loopback socket the root
bound; ``entrance`` is ``HiveEntrance``, the running Entrance from its start-up checks to a clean
stop; ``listeners`` runs the loopback listener and, while exposed and open, the remote one and its
tunnel child; ``server`` is one uvicorn server on one bound socket; ``tls`` keeps the remote
listener's TLS context current; ``seams`` implements enrolment's offboarder and goal ledger.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Called by
    ``hivemind.cli`` (``hive serve``) and by tests. Calls into every Entrance package, uvicorn and
    the Queen's door.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every task the Entrance starts belongs to ``HiveEntrance.run``'s task group.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the process shape.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for exposure.

Public API:
    - EntranceParts, EntranceSettings, EntranceTables, EntranceHive, EntranceKeys, IPAddress: what
      the Entrance is built from (parts).
    - build_entrance, BuiltEntrance, RELYING_PARTY_NAME, LOOPBACK_RP_ID: the wiring (build).
    - HiveEntrance, EntranceWorkers, SWEEP_INTERVAL_S: the running Entrance (entrance).
    - EntranceListeners, RemoteSetup, TunnelLaunch, FailureHandler: the listeners (listeners).
    - ListenerServer, bind_listener, GRACEFUL_SHUTDOWN_S, STOP_TIMEOUT_S, LOOPBACK_NAME: one
      server on one socket (server).
    - RemoteTls, RevokedSerials, NoCertificates: the remote listener's TLS (tls).
    - EntranceOffboarder, QueenGoalLedger: enrolment's seams (seams).
"""

from hivemind.entrance.runtime.build import (
    LOOPBACK_RP_ID,
    RELYING_PARTY_NAME,
    BuiltEntrance,
    build_entrance,
)
from hivemind.entrance.runtime.entrance import SWEEP_INTERVAL_S, EntranceWorkers, HiveEntrance
from hivemind.entrance.runtime.listeners import (
    EntranceListeners,
    FailureHandler,
    RemoteSetup,
    TunnelLaunch,
)
from hivemind.entrance.runtime.parts import (
    EntranceHive,
    EntranceKeys,
    EntranceParts,
    EntranceSettings,
    EntranceTables,
    IPAddress,
)
from hivemind.entrance.runtime.seams import EntranceOffboarder, QueenGoalLedger
from hivemind.entrance.runtime.server import (
    GRACEFUL_SHUTDOWN_S,
    LOOPBACK_NAME,
    STOP_TIMEOUT_S,
    ListenerServer,
    bind_listener,
)
from hivemind.entrance.runtime.tls import NoCertificates, RemoteTls, RevokedSerials

__all__ = [
    "GRACEFUL_SHUTDOWN_S",
    "LOOPBACK_NAME",
    "LOOPBACK_RP_ID",
    "RELYING_PARTY_NAME",
    "STOP_TIMEOUT_S",
    "SWEEP_INTERVAL_S",
    "BuiltEntrance",
    "EntranceHive",
    "EntranceKeys",
    "EntranceListeners",
    "EntranceOffboarder",
    "EntranceParts",
    "EntranceSettings",
    "EntranceTables",
    "EntranceWorkers",
    "FailureHandler",
    "HiveEntrance",
    "IPAddress",
    "ListenerServer",
    "NoCertificates",
    "QueenGoalLedger",
    "RemoteSetup",
    "RemoteTls",
    "RevokedSerials",
    "TunnelLaunch",
    "bind_listener",
    "build_entrance",
]
