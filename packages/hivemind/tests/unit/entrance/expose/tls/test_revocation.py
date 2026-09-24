"""Tests for hivemind.entrance.expose.tls.revocation: the list of certificates taken back.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/tls/revocation.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.tls.revocation for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from unit.entrance.expose.support import HIVE_ID, START

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose.tls import (
    NOT_BEFORE_SKEW,
    HiveAuthority,
    RevokedSerial,
    build_crl,
    load_or_create_authority,
)


@pytest.fixture
async def authority() -> HiveAuthority:
    """A fresh authority minted at START."""
    return await load_or_create_authority(MemorySecretStore(), HIVE_ID, START)


def test_an_empty_list_is_still_a_signed_list_naming_the_authority(
    authority: HiveAuthority,
) -> None:
    crl = build_crl(authority, [], START)

    signer = authority.certificate.public_key()
    assert isinstance(signer, ec.EllipticCurvePublicKey)
    assert list(crl) == []
    assert crl.is_signature_valid(signer)
    assert crl.issuer == authority.subject
    assert crl.last_update_utc == START - NOT_BEFORE_SKEW
    # Freshness comes from rebuilding, so the list lives as long as the authority itself.
    assert crl.next_update_utc == authority.not_after
    identifier = crl.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    assert identifier == authority.key_identifier


def test_every_revoked_serial_is_listed_once_with_its_first_revocation(
    authority: HiveAuthority,
) -> None:
    later = START + timedelta(hours=1)
    revoked = [
        RevokedSerial(serial=7, revoked_at=later),
        RevokedSerial(serial=3, revoked_at=START),
        RevokedSerial(serial=7, revoked_at=START),
        RevokedSerial(serial=3, revoked_at=later),
    ]

    crl = build_crl(authority, revoked, later)

    entries = {entry.serial_number: entry.revocation_date_utc for entry in crl}
    assert entries == {3: START, 7: START}
    assert crl.get_revoked_certificate_by_serial_number(7) is not None
    assert crl.get_revoked_certificate_by_serial_number(8) is None


def test_a_later_list_carries_a_larger_number(authority: HiveAuthority) -> None:
    first = build_crl(authority, [], START)
    second = build_crl(authority, [], START + timedelta(seconds=1))

    number = x509.CRLNumber
    assert (
        second.extensions.get_extension_for_class(number).value.crl_number
        > first.extensions.get_extension_for_class(number).value.crl_number
    )
