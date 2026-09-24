"""Tests for hivemind.entrance.auth.session.request: signed requests and a socket's first frame.

Roadmap 10.5e's first named test lives here: a stolen session token without the device key is
refused. So does every refusal ADR-0033 lists for a request: a replayed nonce (also after the
tables are reopened), a timestamp outside the skew, the other listener, an idle or expired
session, a revoked device, and a browser socket from a foreign origin.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/session/request.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.session.request for the module under test.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from builders.entrance import (
    ORIGIN,
    REMOTE,
    AuthRig,
    BrowserKey,
    admitted_browser,
    admitted_program,
    auth_rig,
    browser_login,
    program_login,
    session_book,
    signed_request,
    socket_opening,
    sqlite_enrolment,
)
from starlette.datastructures import Headers

from hivemind.common.sqlite import connect
from hivemind.entrance.auth import (
    SignedRequest,
    SocketOpening,
    authenticate_request,
    authenticate_websocket,
)
from hivemind.entrance.enrol import revoke
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.entrance.store import SqliteEntranceStore
from hivemind.guard import Capability
from waggle.signing import Ed25519Signer

_FAILED = "guard.entrance_login_failed"


async def _reasons(auth: AuthRig) -> list[object]:
    """Return the reason of every recorded authentication failure, in trail order."""
    return [event.payload["reason"] for event in await auth.enrolment.events(_FAILED)]


async def test_a_signed_request_is_admitted_with_its_devices_capabilities() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    auth.clock.advance(10)

    admitted = await authenticate_request(
        auth.book, signed_request(opened.token, signer, auth.clock.now()), auth.clock.now()
    )

    assert admitted.device.id == device.id
    assert admitted.capabilities.allows(Capability.parse("entrance:submit"))
    assert (admitted.stepped_up, admitted.interactive, admitted.needs_step_up) == (
        False,
        False,
        False,
    )
    stored = await auth.store.sessions.get(opened.session.token_hash)
    assert stored is not None
    assert stored.last_seen_at == auth.clock.now()


async def test_a_stolen_session_token_without_the_device_key_is_refused() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    thief = signed_request(opened.token, Ed25519Signer.generate(), auth.clock.now())

    with pytest.raises(AuthenticationFailedError) as refused:
        await authenticate_request(auth.book, thief, auth.clock.now())

    (event,) = await auth.enrolment.events(_FAILED)
    assert (event.subject_id, event.payload["reason"]) == (device.id, "request_signature")
    dumped = json.dumps(event.model_dump(mode="json")) + str(refused.value)
    for value in (opened.token, *thief.headers.values()):
        assert value not in dumped
    # The device itself, holding its key, is not locked out by the thief's attempt.
    own = signed_request(opened.token, signer, auth.clock.now())
    assert (await authenticate_request(auth.book, own, auth.clock.now())).device.id == device.id


async def test_a_replayed_nonce_is_refused() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    request = signed_request(opened.token, signer, auth.clock.now())
    await authenticate_request(auth.book, request, auth.clock.now())

    with pytest.raises(AuthenticationFailedError):
        await authenticate_request(auth.book, request, auth.clock.now())

    assert await _reasons(auth) == ["request_replay"]


async def test_a_replayed_nonce_is_refused_after_the_tables_are_reopened(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    auth = await auth_rig(await sqlite_enrolment(db))
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    request = signed_request(opened.token, signer, auth.clock.now())
    await authenticate_request(auth.book, request, auth.clock.now())
    # A restart: new connections, a new store and a new book over the same file.
    reopened = await SqliteEntranceStore.create(connect(db), auth.clock)
    records = replace(auth.enrolment.deps.records, store=reopened)
    rig = replace(auth.enrolment, deps=replace(auth.enrolment.deps, records=records))
    book = session_book(rig)

    with pytest.raises(AuthenticationFailedError):
        await authenticate_request(book, request, auth.clock.now())

    fresh = signed_request(opened.token, signer, auth.clock.now())
    assert (await authenticate_request(book, fresh, auth.clock.now())).device.id == device.id


@pytest.mark.parametrize(
    ("offset", "admitted"), [(-59, True), (59, True), (-61, False), (61, False)]
)
async def test_a_timestamp_must_be_within_the_skew(offset: int, admitted: bool) -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    signed_at = auth.clock.now() + timedelta(seconds=offset)

    request = signed_request(opened.token, signer, signed_at)

    if admitted:
        await authenticate_request(auth.book, request, auth.clock.now())
    else:
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(auth.book, request, auth.clock.now())


async def test_a_session_is_refused_on_the_listener_it_was_not_opened_on() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)

    with pytest.raises(AuthenticationFailedError):
        await authenticate_request(
            auth.book,
            signed_request(opened.token, signer, auth.clock.now(), REMOTE),
            auth.clock.now(),
        )


async def test_an_idle_session_and_a_revoked_devices_session_are_refused() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    kept, other = await admitted_program(auth.enrolment)
    idle = await program_login(auth, kept, other)
    revoked = await program_login(auth, device, signer)
    await revoke(auth.deps.enrolment, device.id, "human", cancel_goals=False)
    auth.clock.advance(31 * 60)
    now = auth.clock.now()

    for token, key in ((idle.token, other), (revoked.token, signer)):
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(auth.book, signed_request(token, key, now), now)


async def test_a_browser_session_signs_with_its_webcrypto_key() -> None:
    auth = await auth_rig()
    device, passkey = await admitted_browser(auth.enrolment)
    key = BrowserKey()
    opened = await browser_login(auth, device, passkey, key)

    admitted = await authenticate_request(
        auth.book, signed_request(opened.token, key, auth.clock.now()), auth.clock.now()
    )

    assert (admitted.device.id, admitted.interactive) == (device.id, True)
    with pytest.raises(AuthenticationFailedError):
        await authenticate_request(
            auth.book,
            signed_request(opened.token, BrowserKey(), auth.clock.now()),
            auth.clock.now(),
        )


async def test_a_missing_or_doubled_signing_header_is_refused() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    request = signed_request(opened.token, signer, auth.clock.now())
    missing = {name: value for name, value in request.headers.items() if name != "X-Hive-Nonce"}
    doubled = Headers(
        raw=[
            (name.lower().encode(), value.encode())
            for name, value in [*request.headers.items(), ("Authorization", "Bearer x")]
        ]
    )

    for headers in (missing, doubled):
        broken = SignedRequest(
            "POST", "/v1/goals", "draft=1", request.body, headers, request.arrival
        )
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(auth.book, broken, auth.clock.now())

    assert await _reasons(auth) == []


async def test_a_socket_opens_on_its_first_frame_and_only_from_the_entrances_origin() -> None:
    auth = await auth_rig()
    device, passkey = await admitted_browser(auth.enrolment)
    program, signer = await admitted_program(auth.enrolment)
    key = BrowserKey()
    browser = await browser_login(auth, device, passkey, key)
    tool = await program_login(auth, program, signer)
    now = auth.clock.now()

    opened = await authenticate_websocket(auth.book, socket_opening(browser.token, key, now), now)
    headless = await authenticate_websocket(
        auth.book, socket_opening(tool.token, signer, now, origin=None), now
    )

    assert (opened.device.id, headless.device.id) == (device.id, program.id)
    for origin in ("https://evil.example", None):
        with pytest.raises(AuthenticationFailedError):
            await authenticate_websocket(
                auth.book, socket_opening(browser.token, key, now, origin=origin), now
            )
    with pytest.raises(AuthenticationFailedError):
        await authenticate_websocket(
            auth.book, socket_opening(browser.token, BrowserKey(), now, origin=ORIGIN), now
        )


async def test_a_malformed_first_frame_is_refused_unread() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    good = socket_opening(opened.token, signer, auth.clock.now(), origin=None)

    for frame in ("not json", "{}", good.first_frame + " " * 2_048):
        broken = SocketOpening(frame, good.raw_path, good.raw_query, None, good.arrival)
        with pytest.raises(AuthenticationFailedError):
            await authenticate_websocket(auth.book, broken, auth.clock.now())
