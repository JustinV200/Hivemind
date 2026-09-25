"""Test hivemind.cli.landing.certificate: ask for a client certificate, check it, present it.

The request a laptop sends passes the Hive's own check and names the laptop's key; a certificate the
Hive's authority issued from it reads back as this device's; one that is not a PEM certificate,
certifies another key, has expired or names no device is refused; ``present`` loads the pair into
a client context and leaves nothing on disk, and refuses a certificate for another key. Real
handshakes with a presented certificate are in the ``serve_hive`` start tests and the end-to-end
mutual-TLS test.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/certificate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import ssl
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from hivemind.cli.landing import (
    ClientCertificate,
    LandingError,
    certificate_facts,
    certificate_request,
    present,
)
from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose import (
    HiveAuthority,
    check_certificate_request,
    issue_client_certificate,
    load_or_create_authority,
)
from waggle.clock import FakeClock
from waggle.ids import HiveId, new_device_id
from waggle.signing import Ed25519Signer

_HIVE_ID = HiveId("hive_01DXF6DT00S8CWQEAHWB40R349")  # The Hive the authority is named for.
_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
_A_YEAR = timedelta(days=366)  # Past any client certificate's life.


@pytest.fixture
async def authority() -> HiveAuthority:
    """The Hive's authority, minted at _NOW."""
    return await load_or_create_authority(MemorySecretStore(), _HIVE_ID, _NOW)


def _issued(authority: HiveAuthority, signer: Ed25519Signer) -> tuple[bytes, str]:
    """A certificate the authority issued for ``signer``'s key, and the device it names."""
    device_id = new_device_id(FakeClock(_NOW))
    request = certificate_request(signer, "laptop").encode("ascii")
    return issue_client_certificate(authority, request, device_id, _NOW).pem, device_id


def _named(signer: Ed25519Signer, common_name: str) -> bytes:
    """A certificate for ``signer``'s own key naming ``common_name``: no device id at all."""
    key = Ed25519PrivateKey.from_private_bytes(signer.private_key_bytes)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_NOW - timedelta(hours=1))
        .not_valid_after(_NOW + timedelta(days=30))
        .sign(key, None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def test_the_request_names_the_device_key_and_passes_the_hives_own_check() -> None:
    signer = Ed25519Signer.generate()

    pem = certificate_request(signer, "x" * 80)

    check_certificate_request(pem.encode("ascii"), "laptop")
    request = x509.load_pem_x509_csr(pem.encode("ascii"))
    raw = request.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert raw == signer.public_key_bytes
    assert request.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "x" * 64


def test_a_certificate_the_hive_issued_reads_back_as_this_devices(
    authority: HiveAuthority,
) -> None:
    signer = Ed25519Signer.generate()
    pem, device_id = _issued(authority, signer)

    facts = certificate_facts(pem, signer, _NOW)

    leaf = x509.load_pem_x509_certificate(pem)
    assert facts.device_id == device_id
    assert facts.serial == format(leaf.serial_number, "x")
    assert facts.fingerprint == leaf.fingerprint(hashes.SHA256()).hex()
    assert facts.not_after == leaf.not_valid_after_utc


def test_what_is_not_this_devices_certificate_is_refused(authority: HiveAuthority) -> None:
    signer = Ed25519Signer.generate()
    pem, _ = _issued(authority, signer)
    others, _ = _issued(authority, Ed25519Signer.generate())

    refusals = [
        (b"not a certificate", _NOW, "not a PEM certificate"),
        (others, _NOW, "another key"),
        (pem, _NOW + _A_YEAR, "has expired"),
        (_named(signer, "laptop"), _NOW, "names no device"),
    ]

    for given, now, said in refusals:
        with pytest.raises(LandingError, match=said):
            certificate_facts(given, signer, now)


def test_present_loads_the_pair_and_leaves_nothing_on_disk(
    authority: HiveAuthority, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signer = Ed25519Signer.generate()
    pem, _ = _issued(authority, signer)
    # Every temporary directory this process makes lands here, where the test can look.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    present(context, ClientCertificate(pem, signer))

    assert list(tmp_path.iterdir()) == []
    assert signer.private_key_bytes.hex() not in repr(ClientCertificate(pem, signer))


def test_present_refuses_a_certificate_for_another_key(authority: HiveAuthority) -> None:
    others, _ = _issued(authority, Ed25519Signer.generate())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    with pytest.raises(LandingError, match="does not match its key"):
        present(context, ClientCertificate(others, Ed25519Signer.generate()))
