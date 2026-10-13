"""Test hivemind.entrance.android.origins: the APK and WebView origins, and where they are accepted.

An app signed with a configured certificate reports its passkey origin as
``android:apk-key-hash:<unpadded base64url of the certificate's SHA-256>``; the remote listener's
relying party accepts exactly one such origin per configured fingerprint, and the Capacitor
WebView's pinned origin joins CORS and the sockets on both listeners. With ``[entrance.android]``
unset nothing is added, and each of these is refused as it was before: the tests drive the real
listeners, the real WebAuthn verification and real sockets.

Fits into the Hive:
    Mirrors src/hivemind/entrance/android/origins.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from builders.entrance.images import APK_ORIGIN, FINGERPRINT, WEBVIEW, android_rig
from builders.entrance.serving import PUBLIC_ORIGIN, ProgramGrant, RigOptions, ServingRig, serving
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from hivemind.entrance.android import (
    APK_ORIGIN_PREFIX,
    apk_key_hash_origin,
    apk_origins,
    webview_origins,
)
from hivemind.entrance.auth import SoftPasskey
from hivemind.entrance.auth.passkeys import (
    PasskeyRegistration,
    RelyingParty,
    registration_options,
    verify_registration,
)
from hivemind.entrance.auth.session import Listener
from hivemind.entrance.errors import PasskeyRejectedError
from hivemind.manifest.schema.entrance import EntranceAndroidSection

_PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "authorization,x-hive-signature",
}
_SECURITY = "/v1/entrance/stream"  # Any observing device may open it.
_OTHER = ":".join(["CD"] * 32)  # A certificate the manifest does not list.


def test_the_origin_is_the_unpadded_base64url_of_the_fingerprints_bytes() -> None:
    origin = apk_key_hash_origin(FINGERPRINT)

    assert origin == APK_ORIGIN
    assert origin.startswith(APK_ORIGIN_PREFIX)
    assert "=" not in origin and "+" not in origin and "/" not in origin


@pytest.mark.parametrize("fingerprint", ["", "AA:BB", ":".join(["AA"] * 31), ":".join(["ZZ"] * 32)])
def test_a_fingerprint_that_is_not_a_sha256_digest_has_no_origin(fingerprint: str) -> None:
    with pytest.raises(ValueError, match=r"32 bytes|invalid literal"):
        apk_key_hash_origin(fingerprint)


def test_each_configured_fingerprint_adds_one_origin() -> None:
    section = EntranceAndroidSection(
        package_name="net.example.hivemind", cert_fingerprints=(FINGERPRINT, _OTHER)
    )

    assert apk_origins(section) == (APK_ORIGIN, apk_key_hash_origin(_OTHER))
    assert webview_origins(section) == (WEBVIEW,)


def test_an_unconfigured_section_adds_no_origin() -> None:
    section = EntranceAndroidSection()

    assert apk_origins(section) == ()
    assert webview_origins(section) == ()


def _remote_party(rig: ServingRig) -> RelyingParty:
    """Return the remote listener's WebAuthn relying party in a running rig."""
    return rig.entrance.services.listeners[Listener.REMOTE].enrolment.ceremony.relying_party


async def test_the_remote_relying_party_accepts_the_configured_apk_origin_and_the_web_one() -> None:
    async with serving(android_rig()) as rig:
        party = _remote_party(rig)

        web = _register(party, PUBLIC_ORIGIN)
        android = _register(party, APK_ORIGIN)

    assert party.id == "hive.example.ts.net"
    assert party.origins == (PUBLIC_ORIGIN, APK_ORIGIN)
    assert web.rp_id == android.rp_id == "hive.example.ts.net"


async def test_an_apk_origin_is_refused_when_android_is_unset_or_names_another_certificate() -> (
    None
):
    other = android_rig(cert_fingerprints=[_OTHER])
    for options in (RigOptions(remote=True), other):
        async with serving(options) as rig:
            party = _remote_party(rig)

            with pytest.raises(PasskeyRejectedError):
                _register(party, APK_ORIGIN)

    assert party.origins == (PUBLIC_ORIGIN, apk_key_hash_origin(_OTHER))


async def test_loopback_relying_party_never_accepts_an_apk_origin() -> None:
    async with serving(android_rig()) as rig:
        party = rig.entrance.services.listeners[Listener.LOOPBACK].enrolment.ceremony.relying_party

        with pytest.raises(PasskeyRejectedError):
            _register(party, APK_ORIGIN)


async def test_a_phone_enrols_and_logs_in_on_the_remote_listener_with_an_apk_origin() -> None:
    async with serving(android_rig()) as rig:
        console, session = await rig.console_session()
        invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "phone"})
        phone, passkey = rig.client(remote=True), SoftPasskey(APK_ORIGIN)

        device_id = await phone.enrol_browser(invite.json()["code"], passkey)
        approved = await console.call(
            session,
            "POST",
            f"/v1/entrance/pending/{device_id}/approve",
            {"name": "phone", "capabilities": ["observe"], "spend_cap_usd_per_day": 1.0},
        )
        opened = await phone.login_browser(device_id, passkey)

    assert approved.status_code == 200, approved.text
    assert opened.token


async def test_a_phone_cannot_enrol_with_an_apk_origin_the_hive_was_not_told_of() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        console, session = await rig.console_session()
        invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "phone"})
        phone, passkey = rig.client(remote=True), SoftPasskey(APK_ORIGIN)

        with pytest.raises(httpx.HTTPStatusError):
            await phone.enrol_browser(invite.json()["code"], passkey)


async def test_cors_accepts_the_webview_on_both_listeners_only_when_android_is_configured() -> None:
    async with serving(android_rig()) as configured:
        remote, local = (configured.client(remote=side).http for side in (True, False))
        answers = [
            await http.options("/v1/goals", headers={**_PREFLIGHT, "Origin": origin})
            for http in (remote, local)
            for origin in (WEBVIEW, "https://evil.example")
        ]
        public = await remote.options("/v1/goals", headers={**_PREFLIGHT, "Origin": PUBLIC_ORIGIN})
    async with serving(RigOptions(remote=True)) as plain:
        refused = [
            await plain.client(remote=side).http.options(
                "/v1/goals", headers={**_PREFLIGHT, "Origin": WEBVIEW}
            )
            for side in (True, False)
        ]

    allowed = [answer.headers.get("access-control-allow-origin") for answer in answers]
    assert allowed == [WEBVIEW, None, WEBVIEW, None]
    assert public.headers.get("access-control-allow-origin") == PUBLIC_ORIGIN
    assert all("access-control-allow-origin" not in answer.headers for answer in refused)


@pytest.mark.parametrize("configured", [True, False])
async def test_a_browser_socket_from_the_webview_is_admitted_only_when_android_is_configured(
    configured: bool,
) -> None:
    options = android_rig() if configured else RigOptions(remote=True)
    async with serving(options) as rig:
        client, session = await rig.browser(ProgramGrant(capabilities=("observe",)))
        url = f"ws://127.0.0.1:{rig.entrance.listeners.loopback_port}{_SECURITY}"
        async with connect(url, origin=Origin(WEBVIEW)) as socket:
            await socket.send(client.hello(session, _SECURITY))
            hub = rig.entrance.services.streams.hub
            if configured:
                await rig.until(lambda: hub.subscribers > 0)
            else:
                closed = await _closed(socket)

    assert configured or closed


async def _closed(socket: ClientConnection) -> bool:
    """Say whether the server closes ``socket`` shortly: a refused first frame does."""
    try:
        async with asyncio.timeout(3.0):
            while True:
                await socket.recv()
    except ConnectionClosed:
        return True


def _register(party: RelyingParty, origin: str) -> PasskeyRegistration:
    """Register a fresh passkey that reports ``origin`` against ``party``; return the result."""
    challenge = os.urandom(32)
    options = registration_options(party, challenge, b"device_01M221E4C10R4XDPNQNRX85AAA", "op")
    return verify_registration(party, challenge, SoftPasskey(origin).create(options))
