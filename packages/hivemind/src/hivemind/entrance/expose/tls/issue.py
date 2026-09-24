"""Issue device client certificates from the Hive's authority: from a CSR, or as a PKCS#12 bundle.

A device that will reach the remote listener under mutual TLS receives its client certificate at
approval (ADR-0033). A program (the CLI, a script) holds its own key and sends a certificate
signing request: ``issue_client_certificate`` checks the request's signature, which proves the
sender holds the key, and signs a certificate for that key alone. A browser cannot make a request,
so ``issue_pkcs12`` generates a fresh P-256 key, certifies it and seals key and certificate into a
PKCS#12 bundle under a passphrase, for the operator to import on the device. WHY not the
authority's certificate too: a phone installs a CA found in a bundle as a trusted root, and then
whoever held the authority's key (it sits in the secret store) could impersonate any site to it;
the server holds the authority, the device never needs it. Either way the certificate is the Hive's
own design, never the request's: subject common name the device id, ``CLIENT_CERT_VALIDITY`` long
(and never past the authority's own end), ``clientAuth`` extended key usage, digital signature key
usage, and basic constraints with no CA bit; the request's own subject and extensions are ignored,
so a request asking to be an authority gets a plain device certificate.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.tls``. Called by
    the approval flow when a device gets its certificate. Calls into ``cryptography``,
    ``HiveAuthority`` and ``waggle.ids`` (the device id's shape).

Key invariants:
    - Nothing is signed for a request whose signature does not verify, or whose key is not
      Ed25519, P-256, P-384 or RSA of at least ``MIN_RSA_BITS``.
    - No certificate outlives the authority, and none is issued by an authority out of date.
    - The PKCS#12 bundle holds the device's key and certificate and no authority certificate;
      it is sealed with AES-256-CBC under PBKDF2-SHA256 and a SHA-256 MAC, and its passphrase
      and generated key never appear in a message, a log line or a ``repr``.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "receives its
      client certificate at approval".
    - hivemind.entrance.expose.tls.revocation for taking a certificate back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificatePublicKeyTypes
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import SecretStr

from hivemind.entrance.expose.errors import CertificateIssueError, CertificateRequestError
from hivemind.entrance.expose.tls.authority import NOT_BEFORE_SKEW, HiveAuthority
from waggle.errors import InvalidIdError
from waggle.ids import DeviceId, IdKind, parse_id

CLIENT_CERT_VALIDITY = timedelta(days=90)  # A lost device's certificate dies on its own, soon.
MIN_RSA_BITS = 2048  # Below this an RSA key is breakable; nothing current makes smaller ones.
MAX_CSR_BYTES = 16 * 1024  # A request is under 2 KiB; the bound caps parsing work on bad input.
MIN_PASSPHRASE_CHARS = 16  # The bundle's only protection in transit; the Entrance generates it.
# PBKDF2 rounds sealing the bundle; ample for a generated passphrase, still instant to import.
PKCS12_KDF_ROUNDS = 100_000
_ACCEPTED_CURVES = (ec.SECP256R1, ec.SECP384R1)  # What browsers, OpenSSL and phones all accept.
_BUNDLE_NAME_PREFIX = "HiveMind"  # The friendly name a device's key store shows for the bundle.
# digitalSignature only: a TLS client certificate signs the handshake and does nothing else.
_LEAF_KEY_USAGE = x509.KeyUsage(
    digital_signature=True,
    content_commitment=False,
    key_encipherment=False,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)

__all__ = [
    "CLIENT_CERT_VALIDITY",
    "MAX_CSR_BYTES",
    "MIN_PASSPHRASE_CHARS",
    "MIN_RSA_BITS",
    "PKCS12_KDF_ROUNDS",
    "DeviceBundle",
    "DeviceCertificate",
    "issue_client_certificate",
    "issue_pkcs12",
]


@dataclass(frozen=True, slots=True)
class DeviceCertificate:
    """A client certificate the Hive issued to one device.

    Attributes:
        device_id: The device it names (its subject common name).
        serial: Its serial number: what the Entrance records, and revokes by.
        not_after: When it expires.
        pem: The certificate in PEM, to hand to the device.
    """

    device_id: DeviceId
    serial: int
    not_after: datetime
    pem: bytes


@dataclass(frozen=True, slots=True)
class DeviceBundle:
    """A PKCS#12 bundle for a browser: a fresh key and its certificate, nothing else.

    Attributes:
        certificate: The certificate inside, for the Entrance's records.
        pkcs12: The sealed bundle; opaque without the passphrase, never shown in ``repr``.
    """

    certificate: DeviceCertificate
    pkcs12: bytes = field(repr=False)


def issue_client_certificate(
    authority: HiveAuthority, csr_pem: bytes, device_id: DeviceId, now: datetime
) -> DeviceCertificate:
    """Sign a client certificate for the key in a device's certificate signing request.

    Args:
        authority: The Hive's certificate authority.
        csr_pem: The device's PKCS#10 request, PEM; only its key and signature are used.
        device_id: The approved device the certificate is for.
        now: The issuing time; the certificate is valid from just before it.

    Returns:
        The certificate, valid for ``CLIENT_CERT_VALIDITY`` or until the authority expires,
        whichever comes first.

    Raises:
        CertificateRequestError: The request does not parse, its signature does not verify, or
            its key is not a kind and size the Hive certifies.
        CertificateIssueError: ``device_id`` is not a device id.
        CertificateAuthorityError: The authority is not current at ``now``.
    """
    _check_device_id(device_id)
    authority.check_current(now)
    public_key = _requested_key(csr_pem, device_id)
    return _described(_sign_leaf(authority, public_key, device_id, now), device_id)


def issue_pkcs12(
    authority: HiveAuthority, device_id: DeviceId, now: datetime, passphrase: SecretStr
) -> DeviceBundle:
    """Generate a key for a browser, certify it, and seal both into a PKCS#12 bundle.

    Args:
        authority: The Hive's certificate authority.
        device_id: The approved device the bundle is for.
        now: The issuing time.
        passphrase: Seals the bundle; at least ``MIN_PASSPHRASE_CHARS`` characters, generated by
            the Entrance and shown to the operator for the import.

    Returns:
        The bundle and a description of the certificate inside it.

    Raises:
        CertificateIssueError: ``device_id`` is not a device id, or the passphrase is too short.
        CertificateAuthorityError: The authority is not current at ``now``.
    """
    _check_device_id(device_id)
    secret = passphrase.get_secret_value()
    # The rule, never the passphrase or its length, goes into the message.
    if len(secret) < MIN_PASSPHRASE_CHARS:
        raise CertificateIssueError(
            f"A device bundle's passphrase must be at least {MIN_PASSPHRASE_CHARS} characters."
        )
    authority.check_current(now)
    # P-256: the one key type every browser and phone accepts for a client certificate.
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = _sign_leaf(authority, key.public_key(), device_id, now)
    sealing = (
        serialization.PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(PKCS12_KDF_ROUNDS)
        .key_cert_algorithm(pkcs12.PBES.PBESv2SHA256AndAES256CBC)
        .hmac_hash(hashes.SHA256())
        .build(secret.encode("utf-8"))
    )
    bundle = pkcs12.serialize_key_and_certificates(
        name=f"{_BUNDLE_NAME_PREFIX} {device_id}".encode(),
        key=key,
        cert=certificate,
        # No authority certificate: a device would install it as a trusted root (see above).
        cas=None,
        encryption_algorithm=sealing,
    )
    return DeviceBundle(certificate=_described(certificate, device_id), pkcs12=bundle)


def _check_device_id(device_id: str) -> None:
    """Refuse a subject that is not a well-formed device id (it becomes the common name)."""
    try:
        parse_id(device_id, IdKind.DEVICE)
    except InvalidIdError as exc:
        raise CertificateIssueError(
            f"{device_id!r} is not a device id; a certificate names exactly one device."
        ) from exc


def _requested_key(csr_pem: bytes, device_id: DeviceId) -> CertificatePublicKeyTypes:
    """Parse a request and return its key once its signature and key kind are acceptable."""
    if len(csr_pem) > MAX_CSR_BYTES:
        raise CertificateRequestError(device_id, f"it is larger than {MAX_CSR_BYTES} bytes")
    try:
        request = x509.load_pem_x509_csr(csr_pem)
        public_key = request.public_key()
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise CertificateRequestError(device_id, "it is not a PEM certificate request") from exc
    # The signature is the proof of possession: without it anyone could get any key certified.
    if not request.is_signature_valid:
        raise CertificateRequestError(device_id, "its signature does not verify")
    if not _acceptable(public_key):
        raise CertificateRequestError(
            device_id,
            f"its key must be Ed25519, P-256, P-384 or RSA of at least {MIN_RSA_BITS} bits",
        )
    return public_key


def _acceptable(public_key: CertificatePublicKeyTypes) -> bool:
    """Return whether the Hive certifies keys of this kind and size."""
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return True
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        return isinstance(public_key.curve, _ACCEPTED_CURVES)
    if isinstance(public_key, rsa.RSAPublicKey):
        return public_key.key_size >= MIN_RSA_BITS
    return False


def _sign_leaf(
    authority: HiveAuthority,
    public_key: CertificatePublicKeyTypes,
    device_id: DeviceId,
    now: datetime,
) -> x509.Certificate:
    """Build and sign the device certificate for ``public_key``, on the Hive's terms only."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    # Never past the authority's own end: a certificate cannot outlive what vouches for it.
    not_after = min(now + CLIENT_CERT_VALIDITY, authority.not_after)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(authority.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - NOT_BEFORE_SKEW)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_LEAF_KEY_USAGE, critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(authority.key_identifier, critical=False)
    )
    return authority.sign_certificate(builder)


def _described(certificate: x509.Certificate, device_id: DeviceId) -> DeviceCertificate:
    """Describe a signed certificate for the Entrance's records."""
    return DeviceCertificate(
        device_id=device_id,
        serial=certificate.serial_number,
        not_after=certificate.not_valid_after_utc,
        pem=certificate.public_bytes(serialization.Encoding.PEM),
    )
