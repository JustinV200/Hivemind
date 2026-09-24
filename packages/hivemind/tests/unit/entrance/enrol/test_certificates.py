"""Test hivemind.entrance.enrol.certificates: a device's client certificate, issued at approval.

The certifier itself: what it issues with the Hive's authority and refuses without one, the bundle a
browser gets and its passphrase, a withdrawal, and the record's boundary. Then the certificate's
life through the real enrolment flows over in-memory tables: a program's request kept at
redemption and signed at approval (named on the trail by serial and fingerprint, never by its
bytes), a damaged request refused before anything is written, a Hive without an authority issuing
nothing, a browser's bundle sealed only by ``approve_with_bundle`` and only under mutual TLS (and
never stored), an offline registration by the operator, and the withdrawal a revocation or an
expiry stamps.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/certificates.py (codingrules section 3), with the flows
    that issue through it (``redeem``, ``decisions``, ``standing``) as far as certificates go;
    their other behaviour is in their own modules' tests.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import base64
import re
from datetime import timedelta

import pytest
from builders.entrance import (
    Enrolment,
    approval,
    memory_enrolment,
    mint,
    program_request,
    redeem_browser,
    redeem_program,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from hivemind.common.errors import InvariantViolationError
from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.enrol import (
    APPROVED_TRAIL_KIND,
    CertificateRecord,
    DeviceCertifier,
    DeviceStatus,
    EnrolledDevice,
    OfflineRegistration,
    approve,
    approve_with_bundle,
    expire_due,
    new_bundle_passphrase,
    register_offline,
    revoke,
    withdrawn,
)
from hivemind.entrance.errors import EnrolmentRefusedError
from hivemind.entrance.expose import CertificateRequestError, HiveAuthority
from hivemind.entrance.expose import load_or_create_authority as authority_in
from waggle.clock import FakeClock
from waggle.ids import HiveId, new_device_id
from waggle.signing import Ed25519Signer

# A request in PEM armour whose body is no request at all.
_DAMAGED = (
    "-----BEGIN CERTIFICATE REQUEST-----\nbm90IGEgcmVxdWVzdA==\n-----END CERTIFICATE REQUEST-----\n"
)
_HIVE_ID = HiveId("hive_01DXF6DT00S8CWQEAHWB40R349")  # The Hive the authority is named for.
_PASSPHRASE = re.compile(r"^[A-Z2-7]{4}(-[A-Z2-7]{4}){4}$")  # Five groups of four base32 letters.
_ONE_HOUR = timedelta(hours=1)


async def _authority(clock: FakeClock) -> HiveAuthority:
    """A fresh authority for a Hive, current at the clock's time."""
    return await authority_in(MemorySecretStore(), _HIVE_ID, clock.now())


async def _issuing(*, bundles: bool = False) -> Enrolment:
    """An enrolment rig whose Hive runs its authority (and seals bundles, when asked)."""
    clock = FakeClock()
    return memory_enrolment(certifier=DeviceCertifier(await _authority(clock), bundles=bundles))


async def _certified(rig: Enrolment, **changes: object) -> tuple[EnrolledDevice, Ed25519Signer]:
    """A program that sent a request, approved: it holds a certificate for its own key."""
    signer = Ed25519Signer.generate()
    redemption = await redeem_program(rig, await mint(rig), signer, program_request(signer))
    return await approve(rig.deps, redemption.device_id, approval(**changes)), signer


def _leaf(record: CertificateRecord) -> x509.Certificate:
    """The certificate a record keeps, parsed."""
    return x509.load_pem_x509_certificate(record.pem.encode("ascii"))


def _raw(certificate: x509.Certificate) -> bytes:
    """The raw public key a certificate certifies (an Ed25519 key's 32 bytes)."""
    return certificate.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


# ──────────────────────────────────────────────────────────────────────────────
# The certifier
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_certifier_without_an_authority_issues_and_seals_nothing() -> None:
    clock, signer = FakeClock(), Ed25519Signer.generate()
    certifier = DeviceCertifier(None, bundles=True)
    device_id = new_device_id(clock)

    assert (certifier.issues, certifier.bundles) == (False, False)
    with pytest.raises(InvariantViolationError):
        certifier.certify(device_id, program_request(signer), clock.now())
    with pytest.raises(InvariantViolationError):
        certifier.bundle(device_id, clock.now())


async def test_a_request_is_checked_alike_with_or_without_an_authority() -> None:
    authority = await _authority(FakeClock())

    for certifier in (DeviceCertifier(), DeviceCertifier(authority)):
        certifier.check_request(program_request(Ed25519Signer.generate()), "laptop")
        with pytest.raises(CertificateRequestError):
            certifier.check_request(_DAMAGED, "laptop")


async def test_a_signed_request_is_recorded_by_serial_fingerprint_and_expiry() -> None:
    clock, signer = FakeClock(), Ed25519Signer.generate()
    authority = await _authority(clock)
    device_id = new_device_id(clock)

    record = DeviceCertifier(authority).certify(device_id, program_request(signer), clock.now())

    leaf = _leaf(record)
    leaf.verify_directly_issued_by(authority.certificate)
    assert _raw(leaf) == signer.public_key_bytes
    assert leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == device_id
    assert record.serial == format(leaf.serial_number, "x")
    assert record.fingerprint == leaf.fingerprint(hashes.SHA256()).hex()
    assert (record.not_after, record.revoked_at) == (leaf.not_valid_after_utc, None)


async def test_a_bundle_opens_with_its_passphrase_and_neither_shows_in_its_repr() -> None:
    clock = FakeClock()
    certifier = DeviceCertifier(await _authority(clock), bundles=True)

    record, bundle = certifier.bundle(new_device_id(clock), clock.now())

    secret = bundle.passphrase.get_secret_value()
    key, certificate, _ = pkcs12.load_key_and_certificates(bundle.pkcs12, secret.encode())
    assert key is not None and certificate is not None
    assert certificate.fingerprint(hashes.SHA256()).hex() == record.fingerprint
    assert secret not in repr(bundle) and repr(bundle.pkcs12) not in repr(bundle)


async def test_bundles_are_sealed_only_when_the_certifier_is_told_to() -> None:
    clock = FakeClock()
    certifier = DeviceCertifier(await _authority(clock))

    assert certifier.issues and not certifier.bundles
    with pytest.raises(InvariantViolationError):
        certifier.bundle(new_device_id(clock), clock.now())


async def test_a_withdrawal_keeps_the_first_date() -> None:
    clock = FakeClock()
    device, _ = await _certified(await _issuing())
    assert device.certificate is not None

    first = withdrawn(device.certificate, clock.now())
    again = withdrawn(first, clock.now() + _ONE_HOUR)

    assert first.revoked_at == clock.now()
    assert again == first
    assert first.model_copy(update={"revoked_at": None}) == device.certificate


def test_a_bundle_passphrase_is_five_groups_of_four_base32_letters() -> None:
    first, second = new_bundle_passphrase(), new_bundle_passphrase()

    assert _PASSPHRASE.match(first.get_secret_value())
    assert first.get_secret_value() != second.get_secret_value()
    assert first.get_secret_value() not in repr(first)


async def test_the_record_round_trips_and_refuses_a_serial_that_is_not_lowercase_hex() -> None:
    device, _ = await _certified(await _issuing())
    record = device.certificate
    assert record is not None

    assert CertificateRecord.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError):
        CertificateRecord.model_validate({**record.model_dump(), "serial": "ABC"})


# ──────────────────────────────────────────────────────────────────────────────
# Through the enrolment flows
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_programs_request_is_kept_at_redemption_and_signed_at_approval() -> None:
    rig, signer = await _issuing(), Ed25519Signer.generate()
    request = program_request(signer)

    redemption = await redeem_program(rig, await mint(rig), signer, request)
    pending = await rig.store.get_device(redemption.device_id)
    device = await approve(rig.deps, redemption.device_id, approval())
    [event] = await rig.events(APPROVED_TRAIL_KIND)

    assert (pending.certificate_request, pending.certificate) == (request, None)
    assert device.certificate is not None
    assert _raw(_leaf(device.certificate)) == signer.public_key_bytes
    assert event.payload["certificate_serial"] == device.certificate.serial
    assert event.payload["certificate_fingerprint"] == device.certificate.fingerprint
    # The trail names the certificate; neither it nor the request is ever written there.
    assert "-----BEGIN" not in repr([entry.payload for entry in await rig.events()])


async def test_a_damaged_request_is_refused_and_the_invite_stays_unspent() -> None:
    rig = await _issuing()
    minted = await mint(rig)

    with pytest.raises(EnrolmentRefusedError):
        await redeem_program(rig, minted, None, _DAMAGED)
    [failure] = await rig.events("guard.entrance_redeem_failed")

    assert failure.payload["reason"] == "bad_certificate_request"
    assert (await rig.store.get_device(minted.device_id)).status is DeviceStatus.INVITED


async def test_a_hive_without_an_authority_keeps_the_request_and_issues_nothing() -> None:
    rig, signer = memory_enrolment(), Ed25519Signer.generate()

    redemption = await redeem_program(rig, await mint(rig), signer, program_request(signer))
    device = await approve(rig.deps, redemption.device_id, approval())
    [event] = await rig.events(APPROVED_TRAIL_KIND)

    assert device.certificate_request is not None and device.certificate is None
    assert "certificate_serial" not in event.payload


async def test_a_browser_is_sealed_a_bundle_only_by_the_sealing_approval() -> None:
    rig = await _issuing(bundles=True)
    sealed_id = (await redeem_browser(rig, await mint(rig))).device_id
    plain_id = (await redeem_browser(rig, await mint(rig))).device_id

    sealed = await approve_with_bundle(rig.deps, sealed_id, approval())
    plain = await approve(rig.deps, plain_id, approval())
    stored = await rig.store.get_device(sealed_id)

    assert sealed.bundle is not None and sealed.device.certificate is not None
    secret = sealed.bundle.passphrase.get_secret_value()
    _, certificate, _ = pkcs12.load_key_and_certificates(sealed.bundle.pkcs12, secret.encode())
    assert certificate is not None
    assert certificate.fingerprint(hashes.SHA256()).hex() == sealed.device.certificate.fingerprint
    assert plain.certificate is None
    # Only the public certificate is kept: the bundle and its passphrase are handed out once.
    kept = stored.model_dump_json() + repr([entry.payload for entry in await rig.events()])
    assert secret not in kept and base64.b64encode(sealed.bundle.pkcs12).decode() not in kept
    assert stored.certificate == sealed.device.certificate


async def test_without_mutual_tls_a_browser_gets_no_bundle() -> None:
    rig = await _issuing(bundles=False)
    redemption = await redeem_browser(rig, await mint(rig))

    approved = await approve_with_bundle(rig.deps, redemption.device_id, approval())

    assert approved.bundle is None and approved.device.certificate is None


async def test_revoking_and_expiring_withdraw_the_certificate_when_the_device_leaves() -> None:
    rig = await _issuing()
    kept, _ = await _certified(rig)
    lapsing, _ = await _certified(rig, expires_at=rig.clock.now() + _ONE_HOUR)
    revoked_at = rig.clock.now()

    revoked = (await revoke(rig.deps, kept.id, "human", cancel_goals=False)).device
    rig.clock.advance(2 * _ONE_HOUR.total_seconds())
    await expire_due(rig.deps)
    expired = await rig.store.get_device(lapsing.id)

    assert revoked.certificate is not None and kept.certificate is not None
    assert revoked.certificate.revoked_at == revoked_at
    assert revoked.certificate.serial == kept.certificate.serial
    assert expired.status is DeviceStatus.EXPIRED and expired.certificate is not None
    assert expired.certificate.revoked_at == rig.clock.now()


async def test_the_operator_registers_a_device_offline_and_it_waits_for_approval() -> None:
    rig, signer = await _issuing(), Ed25519Signer.generate()
    registration = OfflineRegistration(
        name="field laptop",
        public_key_hex=signer.public_key_bytes.hex(),
        certificate_request=program_request(signer),
    )

    redemption = await register_offline(rig.deps, registration, "human")
    pending = await rig.store.get_device(redemption.device_id)
    [waiting] = await rig.events("guard.entrance_pending")
    device = await approve(rig.deps, redemption.device_id, approval(name="field laptop"))

    assert pending.status is DeviceStatus.PENDING
    assert pending.certificate_request == registration.certificate_request
    assert waiting.actor == "human"
    assert device.certificate is not None
    assert _raw(_leaf(device.certificate)) == signer.public_key_bytes


async def test_a_damaged_offline_request_writes_nothing() -> None:
    rig, signer = await _issuing(), Ed25519Signer.generate()
    registration = OfflineRegistration(
        name="field laptop",
        public_key_hex=signer.public_key_bytes.hex(),
        certificate_request=_DAMAGED,
    )

    with pytest.raises(CertificateRequestError):
        await register_offline(rig.deps, registration, "human")

    assert tuple(await rig.store.list_devices()) == ()
    assert await rig.events() == ()
