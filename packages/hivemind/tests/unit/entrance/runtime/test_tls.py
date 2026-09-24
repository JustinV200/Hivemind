"""Test hivemind.entrance.runtime.tls: which certificates the revocation list names.

``revoked_serial`` over each standing a certificate-holding device can have: approved or locked
(honoured), revoked or expired with its certificate withdrawn (named, dated at the withdrawal), and
departed by a path that forgot to withdraw it (named anyway, dated at its approval: fail closed).
``StoredSerials`` reads the same answer from the Entrance tables. The devices are enrolled and
approved through the real flows by a certifier that holds the Hive's authority. That the rebuilt
list reaches the next handshake is proven over real sockets, by the start tests of
``hivemind.cli.compose.entrance``, the CLI's certificate tests and the end-to-end test.

Fits into the Hive:
    Mirrors src/hivemind/entrance/runtime/tls.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance import Enrolment, admitted_program, memory_enrolment

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.enrol import (
    DeviceCertifier,
    DeviceStatus,
    EnrolledDevice,
    LockReason,
    lock,
    revoke,
)
from hivemind.entrance.expose import RevokedSerial, load_or_create_authority
from hivemind.entrance.runtime import StoredSerials, revoked_serial
from waggle.clock import FakeClock
from waggle.ids import HiveId

_HIVE_ID = HiveId("hive_01DXF6DT00S8CWQEAHWB40R349")  # The Hive the authority is named for.


async def _issuing() -> Enrolment:
    """An enrolment rig whose certifier holds a fresh authority."""
    authority = await load_or_create_authority(MemorySecretStore(), _HIVE_ID, FakeClock().now())
    return memory_enrolment(certifier=DeviceCertifier(authority))


async def _certified(rig: Enrolment) -> EnrolledDevice:
    """A program approved with a certificate for its own key."""
    device, _ = await admitted_program(rig, requesting=True)
    assert device.certificate is not None
    return device


def _serial(device: EnrolledDevice) -> int:
    """The device's certificate serial, as the revocation list carries it."""
    assert device.certificate is not None
    return int(device.certificate.serial, 16)


async def test_a_standing_device_or_one_without_a_certificate_is_not_listed() -> None:
    rig = await _issuing()
    approved = await _certified(rig)
    locked = await lock(rig.deps, (await _certified(rig)).id, "human", LockReason.LOCKOUT)
    bare, _ = await admitted_program(rig)

    assert [revoked_serial(device) for device in (approved, locked, bare)] == [None, None, None]


async def test_a_revoked_device_is_listed_from_its_withdrawal() -> None:
    rig = await _issuing()
    device = await _certified(rig)
    rig.clock.advance(60)

    revoked = (await revoke(rig.deps, device.id, "human", cancel_goals=False)).device

    assert revoked_serial(revoked) == RevokedSerial(
        serial=_serial(device), revoked_at=rig.clock.now()
    )


@pytest.mark.parametrize("status", [DeviceStatus.EXPIRED, DeviceStatus.REVOKED])
async def test_a_departed_device_whose_certificate_was_never_withdrawn_is_listed_anyway(
    status: DeviceStatus,
) -> None:
    device = await _certified(await _issuing())
    # A record a path left without marking the certificate; the list must not depend on it.
    departed = device.model_copy(update={"status": status})

    listed = revoked_serial(departed)

    assert listed == RevokedSerial(serial=_serial(device), revoked_at=device.approved_at)


async def test_stored_serials_name_only_the_certificates_no_longer_honoured() -> None:
    rig = await _issuing()
    standing = await _certified(rig)
    leaving = await _certified(rig)
    await revoke(rig.deps, leaving.id, "human", cancel_goals=False)

    listed = await StoredSerials(rig.store).revoked()

    assert [entry.serial for entry in listed] == [_serial(leaving)]
    assert _serial(standing) not in {entry.serial for entry in listed}
