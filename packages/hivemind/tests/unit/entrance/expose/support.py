"""Test helpers for hivemind.entrance.expose: sections, facts, certificates and TLS handshakes.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped. The
    exposure tests need an ``[entrance]`` section per mode with sensible defaults, facts for a
    host that satisfies it, server certificates and keys written to disk, certificate signing
    requests from a device's own key, TLS contexts for a device, and a real TLS handshake between
    two in-memory endpoints (``ssl.MemoryBIO``, no sockets and no threads) so every refusal is the
    one OpenSSL itself makes. None of these is a production fake of a Protocol this package
    defines (``FakeInterfaces`` is).

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - hivemind.entrance.expose for the package under test.
"""

from __future__ import annotations

import ipaddress
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519
from cryptography.hazmat.primitives.asymmetric.types import (
    CertificateIssuerPrivateKeyTypes,
    PrivateKeyTypes,
)
from cryptography.x509.oid import NameOID

from hivemind.entrance.expose import (
    ExposureFacts,
    FileState,
    HostPlatform,
    InterfaceAddresses,
    TlsFacts,
)
from hivemind.entrance.expose.interfaces import unscoped
from hivemind.manifest.schema import EntranceExposure, EntranceSection, EntranceTlsSection
from waggle.ids import DeviceId, HiveId

START = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # A fixed "now" for every pure check.
HIVE_ID = HiveId("hive_01DXF6DT00S8CWQEAHWB40R349")  # A well-formed Hive id.
DEVICE_ID = DeviceId("device_01DXF6DT005N2MEV2PZA3ZM92T")  # A well-formed device id.
OTHER_DEVICE_ID = DeviceId("device_01DXF6DT00Q1ZT4C9Q0W1R6X7B")  # A second device.
PUBLIC_HOST = "hive.example.test"  # public_url's host, and the server certificate's name.
PUBLIC_URL = f"https://{PUBLIC_HOST}:8711"  # What devices reach the remote listener by.
TAILSCALE_V4 = "100.101.102.103"  # An address inside Tailscale's CGNAT range.
TAILSCALE_V6 = "fd7a:115c:a1e0::1"  # An address inside Tailscale's IPv6 prefix.
LAN_V4 = "192.168.1.20"  # A private LAN address.
CERT_PATH = Path("/etc/hive/tls/cert.pem")  # Where the facts say the certificate lives.
KEY_PATH = Path("/etc/hive/tls/key.pem")  # Where the facts say the key lives.
_MAX_HANDSHAKE_ROUNDS = 20  # A TLS 1.3 handshake takes three; anything past this is a hang.

# ──────────────────────────────────────────────────────────────────────────────
# Sections and facts for the pure check
# ──────────────────────────────────────────────────────────────────────────────


def section(expose: EntranceExposure, **changes: object) -> EntranceSection:
    """Build an ``[entrance]`` section that satisfies ``expose``, then apply ``changes``."""
    base: dict[str, object] = {
        "expose": expose,
        "public_url": PUBLIC_URL,
        "tls": EntranceTlsSection(cert=str(CERT_PATH), key=str(KEY_PATH)),
    }
    # Each remote mode's own address: the overlay, the LAN, or loopback for the tunnel client.
    if expose is EntranceExposure.VPN:
        base["remote_bind"] = f"{TAILSCALE_V4}:8711"
    elif expose is EntranceExposure.LAN:
        base["remote_bind"] = f"{LAN_V4}:8711"
    elif expose is EntranceExposure.TUNNEL:
        base["remote_bind"] = "127.0.0.1:8711"
        base["tunnel_command"] = ("cloudflared", "tunnel", "run")
    return EntranceSection.model_validate({**base, **changes})


def tls_facts() -> TlsFacts:
    """Build TLS facts for a readable, matching, current certificate for PUBLIC_HOST.

    A test varies them with ``dataclasses.replace``, which mypy checks field by field.
    """
    return TlsFacts(
        cert_path=CERT_PATH,
        key_path=KEY_PATH,
        cert=FileState.VALID,
        key=FileState.VALID,
        key_matches_cert=True,
        dns_names=(PUBLIC_HOST,),
        not_before=START - timedelta(days=30),
        not_after=START + timedelta(days=60),
    )


def host_facts(
    interfaces: Mapping[str, Sequence[str]] | None = None,
    *,
    platform: HostPlatform = HostPlatform.LINUX,
    tls: TlsFacts | None = None,
) -> ExposureFacts:
    """Build facts for a Linux host on Tailscale and a LAN, with good TLS files."""
    table = (
        interfaces
        if interfaces is not None
        else {
            "lo": ("127.0.0.1", "::1"),
            "eth0": (LAN_V4, "fe80::1%eth0"),
            "tailscale0": (TAILSCALE_V4, TAILSCALE_V6),
        }
    )
    return ExposureFacts(
        platform=platform,
        interfaces=tuple(
            InterfaceAddresses(
                name=name,
                addresses=frozenset(unscoped(ipaddress.ip_address(text)) for text in addresses),
            )
            for name, addresses in table.items()
        ),
        tls=tls if tls is not None else tls_facts(),
        now=START,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Certificates, keys and requests
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerFiles:
    """A server certificate and key written to disk, and the certificate a client trusts."""

    cert_path: Path
    key_path: Path
    certificate_pem: bytes


def write_server_files(
    directory: Path,
    names: tuple[str, ...] = (PUBLIC_HOST,),
    *,
    now: datetime,
    lifetime: timedelta = timedelta(days=90),
) -> ServerFiles:
    """Write a self-signed server certificate for ``names`` (valid from a day before ``now``)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "none")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now - timedelta(days=1) + lifetime)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in names]), critical=False
        )
    certificate = builder.sign(key, hashes.SHA256())
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path = directory / "server-cert.pem"
    key_path = directory / "server-key.pem"
    cert_path.write_bytes(pem)
    key_path.write_bytes(private_pem(key))
    return ServerFiles(cert_path=cert_path, key_path=key_path, certificate_pem=pem)


def private_pem(key: PrivateKeyTypes) -> bytes:
    """Serialise a private key as unencrypted PKCS#8 PEM."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def make_csr(key: CertificateIssuerPrivateKeyTypes, common_name: str = "whatever") -> bytes:
    """Build a PEM certificate signing request signed by ``key`` (a device's own key)."""
    # The Edwards curves sign without a separate hash; every other key type uses SHA-256.
    algorithm = None if _signs_whole_messages(key) else hashes.SHA256()
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        # Asking to be an authority: the Hive must ignore whatever a request asks for.
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, algorithm)
    )
    return request.public_bytes(serialization.Encoding.PEM)


def _signs_whole_messages(key: CertificateIssuerPrivateKeyTypes) -> bool:
    """Return whether ``key`` is an Edwards-curve key, which signs without a separate hash."""
    return isinstance(key, ed25519.Ed25519PrivateKey | ed448.Ed448PrivateKey)


# ──────────────────────────────────────────────────────────────────────────────
# TLS clients and in-memory handshakes
# ──────────────────────────────────────────────────────────────────────────────


def client_context(
    directory: Path,
    trusted_server_pem: bytes,
    certificate_pem: bytes | None = None,
    key: PrivateKeyTypes | None = None,
) -> ssl.SSLContext:
    """Build a device's client context: trusts the server, presents a certificate if given."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=trusted_server_pem.decode("ascii"))
    # load_cert_chain reads files only, so the device's certificate and key go to disk first.
    if certificate_pem is not None and key is not None:
        cert_path = directory / "client-cert.pem"
        key_path = directory / "client-key.pem"
        cert_path.write_bytes(certificate_pem)
        key_path.write_bytes(private_pem(key))
        context.load_cert_chain(cert_path, key_path)
    return context


@dataclass
class _Endpoint:
    """One side of an in-memory TLS connection and whether its handshake has finished."""

    tls: ssl.SSLObject
    incoming: ssl.MemoryBIO
    outgoing: ssl.MemoryBIO
    done: bool = False

    def step(self) -> None:
        """Advance the handshake as far as the bytes received so far allow."""
        if self.done:
            return
        try:
            self.tls.do_handshake()
        except ssl.SSLWantReadError:
            return
        self.done = True


@dataclass(frozen=True)
class Connection:
    """Both ends of a completed in-memory TLS connection."""

    client_end: _Endpoint
    server_end: _Endpoint

    @property
    def client(self) -> ssl.SSLObject:
        """The device's end."""
        return self.client_end.tls

    @property
    def server(self) -> ssl.SSLObject:
        """The listener's end."""
        return self.server_end.tls

    def send(self, data: bytes) -> bytes:
        """Write ``data`` on the device's end and return what the listener's end reads."""
        self.client.write(data)
        self.server_end.incoming.write(self.client_end.outgoing.read())
        return self.server.read(len(data))


def handshake(
    client: ssl.SSLContext,
    server: ssl.SSLContext,
    server_hostname: str | None = PUBLIC_HOST,
    session: ssl.SSLSession | None = None,
) -> Connection:
    """Run a real TLS handshake between two in-memory endpoints and exchange one record.

    Raises:
        ssl.SSLError: Either side refused; a refused client certificate raises on the server
            side (``ssl.SSLCertVerificationError`` with OpenSSL's ``verify_code``).
    """
    client_end = _endpoint(client, server_side=False, hostname=server_hostname, session=session)
    server_end = _endpoint(server, server_side=True, hostname=None, session=None)
    for _ in range(_MAX_HANDSHAKE_ROUNDS):
        client_end.step()
        server_end.incoming.write(client_end.outgoing.read())
        server_end.step()
        client_end.incoming.write(server_end.outgoing.read())
        if client_end.done and server_end.done:
            break
    else:
        raise AssertionError("The TLS handshake did not finish.")
    # One application record proves both ends really share keys.
    connection = Connection(client_end=client_end, server_end=server_end)
    assert connection.send(b"ping") == b"ping"
    return connection


def _endpoint(
    context: ssl.SSLContext,
    *,
    server_side: bool,
    hostname: str | None,
    session: ssl.SSLSession | None,
) -> _Endpoint:
    """Wrap a pair of memory buffers into one end of a TLS connection."""
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = context.wrap_bio(
        incoming, outgoing, server_side=server_side, server_hostname=hostname, session=session
    )
    return _Endpoint(tls=tls, incoming=incoming, outgoing=outgoing)


def now_utc() -> datetime:
    """Return the real current time: OpenSSL judges certificate validity by the system clock."""
    return datetime.now(UTC)
