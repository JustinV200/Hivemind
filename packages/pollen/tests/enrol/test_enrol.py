"""Test the enrolment ceremony end to end against a scripted Queen: dial, request, accept, persist.

Enrolment is one invite, one signed request and one signed accept (enrolment.md). The Pollen Packet
(the thin gateway program on a device the Hive borrows) writes its key to disk before the first
dial, dials the Queen (the Hive's central orchestrator) with the certificate fingerprint from the
invite so the token never reaches a server that cannot prove it holds that certificate, sends the
single ``swarm.enrol_request`` and accepts only an answer signed by the Hive node the invite
pinned. It persists nothing unless every check on the accept passed, so a device never holds half
an enrolment. These tests check what the Queen receives, what the device leaves on disk, and every
way an enrolment must fail without leaving a state behind.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.enrol.enrol and pollen.enrol.request
    over a real loopback WebSocket, plain and TLS.

Key invariants:
    - A failed enrolment leaves no ``state.json``; only the unused key may remain.
    - The token reaches the Queen in the one request and nowhere else: not in the state, not in
      any error.

See Also:
    - docs/waggle/enrolment.md sections 4 to 7 and 9.
"""

from __future__ import annotations

import asyncio
import stat
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from device_kit import Device, running
from fast_clock import FastClock
from frame_overrides import Overrides
from queen_double import INVITE_CODE, QueenDouble
from queen_tls import make_tls_material

from pollen.enrol import EnrolPlan, enrol_device, parse_invite
from pollen.errors import EnrolmentDeniedError, EnrolmentError
from pollen.platform import select_shim
from pollen.state import DataLayout, load_signer, load_state
from waggle.clock import Clock
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage, Ping
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import DeviceHeartbeat, EnrolAccept, EnrolRequest, NodeKey

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def _plan(
    queen: QueenDouble,
    root: Path,
    clock: Clock,
    *,
    requested_access: AccessLevel = AccessLevel.FULL,
    is_forced: bool = False,
) -> EnrolPlan:
    """Plan an enrolment from the invite the scripted Queen mints."""
    invite = parse_invite(queen.invite(), clock.now())
    layout = DataLayout(root)
    return EnrolPlan(invite, layout, select_shim(), clock, requested_access, is_forced)


@pytest.fixture
async def tls_queen(clock: Clock, tmp_path: Path) -> AsyncIterator[QueenDouble]:
    """A scripted Queen serving ``wss://`` with a self-signed certificate."""
    scripted = QueenDouble(clock, tls=make_tls_material(tmp_path, "listener"))
    await scripted.start()
    try:
        yield scripted
    finally:
        await scripted.stop()


@posix_only
async def test_an_enrolment_persists_the_pins_the_grants_and_a_private_key(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    root = tmp_path / "device"
    state = await enrol_device(_plan(queen, root, clock))
    assert load_state(DataLayout(root).state) == state
    assert (state.queen_url, state.tls_pin_sha256) == (queen.url, None)
    assert (state.hive_id, state.hive_node_id, state.hive_key_hex) == (
        queen.hive_id,
        queen.hive_node_id,
        queen.hive_key_hex,
    )
    assert (state.cell_id, state.warden_id) == (queen.cell_id, queen.warden_id)
    assert state.access_level is AccessLevel.FULL
    assert (state.offline_limit_s, state.heartbeat_interval_s) == (120.0, 0.2)
    assert "exec:*" in state.granted_capabilities
    layout = DataLayout(root)
    for private in (layout.key, layout.state):
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
    sent = queen.enrolments[0].payload
    assert isinstance(sent, EnrolRequest)
    assert load_signer(layout.key).public_key_bytes == bytes.fromhex(sent.public_key_hex)


async def test_the_request_carries_the_identities_the_token_and_the_probes_once(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    state = await enrol_device(
        _plan(queen, tmp_path / "device", clock, requested_access=AccessLevel.SCRATCH)
    )
    (envelope,) = queen.enrolments
    request = envelope.payload
    assert isinstance(request, EnrolRequest)
    assert request.invite_token == INVITE_CODE
    assert (envelope.sender, envelope.node_id) == (state.device_id, state.node_id)
    assert envelope.recipient == queen.hive_id
    assert (request.device_id, request.node_id) == (state.device_id, state.node_id)
    assert (
        request.requested_access is AccessLevel.SCRATCH
        and state.access_level is AccessLevel.SCRATCH
    )
    assert request.capacity.cores >= 1 and request.runtime_version
    assert INVITE_CODE not in state.model_dump_json()


async def test_an_enrolled_device_runs_and_is_heard_by_the_queen(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    state = await enrol_device(_plan(queen, tmp_path / "device", clock))
    await queen.next_session()  # The enrolment connection, with the request on it.
    layout = DataLayout(tmp_path / "device")
    device = Device(layout, state, load_signer(layout.key))
    async with running(device, clock):
        session = await queen.next_session()
        first = session.frames[0]
        assert isinstance(first.payload, DeviceHeartbeat)
        assert first.sender == state.device_id


async def test_a_denial_ends_the_attempt_and_leaves_no_state(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    queen.decide = queen.deny
    root = tmp_path / "device"
    with pytest.raises(EnrolmentDeniedError, match="refused"):
        await enrol_device(_plan(queen, root, clock))
    assert not DataLayout(root).state.exists()


async def test_a_wss_enrolment_dials_with_the_pin(
    tls_queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    state = await enrol_device(_plan(tls_queen, tmp_path / "device", clock))
    assert state.queen_url.startswith("wss://")
    assert state.tls_pin_sha256 == tls_queen.pin


async def test_a_certificate_that_is_not_the_pinned_one_is_refused_before_any_frame(
    tls_queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    other = make_tls_material(tmp_path, "impostor")
    forged = parse_invite(tls_queen.invite(), clock.now()).model_copy(
        update={"tls_sha256": other.sha256}
    )
    plan = EnrolPlan(forged, DataLayout(tmp_path / "device"), select_shim(), clock)
    with pytest.raises(EnrolmentError, match="certificate"):
        await enrol_device(plan)
    assert tls_queen.enrolments == []
    assert not plan.layout.state.exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda good: {"hive_key_hex": "ab" * 32},
        lambda good: {"hive_id": "hive_01M4TJ4GG0248H248H248H248J"},
        lambda good: {"device_id": "device_01M4TJ4GG06CSK6CSK6CSK6CSK"},
        lambda good: {"access_level": AccessLevel.FULL},
    ],
    ids=["another_hive_key", "another_hive_id", "another_device", "more_access_than_asked"],
)
async def test_an_accept_that_does_not_match_what_was_pinned_or_asked_is_discarded(
    queen: QueenDouble,
    tmp_path: Path,
    clock: Clock,
    change: Callable[[EnrolAccept], dict[str, object]],
) -> None:

    def decide(request: EnrolRequest) -> WaggleMessage:
        good = queen.accept_for(request)
        return good.model_copy(update=change(good))

    queen.decide = decide
    root = tmp_path / "device"
    plan = _plan(queen, root, clock, requested_access=AccessLevel.READ_ONLY)
    with pytest.raises(EnrolmentError, match="discarded"):
        await enrol_device(plan)
    assert not DataLayout(root).state.exists()


async def test_an_accept_that_rekeys_the_hive_node_is_discarded(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    def decide(request: EnrolRequest) -> WaggleMessage:
        good = queen.accept_for(request)
        rekeyed = (NodeKey(node_id=queen.hive_node_id, public_key_hex="cd" * 32),)
        return good.model_copy(update={"trusted_nodes": rekeyed})

    queen.decide = decide
    with pytest.raises(EnrolmentError, match="discarded"):
        await enrol_device(_plan(queen, tmp_path / "device", clock))


async def test_enrolling_over_an_enrolment_needs_force_and_replaces_the_key(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    root = tmp_path / "device"
    first = await enrol_device(_plan(queen, root, clock))
    key = DataLayout(root).key.read_bytes()
    with pytest.raises(EnrolmentError, match="already enrolled"):
        await enrol_device(_plan(queen, root, clock))
    again = await enrol_device(_plan(queen, root, clock, is_forced=True))
    assert again.device_id != first.device_id
    assert DataLayout(root).key.read_bytes() != key


async def test_a_failed_attempt_can_be_retried_with_a_new_invite(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    root = tmp_path / "device"
    queen.decide = queen.deny
    with pytest.raises(EnrolmentDeniedError):
        await enrol_device(_plan(queen, root, clock))
    queen.decide = None
    state = await enrol_device(
        _plan(queen, root, clock)
    )  # The unused key does not block the retry.
    assert load_state(DataLayout(root).state) == state


async def test_an_unreachable_queen_is_a_clear_failure(tmp_path: Path, clock: Clock) -> None:
    gone = QueenDouble(clock)
    await gone.start()
    invite = gone.invite()
    await gone.stop()
    plan = EnrolPlan(
        parse_invite(invite, clock.now()), DataLayout(tmp_path / "d"), select_shim(), clock
    )
    with pytest.raises(EnrolmentError, match="could not be reached"):
        await enrol_device(plan)


async def test_a_queen_that_closes_the_link_without_answering_is_a_failure(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    queen.is_revoking = True
    with pytest.raises(EnrolmentError, match="connection"):
        await enrol_device(_plan(queen, tmp_path / "device", clock))


async def test_a_queen_that_never_answers_times_out(
    queen: QueenDouble, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pollen.enrol.enrol.ENROL_REPLY_TIMEOUT_S", 0.3)
    queen.is_silent = True
    with pytest.raises(EnrolmentError, match="in time"):
        await enrol_device(_plan(queen, tmp_path / "device", FastClock()))


async def test_a_frame_that_does_not_answer_the_request_is_ignored_until_the_answer_comes(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    queen.is_silent = True  # The test speaks for the Queen, to put a stray frame first.
    enrolling = asyncio.create_task(enrol_device(_plan(queen, tmp_path / "device", clock)))
    session = await queen.next_session()
    request = session.frames[0]
    assert isinstance(request.payload, EnrolRequest)
    await session.send(Ping())  # Not an answer: nothing correlates it to the request.
    answer = queen.accept_for(request.payload)
    await session.send(answer, overrides=Overrides(correlation_id=request.id))
    state = await asyncio.wait_for(enrolling, timeout=10)
    assert state.cell_id == queen.cell_id


async def test_a_queen_that_closes_the_link_politely_without_answering_is_a_failure(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    queen.is_silent = True
    root = tmp_path / "device"
    enrolling = asyncio.create_task(enrol_device(_plan(queen, root, clock)))
    session = await queen.next_session()
    await session.close(1000)
    with pytest.raises(EnrolmentError, match="closed before"):
        await asyncio.wait_for(enrolling, timeout=10)
    assert not DataLayout(root).state.exists()


async def test_a_refusal_with_any_other_code_names_the_code(
    queen: QueenDouble, tmp_path: Path, clock: Clock
) -> None:
    def refuse(_request: EnrolRequest) -> WaggleMessage:
        return ErrorMessage(
            code="hive.swarm.busy",
            message="The Hive cannot enrol devices right now.",
            failed_kind="swarm.enrol_request",
            is_retryable=True,
        )

    queen.decide = refuse
    with pytest.raises(EnrolmentError, match=r"hive\.swarm\.busy") as raised:
        await enrol_device(_plan(queen, tmp_path / "device", clock))
    assert not isinstance(raised.value, EnrolmentDeniedError)
