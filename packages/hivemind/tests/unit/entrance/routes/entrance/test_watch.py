"""Test hivemind.entrance.routes.entrance.watch: only the Hive Stand answers a watch request.

ADR-0069 over real listeners and a real Queen: a task waiting for the owner's yes to drive the
running screen or audio can be approved only by a steward session that carries the Hive Stand's
stand key on loopback. Without the key (a Virtual Cell's guest riding the console's own session),
from the remote listener (any remote device, the one that would be watched included) or from
another device's session on the Hive Stand, the answer is refused and the request keeps waiting.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving

from hivemind.queen.peripherals import WatchRequest, WatchStatus, new_watch_id
from waggle.ids import new_task_id
from waggle.messages.task import WorkerRole


def _file_request(rig: ServingRig) -> WatchRequest:
    """File one PENDING request in the Queen's book, as her dispatcher would."""
    # The Hive Stand's own Warden, the one link the rig's Queen has attached.
    clock, link = rig.clock, next(iter(rig.queen._wardens.values()))
    now = clock.now()
    request = WatchRequest(
        id=new_watch_id(clock),
        task_id=new_task_id(clock),
        title="Listen to the meeting",
        cell_id=link.cell.id,
        cell_name=link.cell.name,
        warden_id=link.warden_id,
        role=WorkerRole.DRONE,
        scopes=("exoskeleton:audio", "exoskeleton:real_display"),
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    rig.deps.dispatch.watch.file(request)
    return request


def _keyless(rig: ServingRig) -> LandingClient:
    """A loopback client as a Virtual Cell's guest arrives: a loopback Host, no stand key."""
    headers = {"Host": f"localhost:{rig.entrance.listeners.loopback_port}"}
    http = httpx.AsyncClient(base_url=rig.loopback_url, headers=headers, timeout=5.0)
    rig.http.append(http)
    return LandingClient(http, rig.hive_id, rig.clock)


async def _caller(rig: ServingRig, case: str) -> tuple[LandingClient, LandingSession]:
    """The client and session each case answers with."""
    if case == "remote session":
        return await rig.program(ProgramGrant(remote=True))
    if case == "device session":
        # Another device's session on the Hive Stand, with the stand key: not the steward.
        return await rig.program()
    console, session = await rig.console_session()
    return (_keyless(rig), session) if case == "no stand key" else (console, session)


@pytest.mark.parametrize(
    ("case", "status"),
    [
        ("no stand key", 403),
        ("remote session", 404),
        ("device session", 403),
        ("stand key", 200),
    ],
)
async def test_only_a_steward_session_with_the_stand_key_approves_a_watch_request(
    case: str, status: int
) -> None:
    async with serving(RigOptions(remote=True)) as rig:
        request = _file_request(rig)
        client, session = await _caller(rig, case)

        answer = await client.call(session, "POST", f"/v1/entrance/watch/{request.id}/approve")
        waiting = [r.id for r in rig.queen.pending_watch_requests()]

    assert answer.status_code == status, answer.text
    if status == 200:
        assert answer.json()["status"] == WatchStatus.APPROVED.value
        assert waiting == []
    else:
        assert waiting == [request.id]


async def test_the_hive_stand_lists_what_waits_and_a_second_answer_is_refused() -> None:
    async with serving() as rig:
        request = _file_request(rig)
        console, session = await rig.console_session()

        listed = await console.call(session, "GET", "/v1/entrance/watch")
        denied = await console.call(session, "POST", f"/v1/entrance/watch/{request.id}/deny")
        again = await console.call(session, "POST", f"/v1/entrance/watch/{request.id}/approve")

    assert [row["id"] for row in listed.json()["requests"]] == [request.id]
    assert denied.json()["status"] == WatchStatus.DENIED.value
    assert again.status_code == 409
