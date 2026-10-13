"""Build an enrolled device on disk and run a packet on it: the end-to-end tests' scaffolding.

The enrolment ceremony has its own tests; every other end-to-end test starts from a device that is
already enrolled. ``make_device`` writes exactly what a successful enrolment leaves in the data
directory (the node key, mode 0600, and the state document pinning the scripted Queen's identity),
and tells the scripted Queen to trust the device's key. ``running`` assembles a real packet with
``build_packet`` on that directory and runs it as a background task for the length of a ``with``
block, then stops it the way a service manager would. ``FAST_TIMINGS`` shrinks every interval the
packet paces itself with so an end-to-end test takes a fraction of a second, not a minute.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the pollen end-to-end tests; depends on
    pollen's public composition root and waggle, and on queen_double.py.

Key invariants:
    - The state a device is given passes the same validation a real enrolment's does.
    - ``running`` always stops the packet and awaits its task, so no connection, process or file
      handle outlives a test (the suite turns a ResourceWarning into a failure).

See Also:
    - queen_double.py for the Queen these devices dial.
    - pollen.build for the composition root ``running`` uses.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from queen_double import CAPABILITIES, QueenDouble, QueenSession
from scenarios import open_lease

from pollen import DataLayout, PacketState, PollenPacket, build_packet
from pollen.agent import Timings
from pollen.platform import PlatformShim, select_shim
from pollen.state import create_key, save_state
from waggle.clock import Clock
from waggle.ids import new_device_id, new_node_id
from waggle.messages.cell import LeaseOpened
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.messages.swarm import NodeKey
from waggle.signing import Ed25519Signer

STOP_WAIT_S = 15.0  # Real seconds a test gives the packet to stop before the task is cancelled.
FAST_TIMINGS = Timings(
    capacity_period_s=0.3,
    watchdog_s=0.05,
    persist_s=0.3,
    retry_s=0.3,
    stable_link_s=0.05,
    link_proof_s=0.1,
)  # Every interval the packet paces itself with, shrunk so a scenario takes moments.

__all__ = ["FAST_TIMINGS", "Device", "Live", "Terms", "live_lease", "make_device", "running"]


@dataclass(frozen=True, slots=True)
class Device:
    """An enrolled device: its data directory, its persisted state and its key."""

    layout: DataLayout
    state: PacketState
    signer: Ed25519Signer


@dataclass(frozen=True, slots=True)
class Terms:
    """What a device was enrolled on; the defaults are a full-access device with a long leash."""

    access: AccessLevel = AccessLevel.FULL  # The access level granted.
    capabilities: Sequence[str] | None = None  # The grants; the guard's for ``access`` when None.
    heartbeat_interval_s: float = 0.2  # Seconds between heartbeats.
    offline_limit_s: float = 120.0  # The dead-man limit.


def make_device(queen: QueenDouble, root: Path, clock: Clock, terms: Terms | None = None) -> Device:
    """Write what a successful enrolment leaves behind, and register the key with the Queen.

    Args:
        queen: The scripted Queen the device will dial.
        root: The data directory to create.
        clock: Mints the device's ids.
        terms: What the device was enrolled on; the defaults when None.

    Returns:
        The device, ready for ``running``.
    """
    given = terms or Terms()
    access, capabilities = given.access, given.capabilities
    layout = DataLayout(root)
    layout.ensure()
    signer = create_key(layout.key)
    device_id, node_id = new_device_id(clock), new_node_id(clock)
    granted = (*CAPABILITIES[access], f"watch:{queen.cell_id}")
    state = PacketState(
        device_id=device_id,
        node_id=node_id,
        queen_url=queen.url,
        tls_pin_sha256=queen.pin,
        hive_id=queen.hive_id,
        hive_node_id=queen.hive_node_id,
        hive_key_hex=queen.hive_key_hex,
        trusted_nodes=(NodeKey(node_id=queen.hive_node_id, public_key_hex=queen.hive_key_hex),),
        cell_id=queen.cell_id,
        warden_id=queen.warden_id,
        access_level=access,
        comb_shield=CombShieldLevel.MEADOW,
        granted_capabilities=tuple(granted if capabilities is None else capabilities),
        offline_limit_s=given.offline_limit_s,
        heartbeat_interval_s=given.heartbeat_interval_s,
        enrolled_at=clock.now(),
    )
    save_state(layout.state, state)
    queen.register(node_id, signer.public_key_bytes)
    return Device(layout, state, signer)


@contextlib.asynccontextmanager
async def running(
    device: Device,
    clock: Clock,
    *,
    timings: Timings = FAST_TIMINGS,
    shim: PlatformShim | None = None,
) -> AsyncIterator[PollenPacket]:
    """Run a packet on ``device`` in the background for the length of the block.

    Args:
        device: An enrolled device.
        clock: The packet's clock.
        timings: The packet's intervals.
        shim: The platform shim; the host's by default.

    Yields:
        The packet, already running.
    """
    packet = build_packet(device.layout, shim or select_shim(), clock, timings)
    task = asyncio.create_task(packet.run(), name="packet-under-test")
    try:
        yield packet
    finally:
        packet.stop()
        done, _ = await asyncio.wait({task}, timeout=STOP_WAIT_S)
        if not done:
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class Live:
    """A running packet, the Queen's session with it and one open lease."""

    queen: QueenDouble
    device: Device
    session: QueenSession
    lease: LeaseOpened
    packet: PollenPacket


@contextlib.asynccontextmanager
async def live_lease(
    queen: QueenDouble,
    device: Device,
    clock: Clock,
    *,
    access: AccessLevel = AccessLevel.FULL,
    quota: int | None = None,
) -> AsyncIterator[Live]:
    """Run a packet, wait for it to connect and open a lease on it.

    Args:
        queen: The scripted Queen the device dials.
        device: An enrolled device.
        clock: The packet's clock.
        access: The access level the lease asks for.
        quota: The scratch quota the lease asks for.

    Yields:
        The packet, the Queen's session and the lease.
    """
    async with running(device, clock) as packet:
        session = await queen.next_session()
        lease = await open_lease(queen, session, access=access, quota=quota)
        yield Live(queen, device, session, lease, packet)
