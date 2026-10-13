"""Assemble the Pollen Packet from its data directory: the composition root.

Everything the packet is made of is built in this one place and handed down: the persisted state
and key, the lease table, the outbox, the courier that sends, the four executors that do the work,
the lease and control services, the dispatcher that routes by payload type, the timers and the loop
itself. Nothing below this module constructs its own collaborators, so a test builds the same
packet with a fake clock, a short heartbeat or a scripted platform by passing different
arguments. The Pollen Packet (the thin gateway program on a device the Hive borrows) is a gateway
and nothing more: this wiring has no model, no trail and no supervisor in it, and that absence is
part of its design (gateway-contract section 16).

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli
    (``pollen run``) and by tests; imports every pollen sub-package, which no other module does.

Key invariants:
    - Building is cheap and does no network or process work: it reads files and creates objects;
      dialling and the startup sweep happen in ``PollenPacket.run``.
    - The verifier starts with every node the state trusts plus the pinned Hive key, so a restart
      never trusts less than it did before.
    - One ``TaskScope`` owns every task that outlives a request.

See Also:
    - docs/waggle/gateway-contract.md for what the assembled packet must do.
    - pollen.agent.packet for the loop this builds.
"""

from __future__ import annotations

from pollen.agent import (
    ControlService,
    Courier,
    DispatchDeps,
    Dispatcher,
    Identity,
    LeaseService,
    LiveVerifier,
    PacketParts,
    PacketStatus,
    PollenPacket,
    Pulse,
    PulseParts,
    Sentinel,
    SentinelParts,
    Timings,
    trusted_keys,
)
from pollen.errors import StateError
from pollen.executors import (
    ExecutorDeps,
    FileExecutor,
    ObserveExecutor,
    ProcessExecutor,
    SessionExecutor,
    TaskScope,
)
from pollen.lease import DeadManSwitch, LeaseTable
from pollen.platform import PlatformShim
from pollen.state import DataLayout, StateStore, load_signer
from waggle.clock import Clock
from waggle.errors import OutboxError
from waggle.outbox import Outbox
from waggle.signing import Ed25519Signer

__all__ = ["build_packet"]


def build_packet(
    layout: DataLayout, shim: PlatformShim, clock: Clock, timings: Timings | None = None
) -> PollenPacket:
    """Build the packet from an enrolled data directory.

    Args:
        layout: The data directory; it must hold an enrolment.
        shim: The platform shim for this host.
        clock: The injected time source.
        timings: The packet's intervals; the production ones when None.

    Returns:
        A packet ready to ``run``.

    Raises:
        NotEnrolledError: There is no enrolment in the data directory.
        StateError: The state, the key, the lease table or the outbox cannot be read.
    """
    store = StateStore.load(layout.state)
    signer = load_signer(layout.key)
    try:
        outbox = Outbox(layout.outbox)
    except OutboxError as exc:
        raise StateError("The outbox file cannot be read.") from exc
    state = store.current
    courier = Courier(Identity(state.device_id, state.node_id, state.hive_id), clock, outbox)
    table = LeaseTable.load(layout.lease_table)
    deps = ExecutorDeps(store, table, courier, clock, shim, layout, TaskScope())
    return _assemble(deps, courier, signer, Timings() if timings is None else timings)


def _assemble(
    deps: ExecutorDeps, courier: Courier, signer: Ed25519Signer, timings: Timings
) -> PollenPacket:
    """Wire the executors, services, dispatcher and timers around the shared collaborators."""
    state, clock = deps.state, deps.clock
    session, files = SessionExecutor(deps), FileExecutor(deps)
    process, observe = ProcessExecutor(deps), ObserveExecutor(deps)
    leases = LeaseService(deps, [session, files, process])
    verifier = LiveVerifier(trusted_keys(state.current))
    control = ControlService(deps, verifier)
    deadman = DeadManSwitch(clock, state.current.last_heard_at)
    status = PacketStatus(clock)
    dispatch = DispatchDeps(
        state, clock, courier, deadman.heard, status.note_drop, status.note_admitted
    )
    dispatcher = Dispatcher(dispatch, [session, files, process, observe, leases, control])
    pulse = Pulse(PulseParts(state, deps.leases, courier, clock, deps.layout), timings)
    watch = SentinelParts(
        state, leases, deps.leases, courier, deadman, verifier, status, deps.layout, clock
    )
    parts = PacketParts(
        state, signer, verifier, courier, dispatcher, pulse, Sentinel(watch, timings),
        leases, status, deadman, deps.scope,
    )  # fmt: skip
    return PollenPacket(parts, clock, timings)
