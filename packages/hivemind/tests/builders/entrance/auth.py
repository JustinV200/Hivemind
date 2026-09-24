"""Build a login rig over an enrolment rig, log devices in, and sign requests as they would.

``AuthRig`` is ``AuthDeps`` over an ``Enrolment`` rig's real stores: a ``SessionBook`` over a
``SplitSessionTable`` (the console's sessions in memory), wired as the enrolment step's offboarder
so a lock or a revocation really ends sessions; a ``CountingHasher`` that runs the real Argon2id
verification and counts it (so a test can assert the password check was never reached); and an
operator password stored as a real Argon2id PHC string with deliberately tiny parameters, so each
verification takes milliseconds instead of a tenth of a second. ``program_login`` and
``browser_login`` drive the real login flow as an Ed25519 program and a passkey browser would;
``signed_request`` and ``socket_opening`` sign exactly what ``hivemind.entrance.auth.canonical``
says a client signs. ``RecordingListener`` and ``RecordingStreams`` are the Entrance Reducer's two
seams, recording what the app would have done.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through the
    ``builders.entrance`` face by the tests of hivemind.entrance.auth and hivemind.entrance.reducer.

Key invariants:
    - Every signature here is built from the canonical strings, never from a copy of them.
    - The password hash is a real Argon2id PHC string; only its cost parameters are small.

See Also:
    - hivemind.entrance.auth.login for the flow driven here.
    - builders.entrance.enrolment for the rig this one wraps.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from builders.entrance.enrolment import Enrolment, memory_enrolment
from builders.entrance.records import (
    ADDRESS,
    ORIGIN,
    RELYING_PARTY,
    ed25519_public_key,
    entry_event,
    make_device,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from hivemind.entrance.auth import (
    LOGIN_CHALLENGE_TTL,
    Arrival,
    AuthDeps,
    ChallengeBook,
    DeviceProof,
    FakePeerEndpointSource,
    Listener,
    LoginCeremony,
    LoginGuards,
    OpenedSession,
    PasswordHasher,
    RateLimiter,
    RelyingParty,
    SessionBook,
    SessionRules,
    SignedRequest,
    SocketOpening,
    SoftPasskey,
    TravelLock,
    b64url_encode,
    begin_login,
    finish_login,
    login_string,
    new_nonce,
    path_and_query,
    request_string,
    sha256_hex,
    websocket_string,
)
from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice, EnrolmentDeps
from hivemind.entrance.store import EntranceStore, MemorySessionTable, SplitSessionTable
from waggle.clock import FakeClock
from waggle.signing import Ed25519Signer

PASSWORD = "correct horse battery staple"  # noqa: S105 -- a test's password, not a credential
WRONG_PASSWORD = "a wrong but well formed password"  # noqa: S105 -- a test's wrong password
REMOTE_ORIGIN = "https://hive.example.ts.net"  # The remote listener's public_url.
REMOTE_RELYING_PARTY = RelyingParty(
    id="hive.example.ts.net", name="HiveMind", origins=(REMOTE_ORIGIN,)
)
LOOPBACK = Arrival(Listener.LOOPBACK, "127.0.0.1")  # A request on the Hive Stand itself.
REMOTE = Arrival(Listener.REMOTE, ADDRESS)  # A request from the overlay.
SESSION_RULES = SessionRules(
    ttl=timedelta(hours=12),
    idle_timeout=timedelta(minutes=30),
    step_up_window=timedelta(minutes=5),
    request_skew=timedelta(seconds=60),
    origins={Listener.LOOPBACK: frozenset({ORIGIN}), Listener.REMOTE: frozenset({REMOTE_ORIGIN})},
)
_CHEAP_MEMORY_KIB = 8  # Argon2id's floor for one lane: the real verification, in milliseconds.


class CountingHasher(PasswordHasher):
    """The real PasswordHasher, counting how many verifications reached it."""

    def __init__(self) -> None:
        """Start with no verifications."""
        super().__init__()
        self.verifications = 0

    async def verify(self, password: str, encoded: str) -> bool:
        """Count, then verify for real."""
        self.verifications += 1
        return await super().verify(password, encoded)


class BrowserKey:
    """A browser's non-extractable WebCrypto P-256 session key, in software."""

    def __init__(self) -> None:
        """Generate the key pair."""
        self._key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_key(self) -> str:
        """The uncompressed point, unpadded base64url, as a browser registers it."""
        point = self._key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return b64url_encode(point)

    def sign(self, message: bytes) -> str:
        """Sign as WebCrypto does: ECDSA P-256/SHA-256 in IEEE P1363 r||s, unpadded base64url."""
        r, s = decode_dss_signature(self._key.sign(message, ec.ECDSA(hashes.SHA256())))
        return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


@dataclass(frozen=True, slots=True)
class AuthRig:
    """Login dependencies over an enrolment rig, with the counting hasher a test inspects."""

    enrolment: Enrolment
    deps: AuthDeps
    hasher: CountingHasher

    @property
    def clock(self) -> FakeClock:
        """The rig's clock."""
        return self.enrolment.clock

    @property
    def store(self) -> EntranceStore:
        """The Entrance tables."""
        return self.enrolment.store

    @property
    def book(self) -> SessionBook:
        """The session book."""
        return self.deps.sessions


def cheap_password_hash(password: str) -> str:
    """Hash ``password`` into a real Argon2id PHC string with the smallest legal cost."""
    kdf = Argon2id(
        salt=os.urandom(16), length=32, iterations=1, lanes=1, memory_cost=_CHEAP_MEMORY_KIB
    )
    return kdf.derive_phc_encoded(password.encode("utf-8"))


def session_book(rig: Enrolment) -> SessionBook:
    """Build a session book over the rig's tables, the console's sessions kept in memory."""
    tables = SplitSessionTable(rig.store.sessions, MemorySessionTable())
    return SessionBook(rig.deps.records, SESSION_RULES, tables)


async def auth_rig(
    rig: Enrolment | None = None,
    source: FakePeerEndpointSource | None = None,
    lockout_attempts: int = 5,
    cheap_password: bool = True,
) -> AuthRig:
    """Build login dependencies over ``rig`` (a fresh in-memory one by default).

    The session book replaces the rig's recording offboarder, so locking or revoking a device
    really ends its sessions; ``source`` switches the travel lock on; ``cheap_password`` stores
    ``PASSWORD`` under a cheap Argon2id hash (False keeps what the tables hold, for a rig whose
    operator was bootstrapped for real).
    """
    enrolment = rig if rig is not None else memory_enrolment()
    clock = enrolment.clock
    if cheap_password:
        await enrolment.store.set_operator_password_hash(cheap_password_hash(PASSWORD), clock.now())
    book = session_book(enrolment)
    deps: EnrolmentDeps = replace(
        enrolment.deps, seams=replace(enrolment.deps.seams, offboarder=book)
    )
    hasher = CountingHasher()
    ceremony = LoginCeremony(
        ChallengeBook(clock, LOGIN_CHALLENGE_TTL), (RELYING_PARTY, REMOTE_RELYING_PARTY), hasher
    )
    travel = TravelLock(deps, source) if source is not None else None
    guards = LoginGuards(RateLimiter(clock, 60, 30), lockout_attempts, travel)
    return AuthRig(enrolment, AuthDeps(deps, book, ceremony, guards), hasher)


async def admitted_console(auth: AuthRig) -> tuple[EnrolledDevice, Ed25519Signer]:
    """Record a loopback-bound console holding a fresh key, as the operator bootstrap does."""
    signer = Ed25519Signer.generate()
    console = make_device(
        auth.clock,
        DeviceStatus.APPROVED,
        loopback_bound=True,
        interactive=True,
        expires_at=None,
        public_key=ed25519_public_key(signer),
    )
    await auth.store.put_device(console, entry_event(console, auth.clock))
    return console, signer


def sign_b64url(key: Ed25519Signer | BrowserKey, message: bytes) -> str:
    """Sign ``message`` with a program's Ed25519 key or a browser's P-256 key, as base64url."""
    if isinstance(key, BrowserKey):
        return key.sign(message)
    # Ed25519Signer returns padded standard base64; the Entrance's wire form is base64url.
    return b64url_encode(base64.b64decode(key.sign(message)))


async def program_login(
    auth: AuthRig,
    device: EnrolledDevice,
    signer: Ed25519Signer,
    arrival: Arrival = LOOPBACK,
    password: str = PASSWORD,
) -> OpenedSession:
    """Log an Ed25519 device in through the real flow."""
    challenge = await begin_login(auth.deps, device.id, arrival)
    message = login_string(auth.deps.records.identity.hive_id, device.id, challenge.nonce)
    proof = DeviceProof(device.id, challenge.nonce, signature=sign_b64url(signer, message))
    return await finish_login(auth.deps, proof, password, arrival)


async def browser_login(
    auth: AuthRig,
    device: EnrolledDevice,
    passkey: SoftPasskey,
    key: BrowserKey,
    arrival: Arrival = LOOPBACK,
) -> OpenedSession:
    """Log a passkey device in through the real flow, registering ``key`` as its binding key."""
    challenge = await begin_login(auth.deps, device.id, arrival, key.public_key)
    assert challenge.passkey_options is not None
    assertion = passkey.get(challenge.passkey_options)
    proof = DeviceProof(device.id, challenge.nonce, assertion=assertion, binding_key=key.public_key)
    return await finish_login(auth.deps, proof, PASSWORD, arrival)


def signed_request(
    token: str,
    key: Ed25519Signer | BrowserKey,
    at: datetime,
    arrival: Arrival = LOOPBACK,
    nonce: str | None = None,
) -> SignedRequest:
    """Build a POST of a small body, signed by ``key`` at ``at`` exactly as a client signs it."""
    body = b'{"goal": "tidy the garden"}'
    used = nonce if nonce is not None else new_nonce(16)
    stamp = int(at.timestamp())
    message = request_string("POST", "/v1/goals?draft=1", stamp, used, sha256_hex(body))
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Hive-Timestamp": str(stamp),
        "X-Hive-Nonce": used,
        "X-Hive-Signature": sign_b64url(key, message),
    }
    return SignedRequest("POST", "/v1/goals", "draft=1", body, headers, arrival)


def socket_opening(
    token: str,
    key: Ed25519Signer | BrowserKey,
    at: datetime,
    origin: str | None = ORIGIN,
    arrival: Arrival = LOOPBACK,
) -> SocketOpening:
    """Build a socket's first frame, signed by ``key`` at ``at``, opened from ``origin``."""
    nonce = new_nonce(16)
    stamp = int(at.timestamp())
    signature = sign_b64url(key, websocket_string(path_and_query("/v1/stream", ""), stamp, nonce))
    frame = (
        f'{{"token": "{token}", "timestamp": {stamp}, "nonce": "{nonce}", '
        f'"signature": "{signature}"}}'
    )
    return SocketOpening(frame, "/v1/stream", "", origin, arrival)


class RecordingListener:
    """The Reducer's RemoteListenerControl seam, recording stops and starts."""

    def __init__(self, fail_stop: bool = False) -> None:
        """Start with nothing recorded; ``fail_stop`` makes every stop raise after recording."""
        self.calls: list[str] = []
        self._fail_stop = fail_stop

    async def stop(self) -> None:
        """Record a stop."""
        self.calls.append("stop")
        if self._fail_stop:
            raise OSError("the remote listener would not stop")

    async def start(self) -> None:
        """Record a start."""
        self.calls.append("start")


class RecordingStreams:
    """The Reducer's StreamCloser seam, recording each close."""

    def __init__(self) -> None:
        """Start with no closes."""
        self.closes = 0

    async def close_remote(self) -> None:
        """Record a close of every remote socket."""
        self.closes += 1
