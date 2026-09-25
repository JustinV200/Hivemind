"""Tests for hivemind.entrance.expose.tls.context: real TLS handshakes against the listener context.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/tls/context.py (codingrules section 3). Every verdict is
    OpenSSL's own, reached by a real handshake between two in-memory endpoints; certificates are
    dated from the real clock, because OpenSSL judges validity by it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.tls.context for the module under test.
"""

from __future__ import annotations

import ssl
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.serialization import pkcs12
from pydantic import SecretStr
from unit.entrance.expose.support import (
    DEVICE_ID,
    HIVE_ID,
    PUBLIC_HOST,
    ServerFiles,
    client_context,
    handshake,
    make_csr,
    now_utc,
    write_server_files,
)

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose import ListenerTls
from hivemind.entrance.expose.tls import (
    HiveAuthority,
    RevokedSerial,
    build_crl,
    issue_client_certificate,
    issue_pkcs12,
    load_or_create_authority,
    server_context,
)

_X509_V_ERR_CERT_HAS_EXPIRED = 10  # OpenSSL's verify code for an expired certificate.
_X509_V_ERR_CERT_REVOKED = 23  # OpenSSL's verify code for a revoked certificate.
_PASSPHRASE = SecretStr("a-generated-bundle-passphrase")


@pytest.fixture
def now() -> datetime:
    """The real time: OpenSSL checks every certificate against the system clock."""
    return now_utc()


@pytest.fixture
def server(tmp_path: Path, now: datetime) -> ServerFiles:
    """The operator's server certificate for PUBLIC_HOST, on disk."""
    return write_server_files(tmp_path, now=now)


@pytest.fixture
async def authority(now: datetime) -> HiveAuthority:
    """The Hive's authority, minted a moment ago."""
    return await load_or_create_authority(MemorySecretStore(), HIVE_ID, now)


def _tls(server: ServerFiles, *, mutual: bool) -> ListenerTls:
    """The plan's TLS settings for the test server."""
    return ListenerTls(server.cert_path, server.key_path, PUBLIC_HOST, mutual)


def _device(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> tuple[ssl.SSLContext, int]:
    """A device holding a certificate from ``authority``, and that certificate's serial."""
    key = ec.generate_private_key(ec.SECP256R1())
    issued = issue_client_certificate(authority, make_csr(key), DEVICE_ID, now)
    return client_context(tmp_path, server.certificate_pem, issued.pem, key), issued.serial


def test_without_mutual_tls_any_client_completes_from_tls_1_2(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    context = server_context(_tls(server, mutual=False), authority, build_crl(authority, [], now))
    legacy = client_context(tmp_path, server.certificate_pem)
    legacy.maximum_version = ssl.TLSVersion.TLSv1_2

    connection = handshake(legacy, context)

    assert context.verify_mode is ssl.CERT_NONE
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert connection.server.version() == "TLSv1.2"


def test_the_mutual_context_trusts_only_the_hive_and_carries_its_list(
    server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.verify_flags & ssl.VERIFY_CRL_CHECK_LEAF
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert context.num_tickets == 0
    assert context.options & ssl.OP_NO_TICKET
    assert context.cert_store_stats() == {"x509": 1, "crl": 1, "x509_ca": 1}


def test_a_device_certificate_from_a_program_key_completes_the_handshake(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    # The CLI's own Ed25519 device key, certified from its request.
    program_key = ed25519.Ed25519PrivateKey.generate()
    issued = issue_client_certificate(authority, make_csr(program_key), DEVICE_ID, now)
    device = client_context(tmp_path, server.certificate_pem, issued.pem, program_key)
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    connection = handshake(device, context)

    assert connection.server.version() == "TLSv1.3"
    assert connection.server.getpeercert() is not None


def test_a_browser_bundle_completes_the_handshake(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    bundle = issue_pkcs12(authority, DEVICE_ID, now, _PASSPHRASE)
    opened = pkcs12.load_pkcs12(bundle.pkcs12, _PASSPHRASE.get_secret_value().encode())
    assert opened.cert is not None
    assert opened.key is not None
    device = client_context(tmp_path, server.certificate_pem, bundle.certificate.pem, opened.key)
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    assert handshake(device, context).server.version() == "TLSv1.3"


def test_a_client_without_a_certificate_is_refused(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    with pytest.raises(ssl.SSLError, match="PEER_DID_NOT_RETURN_A_CERTIFICATE"):
        handshake(client_context(tmp_path, server.certificate_pem), context)


async def test_a_certificate_from_another_authority_is_refused(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    stranger = await load_or_create_authority(MemorySecretStore(), HIVE_ID, now)
    device, _ = _device(tmp_path, server, stranger, now)
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(device, context)


def test_a_revoked_serial_is_refused_by_the_handshake(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    device, serial = _device(tmp_path, server, authority, now)
    crl = build_crl(authority, [RevokedSerial(serial=serial, revoked_at=now)], now)
    context = server_context(_tls(server, mutual=True), authority, crl)

    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        handshake(device, context)

    assert caught.value.verify_code == _X509_V_ERR_CERT_REVOKED


async def test_an_expired_device_certificate_is_refused_by_the_handshake(
    tmp_path: Path, server: ServerFiles, now: datetime
) -> None:
    # The authority and the certificate were both issued long enough ago for the latter to lapse.
    long_ago = now - timedelta(days=120)
    authority = await load_or_create_authority(MemorySecretStore(), HIVE_ID, long_ago)
    device, _ = _device(tmp_path, server, authority, long_ago)
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        handshake(device, context)

    assert caught.value.verify_code == _X509_V_ERR_CERT_HAS_EXPIRED


def test_a_tls_1_2_client_is_refused_under_mutual_tls(
    tmp_path: Path, server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    # TLS 1.2 could resume a session cached before a revocation, skipping the list entirely.
    device, _ = _device(tmp_path, server, authority, now)
    device.maximum_version = ssl.TLSVersion.TLSv1_2
    context = server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    with pytest.raises(ssl.SSLError):
        handshake(device, context)


def test_the_temporary_trust_file_is_gone_after_the_build(
    server: ServerFiles, authority: HiveAuthority, now: datetime
) -> None:
    before = set(Path(tempfile.gettempdir()).glob("hive-tls-*"))

    server_context(_tls(server, mutual=True), authority, build_crl(authority, [], now))

    assert set(Path(tempfile.gettempdir()).glob("hive-tls-*")) == before
