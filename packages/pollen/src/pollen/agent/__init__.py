"""Run the gateway loop: dial the Queen, announce, serve her requests, and dial again.

The Pollen Packet (the thin gateway program on a device the Hive borrows) dials the Queen (the
Hive's central orchestrator) and never listens. This package is that loop. ``link`` holds one
pinned, verified connection; ``courier`` builds and sends every frame, live or through the outbox;
``dispatch`` admits each verified frame by the sender and freshness rules and routes it to an
executor by payload type, after a capability check; ``leases`` opens, closes and releases device
leases and sweeps the ones a crash left; ``control`` answers ping, node update and the Queen's
relocation notice; ``moved`` holds the pure rules of that notice; ``heartbeat`` sends the heartbeat
and capacity reports and watches the dead-man switch; ``status`` keeps the operator's diagnostics;
``packet`` is the loop itself. It persists the Queen's address and honours a signed QueenMoved
message if Supersedure (moving the Hive Stand to another machine) relocates it.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Handles the gateway loop:
    connect out, heartbeat, answer a lease and its session. Called by pollen.cli, the composition
    root; calls into pollen.executors, pollen.lease, pollen.platform, pollen.state and waggle.

Key invariants:
    - Nothing is acted on until the frame is verified, addressed here, from an admitted sender,
      fresh and not a duplicate (gateway-contract sections 6 and 7).
    - The first frame of every connection is a signed heartbeat.
    - A request's needs are checked against the granted capabilities before any executor runs.

See Also:
    - docs/waggle/gateway-contract.md for every rule this package carries out.
    - .claude/codingrules.md section 3 for where this sub-package sits under pollen.

Public API:
    - PollenPacket, PacketParts: the loop and what it drives.
    - SHUTDOWN_WAIT_S, SHUTDOWN_RELEASE_TIMEOUT_S: how long a stop and its lease release take.
    - Dispatcher, DispatchDeps, DropReason, admit: frame admission and routing.
    - Courier, Identity: sending.
    - Link, LiveVerifier: one connection and its swappable keys.
    - LeaseService, ControlService: the two services that are not executors of work.
    - Pulse, PulseParts, Sentinel, SentinelParts, Timings: the timers.
    - PacketStatus, StatusDocument, read_status, write_status: diagnostics.
    - check_needs, covers, parse_capability: the capability matcher.
    - accept_moved, dial_plan, hive_nodes, pin_for, settle, trusted_keys: the relocation rules.
"""

from pollen.agent.checks import check_needs, covers, parse_capability
from pollen.agent.control import ControlService
from pollen.agent.courier import Courier, Identity
from pollen.agent.dispatch import DispatchDeps, Dispatcher, DropReason, admit
from pollen.agent.heartbeat import Pulse, PulseParts, Sentinel, SentinelParts, Timings
from pollen.agent.leases import LeaseService
from pollen.agent.link import Link, LiveVerifier
from pollen.agent.moved import accept_moved, dial_plan, hive_nodes, pin_for, settle, trusted_keys
from pollen.agent.packet import (
    SHUTDOWN_RELEASE_TIMEOUT_S,
    SHUTDOWN_WAIT_S,
    PacketParts,
    PollenPacket,
)
from pollen.agent.status import PacketStatus, StatusDocument, read_status, write_status

__all__ = [
    "SHUTDOWN_RELEASE_TIMEOUT_S",
    "SHUTDOWN_WAIT_S",
    "ControlService",
    "Courier",
    "DispatchDeps",
    "Dispatcher",
    "DropReason",
    "Identity",
    "LeaseService",
    "Link",
    "LiveVerifier",
    "PacketParts",
    "PacketStatus",
    "PollenPacket",
    "Pulse",
    "PulseParts",
    "Sentinel",
    "SentinelParts",
    "StatusDocument",
    "Timings",
    "accept_moved",
    "admit",
    "check_needs",
    "covers",
    "dial_plan",
    "hive_nodes",
    "parse_capability",
    "pin_for",
    "read_status",
    "settle",
    "trusted_keys",
    "write_status",
]
