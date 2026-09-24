"""Build a Hive Stand exposed on this machine's own private address, under TLS and mutual TLS.

The mutual-TLS tests run ``hive serve``'s real composition with its remote listener bound to a
private address this machine really has, so every handshake crosses the kernel's network stack the
way a laptop's would. ``private_address`` finds one (a private, non-loopback IPv4 address on a real
interface); ``server_tls`` makes a throwaway certificate authority and a server certificate it
signed for ``PUBLIC_NAME`` (the DNS name the exposure check requires public_url to carry) and for
that address (the laptop connects by address: nothing here edits a trust store or a hosts file);
``mtls_manifest`` writes the stand's manifest for ``lan`` or ``vpn`` exposure over those files. The
laptop pins the throwaway authority with ``--ca-file`` and reaches the listener at
``https://<address>:<port>``, the certificate's IP subject-alternative name. ``tunnel_manifest``
needs no private address: tunnel mode binds the remote listener to loopback (a stand-in tunnel
client idles as the Entrance's child), with TLS and mutual TLS exactly as in lan.
``self_signed_tls`` makes the plainer kind an operator may bring, a self-signed certificate on a DNS
name alone (or an expired one, for the refusal that names it), and ``served_exposed`` composes
``hive serve``'s Hive over an ``[entrance]`` section and a fake interface table, for tests that
enter ``serve_hive`` themselves.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the CLI's certificate
    tests, the ``serve_hive`` start and refusal tests, and the end-to-end mutual-TLS test, beside
    ``builders.entrance.stand``.

Key invariants:
    - Every key here is generated for one test and written only under its tmp_path.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
from builders.cli import fake_manifest
from builders.entrance.stand import stand_manifest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from hivemind.cli.compose.entrance import ServedHive, build_served_hive
from hivemind.entrance.expose import FakeInterfaces
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

PUBLIC_NAME = "hive.test"  # public_url's host: a DNS name, as every remote mode requires.
# Container and VM bridges: real private addresses too, but a LAN's own interface is preferred.
_BRIDGE_PREFIXES = ("docker", "br-", "veth", "virbr", "cni", "vmnet", "vboxnet")
_LIFETIME = timedelta(days=30)  # Long enough for any test; the checks read the real clock.
_SKEW = timedelta(hours=1)  # Valid from a little before now, as a real authority issues.
_IDLE_TUNNEL = "import time; time.sleep(3600)"  # A tunnel client's stand-in: forwards nothing.

__all__ = [
    "PUBLIC_NAME",
    "PrivateAddress",
    "ServerTls",
    "mtls_manifest",
    "private_address",
    "self_signed_tls",
    "served_exposed",
    "server_tls",
    "tunnel_manifest",
]


@dataclass(frozen=True, slots=True)
class PrivateAddress:
    """A private IPv4 address this machine has, and the interface that carries it.

    Attributes:
        address: E.g. ``192.168.1.20``.
        interface: E.g. ``eth0``; a ``vpn`` test names it as the overlay.
    """

    address: str
    interface: str


@dataclass(frozen=True, slots=True)
class ServerTls:
    """The remote listener's TLS files, and the throwaway authority a device pins.

    Attributes:
        ca_path: The authority's certificate: what the laptop passes as ``--ca-file``.
        cert_path: The server certificate, for ``[entrance.tls] cert``.
        key_path: Its key, for ``[entrance.tls] key``.
    """

    ca_path: Path
    cert_path: Path
    key_path: Path


def private_address() -> PrivateAddress | None:
    """Find a private, non-loopback IPv4 address on one of this machine's interfaces.

    Returns:
        The first one found (real interfaces before bridges, each in name order), or None when
        the machine has none.
    """
    interfaces = psutil.net_if_addrs().items()
    for name, records in sorted(interfaces, key=lambda item: (_is_bridge(item[0]), item[0])):
        for record in records:
            if record.family != socket.AF_INET:
                continue
            address = ipaddress.ip_address(record.address)
            if address.is_private and not address.is_loopback and not address.is_link_local:
                return PrivateAddress(str(address), name)
    return None


def server_tls(directory: Path, address: str) -> ServerTls:
    """Write a throwaway authority and the server certificate it signed for the name and address.

    Args:
        directory: Where the three files go.
        address: The IP address the certificate also names (the laptop connects by it).

    Returns:
        Where each file is.
    """
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test LAN authority")])
    ca = _authority(ca_key, ca_name, now)
    key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName(PUBLIC_NAME), x509.IPAddress(ipaddress.ip_address(address))]
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, PUBLIC_NAME)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _SKEW)
        .not_valid_after(now + _LIFETIME)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    files = ServerTls(directory / "lan-ca.pem", directory / "server.pem", directory / "server.key")
    directory.mkdir(parents=True, exist_ok=True)
    files.ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    files.cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    files.key_path.write_bytes(_private_pem(key))
    return files


def tunnel_manifest(root: Path) -> tuple[Path, ServerTls]:
    """Write a stand manifest in tunnel mode: TLS and mutual TLS, all on loopback.

    Args:
        root: The stand's directory.

    Returns:
        The manifest's path and the TLS files it names (the server certificate also names
        127.0.0.1, where the laptop reaches the remote listener).
    """
    tls = server_tls(root / "tls", "127.0.0.1")
    argv = json.dumps([sys.executable, "-c", _IDLE_TUNNEL])
    table = (
        f'expose = "tunnel"\nremote_bind = "127.0.0.1:0"\npublic_url = "https://{PUBLIC_NAME}"\n'
        f"tunnel_command = {argv}\n"
        f'\n[entrance.tls]\ncert = "{tls.cert_path}"\nkey = "{tls.key_path}"\n'
    )
    return stand_manifest(root, table), tls


def self_signed_tls(
    directory: Path, name: str = PUBLIC_NAME, *, expired: bool = False
) -> ServerTls:
    """Write a self-signed server certificate on the DNS name ``name`` alone, and its key.

    Args:
        directory: Where the two files go (created if missing).
        name: The certificate's one DNS subject-alternative name.
        expired: Make it one that ended an hour ago, a month after it began.

    Returns:
        The files; ``ca_path`` is the certificate itself, since a client pins it directly.
    """
    now = datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    start, end = (now - _LIFETIME, now - _SKEW) if expired else (now - _SKEW, now + _LIFETIME)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "self-signed.pem", directory / "self-signed.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(_private_pem(key))
    return ServerTls(ca_path=cert_path, cert_path=cert_path, key_path=key_path)


def served_exposed(
    root: Path, entrance: str, interfaces: Mapping[str, Sequence[str]]
) -> ServedHive:
    """Compose ``hive serve``'s Hive over a fake manifest, not yet started.

    Args:
        root: The Hive's directory (its manifest, SQLite file and secrets go under it).
        entrance: The ``[entrance]`` section's body, sub-tables (``[entrance.tls]``) included.
        interfaces: What the host's interface table says, by interface name.

    Returns:
        The Hive, its interfaces replaced by the fake table.
    """
    path = fake_manifest(root)
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n[entrance]\n{entrance}", encoding="utf-8"
    )
    served = build_served_hive(load_manifest(path, {}), environ={}, clock=SystemClock())
    return replace(served, interfaces=FakeInterfaces(interfaces))


def mtls_manifest(
    root: Path, place: PrivateAddress, *, expose: str = "lan", extra: str = ""
) -> tuple[Path, ServerTls]:
    """Write a stand manifest exposing the remote listener on ``place`` under TLS.

    Args:
        root: The stand's directory.
        place: The private address to bind (port 0: the system picks) and its interface.
        expose: ``lan`` (mutual TLS by default) or ``vpn`` (the interface named as the overlay).
        extra: More ``[entrance]`` lines (``mutual_tls = true``, say).

    Returns:
        The manifest's path and the TLS files it names.
    """
    tls = server_tls(root / "tls", place.address)
    lines = [
        f'expose = "{expose}"',
        f'remote_bind = "{place.address}:0"',
        f'public_url = "https://{PUBLIC_NAME}"',
    ]
    # In vpn mode the machine's own interface plays the overlay, its one address the range.
    if expose == "vpn":
        lines += [f'vpn_interface = "{place.interface}"', f'vpn_cidrs = ["{place.address}/32"]']
    table = "\n".join(lines) + "\n" + extra
    table += f'\n[entrance.tls]\ncert = "{tls.cert_path}"\nkey = "{tls.key_path}"\n'
    return stand_manifest(root, table), tls


def _authority(key: ec.EllipticCurvePrivateKey, name: x509.Name, now: datetime) -> x509.Certificate:
    """A self-signed certificate authority that may sign server certificates only."""
    usage = x509.KeyUsage(
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
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _SKEW)
        .not_valid_after(now + _LIFETIME)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(usage, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )


def _is_bridge(name: str) -> bool:
    """Whether an interface is a container or VM bridge rather than the machine's own link."""
    return name.startswith(_BRIDGE_PREFIXES)


def _private_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    """A server key as the exposure check reads one: unencrypted PKCS#8 PEM."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
