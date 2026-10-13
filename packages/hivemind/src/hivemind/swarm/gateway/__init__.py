"""Re-export the swarm gateway: the listener devices dial, their links and their liveness.

The gateway is the Hive Stand's side of every Pollen Packet's connection (a Pollen Packet is the
thin gateway installed on an enrolled device). `listener` is the one door they dial, with TLS and
the transport defences on; `routing` decides from a connection's first frame whether it is an
enrolment, a device's packet or a device's Warden (the always-on supervisor of one Cell); `link`
is a connected packet to send to and wait on, with `exchange` the conversations that share it and
`frames` the single reader and sender of a connection; `hub` is the directory of live links the
Queen's swarm gate subscribes to; `heartbeat` judges whether each device is still reachable;
`verifier` holds the keys the listener trusts; `refusals` records what the transport limits
refused. This file is the package's face: a caller writes `from hivemind.swarm.gateway import
SwarmListener` without knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Used by
    `SwarmSource`, `PollenSession` and the watch probe (links) and by the Queen's swarm gate
    (the listener, the hub, the Warden handler). Calls into hivemind.swarm and waggle.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - docs/waggle/enrolment.md and docs/waggle/gateway-contract.md for the protocol served.
    - hivemind.queen.cell_gate for the equivalent listener of Virtual Cells.

Public API:
    - SwarmListener, ListenerConfig, ListenerDeps, certificate_pin: the listener (listener).
    - ConnectionRouter, RouterDeps, WardenConnection, WardenConnectionHandler: first-frame
      routing and the hand-over of a device Warden's connection (routing).
    - GatewayHub, HubListener: the directory of live links and who hears of them (hub).
    - DeviceLink, Exchange, RouteTable: a connected packet and its conversations (link,
      exchange).
    - Connection, FrameSender, HiveVoice, LinkEnds: one connection's frames and the Hive's
      address on them (frames).
    - HeartbeatMonitor, LivenessLoop, LivenessRules, Liveness, judge_liveness: reachability
      (heartbeat).
    - KeyRing, SwarmVerifier, KeyBinding: the keys trusted on the listener (verifier).
    - TransportRefusals: guard events for a flood and a replay (refusals).
"""

from hivemind.swarm.gateway.exchange import Exchange, RouteTable
from hivemind.swarm.gateway.frames import Connection, FrameSender, HiveVoice, LinkEnds
from hivemind.swarm.gateway.heartbeat import (
    HeartbeatMonitor,
    Liveness,
    LivenessLoop,
    LivenessRules,
    judge_liveness,
)
from hivemind.swarm.gateway.hub import GatewayHub, HubListener
from hivemind.swarm.gateway.link import DeviceLink
from hivemind.swarm.gateway.listener import (
    ListenerConfig,
    ListenerDeps,
    SwarmListener,
    certificate_pin,
)
from hivemind.swarm.gateway.refusals import TransportRefusals
from hivemind.swarm.gateway.routing import (
    ConnectionRouter,
    RouterDeps,
    WardenConnection,
    WardenConnectionHandler,
)
from hivemind.swarm.gateway.verifier import KeyBinding, KeyRing, SwarmVerifier

__all__ = [
    "Connection",
    "ConnectionRouter",
    "DeviceLink",
    "Exchange",
    "FrameSender",
    "GatewayHub",
    "HeartbeatMonitor",
    "HiveVoice",
    "HubListener",
    "KeyBinding",
    "KeyRing",
    "LinkEnds",
    "ListenerConfig",
    "ListenerDeps",
    "Liveness",
    "LivenessLoop",
    "LivenessRules",
    "RouteTable",
    "RouterDeps",
    "SwarmListener",
    "SwarmVerifier",
    "TransportRefusals",
    "WardenConnection",
    "WardenConnectionHandler",
    "certificate_pin",
    "judge_liveness",
]
