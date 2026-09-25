"""Hold one device's side of the Landing Board: redeem an invite, log in, sign, step up, log out.

Every client of the Hive Entrance (the Hive's one HTTP door) is an enrolled device that logs in
with its own key plus the operator's password and signs every request (ADR-0041); the CLI is no
exception, on a laptop or as the Hive Stand's own console. ``LandingClient`` speaks the four
unauthenticated or session-opening calls (redeem an invite with an Ed25519 key, ask for a login
challenge, answer it with the key's signature and the password, step up the same way) and sends
any signed request. ``SignedIn`` is a logged-in device: typed calls that parse the Landing Board's
own models and, when an interactive device is told ``step_up_required``, step up once with the
password it already holds and ask again (a device that cannot step up gets the pending
confirmation's id in the refusal instead). ``signed_in`` opens one and always logs out. The
session token lives only in this process's memory, for as long as the command runs; the password
likewise, only so a step-up can be answered without a second prompt.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Used by ``hivemind.cli.entrance``
    (the console) and ``hivemind.cli.remote`` (a remote device). Calls into ``httpx``, the
    Landing Board's models (``hivemind.entrance.models``), ``hivemind.cli.landing.signing`` and
    ``.errors``.

Key invariants:
    - Every authenticated request is signed exactly as sent (method, target, body bytes).
    - A step-up is attempted at most once per call, and only when the refusal asks this device to.
    - No token, password or signature is logged, printed or put in an error.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the login and
      step-up rules.
    - hivemind.entrance.routes for the routes called here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from hivemind.cli.landing.errors import (
    EntranceUnreachableError,
    LandingProtocolError,
    LandingRefusedError,
)
from hivemind.cli.landing.signing import (
    Credential,
    DeviceKey,
    OutgoingRequest,
    Stamp,
    enrol_signature,
    login_signature,
    request_headers,
)
from hivemind.cli.landing.transport import EntranceAddress
from hivemind.common.logging import get_logger
from hivemind.entrance.enrol import DeviceDescription
from hivemind.entrance.models import (
    ChallengeView,
    HiveView,
    OpenedSessionView,
    RedemptionView,
    SteppedUpView,
)
from waggle.clock import Clock
from waggle.signing import Ed25519Signer

JSON_TYPE = "application/json"  # Every Landing Board body.

log = get_logger(__name__)

__all__ = ["LandingClient", "LandingSession", "SignedIn", "read_hive_id", "signed_in"]


@dataclass(frozen=True, slots=True)
class LandingSession:
    """An open session: what signs its requests, and what the Entrance said about it.

    Attributes:
        credential: The token (memory only) and the key it is bound to.
        listener: ``loopback`` or ``remote``: the only listener the session works on.
        needs_step_up: The travel lock saw a new network; step up before anything else.
    """

    credential: Credential
    listener: str
    needs_step_up: bool


class LandingClient:
    """One device's calls to one Entrance, over one HTTP client."""

    def __init__(
        self, http: httpx.AsyncClient, address: EntranceAddress, hive_id: str, clock: Clock
    ) -> None:
        """Hold the HTTP client, where it points, the Hive's id and the signing clock.

        Args:
            http: A client whose base URL is ``address``'s origin (``transport.open_http``).
            address: The Entrance; its sockets use the same origin and TLS.
            hive_id: The Hive every enrolment and login string names.
            clock: Stamps every signed message; the Entrance refuses one outside its skew.
        """
        self._http = http
        self.address = address
        self.hive_id = hive_id
        self.clock = clock

    async def redeem(
        self,
        code: str,
        signer: Ed25519Signer,
        description: DeviceDescription,
        certificate_request: str | None = None,
    ) -> RedemptionView:
        """Redeem an invite with a new Ed25519 key; the device then waits, PENDING, for approval.

        Args:
            code: The invite code, as the operator read it out.
            signer: The device's freshly minted key.
            description: What the device says about itself (display text only).
            certificate_request: A PEM request for the key's mutual-TLS client certificate,
                signed at approval when the Hive runs its own authority; None for none.

        Returns:
            The device's id, its key's fingerprint and the Hive's public key.

        Raises:
            ValueError: ``code`` is not an invite code.
            LandingRefusedError: The Entrance refused it (it never says why).
            EntranceUnreachableError: Nothing answered.
        """
        body: dict[str, object] = {
            "code": code,
            "public_key_hex": signer.public_key_bytes.hex(),
            "signature": enrol_signature(self.hive_id, code, signer),
            "description": description.model_dump(mode="json"),
        }
        # Sent only when there is one: an older Entrance refuses a field it does not know.
        if certificate_request is not None:
            body["certificate_request"] = certificate_request
        response = await self._public("/v1/enrol/ed25519", body)
        return _parsed(response, RedemptionView)

    async def login(self, key: DeviceKey, password: SecretStr) -> LandingSession:
        """Log the device in: a challenge, then the key's signature over it plus the password.

        Args:
            key: The device's id and key.
            password: The operator's password.

        Returns:
            The open session.

        Raises:
            LandingRefusedError: Refused (``authentication_failed`` never says which factor).
            EntranceUnreachableError: Nothing answered.
        """
        challenge = await self._challenge("/v1/auth/challenge", {"device_id": key.device_id}, None)
        body = {
            "device_id": key.device_id,
            "nonce": challenge.nonce,
            "signature": login_signature(self.hive_id, key, challenge.nonce),
            "password": password.get_secret_value(),
        }
        opened = _parsed(await self._public("/v1/auth/login", body), OpenedSessionView)
        credential = Credential(token=opened.token, key=key)
        return LandingSession(credential, opened.listener, opened.needs_step_up)

    async def step_up(self, credential: Credential, password: SecretStr) -> SteppedUpView:
        """Re-run both factors on a session: a fresh challenge, the key's signature, the password.

        Args:
            credential: The session to step up.
            password: The operator's password.

        Returns:
            When the step-up window closes.

        Raises:
            LandingRefusedError: Refused (a device that cannot step up, or a wrong factor).
            EntranceUnreachableError: Nothing answered.
        """
        challenge = await self._challenge("/v1/auth/step-up/challenge", None, credential)
        body = {
            "nonce": challenge.nonce,
            "signature": login_signature(self.hive_id, credential.key, challenge.nonce),
            "password": password.get_secret_value(),
        }
        answered = await self.send(credential, _json_request("POST", "/v1/auth/step-up", body))
        return _parsed(answered, SteppedUpView)

    async def send(self, credential: Credential, request: OutgoingRequest) -> httpx.Response:
        """Send one request signed by ``credential``, exactly as signed.

        Args:
            credential: The session's token and key.
            request: The method, target and body bytes.

        Returns:
            The response, a 2xx.

        Raises:
            LandingRefusedError: The Entrance refused it.
            EntranceUnreachableError: Nothing answered.
        """
        headers = request_headers(credential, request, Stamp.now(self.clock))
        if request.body:
            headers["Content-Type"] = JSON_TYPE
        built = self._http.build_request(
            request.method, request.target, content=request.body, headers=headers
        )
        return await self._exchange(built)

    async def logout(self, credential: Credential) -> None:
        """End the session on the Entrance, so its token is dead at once.

        Args:
            credential: The session to end.

        Raises:
            LandingRefusedError: The Entrance refused it (the session had already ended).
            EntranceUnreachableError: Nothing answered.
        """
        await self.send(credential, OutgoingRequest("POST", "/v1/auth/logout"))

    async def _challenge(
        self, target: str, body: Mapping[str, str] | None, credential: Credential | None
    ) -> ChallengeView:
        """Ask for a login challenge (public) or a step-up challenge (signed)."""
        if credential is None:
            return _parsed(await self._public(target, body or {}), ChallengeView)
        return _parsed(await self.send(credential, OutgoingRequest("POST", target)), ChallengeView)

    async def _public(self, target: str, body: Mapping[str, object]) -> httpx.Response:
        """Send one unauthenticated JSON POST."""
        request = _json_request("POST", target, body)
        headers = {"Content-Type": JSON_TYPE}
        built = self._http.build_request("POST", target, content=request.body, headers=headers)
        return await self._exchange(built)

    async def _exchange(self, request: httpx.Request) -> httpx.Response:
        """Send ``request``; turn a refusal or a transport failure into the CLI's own errors."""
        return await _exchange(self._http, self.address, request)


class SignedIn:
    """A logged-in device: typed, signed calls that step up once when the Entrance asks."""

    def __init__(self, client: LandingClient, session: LandingSession, password: SecretStr) -> None:
        """Hold the client, the open session and the password a step-up re-presents.

        Args:
            client: The device's client.
            session: Its open session.
            password: The operator's password, kept in memory for a step-up only.
        """
        self.client = client
        self.session = session
        self._password = password

    async def call[ModelT: BaseModel](
        self, method: str, target: str, body: BaseModel | None, model: type[ModelT]
    ) -> ModelT:
        """Make one signed call and parse the answer; step up once if the Entrance asks.

        Args:
            method: The HTTP method.
            target: The path and query exactly as sent.
            body: The request body as a Landing Board model, or None.
            model: The model the answer is parsed as.

        Returns:
            The parsed answer.

        Raises:
            LandingRefusedError: The Entrance refused it (after a step-up, when one was asked for).
            EntranceUnreachableError: Nothing answered.
            LandingProtocolError: The answer is not the model the contract names.
        """
        content = body.model_dump_json().encode("utf-8") if body is not None else b""
        request = OutgoingRequest(method, target, content)
        credential = self.session.credential
        try:
            response = await self.client.send(credential, request)
        except LandingRefusedError as refusal:
            # Only an interactive device's own step-up is worth one more try; every other
            # refusal (a held request included) is the answer.
            if not refusal.wants_step_up:
                raise
            await self.step_up()
            response = await self.client.send(credential, request)
        return _parsed(response, model)

    async def step_up(self) -> SteppedUpView:
        """Step the session up with the password this device already holds.

        Returns:
            When the step-up window closes.

        Raises:
            LandingRefusedError: The Entrance refused the step-up.
        """
        return await self.client.step_up(self.session.credential, self._password)


@asynccontextmanager
async def signed_in(
    client: LandingClient, key: DeviceKey, password: SecretStr
) -> AsyncIterator[SignedIn]:
    """Log ``key``'s device in for the block and log it out on the way out, however it ends.

    Args:
        client: The device's client.
        key: The device's id and key.
        password: The operator's password.

    Yields:
        The logged-in device, stepped up first when the travel lock asked for it.

    Raises:
        LandingRefusedError: The login (or the travel lock's step-up) was refused.
        EntranceUnreachableError: Nothing answered.
    """
    session = await client.login(key, password)
    board = SignedIn(client, session, password)
    try:
        # The travel lock saw a new network: nothing else is served until both factors run again.
        if session.needs_step_up:
            await board.step_up()
        yield board
    finally:
        await _logout(client, session.credential)


async def read_hive_id(http: httpx.AsyncClient, address: EntranceAddress) -> str:
    """Ask an Entrance which Hive it serves (``GET /v1/enrol/hive``: no session, not a secret).

    Asked over the same verified TLS a redemption then uses, so the answer is the Hive at that
    address, not whatever a link said.

    Args:
        http: A client for ``address`` (``transport.open_http``).
        address: The Entrance.

    Returns:
        The Hive's id.

    Raises:
        LandingRefusedError: The Entrance refused (an older one answers 404: no such route).
        EntranceUnreachableError: Nothing answered.
        LandingProtocolError: The answer is not a Hive id.
    """
    response = await _exchange(http, address, http.build_request("GET", "/v1/enrol/hive"))
    return _parsed(response, HiveView).hive_id


async def _exchange(
    http: httpx.AsyncClient, address: EntranceAddress, request: httpx.Request
) -> httpx.Response:
    """Send ``request``; turn a refusal or a transport failure into the CLI's own errors."""
    try:
        # Latency: one round trip, a login's Argon2id check included; httpx's timeout
        # (transport.REQUEST_TIMEOUT_S) bounds it and a timeout is a transport failure.
        response = await http.send(request)
    except httpx.TransportError as exc:
        raise EntranceUnreachableError(
            f"The Entrance at {address.origin} did not answer ({type(exc).__name__}: {exc})"
            f"{_handshake_hint(address, exc)}."
        ) from exc
    if response.is_success:
        return response
    raise LandingRefusedError.from_response(response)


def _handshake_hint(address: EntranceAddress, error: httpx.TransportError) -> str:
    """Say what a connection an HTTPS listener dropped may mean for this device, or nothing."""
    if not address.origin.startswith("https://"):
        return ""
    text = str(error).lower()
    # A refused client certificate reads as a TLS alert, or (TLS 1.3 finishes the client's side
    # of the handshake first) as a server that hung up before answering.
    dropped = isinstance(error, httpx.RemoteProtocolError | httpx.ReadError)
    if not (dropped or "ssl" in text or "tls" in text):
        return ""
    if address.client is None:
        return (
            "; a listener that demands client certificates refuses a device without one: "
            "hive remote certificate fetch (or import)"
        )
    return (
        "; if it demands client certificates, this device's may have been refused (revoked, "
        "expired, or from another Hive)"
    )


async def _logout(client: LandingClient, credential: Credential) -> None:
    """Log out; a session that already ended (revoked, locked, expired) is simply over."""
    try:
        await client.logout(credential)
    except (LandingRefusedError, EntranceUnreachableError) as exc:
        # Ended or unreachable either way: the token dies with this process, never persisted.
        log.debug("landing.logout_skipped", error=type(exc).__name__)


def _json_request(method: str, target: str, body: Mapping[str, object]) -> OutgoingRequest:
    """Encode a plain JSON body once, compactly, so the bytes signed are the bytes sent."""
    return OutgoingRequest(method, target, json.dumps(body, separators=(",", ":")).encode("utf-8"))


def _parsed[ModelT: BaseModel](response: httpx.Response, model: type[ModelT]) -> ModelT:
    """Parse a successful answer as ``model``, or say the Entrance broke the contract."""
    try:
        return model.model_validate_json(response.content)
    except ValidationError as exc:
        raise LandingProtocolError(
            f"The Entrance's answer to {response.request.method} {response.request.url.path} is "
            f"not a {model.__name__} ({exc.error_count()} problem(s))."
        ) from exc
