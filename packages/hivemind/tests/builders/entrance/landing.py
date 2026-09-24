"""Drive the Landing Board over HTTP as a device does: enrol, log in, sign, open a socket.

A test of the Hive Entrance's routes talks to a real listener (uvicorn on a loopback port) or to an
application through ``httpx.ASGITransport``; either way it needs what every client does: redeem an
invite with an Ed25519 key (or, as a browser, with a new passkey), log in with the key plus the
operator's password, sign every request exactly as sent (``hive-request-v1``) and every socket's
first frame (``hive-ws-v1``). ``LandingClient`` does all of it over one ``httpx.AsyncClient``;
``DeviceKey`` is a program's Ed25519 key or a browser's WebCrypto P-256 session key.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance and the Hive Entrance's end-to-end test.

Key invariants:
    - Every signature is built by the same canonical functions the Entrance verifies with.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
from builders.entrance.auth import PASSWORD, BrowserKey, sign_b64url
from builders.entrance.records import make_description

from hivemind.entrance.auth import (
    SoftPasskey,
    enrol_string,
    login_string,
    new_nonce,
    request_string,
    sha256_hex,
    websocket_string,
)
from hivemind.entrance.enrol import invite_code_hash
from waggle.clock import Clock
from waggle.signing import Ed25519Signer

NONCE_BYTES = 16  # Every nonce a test client mints.

__all__ = ["DeviceKey", "LandingClient", "LandingSession"]


@dataclass(frozen=True, slots=True)
class DeviceKey:
    """The key a device signs with (a program's Ed25519 key, a browser's P-256 key), and its id."""

    device_id: str
    signer: Ed25519Signer | BrowserKey = field(repr=False)


@dataclass(frozen=True, slots=True)
class LandingSession:
    """An open session: its token and the key every request is signed with."""

    token: str = field(repr=False)
    key: DeviceKey


class LandingClient:
    """One device's view of a listener: enrol, log in, and make signed calls."""

    def __init__(self, http: httpx.AsyncClient, hive_id: str, clock: Clock) -> None:
        """Hold the HTTP client pointed at the listener, the Hive's id and the signing clock.

        Args:
            http: A client whose base URL is the listener (or an ASGI transport).
            hive_id: The Hive every enrolment and login string names.
            clock: Stamps every signed request; the Entrance's own clock.
        """
        self.http = http
        self._hive_id = hive_id
        self._clock = clock

    async def enrol(self, code: str, name: str = "garden-bot") -> DeviceKey:
        """Redeem ``code`` with a fresh Ed25519 key; the device is PENDING afterwards.

        Args:
            code: The invite code.
            name: What the device calls itself.

        Returns:
            The device's id and key.
        """
        signer = Ed25519Signer.generate()
        public_key_hex = signer.public_key_bytes.hex()
        message = enrol_string(self._hive_id, invite_code_hash(code), public_key_hex)
        body = {
            "code": code,
            "public_key_hex": public_key_hex,
            "signature": sign_b64url(signer, message),
            "description": {"name": name, "platform": "Linux", "user_agent": "garden-bot/1.0"},
        }
        response = await self.http.post("/v1/enrol/ed25519", json=body)
        response.raise_for_status()
        return DeviceKey(response.json()["device_id"], signer)

    async def enrol_browser(self, code: str, passkey: SoftPasskey) -> str:
        """Redeem ``code`` as a browser, creating a passkey; the device is PENDING afterwards.

        Args:
            code: The invite code.
            passkey: The browser's authenticator, for this listener's origin.

        Returns:
            The device's id.
        """
        options = await self.http.post("/v1/enrol/passkey-options", json={"code": code})
        options.raise_for_status()
        registration = passkey.create(json.dumps(options.json()["options"]))
        description = make_description().model_dump(mode="json")
        body = {"code": code, "registration": json.loads(registration), "description": description}
        redeemed = await self.http.post("/v1/enrol/passkey", json=body)
        redeemed.raise_for_status()
        return str(redeemed.json()["device_id"])

    async def login_browser(
        self, device_id: str, passkey: SoftPasskey, password: str = PASSWORD
    ) -> LandingSession:
        """Log a browser in: its passkey's assertion, a fresh P-256 binding key, the password.

        Args:
            device_id: The browser's device.
            passkey: Its authenticator.
            password: The operator's password.

        Returns:
            The open session, bound to the new P-256 key.
        """
        key = BrowserKey()
        asked = {"device_id": device_id, "binding_key": key.public_key}
        challenge = await self.http.post("/v1/auth/challenge", json=asked)
        challenge.raise_for_status()
        assertion = passkey.get(json.dumps(challenge.json()["passkey_options"]))
        body = {
            "device_id": device_id,
            "nonce": challenge.json()["nonce"],
            "assertion": json.loads(assertion),
            "binding_key": key.public_key,
            "password": password,
        }
        response = await self.http.post("/v1/auth/login", json=body)
        response.raise_for_status()
        return LandingSession(response.json()["token"], DeviceKey(device_id, key))

    async def login(self, key: DeviceKey, password: str = PASSWORD) -> LandingSession:
        """Log ``key``'s device in: a challenge, then its signature plus the password.

        Args:
            key: The device's id and key.
            password: The operator's password.

        Returns:
            The open session.
        """
        challenge = await self.http.post("/v1/auth/challenge", json={"device_id": key.device_id})
        challenge.raise_for_status()
        nonce = challenge.json()["nonce"]
        signature = sign_b64url(key.signer, login_string(self._hive_id, key.device_id, nonce))
        body = {
            "device_id": key.device_id,
            "nonce": nonce,
            "signature": signature,
            "password": password,
        }
        response = await self.http.post("/v1/auth/login", json=body)
        response.raise_for_status()
        return LandingSession(response.json()["token"], key)

    async def step_up(self, session: LandingSession, password: str = PASSWORD) -> None:
        """Re-run both factors on ``session``, keeping it stepped up for the window.

        Args:
            session: An interactive device's session.
            password: The operator's password.
        """
        challenge = await self.call(session, "POST", "/v1/auth/step-up/challenge")
        challenge.raise_for_status()
        nonce = challenge.json()["nonce"]
        message = login_string(self._hive_id, session.key.device_id, nonce)
        body = {"nonce": nonce, "signature": sign_b64url(session.key.signer, message)}
        answered = await self.call(
            session, "POST", "/v1/auth/step-up", {**body, "password": password}
        )
        answered.raise_for_status()

    async def call(
        self, session: LandingSession, method: str, target: str, body: object | None = None
    ) -> httpx.Response:
        """Make one request signed by the session's key, exactly as sent.

        Args:
            session: The session to use.
            method: The HTTP method.
            target: The path and query, exactly as sent.
            body: A JSON body, or None.

        Returns:
            The response, whatever its status.
        """
        content = json.dumps(body).encode("utf-8") if body is not None else b""
        headers = self.signed_headers(session, method, target, content)
        if body is not None:
            headers["Content-Type"] = "application/json"
        return await self.http.request(method, target, content=content, headers=headers)

    async def speak(
        self, session: LandingSession, target: str, clip: bytes, media_type: str = "audio/wav"
    ) -> httpx.Response:
        """Send a clip as a raw body with its media type, signed exactly as sent.

        Args:
            session: The session to use.
            target: The path and query, e.g. ``/v1/chat/audio?intent=goal``.
            clip: The encoded audio.
            media_type: Its ``Content-Type``.

        Returns:
            The response, whatever its status.
        """
        headers = self.signed_headers(session, "POST", target, clip)
        headers["Content-Type"] = media_type
        return await self.http.request("POST", target, content=clip, headers=headers)

    def signed_headers(
        self, session: LandingSession, method: str, target: str, content: bytes
    ) -> dict[str, str]:
        """Return the four headers a signed request carries.

        Args:
            session: The session to sign for.
            method: The HTTP method.
            target: The path and query, exactly as sent.
            content: The body bytes (empty for none).

        Returns:
            Authorization, X-Hive-Timestamp, X-Hive-Nonce and X-Hive-Signature.
        """
        stamp = int(self._clock.now().timestamp())
        nonce = new_nonce(NONCE_BYTES)
        message = request_string(method, target, stamp, nonce, sha256_hex(content))
        return {
            "Authorization": f"Bearer {session.token}",
            "X-Hive-Timestamp": str(stamp),
            "X-Hive-Nonce": nonce,
            "X-Hive-Signature": sign_b64url(session.key.signer, message),
        }

    def hello(self, session: LandingSession, target: str) -> str:
        """Return a socket's first frame for ``target``, signed now.

        Args:
            session: The session the socket belongs to.
            target: The socket's path and query, exactly as requested.

        Returns:
            The JSON text of the first frame.
        """
        stamp = int(self._clock.now().timestamp())
        nonce = new_nonce(NONCE_BYTES)
        signature = sign_b64url(session.key.signer, websocket_string(target, stamp, nonce))
        frame = {"token": session.token, "timestamp": stamp, "nonce": nonce, "signature": signature}
        return json.dumps(frame)
