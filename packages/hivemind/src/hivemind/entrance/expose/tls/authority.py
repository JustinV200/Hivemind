"""Hold the Hive's own certificate authority: create it once, load it after, sign with it.

Under mutual TLS (``lan`` and ``tunnel`` always, ``vpn`` when ``mutual_tls`` is on) the remote
listener admits only a client certificate chaining to the Hive's own authority (ADR-0041), issued
to a device when it is approved. ``load_or_create_authority`` creates that authority on first use,
an EC P-256 key in the secret store as ``entrance.ca_key`` and a self-signed certificate as
``entrance.ca_cert`` (basic constraints ``ca=True, path_length=0``: it signs device certificates and
never another authority; key usage ``keyCertSign`` and ``cRLSign`` only), and loads it on every
later start after checking the two halves belong together. ``HiveAuthority`` keeps the private key
to itself: the issuing and revocation modules hand it a builder to sign, never take the key out.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.tls``. Built by
    the Entrance's composition root; used by ``issue`` (device certificates), ``revocation`` (the
    revocation list) and ``context`` (the trust anchor). Calls into ``cryptography`` and the
    ``SecretStore`` it is given.

Key invariants:
    - A stored authority is never replaced: a half-missing, mismatched, foreign or out-of-date
      one is refused loudly, because a new authority would silently orphan every device's
      certificate. The one repair made is re-deriving a lost certificate from a stored key,
      which leaves every certificate the key signed valid.
    - The private key is written only to the secret store (PKCS#8 DER) and, once inside
      ``HiveAuthority``, never leaves it; ``repr`` and every message name the authority by
      subject and serial.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "The Hive runs its
      own certificate authority".
    - hivemind.common.secrets for the store the key lives in.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hivemind.common.secrets import SecretStore
from hivemind.entrance.expose.errors import CertificateAuthorityError
from waggle.ids import HiveId

CA_KEY_NAME = "entrance.ca_key"  # The authority's EC P-256 private key, PKCS#8 DER.
CA_CERT_NAME = "entrance.ca_cert"  # The authority's self-signed certificate, PEM.
CA_VALIDITY = timedelta(days=3650)  # Ten years: replacing it means re-issuing every device.
# Certificates start this far in the past, so a device or host whose clock runs a little behind
# the Hive Stand's still accepts one issued a moment ago.
NOT_BEFORE_SKEW = timedelta(minutes=5)
_CA_NAME_PREFIX = "HiveMind Hive CA"  # Subject common name, followed by the Hive's id.
_ORGANIZATION = "HiveMind"  # Subject organisation: what a browser's certificate picker shows.
_SIGNATURE_HASH = hashes.SHA256()  # ECDSA with SHA-256 on P-256: every TLS stack verifies it.
# keyCertSign and cRLSign only: the authority signs device certificates and its revocation list,
# and never a TLS handshake of its own.
_CA_KEY_USAGE = x509.KeyUsage(
    digital_signature=False,
    content_commitment=False,
    key_encipherment=False,
    data_encipherment=False,
    key_agreement=False,
    key_cert_sign=True,
    crl_sign=True,
    encipher_only=False,
    decipher_only=False,
)

__all__ = [
    "CA_CERT_NAME",
    "CA_KEY_NAME",
    "CA_VALIDITY",
    "NOT_BEFORE_SKEW",
    "HiveAuthority",
    "load_or_create_authority",
]


class HiveAuthority:
    """The Hive's certificate authority: its certificate in the open, its key kept inside."""

    def __init__(self, key: ec.EllipticCurvePrivateKey, certificate: x509.Certificate) -> None:
        """Pair a key with its certificate; prefer ``load_or_create_authority``.

        Args:
            key: The authority's P-256 private key.
            certificate: Its self-signed CA certificate, whose public key is ``key``'s.

        Raises:
            CertificateAuthorityError: The pair does not belong together, or the certificate is
                not a self-signed certificate authority.
        """
        _check_pair(key, certificate)
        self._key = key
        self._certificate = certificate

    @property
    def certificate(self) -> x509.Certificate:
        """The authority's self-signed certificate: the trust anchor for client certificates."""
        return self._certificate

    @property
    def certificate_pem(self) -> bytes:
        """The certificate in PEM, as the listener's TLS context loads it."""
        return self._certificate.public_bytes(serialization.Encoding.PEM)

    @property
    def subject(self) -> x509.Name:
        """The authority's name, the issuer of every certificate it signs."""
        return self._certificate.subject

    @property
    def key_identifier(self) -> x509.AuthorityKeyIdentifier:
        """The authority key identifier every certificate and list it signs carries.

        Taken from the certificate's own subject key identifier when it has one, since that is
        what OpenSSL matches it against when it builds a chain.
        """
        try:
            own = self._certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        except x509.ExtensionNotFound:
            return x509.AuthorityKeyIdentifier.from_issuer_public_key(self._key.public_key())
        return x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(own.value)

    @property
    def not_after(self) -> datetime:
        """When the authority itself expires; nothing it signs may outlive it."""
        return self._certificate.not_valid_after_utc

    def check_current(self, now: datetime) -> None:
        """Refuse to act for an authority outside its validity window.

        Args:
            now: The time to judge at.

        Raises:
            CertificateAuthorityError: ``now`` is before the certificate's start or after its
                end; every certificate it signed is refused by a TLS handshake anyway then.
        """
        start = self._certificate.not_valid_before_utc
        if not start <= now <= self.not_after:
            raise CertificateAuthorityError(
                f"The Hive's certificate authority ({CA_CERT_NAME}) is valid from {start} to "
                f"{self.not_after}, not at {now}; if it has expired, remove {CA_KEY_NAME} and "
                f"{CA_CERT_NAME} from the secret store to mint a new one, then re-issue every "
                "device's certificate."
            )

    def sign_certificate(self, builder: x509.CertificateBuilder) -> x509.Certificate:
        """Sign a certificate the caller built, with the authority's key.

        Args:
            builder: A complete builder; its issuer should be ``subject``.

        Returns:
            The signed certificate.
        """
        return builder.sign(self._key, _SIGNATURE_HASH)

    def sign_revocation_list(
        self, builder: x509.CertificateRevocationListBuilder
    ) -> x509.CertificateRevocationList:
        """Sign a revocation list the caller built, with the authority's key.

        Args:
            builder: A complete builder; its issuer should be ``subject``.

        Returns:
            The signed list.
        """
        return builder.sign(self._key, _SIGNATURE_HASH)

    def __repr__(self) -> str:
        """Name the authority by subject and serial, never by key."""
        serial = self._certificate.serial_number
        return f"HiveAuthority(subject={self.subject.rfc4514_string()!r}, serial={serial:x})"


async def load_or_create_authority(
    store: SecretStore, hive_id: HiveId, now: datetime
) -> HiveAuthority:
    """Return the Hive's certificate authority from ``store``, creating it on first use.

    Args:
        store: The Hive's secret store.
        hive_id: The Hive's id, named in the authority's subject so two Hives' authorities never
            look alike in a browser's certificate picker.
        now: The current time: a new authority's validity starts here, and a stored one must be
            current.

    Returns:
        The authority; the same key on every call once it exists.

    Raises:
        CertificateAuthorityError: The certificate is stored without its key, either half is not
            what it should be, they do not belong together, or the authority is not current.
    """
    # Latency: two small reads from the store (files on the Hive Stand, a dict in tests).
    stored_key = await store.get(CA_KEY_NAME)
    stored_cert = await store.get(CA_CERT_NAME)
    if stored_key is None and stored_cert is not None:
        # The certificate alone cannot sign; minting a new key would orphan every device.
        raise CertificateAuthorityError(
            f"{CA_CERT_NAME} is in the secret store without {CA_KEY_NAME}; restore the key "
            "from a backup, or remove both to mint a new authority and re-issue every device."
        )
    if stored_key is None:
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = _self_signed(key, hive_id, now)
        # The key goes first: a crash before the certificate is written is repaired next start.
        # PKCS#8 DER in the clear: the store is owner-only, as for every key the Hive mints.
        await store.put(CA_KEY_NAME, _key_der(key))
        await store.put(CA_CERT_NAME, certificate.public_bytes(serialization.Encoding.PEM))
        return HiveAuthority(key, certificate)
    key = _load_key(stored_key)
    if stored_cert is None:
        # A lost certificate is re-derived from the stored key: same subject, same key, so every
        # device certificate the key signed still chains to it.
        certificate = _self_signed(key, hive_id, now)
        await store.put(CA_CERT_NAME, certificate.public_bytes(serialization.Encoding.PEM))
        return HiveAuthority(key, certificate)
    authority = HiveAuthority(key, _load_certificate(stored_cert))
    authority.check_current(now)
    return authority


def _self_signed(
    key: ec.EllipticCurvePrivateKey, hive_id: HiveId, now: datetime
) -> x509.Certificate:
    """Build the self-signed authority certificate for ``key``."""
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORGANIZATION),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{_CA_NAME_PREFIX} {hive_id}"),
        ]
    )
    public_key = key.public_key()
    subject_key_id = x509.SubjectKeyIdentifier.from_public_key(public_key)
    # Critical constraints: a CA with no CA below it, able to sign certificates and lists only.
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - NOT_BEFORE_SKEW)
        .not_valid_after(now + CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_CA_KEY_USAGE, critical=True)
        .add_extension(subject_key_id, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(subject_key_id),
            critical=False,
        )
    )
    return builder.sign(key, _SIGNATURE_HASH)


def _key_der(key: ec.EllipticCurvePrivateKey) -> bytes:
    """Serialise the authority's key for the secret store: unencrypted PKCS#8 DER."""
    return key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _load_key(data: bytes) -> ec.EllipticCurvePrivateKey:
    """Parse the stored key, naming the secret (never its bytes) when it is not a P-256 key."""
    try:
        key = serialization.load_der_private_key(data, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise CertificateAuthorityError(
            f"{CA_KEY_NAME} in the secret store is not an unencrypted PKCS#8 private key."
        ) from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise CertificateAuthorityError(f"{CA_KEY_NAME} in the secret store is not a P-256 key.")
    return key


def _load_certificate(data: bytes) -> x509.Certificate:
    """Parse the stored certificate, naming the secret when it is not one."""
    try:
        return x509.load_pem_x509_certificate(data)
    except ValueError as exc:
        raise CertificateAuthorityError(
            f"{CA_CERT_NAME} in the secret store is not a PEM certificate."
        ) from exc


def _check_pair(key: ec.EllipticCurvePrivateKey, certificate: x509.Certificate) -> None:
    """Refuse a certificate that is not a self-signed authority for exactly this key."""
    encoding = serialization.Encoding.DER
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    if certificate.public_key().public_bytes(encoding, spki) != key.public_key().public_bytes(
        encoding, spki
    ):
        raise CertificateAuthorityError(
            f"{CA_CERT_NAME} is not the certificate of {CA_KEY_NAME}; restore the matching "
            "pair from a backup."
        )
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        constraints = None
    if constraints is None or not constraints.value.ca:
        raise CertificateAuthorityError(f"{CA_CERT_NAME} is not a certificate authority.")
    # A self-signature that does not verify means the stored certificate was altered.
    try:
        certificate.verify_directly_issued_by(certificate)
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise CertificateAuthorityError(
            f"{CA_CERT_NAME} does not carry a valid self-signature; restore it from a backup."
        ) from exc
