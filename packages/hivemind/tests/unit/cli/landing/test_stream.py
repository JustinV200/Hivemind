"""Test hivemind.cli.landing.stream: a view authenticated by its first frame, over a real Entrance.

The chat stream sends a line the moment it is written to a device the CLI logged in; a device
without the view's capability sees the socket closed with the Entrance's own code; a quiet view
times out without closing.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/stream.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from builders.entrance.auth import PASSWORD
from builders.entrance.serving import ServingRig, serving
from pydantic import SecretStr

from hivemind.cli.landing import (
    FORBIDDEN,
    DeviceKey,
    LandingClient,
    SignedIn,
    ViewClosedError,
    entrance_address,
    open_http,
    open_view,
    signed_in,
)
from hivemind.entrance.enrol import DeviceDescription
from hivemind.entrance.models import ChatAccepted, ChatFrame, ChatPost
from waggle.signing import Ed25519Signer

_READER = ("entrance:submit", "honey:clearance:c2")  # May read the chat (C2) and write to it.


@asynccontextmanager
async def _device(rig: ServingRig, capabilities: tuple[str, ...]) -> AsyncIterator[SignedIn]:
    """Enrol, approve and log in a device through the CLI's own client."""
    address = entrance_address(rig.loopback_url)
    console, session = await rig.console_session()
    invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "laptop"})
    async with open_http(address) as http:
        client = LandingClient(http, address, rig.hive_id, rig.clock)
        signer = Ed25519Signer.generate()
        description = DeviceDescription(name="laptop")
        redemption = await client.redeem(invite.json()["code"], signer, description)
        body = {"name": "laptop", "capabilities": list(capabilities), "spend_cap_usd_per_day": 1}
        path = f"/v1/entrance/pending/{redemption.device_id}/approve"
        assert (await console.call(session, "POST", path, body)).status_code == 200
        key = DeviceKey(redemption.device_id, signer)
        async with signed_in(client, key, SecretStr(PASSWORD)) as board:
            yield board


async def test_the_chat_view_delivers_a_line_written_after_it_opened() -> None:
    async with serving() as rig, _device(rig, _READER) as board:
        hub = rig.entrance.services.streams.hub
        before = hub.subscribers
        async with open_view(board, "/v1/chat/stream") as view:
            # Admitted and subscribed to the trail before anything is written.
            await rig.until(lambda: hub.subscribers > before)
            await board.call("POST", "/v1/chat", ChatPost(text="Is the page done?"), ChatAccepted)
            frame = await view.next(ChatFrame, timeout_s=5.0)

    assert (frame.entry.author.value, frame.entry.text) == ("human", "Is the page done?")


async def test_a_device_without_the_views_capability_sees_it_closed_as_forbidden() -> None:
    submit_only = ("entrance:submit",)
    async with (
        serving() as rig,
        _device(rig, submit_only) as board,
        open_view(board, "/v1/chat/stream") as view,
    ):
        with pytest.raises(ViewClosedError) as closed:
            await view.next(ChatFrame, timeout_s=5.0)

    assert closed.value.close_code == FORBIDDEN


async def test_a_quiet_view_times_out_and_stays_open() -> None:
    async with (
        serving() as rig,
        _device(rig, _READER) as board,
        open_view(board, "/v1/chat/stream") as view,
    ):
        with pytest.raises(TimeoutError):
            await view.next(ChatFrame, timeout_s=0.2)
        await board.call("POST", "/v1/chat", ChatPost(text="Still there?"), ChatAccepted)
        frame = await view.next(ChatFrame, timeout_s=5.0)

    assert frame.entry.text == "Still there?"
