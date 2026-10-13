"""Tests for hivemind.swarm.session.session: PollenSession's own life, open to close.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/session.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The operations themselves are tested beside their modules (exec, files, process).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.session for the module under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cells import make_lease_request
from builders.swarm import LinkRig, make_source, open_rig_session

from hivemind.cell.errors import SessionClosedError
from hivemind.cell.lease import RealCellLease
from hivemind.cell.session import ExecSpec, run
from hivemind.swarm.errors import DeviceRequestError, GatewayUnavailableError
from waggle.envelope import Envelope
from waggle.messages.session.commands import SessionClose, SessionOpen
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel


async def _plain_lease(rig: LinkRig) -> RealCellLease:
    """Lease the rig's device without opening a session on it."""
    request = make_lease_request(rig.clock, cell_id=rig.node.cell_id, holder=rig.node.warden_id)
    return await make_source(rig).lease(request)


async def test_a_session_is_open_until_it_is_closed(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)

    assert session.is_open
    await session.close()

    assert not session.is_open


async def test_close_is_idempotent_and_sends_session_close_once(rig: LinkRig) -> None:
    session, lease, _guard = await open_rig_session(rig)

    await session.close()
    await session.close()
    await asyncio.wait_for(rig.device.state.session_closed.wait(), 5.0)

    closes = [e.payload for e in rig.device.state.received if isinstance(e.payload, SessionClose)]
    assert [close.lease_id for close in closes] == [lease.id]


async def test_the_scratch_dir_is_the_leases_scratch_root(rig: LinkRig) -> None:
    session, lease, _guard = await open_rig_session(rig)

    assert session.scratch_dir == lease.scratch_root


async def test_every_operation_after_close_raises_session_closed(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)
    await session.close()

    with pytest.raises(SessionClosedError):
        await session.put_file(Path("a"), b"x")
    with pytest.raises(SessionClosedError):
        await session.get_file(Path("a"))
    with pytest.raises(SessionClosedError):
        await session.delete_file(Path("a"))
    with pytest.raises(SessionClosedError):
        await run(session, ExecSpec(argv=("echo",)))


async def test_the_session_is_opened_as_the_holder_with_the_lease_id(rig: LinkRig) -> None:
    _session, lease, _guard = await open_rig_session(rig)

    (opened,) = [e for e in rig.device.state.received if isinstance(e.payload, SessionOpen)]

    assert isinstance(opened.payload, SessionOpen)
    assert opened.payload.lease_id == lease.id
    assert opened.sender == lease.holder


async def test_opening_a_session_on_a_lease_the_device_no_longer_holds_is_closed(
    rig: LinkRig,
) -> None:
    lease = await _plain_lease(rig)
    rig.device.state.leases.clear()

    with pytest.raises(SessionClosedError):
        await make_source(rig).open_session(lease)


async def test_a_device_that_answers_the_open_with_something_else_is_an_error(rig: LinkRig) -> None:
    lease = await _plain_lease(rig)

    async def wrong(request: Envelope) -> None:
        await rig.device.send_raw(
            DeviceHeartbeat(
                device_id=rig.party.device_id,
                level=RuntimeLevel.GATEWAY,
                lease_ids=(),
                hive_started_processes=0,
                outbox_pending=0,
                uptime_s=1.0,
            ),
            request.id,
        )

    rig.device.override(SessionOpen, wrong)

    with pytest.raises(DeviceRequestError):
        await make_source(rig).open_session(lease)


async def test_a_lost_link_raises_gateway_unavailable_and_close_does_not(rig: LinkRig) -> None:
    session, _lease, _guard = await open_rig_session(rig)

    await rig.link.close()

    assert not session.is_open
    with pytest.raises(GatewayUnavailableError):
        await session.put_file(Path("a"), b"x")
    await session.close()  # Never raises for a lost link.
