"""Run a real Pollen Packet in this process against a Hive's swarm listener: enrol, then run.

An operator brings a laptop into the Swarm in two commands, and `enrol` and `running` are those two.
`enrol` is `pollen enrol <invite>`: it parses the invite string the Hive Stand printed, runs
the ceremony against the listener over a real WebSocket, and persists the device's key and state
under a data directory. `running` is `pollen run`: it builds the packet from that directory and
runs its loop as a background task for the length of an `async with`, then stops it the way a
service manager would. They are separate so a scenario can run the packet, stop it (the laptop is
closed) and run it again (it is opened) without enrolling twice. The data directory is the
caller's (a `tmp_path`), so a test can look at what the packet left there: the `leases/<lease id>`
scratch directories above all.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `tests/e2e/
    test_swarm_level0.py`; depends on `pollen` (its enrolment and composition root) and waggle.

Key invariants:
    - The packet is the real one: enrolled through the real ceremony, built by `build_packet`, run
      with its real executors on the host. Only its intervals are shortened (`E2E_TIMINGS`).
    - `running` always stops the packet and awaits its task before it returns, so nothing it
      started outlives the block.

See Also:
    - packages/pollen/tests/support/device_kit.py for `FAST_TIMINGS`, whose values these repeat.
    - pollen.enrol and pollen.build for the two steps this runs.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from pollen import DataLayout, PacketState, PollenPacket, build_packet
from pollen.agent import Timings
from pollen.enrol import EnrolPlan, enrol_device, parse_invite
from pollen.platform import PlatformShim, select_shim
from waggle.clock import Clock

STOP_WAIT_S = 15.0  # Real seconds a test gives the packet to stop before its task is cancelled.
# The same shortened intervals pollen's own end-to-end tests run with, so a scenario takes moments.
E2E_TIMINGS = Timings(
    capacity_period_s=0.3,
    watchdog_s=0.05,
    persist_s=0.3,
    retry_s=0.3,
    stable_link_s=0.05,
    link_proof_s=0.1,
)

__all__ = ["E2E_TIMINGS", "EnrolledDevice", "enrol", "running"]


@dataclass(frozen=True, slots=True)
class EnrolledDevice:
    """A device that has enrolled: where it keeps its state, and what it was granted.

    Attributes:
        layout: The packet's data directory; its scratch directories are under `leases/`.
        state: What the enrolment persisted: the device's ids and the terms it was granted.
        shim: The platform the packet runs on: this host.
        clock: The packet's clock.
    """

    layout: DataLayout
    state: PacketState
    shim: PlatformShim
    clock: Clock


async def enrol(invite_string: str, data_dir: Path, clock: Clock) -> EnrolledDevice:
    """Run `pollen enrol` with `invite_string`: the whole ceremony, persisted under `data_dir`.

    Args:
        invite_string: The `pollen1.` string the Hive Stand's door minted.
        data_dir: Where the packet keeps its key, state and lease scratch.
        clock: The packet's clock.

    Returns:
        The enrolled device, ready for `running`.
    """
    layout, shim = DataLayout(data_dir), select_shim()
    invite = parse_invite(invite_string, clock.now())
    state = await enrol_device(EnrolPlan(invite, layout, shim, clock))
    return EnrolledDevice(layout, state, shim, clock)


@contextlib.asynccontextmanager
async def running(device: EnrolledDevice) -> AsyncIterator[PollenPacket]:
    """Run `pollen run` on `device` in the background for the length of the block.

    Args:
        device: An enrolled device.

    Yields:
        The packet, already running; it is stopped, and its task awaited, when the block ends.
    """
    packet = build_packet(device.layout, device.shim, device.clock, E2E_TIMINGS)
    task = asyncio.create_task(packet.run(), name="packet-under-test")
    try:
        yield packet
    finally:
        packet.stop()
        done, _ = await asyncio.wait({task}, timeout=STOP_WAIT_S)
        if not done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)  # Reaped, never left pending.
            raise AssertionError(f"the packet did not stop within {STOP_WAIT_S} s")
        task.result()  # A packet that died of its own error fails the test here, not silently.
