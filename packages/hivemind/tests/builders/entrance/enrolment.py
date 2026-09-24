"""Build a whole enrolment rig over real stores, and drive devices through the real flows.

``Enrolment`` is ``EnrolmentDeps`` over real stores (in memory, or SQLite with
``sqlite_enrolment``) with recording fakes for the seams, and the certifier a test hands in (none
by default: a loopback-only Hive issues no certificates); ``mint``, ``redeem_program``,
``redeem_browser`` and ``admitted`` drive a device through the real flows, and
``admitted_program`` and ``admitted_browser`` also hand back the key the device holds, so a test
can log it in. ``program_request`` is the certificate signing request a program sends for its key.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used through the
    ``builders.entrance`` face by the tests under packages/hivemind/tests/unit/entrance.

Key invariants:
    - Every rig runs on a FakeClock, and every device it admits went through the real flows.

See Also:
    - hivemind.entrance.enrol for the flows driven here.
    - builders.entrance.records for the records and constants these build on.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from pathlib import Path

from builders.entrance.records import (
    ADDRESS,
    INVITE_TTL,
    ORIGIN,
    PENDING_TTL,
    RELYING_PARTY,
    make_description,
    make_identity,
)
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from hivemind.common.sqlite import connect
from hivemind.entrance.auth import ChallengeBook, SoftPasskey, b64url_encode, enrol_string
from hivemind.entrance.enrol import (
    ENROLMENT_CHALLENGE_TTL,
    ApprovalRequest,
    DeviceCertifier,
    DeviceStatus,
    Ed25519Proof,
    EnrolledDevice,
    EnrolmentCeremony,
    EnrolmentDeps,
    EnrolmentRecords,
    EnrolmentRules,
    EnrolmentSeams,
    FakeGoalLedger,
    MintedInvite,
    RecordingDeviceOffboarder,
    RecordingSecurityNotifier,
    Redemption,
    approve,
    invite_code_hash,
    mint_invite,
    passkey_options,
    redeem_ed25519,
    redeem_passkey,
)
from hivemind.entrance.store import EntranceStore, MemoryEntranceStore, SqliteEntranceStore
from hivemind.guard import load_guard_policy
from hivemind.pheromone import (
    MemoryPheromoneTrail,
    PheromoneEvent,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
from waggle.clock import FakeClock
from waggle.signing import Ed25519Signer


@dataclass(frozen=True, slots=True)
class Fakes:
    """The recording fakes an Enrolment rig's seams are, typed as themselves for assertions."""

    notifier: RecordingSecurityNotifier
    offboarder: RecordingDeviceOffboarder
    goals: FakeGoalLedger


@dataclass(frozen=True, slots=True)
class Enrolment:
    """EnrolmentDeps over real stores, with the recording fakes a test inspects."""

    deps: EnrolmentDeps
    trail: PheromoneTrail
    clock: FakeClock
    fakes: Fakes

    @property
    def store(self) -> EntranceStore:
        """The Entrance tables under test."""
        return self.deps.records.store

    @property
    def notifier(self) -> RecordingSecurityNotifier:
        """Every security notice sent so far."""
        return self.fakes.notifier

    @property
    def offboarder(self) -> RecordingDeviceOffboarder:
        """Every device cut off so far."""
        return self.fakes.offboarder

    @property
    def goals(self) -> FakeGoalLedger:
        """The devices' open goals and every cancellation."""
        return self.fakes.goals

    async def events(self, kind: str | None = None) -> tuple[PheromoneEvent, ...]:
        """Return every trail event, or every event of ``kind``, in trail order."""
        return await self.trail.query(TrailQuery(kind=kind))


def enrolment_over(
    store: EntranceStore,
    trail: PheromoneTrail,
    clock: FakeClock,
    goals: FakeGoalLedger,
    certifier: DeviceCertifier | None = None,
) -> Enrolment:
    """Build an Enrolment rig over ``store`` and ``trail`` (which the store records on).

    ``certifier`` issues devices' client certificates; one without an authority when omitted.
    """
    fakes = Fakes(RecordingSecurityNotifier(), RecordingDeviceOffboarder(), goals)
    active = certifier if certifier is not None else DeviceCertifier()
    deps = EnrolmentDeps(
        records=EnrolmentRecords(store, trail, clock, make_identity(clock)),
        rules=EnrolmentRules(load_guard_policy(), INVITE_TTL, PENDING_TTL, ORIGIN),
        ceremony=EnrolmentCeremony(
            Ed25519Signer.generate().public_key_bytes,
            RELYING_PARTY,
            ChallengeBook(clock, ENROLMENT_CHALLENGE_TTL),
        ),
        seams=EnrolmentSeams(fakes.notifier, fakes.offboarder, fakes.goals, certifier=active),
    )
    return Enrolment(deps, trail, clock, fakes)


def memory_enrolment(
    goals: FakeGoalLedger | None = None, certifier: DeviceCertifier | None = None
) -> Enrolment:
    """Build an Enrolment rig over in-memory tables and trail (and ``certifier``, if given)."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = MemoryEntranceStore(trail)
    return enrolment_over(store, trail, clock, goals or FakeGoalLedger(), certifier)


async def sqlite_enrolment(path: Path, goals: FakeGoalLedger | None = None) -> Enrolment:
    """Build an Enrolment rig over one SQLite file holding both the trail and the tables."""
    clock = FakeClock()
    trail = await SqlitePheromoneTrail.create(connect(path), clock)
    store = await SqliteEntranceStore.create(connect(path), clock)
    return enrolment_over(store, trail, clock, goals or FakeGoalLedger())


def ed25519_proof(rig: Enrolment, code: str, signer: Ed25519Signer) -> Ed25519Proof:
    """Sign the enrolment string for ``code`` with ``signer``, as a program would."""
    public_key_hex = signer.public_key_bytes.hex()
    message = enrol_string(
        rig.deps.records.identity.hive_id, invite_code_hash(code), public_key_hex
    )
    # Ed25519Signer returns padded standard base64; the Entrance's wire form is base64url.
    raw = base64.b64decode(signer.sign(message))
    return Ed25519Proof(public_key_hex=public_key_hex, signature=b64url_encode(raw))


async def mint(rig: Enrolment, label: str = "phone") -> MintedInvite:
    """Mint an invite through the real flow."""
    return await mint_invite(rig.deps, label)


def program_request(signer: Ed25519Signer) -> str:
    """The PEM certificate signing request a program sends for its own Ed25519 key."""
    key = Ed25519PrivateKey.from_private_bytes(signer.private_key_bytes)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "garden-bot")])
    # Ed25519 signs whole messages: the library takes no separate hash for it.
    request = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, None)
    return request.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def redeem_program(
    rig: Enrolment,
    minted: MintedInvite,
    signer: Ed25519Signer | None = None,
    certificate_request: str | None = None,
) -> Redemption:
    """Redeem ``minted`` as a program with an Ed25519 key (and a certificate request, if given)."""
    key = signer if signer is not None else Ed25519Signer.generate()
    proof = ed25519_proof(rig, minted.code, key)
    if certificate_request is not None:
        proof = replace(proof, certificate_request=certificate_request)
    return await redeem_ed25519(rig.deps, minted.code, proof, make_description(), ADDRESS)


async def redeem_browser(
    rig: Enrolment, minted: MintedInvite, passkey: SoftPasskey | None = None
) -> Redemption:
    """Redeem ``minted`` as a browser creating a passkey over the issued options."""
    authenticator = passkey if passkey is not None else SoftPasskey(ORIGIN)
    options = await passkey_options(rig.deps, minted.code, ADDRESS)
    registration = authenticator.create(options)
    return await redeem_passkey(rig.deps, minted.code, registration, make_description(), ADDRESS)


def approval(**overrides: object) -> ApprovalRequest:
    """Build an ApprovalRequest for a phone, with any field overridden."""
    fields: dict[str, object] = {"name": "phone", "spend_cap_usd_per_day": 5.0, "actor": "human"}
    fields.update(overrides)
    return ApprovalRequest.model_validate(fields)


async def admitted(rig: Enrolment, status: DeviceStatus = DeviceStatus.APPROVED) -> EnrolledDevice:
    """Enrol a program through the real flows as far as PENDING or APPROVED."""
    redemption = await redeem_program(rig, await mint(rig))
    if status is DeviceStatus.PENDING:
        return await rig.store.get_device(redemption.device_id)
    return await approve(rig.deps, redemption.device_id, approval())


async def admitted_program(
    rig: Enrolment, *, requesting: bool = False
) -> tuple[EnrolledDevice, Ed25519Signer]:
    """Enrol and approve a program through the real flows; return it and the key it holds.

    With ``requesting`` it also sends a certificate request for its key, so a rig whose certifier
    has an authority issues it a certificate at approval.
    """
    signer = Ed25519Signer.generate()
    request = program_request(signer) if requesting else None
    redemption = await redeem_program(rig, await mint(rig, "program"), signer, request)
    device = await approve(rig.deps, redemption.device_id, approval(name="program"))
    return device, signer


async def admitted_browser(rig: Enrolment) -> tuple[EnrolledDevice, SoftPasskey]:
    """Enrol and approve a browser with a passkey through the real flows; return both."""
    passkey = SoftPasskey(ORIGIN)
    redemption = await redeem_browser(rig, await mint(rig), passkey)
    device = await approve(rig.deps, redemption.device_id, approval())
    return device, passkey
