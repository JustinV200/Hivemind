"""Test hivemind.entrance.enrol.certificates.renewal: a fresh certificate, by key or on loopback.

Through the real enrolment flows, over in-memory tables and over one SQLite file: an approved
program renews its own certificate within ``RENEWAL_WINDOW`` of its end, from a request for the key
it enrolled, and gets one for the same device id; the one replaced is withdrawn in the same step and
stays listed for the revocation list; the step is one ``guard.entrance_certificate_issued`` event
and every other device is told. A renewal too early, after the certificate lapsed, for another key,
from a browser, or on a Hive without an authority is refused with nothing written. The operator's
re-issue gives a device approved while the Hive ran loopback only the certificate its stored
request asks for, a browser its sealed bundle under mutual TLS only, and refuses a device no longer
approved. Of two renewals decided from the same certificate, only the first is applied.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/certificates/renewal.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from builders.entrance import (
    Enrolment,
    admitted_browser,
    admitted_program,
    enrolment_over,
    memory_enrolment,
    program_request,
)
from cryptography import x509
from cryptography.x509.oid import NameOID

from hivemind.common.secrets import MemorySecretStore
from hivemind.common.sqlite import connect
from hivemind.entrance.enrol import (
    CERTIFICATE_TRAIL_KIND,
    RENEWAL_WINDOW,
    CertificateChange,
    DeviceCertifier,
    EnrolledDevice,
    FakeGoalLedger,
    Reissue,
    reissue_certificate,
    renew_certificate,
    revoke,
)
from hivemind.entrance.errors import CertificateIssueRefusedError, DeviceStatusConflictError
from hivemind.entrance.expose import CertificateRequestError, load_or_create_authority
from hivemind.entrance.runtime import StoredSerials
from hivemind.entrance.store import MemoryEntranceStore, SqliteEntranceStore
from hivemind.pheromone import MemoryPheromoneTrail, SqlitePheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import HiveId
from waggle.signing import Ed25519Signer

_HIVE_ID = HiveId("hive_01DXF6DT00S8CWQEAHWB40R349")  # The Hive the authority is named for.
_DAYS = 86_400.0  # Seconds in a day, for the fake clock.
_DUE = 61 * _DAYS  # Into the last 30 days of a 90-day certificate.


async def _certifier(clock: FakeClock, *, bundles: bool = False) -> DeviceCertifier:
    """A certifier holding a fresh authority, current at the clock's time."""
    authority = await load_or_create_authority(MemorySecretStore(), _HIVE_ID, clock.now())
    return DeviceCertifier(authority, bundles=bundles)


async def _issuing(*, bundles: bool = False) -> Enrolment:
    """An in-memory enrolment rig whose certifier holds an authority."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    certifier = await _certifier(clock, bundles=bundles)
    return enrolment_over(MemoryEntranceStore(trail), trail, clock, FakeGoalLedger(), certifier)


def _common_name(record: EnrolledDevice) -> str:
    """The device id the device's current certificate names."""
    assert record.certificate is not None
    leaf = x509.load_pem_x509_certificate(record.certificate.pem.encode("ascii"))
    return str(leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)


async def test_a_program_renews_its_own_certificate_and_the_old_one_is_withdrawn() -> None:
    rig = await _issuing()
    device, signer = await admitted_program(rig, requesting=True)
    old = device.certificate
    assert old is not None
    rig.clock.advance(_DUE)
    notified = len(rig.notifier.notices)

    renewed = await renew_certificate(rig.deps, device.id, program_request(signer))

    assert renewed.certificate is not None and renewed.certificate.serial != old.serial
    assert _common_name(renewed) == device.id
    [superseded] = renewed.superseded_certificates
    assert (superseded.serial, superseded.revoked_at) == (old.serial, rig.clock.now())
    listed = {entry.serial for entry in await StoredSerials(rig.store).revoked()}
    assert listed == {int(old.serial, 16)}  # The replaced one only; the new one stands.
    [event] = await rig.events(CERTIFICATE_TRAIL_KIND)
    assert (event.subject_id, event.actor) == (device.id, device.id)
    assert event.payload["how"] == "renewed"
    assert event.payload["superseded_serial"] == old.serial
    assert event.payload["certificate_serial"] == renewed.certificate.serial
    assert len(rig.notifier.notices) == notified + 1


async def test_a_renewal_before_the_window_is_refused_and_writes_nothing() -> None:
    rig = await _issuing()
    device, signer = await admitted_program(rig, requesting=True)
    rig.clock.advance(_DUE - 2 * _DAYS)

    with pytest.raises(CertificateIssueRefusedError, match="not due for renewal until"):
        await renew_certificate(rig.deps, device.id, program_request(signer))

    assert (await rig.store.get_device(device.id)).certificate == device.certificate
    assert await rig.events(CERTIFICATE_TRAIL_KIND) == ()


async def test_a_lapsed_certificate_is_not_renewed_but_re_issued_on_loopback() -> None:
    rig = await _issuing()
    device, signer = await admitted_program(rig, requesting=True)
    rig.clock.advance(91 * _DAYS)

    with pytest.raises(CertificateIssueRefusedError, match="re-issues one on loopback"):
        await renew_certificate(rig.deps, device.id, program_request(signer))
    reissued = await reissue_certificate(rig.deps, device.id, Reissue(actor="human"))

    assert reissued.device.certificate is not None
    assert reissued.device.certificate.not_after > rig.clock.now()
    # The lapsed one no handshake accepts any more needs no list entry.
    assert reissued.device.superseded_certificates == ()


async def test_a_renewal_for_another_key_is_refused_and_writes_nothing() -> None:
    rig = await _issuing()
    device, _ = await admitted_program(rig, requesting=True)
    rig.clock.advance(_DUE)

    with pytest.raises(CertificateRequestError, match="not the device's own enrolled key"):
        await renew_certificate(rig.deps, device.id, program_request(Ed25519Signer.generate()))

    assert (await rig.store.get_device(device.id)).certificate == device.certificate


async def test_a_hive_without_an_authority_renews_nothing() -> None:
    rig = memory_enrolment()
    device, signer = await admitted_program(rig, requesting=True)

    with pytest.raises(CertificateIssueRefusedError, match="no certificate authority"):
        await renew_certificate(rig.deps, device.id, program_request(signer))


async def test_a_browser_renews_nothing_and_is_re_issued_a_bundle_only_under_mutual_tls() -> None:
    sealing = await _issuing(bundles=True)
    browser, _ = await admitted_browser(sealing)
    plain = await _issuing()
    other, _ = await admitted_browser(plain)

    with pytest.raises(CertificateIssueRefusedError, match="re-issued at the Hive Stand"):
        await renew_certificate(sealing.deps, browser.id, "unused")
    sealed = await reissue_certificate(sealing.deps, browser.id, Reissue(actor="human"))
    with pytest.raises(CertificateIssueRefusedError, match="only while mutual TLS is on"):
        await reissue_certificate(plain.deps, other.id, Reissue(actor="human"))

    assert sealed.bundle is not None and sealed.device.certificate is not None


async def test_a_device_approved_loopback_only_is_re_issued_from_its_stored_request() -> None:
    loopback = memory_enrolment()  # The Hive ran no authority when it approved the program.
    device, _ = await admitted_program(loopback, requesting=True)
    assert device.certificate is None and device.certificate_request is not None
    certifier = await _certifier(loopback.clock)
    exposed = enrolment_over(
        loopback.store, loopback.trail, loopback.clock, FakeGoalLedger(), certifier
    )

    reissued = await reissue_certificate(exposed.deps, device.id, Reissue(actor="human"))

    assert reissued.bundle is None
    assert _common_name(reissued.device) == device.id
    [event] = await exposed.events(CERTIFICATE_TRAIL_KIND)
    assert (event.actor, event.payload["how"], event.payload["superseded_serial"]) == (
        "human",
        "reissued",
        None,
    )


async def test_a_device_that_sent_no_request_needs_one_given_and_must_still_stand() -> None:
    rig = await _issuing()
    device, signer = await admitted_program(rig)  # Sent no request at redemption.

    with pytest.raises(CertificateIssueRefusedError, match="sent no certificate request"):
        await reissue_certificate(rig.deps, device.id, Reissue(actor="human"))
    given = Reissue(actor="human", request_pem=program_request(signer))
    reissued = await reissue_certificate(rig.deps, device.id, given)
    await revoke(rig.deps, device.id, "human", cancel_goals=False)

    assert reissued.device.certificate is not None
    with pytest.raises(DeviceStatusConflictError):
        await reissue_certificate(rig.deps, device.id, given)


async def test_of_two_renewals_decided_from_one_certificate_only_the_first_lands(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    db = tmp_path / "hive.sqlite3"
    trail = await SqlitePheromoneTrail.create(connect(db), clock)
    store = await SqliteEntranceStore.create(connect(db), clock)
    rig = enrolment_over(store, trail, clock, FakeGoalLedger(), await _certifier(clock))
    device, signer = await admitted_program(rig, requesting=True)
    rig.clock.advance(_DUE)
    renewed = await renew_certificate(rig.deps, device.id, program_request(signer))
    assert device.certificate is not None and renewed.certificate is not None
    stale = CertificateChange(renewed.certificate, (), replacing=device.certificate.serial)
    event = rig.deps.records.identity.event(rig.clock, CERTIFICATE_TRAIL_KIND, device.id, {})

    with pytest.raises(CertificateIssueRefusedError, match="replaced meanwhile"):
        await store.update_device_certificate(device.id, event, stale)

    assert (await store.get_device(device.id)).certificate == renewed.certificate
    assert len(await rig.events(CERTIFICATE_TRAIL_KIND)) == 1


def test_the_window_is_the_last_third_of_a_certificate() -> None:
    assert timedelta(days=30) == RENEWAL_WINDOW
