"""Phase 11 exit criterion 8 across a container boundary: a replayed or tampered message is refused.

"A replayed or tampered session message is rejected." The gateway conformance suite proves it
against a packet on the Hive Stand's own machine (`contracts/test_gateway_contract_resilience.py`);
this proves it against a packet in a container, over `wss://`, which is the boundary a real device
is across. The Hive Stand's listener is replaced by `contracts.gateway.RawHive`, a bare server with
the same certificate, so the test holds the whole conversation: it opens a lease and a session by
hand, runs a command, and then does what an attacker who captured a signed frame (or controlled the
wire) would do.

What it proves: a signed `session.exec` sent twice (the same bytes, the same envelope id) runs once:
one `session.exit`, and the command, which appends a line to a file in scratch, leaves one line
there; and a frame whose signature does not match its bytes closes the link with WebSocket code 1008
(policy violation) and the packet dials again, its lease untouched by either attack, then releases
it and leaves the device as it found it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one.

Key invariants:
    - The tampered frame is a genuine signature over other bytes, so it is exactly what a frame
      altered in transit looks like to the receiver.
    - What did not happen is shown after the packet has had time to do it: the replay is followed
      by a marker, a pause longer than a command takes, and a look at the file.
    - Every container and network is removed, whatever the test did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", the replay bullet; steps 11.3a and 11.3b.
    - docs/waggle/gateway-contract.md sections 5 to 7 for the clauses tested here.
    - contracts/gateway/wire.py for RawHive.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.swarm.live import LiveHiveOptions
from contracts.gateway import Frame, RawHive
from e2e.swarm.container import Container, Janitor, Network
from e2e.swarm.enrolled import ContainerDevice, enrol_container, start_stand
from e2e.swarm.image import device_image_or_skip

from hivemind.cell import AccessLevel as HiveAccess
from waggle.envelope import Envelope
from waggle.messages.base import WaggleMessage
from waggle.messages.cell import CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.control import Ping
from waggle.messages.labels import Urgency
from waggle.messages.session import SessionExec, SessionExit, SessionOpen, SessionOutcome
from waggle.messages.swarm import DeviceHeartbeat

pytestmark = pytest.mark.e2e

_HEARTBEAT_S = 0.5  # The beat the packet is told to keep.
_POLICY_VIOLATION = 1008  # A missing or invalid signature (gateway contract section 5).
_APPEND = ("sh", "-c", "echo run >> counter.txt")  # Each run of it adds a line to scratch.
_SETTLE_S = 1.5  # Longer than a command takes to start, run and report on a container.
_EXEC_TIMEOUT_S = 30.0


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


def test_a_replayed_exec_runs_once_and_a_tampered_frame_closes_the_link_across_the_container(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        asyncio.run(_scenario(janitor, image, janitor.network(), tmp_path))
    finally:
        janitor.close()


async def _scenario(janitor: Janitor, image: str, network: Network, directory: Path) -> None:
    """Enrol a container, take over the Stand's address, and replay and tamper with a session."""
    stand = await start_stand(
        directory, network, LiveHiveOptions(heartbeat_interval_s=_HEARTBEAT_S)
    )
    try:
        device = await enrol_container(stand, janitor, image, network)
        raw = RawHive(stand, device.node)
        await raw.take_over()
        try:
            await _attack(raw, device)
        finally:
            raw.drain()  # So the packet can end its connection politely when the container goes.
            await raw.stop()
    finally:
        janitor.close()  # The container first, so the Stand sees a device go, not a Stand go.
        await stand.stop()


async def _attack(raw: RawHive, device: ContainerDevice) -> None:
    """Run a command under a lease, replay it, tamper with a frame, and release the lease."""
    container = device.container
    before = await asyncio.to_thread(container.processes)
    lease = await _lease(raw, device)
    scratch = lease.scratch_root

    exec_frame = await _run(raw, lease)
    await _a_replay_runs_nothing(raw, container, exec_frame, scratch)
    await _a_tampered_frame_closes_the_link(raw, lease)

    released = await _ask(raw, _teardown(device, lease))
    assert isinstance(released.payload, LeaseReleased) and released.payload.is_restored
    assert not await asyncio.to_thread(container.exists, scratch)
    assert await asyncio.to_thread(container.processes) == before


async def _ask(raw: RawHive, payload: WaggleMessage) -> Envelope:
    """Send `payload` as the Queen and return the first frame that answers it."""
    frame = raw.signed(payload)
    await raw.send(frame.data)
    return (await raw.until_answered(frame.id))[-1]


async def _lease(raw: RawHive, device: ContainerDevice) -> LeaseOpened:
    """Open a lease on the device and its session, as the Queen and the Warden would."""
    node = device.node
    request = CellRequest(
        cell_id=node.cell_id,
        needs=None,
        holder=node.warden_id,
        task_id=None,
        access_level=HiveAccess.FULL.to_wire(),
        lifetime_s=None,
        scratch_quota_bytes=None,
        reason="The replay scenario leases the device.",
    )
    opened = (await _ask(raw, request)).payload
    assert isinstance(opened, LeaseOpened)
    session = (await _ask(raw, SessionOpen(lease_id=opened.lease_id, reason="Open it."))).payload
    assert isinstance(session, SessionExit) and session.outcome is SessionOutcome.COMPLETED
    return opened


def _teardown(device: ContainerDevice, lease: LeaseOpened) -> CellTeardownRequest:
    """The Queen's request to release `lease`, politely."""
    return CellTeardownRequest(
        cell_id=device.node.cell_id,
        lease_id=lease.lease_id,
        urgency=Urgency.GRACEFUL,
        cause=ReleaseCause.COMPLETED,
        reason="The replay scenario is done with the device.",
    )


async def _run(raw: RawHive, lease: LeaseOpened) -> Frame:
    """Run the appending command once, to its one exit, and return the frame that asked for it."""
    frame = raw.signed(
        SessionExec(
            lease_id=lease.lease_id,
            argv=_APPEND,
            cwd=None,
            env=(),
            timeout_s=_EXEC_TIMEOUT_S,
            has_stdin=False,
            task_id=None,
            worker_id=None,
        )
    )
    await raw.send(frame.data)
    exit_ = await _exit_of(raw, frame)
    assert (exit_.outcome, exit_.exit_code) == (SessionOutcome.EXITED, 0)
    return frame


async def _exit_of(raw: RawHive, request: Frame) -> SessionExit:
    """Read frames until the `session.exit` that answers `request`; return it."""
    while True:
        envelope = await raw.next_envelope()
        if envelope.correlation_id == request.id and isinstance(envelope.payload, SessionExit):
            return envelope.payload


async def _a_replay_runs_nothing(
    raw: RawHive, container: Container, exec_frame: Frame, scratch: str
) -> None:
    """The captured `session.exec` sent again, byte for byte, is dropped: no run, no second exit."""
    counter = f"{scratch}/counter.txt"
    assert (await asyncio.to_thread(container.exec, ["cat", counter])).stdout == "run\n"

    await raw.send(exec_frame.data)
    marker = raw.signed(Ping())
    await raw.send(marker.data)
    heard = await raw.until_answered(marker.id)
    # Frames are handled in order, so the packet has dealt with the replay by now; the pause
    # makes sure a run it should not have made would have finished and reported.
    deadline = asyncio.get_running_loop().time() + _SETTLE_S
    while (left := deadline - asyncio.get_running_loop().time()) > 0:
        try:
            async with asyncio.timeout(left):
                heard.append(await raw.next_envelope())
        except TimeoutError:
            break

    assert [e for e in heard if e.correlation_id == exec_frame.id] == []
    assert (await asyncio.to_thread(container.exec, ["cat", counter])).stdout == "run\n"


async def _a_tampered_frame_closes_the_link(raw: RawHive, lease: LeaseOpened) -> None:
    """A frame altered after signing closes the link with 1008; the packet dials again, leased."""
    await raw.send(_altered(raw.signed(Ping())))

    assert await raw.closed_code() == _POLICY_VIOLATION
    await raw.next_connection()  # It dials again: a gateway never gives up on the Queen.
    first = await raw.next_envelope()
    assert isinstance(first.payload, DeviceHeartbeat)
    assert first.payload.lease_ids == (lease.lease_id,)  # Neither attack disturbed the lease.


def _altered(frame: Frame) -> bytes:
    """The frame with one character of its id changed: still well formed, no longer as signed."""
    other = "1" if frame.id[-1] == "0" else "0"
    return frame.data.replace(frame.id.encode(), (frame.id[:-1] + other).encode())
