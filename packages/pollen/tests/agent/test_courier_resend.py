"""Test what the packet sends again after a connection that died without anyone noticing.

A cable pulled out from under the device leaves the packet's connection looking alive for about
forty seconds, and a durable frame sent in that time (the release of a lease, the exit of a
program) returns as sent, is acknowledged and is gone from the outbox, never having reached the
Queen. So the courier keeps the durable frames it sent live in a short bounded memory, and after a
reconnection sends those again, in order and under their own ids, ahead of the outbox (gateway
contract section 14). These tests drive a real courier over real links to the scripted Queen: a
frame comes again under the id it had; it comes before the outbox's; it is not sent again when the
Queen said goodbye, when it is older than her memory of a frame, or before any connection was lost;
a frame that was only replayed from the outbox is held too; and the memory is bounded.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.courier's ring beside
    test_courier.py, which tests how the packet sends.

Key invariants:
    - A frame sent again carries the envelope id it was first sent with, never a new one.

See Also:
    - docs/waggle/gateway-contract.md section 14.
    - waggle.transport.recent for the memory the courier uses.
"""

from __future__ import annotations

from pathlib import Path

from device_kit import Device
from queen_double import QueenDouble, QueenSession

from pollen.agent import Courier, Identity, Link, LiveVerifier
from waggle.clock import FakeClock
from waggle.messages.control import Ping
from waggle.messages.session import SessionClose
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.outbox import Outbox
from waggle.signing import public_key_from_hex
from waggle.transport.recent import RESEND_HORIZON_S, RecentFrames, RecentLimits

_LEASE = "lease_01M4TJ4GG08H248H248H248H24"


def _courier(
    device: Device, clock: FakeClock, tmp_path: Path, recent: RecentFrames | None = None
) -> Courier:
    """A courier for `device` over an outbox of its own."""
    state = device.state
    return Courier(
        Identity(state.device_id, state.node_id, state.hive_id),
        clock,
        Outbox(tmp_path / "outbox.jsonl"),
        recent,
    )


async def _connect(
    queen: QueenDouble, device: Device, courier: Courier
) -> tuple[Link, QueenSession]:
    """Dial the Queen as the packet does: a link, and its first frame so she sees a session."""
    keys: dict[str, bytes] = {queen.hive_node_id: public_key_from_hex(queen.hive_key_hex)}
    link = Link(queen.url, None, device.signer, LiveVerifier(keys), FakeClock())
    await link.connect()
    state = device.state
    beat = DeviceHeartbeat(
        device_id=state.device_id,
        level=RuntimeLevel.GATEWAY,
        lease_ids=(),
        hive_started_processes=0,
        outbox_pending=0,
        uptime_s=0.0,
    )
    await link.send(courier.envelope(beat, to=state.hive_id))
    return link, await queen.next_session()


def _close(reason: str) -> SessionClose:
    """A durable frame: the end of a session, which the Queen must learn of."""
    return SessionClose(lease_id=_LEASE, reason=reason)


async def test_a_durable_frame_sent_into_a_dead_link_comes_again_under_its_own_id(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    courier = _courier(device, clock, tmp_path)
    first, first_session = await _connect(queen, device, courier)
    courier.attach(first)
    hive = device.state.hive_id

    await courier.send_durable(_close("first"), to=hive)
    await courier.send_durable(_close("second"), to=hive)
    sent = [(await first_session.expect(SessionClose)).id for _ in range(2)]
    assert courier.pending == 0  # Acknowledged: as far as the outbox knows, it is delivered.
    courier.detach()  # The keepalive gave up; the link was dead before they were sent.
    await first.close()
    clock.advance(5.0)
    second, second_session = await _connect(queen, device, courier)

    assert await courier.resend_recent(second) == 2

    again = [await second_session.expect(SessionClose) for _ in range(2)]
    assert [frame.id for frame in again] == sent  # The ids the Queen's window knows, in order.
    assert [frame.payload.reason for frame in again] == ["first", "second"]
    await second.close()


async def test_the_frames_sent_again_come_before_the_outbox_replay(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    courier = _courier(device, clock, tmp_path)
    first, first_session = await _connect(queen, device, courier)
    courier.attach(first)
    hive = device.state.hive_id
    await courier.send_durable(_close("sent while the link looked alive"), to=hive)
    await first_session.expect(SessionClose)
    courier.detach()
    await first.close()
    await courier.send_durable(_close("queued, the link known down"), to=hive)
    second, second_session = await _connect(queen, device, courier)

    await courier.resend_recent(second)
    report = await courier.flush(second)

    assert report.sent == 1
    reasons = [(await second_session.expect(SessionClose)).payload.reason for _ in range(2)]
    assert reasons == ["sent while the link looked alive", "queued, the link known down"]
    await second.close()


async def test_nothing_is_sent_again_before_a_connection_was_lost(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    courier = _courier(device, FakeClock(), tmp_path)
    link, session = await _connect(queen, device, courier)
    courier.attach(link)
    await courier.send_durable(_close("first connection"), to=device.state.hive_id)
    await session.expect(SessionClose)

    # The first connection has not been lost: there is no stretch to send again on it.
    assert await courier.resend_recent(link) == 0
    await link.close()


async def test_a_queen_that_said_goodbye_is_owed_nothing_again(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    courier = _courier(device, FakeClock(), tmp_path)
    first, first_session = await _connect(queen, device, courier)
    courier.attach(first)
    await courier.send_durable(_close("read before the goodbye"), to=device.state.hive_id)
    await first_session.expect(SessionClose)

    courier.forget_recent()  # Her close frame: she read everything before it.
    courier.detach()
    await first.close()
    second, _ = await _connect(queen, device, courier)

    assert await courier.resend_recent(second) == 0
    await second.close()


async def test_a_frame_the_queens_window_may_have_forgotten_is_not_sent_again(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    courier = _courier(device, clock, tmp_path)
    first, first_session = await _connect(queen, device, courier)
    courier.attach(first)
    await courier.send_durable(_close("long ago"), to=device.state.hive_id)
    await first_session.expect(SessionClose)
    courier.detach()
    await first.close()

    clock.advance(RESEND_HORIZON_S + 1.0)  # The outage outlasts what she remembers of a frame.
    second, _ = await _connect(queen, device, courier)

    assert await courier.resend_recent(second) == 0  # Sent now, it could be handled twice.
    await second.close()


async def test_only_a_durable_frame_is_held_and_a_live_one_is_not(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    courier = _courier(device, clock, tmp_path)
    first, first_session = await _connect(queen, device, courier)
    courier.attach(first)
    hive = device.state.hive_id

    await courier.send(Ping(), to=hive)  # Live: dropped when there is no link, never held.
    await courier.send_durable(_close("durable"), to=hive)
    await first_session.expect(SessionClose)
    courier.detach()
    await first.close()
    second, second_session = await _connect(queen, device, courier)

    assert await courier.resend_recent(second) == 1
    await second_session.expect(SessionClose)
    await second_session.expect_none(Ping)
    await second.close()


async def test_what_the_outbox_replayed_is_held_for_the_next_lost_connection(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    courier = _courier(device, clock, tmp_path)
    hive = device.state.hive_id
    await courier.send_durable(_close("queued while down"), to=hive)  # No link: only the outbox.
    first, first_session = await _connect(queen, device, courier)
    await courier.flush(first)  # Replayed, acknowledged: as far as the outbox knows, delivered.
    replayed = await first_session.expect(SessionClose)
    courier.detach()  # But that connection was dead too.
    await first.close()
    second, second_session = await _connect(queen, device, courier)

    assert await courier.resend_recent(second) == 1

    assert (await second_session.expect(SessionClose)).id == replayed.id
    await second.close()


async def test_the_memory_is_bounded_and_the_oldest_frames_go_first(
    queen: QueenDouble, device: Device, tmp_path: Path
) -> None:
    clock = FakeClock()
    courier = _courier(device, clock, tmp_path, RecentFrames(RecentLimits(max_frames=2)))
    first, first_session = await _connect(queen, device, courier)
    courier.attach(first)
    for reason in ("one", "two", "three"):
        await courier.send_durable(_close(reason), to=device.state.hive_id)
        await first_session.expect(SessionClose)
    courier.detach()
    await first.close()
    second, second_session = await _connect(queen, device, courier)

    assert await courier.resend_recent(second) == 2

    reasons = [(await second_session.expect(SessionClose)).payload.reason for _ in range(2)]
    assert reasons == ["two", "three"]
    await second.close()
