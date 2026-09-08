"""Re-export the control family: the protocol's own plumbing and the Hive-wide orders.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Control is
the protocol's own plumbing plus the orders that address the whole Hive. ``protocol`` holds
liveness (ping, pong), the error reply, shutdown and the Clustering pair (cluster, wake: pause and
resume while a model provider is down); ``hive`` the Hive-wide messages: a human's words carried
in, a Pheromone Mask override and the notice that the Hive Stand has moved. This package is the
family's face: a caller imports any of its messages, enums or value models from here without
knowing which module defines them. The bounds each module names stay in that module, because the
spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a control payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``control.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.11 for the family's normative fields and rules.
    - waggle.messages.control.protocol and waggle.messages.control.hive for the definitions.

Public API:
    - Protocol (protocol): Cluster, ClusterCause, ErrorMessage, Ping, Pong, Shutdown, Wake.
    - Hive (hive): HumanMessage, MaskOverride, MaskOverrideAction, MaskTactic, QueenMoved.
"""

from waggle.messages.control.hive import (
    HumanMessage,
    MaskOverride,
    MaskOverrideAction,
    MaskTactic,
    QueenMoved,
)
from waggle.messages.control.protocol import (
    Cluster,
    ClusterCause,
    ErrorMessage,
    Ping,
    Pong,
    Shutdown,
    Wake,
)

__all__ = [
    "Cluster",
    "ClusterCause",
    "ErrorMessage",
    "HumanMessage",
    "MaskOverride",
    "MaskOverrideAction",
    "MaskTactic",
    "Ping",
    "Pong",
    "QueenMoved",
    "Shutdown",
    "Wake",
]
