"""Gateway conformance suite, part four: forgeries, replays, reconnection and the dead-man switch.

These tests state sections 5 to 7, 13 and 14 of the gateway contract, and enrolment.md sections 9
and 12: a gateway acts on nothing it cannot verify, answers a replayed envelope once, closes a
connection that carries a forged frame with the WebSocket code the contract names, dials again
after a loss and opens each connection with a heartbeat, replays what it could not send, releases
its leases when the Queen has been silent too long and says so when she returns, and honours a
signed `QueenMoved` only if it is newer and genuine. The bytes-level tests talk to the gateway
through `contracts.gateway.RawHive`, which stands where the real listener stood; the rest use
the real listener and break its link. A gateway that is closed on or redirected is a gateway
spoiled for the next test, so the tests that do that start one of their own, and the rest share
two (one on the raw server, one on the real listener) to keep the suite short. Part one,
`test_gateway_contract.py`, documents how to point the suite at another gateway.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises sections 5 to 7, 13 and
    14 of the gateway contract and sections 9 and 12 of the enrolment ceremony through
    the rig in `contracts.gateway`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - contracts.test_gateway_contract for the suite's overview and the command template.
    - docs/waggle/gateway-contract.md sections 5 to 7, 13 and 14 for the clauses tested here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from builders.swarm.live import LiveHive, LiveHiveOptions, start_live_hive
from builders.swarm.tls import TlsMaterial, make_tls_material
from contracts.gateway import (
    Frame,
    Gateway,
    GatewayStartError,
    RawHive,
    alive,
    exists,
    python_argv,
    running_gateway,
    start_gateway,
    wait_until,
)

from hivemind.cell import AccessLevel
from waggle.clock import SystemClock
from waggle.codec import MAX_FRAME_BYTES
from waggle.envelope import Envelope
from waggle.ids import new_device_id
from waggle.messages.base import MAX_CLOCK_SKEW_S, MAX_MESSAGE_AGE_S
from waggle.messages.cell import CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.control import Ping, Pong, QueenMoved
from waggle.messages.forage.capacity import CapacityTrigger
from waggle.messages.forage.hosting import CapacityReport
from waggle.messages.labels import AccessLevel as WireAccessLevel
from waggle.messages.labels import Urgency
from waggle.messages.session import SessionExit, SessionOutcome, SessionOutput
from waggle.messages.swarm import DeviceHeartbeat
from waggle.signing import Ed25519Signer

pytestmark = pytest.mark.asyncio(loop_scope="module")

HEARTBEAT_S = 0.5  # The beat every gateway is told to keep.
WAIT_S = 30.0  # The longest a test waits for a reconnection, a replay or a redirect.
CLOSE_POLICY_VIOLATION = 1008  # A missing, unknown or invalid signature (contract section 5).
CLOSE_PROTOCOL_ERROR = 1002  # A malformed frame.
CLOSE_MESSAGE_TOO_BIG = 1009  # A frame over the limit.
_SLEEP_FOREVER = "import time; time.sleep(600)"  # A program that runs until it is killed.


@dataclass
class Stand:
    """A Hive Stand, the gateway enrolled on it and, when taken over, the bare server."""

    hive: LiveHive
    gateway: Gateway
    raw: RawHive | None = None


@asynccontextmanager
async def _stand(work_dir: Path, *, take_over: bool) -> AsyncIterator[Stand]:
    """Start a Hive Stand and a gateway on it; swap the listener for a bare server if asked."""
    hive = await start_live_hive(LiveHiveOptions(heartbeat_interval_s=HEARTBEAT_S))
    raw: RawHive | None = None
    try:
        async with running_gateway(hive, work_dir, AccessLevel.FULL) as gateway:
            if take_over:
                raw = RawHive(hive, gateway.node)
                await raw.take_over()
            try:
                yield Stand(hive, gateway, raw)
            finally:
                if raw is not None:
                    raw.drain()  # So the gateway, stopped next, can end its connection politely.
    finally:
        # After the gateway has been stopped, so it ends its connection before the server goes.
        if raw is not None:
            await raw.stop()
        await hive.stop()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def wire(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Stand]:
    """One gateway on a bare server, for the tests that read and send frames without a close."""
    async with _stand(tmp_path_factory.mktemp("wire"), take_over=True) as stand:
        yield stand


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def linked(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Stand]:
    """One gateway on the real listener, for the tests that break its link and bring it back."""
    async with _stand(tmp_path_factory.mktemp("linked"), take_over=False) as stand:
        yield stand


@pytest_asyncio.fixture(loop_scope="module")
async def closing(tmp_path: Path) -> AsyncIterator[Stand]:
    """A gateway of its own on a bare server, for a test that makes it close its connection."""
    async with _stand(tmp_path, take_over=True) as stand:
        yield stand


@pytest_asyncio.fixture(loop_scope="module")
async def moving(tmp_path: Path) -> AsyncIterator[Stand]:
    """A gateway of its own on the real listener, for a test that redirects it."""
    async with _stand(tmp_path, take_over=False) as stand:
        yield stand


async def _wait_for_frame(
    stand: Stand, matches: Callable[[Envelope], bool], what: str, after: int = 0
) -> Envelope:
    """Wait for a frame the gateway sent unprompted, at or after index `after`, that matches."""
    device = stand.gateway.node.device_id
    deadline = asyncio.get_running_loop().time() + WAIT_S
    while True:
        found = [f for f in stand.hive.log.frames[after:] if f.sender == device and matches(f)]
        if found:
            return found[0]
        assert asyncio.get_running_loop().time() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


# ── Frames that must not be acted on (sections 6 and 7) ──────────────────────────────────


async def test_the_first_frame_of_a_dial_is_a_signed_heartbeat_to_the_queen_then_a_report(
    wire: Stand,
) -> None:
    raw, node = wire.raw, wire.gateway.node
    assert raw is not None

    first, second = await raw.next_envelope(), await raw.next_envelope()

    assert isinstance(first.payload, DeviceHeartbeat)
    assert first.sender == node.device_id and first.recipient == raw.hive.party.hive_id
    assert first.node_id == node.node_id
    assert isinstance(second.payload, CapacityReport)
    assert second.payload.trigger is CapacityTrigger.RECONNECTED


async def test_a_replayed_envelope_is_acted_on_once(wire: Stand) -> None:
    raw = wire.raw
    assert raw is not None
    ping, marker = raw.signed(Ping()), raw.signed(Ping())

    await raw.send(ping.data)
    await raw.send(ping.data)
    await raw.send(marker.data)

    seen = await raw.until_answered(marker.id)
    assert len([e for e in seen if e.correlation_id == ping.id]) == 1


def _stale() -> datetime:
    """A time older than a gateway keeps a message."""
    return datetime.now(UTC) - timedelta(seconds=MAX_MESSAGE_AGE_S + 3_600)


def _ahead() -> datetime:
    """A time further ahead of the gateway's clock than the contract allows."""
    return datetime.now(UTC) + timedelta(seconds=MAX_CLOCK_SKEW_S + 300)


_FLAWED: dict[str, Callable[[RawHive], Frame]] = {
    "misaddressed": lambda raw: raw.signed(Ping(), recipient=new_device_id(SystemClock())),
    "stale": lambda raw: raw.signed(Ping(), sent_at=_stale()),
    "from_the_future": lambda raw: raw.signed(Ping(), sent_at=_ahead()),
}


@pytest.mark.parametrize("flaw", sorted(_FLAWED))
async def test_a_frame_that_is_misaddressed_stale_or_from_the_future_is_dropped_unanswered(
    wire: Stand, flaw: str
) -> None:
    raw = wire.raw
    assert raw is not None
    bad, marker = _FLAWED[flaw](raw), raw.signed(Ping())

    await raw.send(bad.data)
    await raw.send(marker.data)

    seen = await raw.until_answered(marker.id)
    assert not [e for e in seen if e.correlation_id == bad.id]


def _altered(frame: Frame) -> bytes:
    """The frame with one character of its id changed: still well formed, no longer as signed."""
    other = "1" if frame.id[-1] == "0" else "0"
    return frame.data.replace(frame.id.encode(), (frame.id[:-1] + other).encode())


async def test_a_frame_altered_after_signing_closes_the_link_and_the_gateway_dials_again(
    closing: Stand,
) -> None:
    raw = closing.raw
    assert raw is not None

    await raw.send(_altered(raw.signed(Ping())))

    assert await raw.closed_code() == CLOSE_POLICY_VIOLATION
    await raw.next_connection()  # It dials again: a gateway never gives up on the Queen.


async def test_a_connection_that_carries_what_a_gateway_cannot_accept_is_closed_with_its_code(
    closing: Stand,
) -> None:
    raw = closing.raw
    assert raw is not None
    unacceptable: list[tuple[str, bytes | str, int]] = [
        ("an unsigned frame", raw.unsigned(Ping()).data, CLOSE_POLICY_VIOLATION),
        ("an untrusted key", raw.forged(Ping(), Ed25519Signer.generate()).data, 1008),
        ("a text frame", "not a binary frame", CLOSE_PROTOCOL_ERROR),
        ("a frame over the limit", b"x" * (MAX_FRAME_BYTES + 1), CLOSE_MESSAGE_TOO_BIG),
    ]

    for index, (what, data, code) in enumerate(unacceptable):
        if index:
            await raw.next_connection()  # It dials again after each close, after a longer wait.
        await raw.send(data)

        assert await raw.closed_code() == code, what


# ── Reconnection and the outbox (sections 5, 13 and 14) ──────────────────────────────────


async def test_a_dropped_link_is_dialled_again_and_opens_the_same_way(linked: Stand) -> None:
    hive, driver = linked.hive, linked.gateway.driver
    lease = await driver.open_lease()
    started = await driver.start_background(lease, python_argv(_SLEEP_FOREVER))
    before = len(hive.log.frames)

    await hive.listener.disconnect(linked.gateway.node.device_id)
    report = await _wait_for_frame(
        linked, lambda f: isinstance(f.payload, CapacityReport), "a report", before
    )

    fresh = [f for f in hive.log.frames[before:] if f.sender == linked.gateway.node.device_id]
    beat = next(f for f in fresh if isinstance(f.payload, DeviceHeartbeat))
    assert fresh.index(beat) < fresh.index(report)  # The heartbeat first, then the report.
    assert isinstance(beat.payload, DeviceHeartbeat) and isinstance(report.payload, CapacityReport)
    assert beat.payload.lease_ids == (lease.lease_id,) and beat.payload.hive_started_processes == 1
    assert report.payload.trigger is CapacityTrigger.RECONNECTED
    assert alive(started.pid)  # A short loss ends nothing: the lease outlives the link.
    await driver.release(lease)


async def test_an_exit_the_link_could_not_carry_is_replayed_after_the_reconnection(
    linked: Stand,
) -> None:
    hive, driver = linked.hive, linked.gateway.driver
    lease = await driver.open_lease()
    program = python_argv("import time; print('x' * 99); time.sleep(0.4)")
    async with driver.start_exec(lease, program) as running:
        before = len(hive.log.frames)
        await hive.stop()  # The link is lost while the program runs, and the Hive is not back
        await asyncio.sleep(1.2)  # until it has ended: its exit can only have reached the outbox.
        await hive.restart()
        replayed = await _wait_for_frame(
            linked,
            lambda f: isinstance(f.payload, SessionExit) and f.correlation_id == running.id,
            "the exit of the exec that outlived the link",
            before,
        )

    assert isinstance(replayed.payload, SessionExit)
    assert (replayed.payload.outcome, replayed.payload.exit_code) == (SessionOutcome.EXITED, 0)
    # What the link could not carry live is dropped, never queued: no output chunk is replayed.
    chunks = [f for f in hive.log.frames if isinstance(f.payload, SessionOutput)]
    assert not [f for f in chunks if f.correlation_id == running.id]
    await driver.release(lease)


async def test_what_was_sent_just_before_a_link_ended_without_a_close_frame_is_sent_again(
    closing: Stand,
) -> None:
    raw, node = closing.raw, closing.gateway.node
    assert raw is not None
    lease = raw.signed(
        CellRequest(
            cell_id=node.cell_id,
            needs=None,
            holder=node.warden_id,
            task_id=None,
            access_level=WireAccessLevel.FULL,
            lifetime_s=None,
            scratch_quota_bytes=None,
            reason="The conformance suite leases the device.",
        )
    )
    await raw.send(lease.data)
    opened = next(
        e for e in await raw.until_answered(lease.id) if isinstance(e.payload, LeaseOpened)
    )
    assert isinstance(opened.payload, LeaseOpened)
    teardown = raw.signed(
        CellTeardownRequest(
            cell_id=node.cell_id,
            lease_id=opened.payload.lease_id,
            urgency=Urgency.IMMEDIATE,
            cause=ReleaseCause.COMPLETED,
            reason="The conformance suite is done with the device.",
        )
    )
    await raw.send(teardown.data)
    released = next(
        e for e in await raw.until_answered(teardown.id) if isinstance(e.payload, LeaseReleased)
    )

    # The Hive Stand never read it, for all the gateway can tell: the connection ends with no close
    # frame, as a cable pulled out from under it ends one, forty seconds after the fact.
    raw.drop()
    await raw.next_connection()
    again: list[Envelope] = []
    while not any(e.id == released.id for e in again):
        again.append(await raw.next_envelope())

    # The same envelope, after the new connection's heartbeat and report (section 5) and not a new
    # one: the Hive Stand's replay window drops it if it had in fact been heard (section 14).
    order = [type(e.payload) for e in again]
    assert order.index(DeviceHeartbeat) < order.index(CapacityReport) < order.index(LeaseReleased)


async def test_a_gateway_that_lost_the_queen_past_its_limit_releases_and_says_so_on_return(
    linked: Stand,
) -> None:
    hive, driver = linked.hive, linked.gateway.driver
    await driver.update_terms(offline_limit_s=1.0)
    lease = await driver.open_lease()
    started = await driver.start_background(lease, python_argv(_SLEEP_FOREVER))
    await driver.put_file(lease, "marker.txt", b"here")

    await hive.stop()
    await wait_until(
        lambda: not alive(started.pid) and not exists(lease.scratch_root),
        "the dead-man switch to release the lease",
    )
    before = len(hive.log.frames)
    await hive.restart()

    told = await _wait_for_frame(
        linked,
        lambda f: isinstance(f.payload, LeaseReleased) and f.payload.lease_id == lease.lease_id,
        "the dead-man release to be reported",
        before,
    )
    assert isinstance(told.payload, LeaseReleased)
    assert told.payload.cause is ReleaseCause.DEAD_MAN
    assert told.payload.is_restored and told.payload.killed_processes >= 1
    # Told after the new connection's heartbeat and report, from the outbox (section 14).
    device = linked.gateway.node.device_id
    order = [type(f.payload) for f in hive.log.frames[before:] if f.sender == device]
    assert order.index(DeviceHeartbeat) < order.index(CapacityReport) < order.index(LeaseReleased)


# ── A new Hive Stand (enrolment.md section 12) ────────────────────────────────────────────


async def _stand_b(hive: LiveHive) -> LiveHive:
    """A second Hive Stand over the same registry: the Queen's new home, with a node of its own."""
    return await start_live_hive(
        LiveHiveOptions(
            registry=hive.registry, hive_id=hive.party.hive_id, heartbeat_interval_s=HEARTBEAT_S
        )
    )


def _moved(sequence: int, to: LiveHive) -> QueenMoved:
    """A notice that the Hive Stand now lives at `to`, in force at once and for an hour."""
    now = datetime.now(UTC)
    return QueenMoved(
        sequence=sequence,
        new_address=to.url,
        new_node_id=to.party.node_id,
        new_node_public_key_hex=to.party.public_hex,
        effective_at=now - timedelta(seconds=1),
        grace_until=now + timedelta(hours=1),
        reason="The conformance suite moves the Hive Stand.",
    )


async def test_a_newer_queen_moved_redirects_the_gateway_and_an_older_one_changes_nothing(
    moving: Stand,
) -> None:
    gateway = moving.gateway
    new_home, stale_home = await _stand_b(moving.hive), await _stand_b(moving.hive)
    try:
        await gateway.driver.link.send(_moved(5, new_home), sender=gateway.driver.queen)
        link = await new_home.wait_for(gateway.node.device_id, WAIT_S)
        pong = await link.request(Ping(), sender=link.hive_id, timeout_s=WAIT_S)
        assert isinstance(pong.payload, Pong)  # It dials the new address and trusts the new key.

        await link.send(_moved(3, stale_home), sender=link.hive_id)  # An older notice.
        await new_home.listener.disconnect(gateway.node.device_id)
        await new_home.wait_for(gateway.node.device_id, WAIT_S)

        assert stale_home.log.online == []  # It never dialled the older notice's address.
    finally:
        await new_home.stop()
        await stale_home.stop()


async def test_a_queen_moved_with_a_bad_signature_is_not_honoured(linked: Stand) -> None:
    gateway = linked.gateway
    new_home = await _stand_b(linked.hive)
    try:
        linked.hive.signer.tamper_next = 1  # The next frame the listener signs is signed wrongly.
        await gateway.driver.link.send(_moved(1, new_home), sender=gateway.driver.queen)

        await linked.hive.wait_for(gateway.node.device_id, WAIT_S)  # Back on the old address.

        assert new_home.log.online == []
    finally:
        await new_home.stop()


# ── TLS (section 5) ───────────────────────────────────────────────────────────────────────


async def test_a_gateway_enrols_and_connects_over_tls_with_the_pinned_certificate(
    tmp_path: Path,
) -> None:
    material = make_tls_material(tmp_path, "hive")
    stand = await start_live_hive(LiveHiveOptions(tls=material, heartbeat_interval_s=HEARTBEAT_S))
    try:
        async with running_gateway(stand, tmp_path / "gw") as running:
            assert stand.url.startswith("wss://")
            assert stand.hub.link(running.node.device_id) is not None
    finally:
        await stand.stop()


async def test_a_certificate_other_than_the_pinned_one_ends_the_attempt_before_any_frame(
    tmp_path: Path,
) -> None:
    served, pinned = make_tls_material(tmp_path, "served"), make_tls_material(tmp_path, "pinned")
    lying = TlsMaterial(served.cert_path, served.key_path, pinned.sha256)
    stand = await start_live_hive(LiveHiveOptions(tls=lying, heartbeat_interval_s=HEARTBEAT_S))
    try:
        with pytest.raises(GatewayStartError):
            await start_gateway(stand, tmp_path / "gw")

        assert len(await stand.registry.nodes()) == 0
        assert "swarm.enrol_refused" not in await stand.kinds()  # The token was never sent.
    finally:
        await stand.stop()
