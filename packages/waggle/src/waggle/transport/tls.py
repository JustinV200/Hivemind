"""Provide the TLS pieces of a Waggle link: a server context, a pin-only client, the pin check.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) gives every
frame authenticity and integrity through signatures and leaves confidentiality to the link (spec
section 6), which off the Hive Stand's own machine is TLS on a ``wss://`` endpoint. The Hive
Stand (the machine the Queen, the central orchestrator, runs on) serves its swarm listener with a
certificate of its own, usually self-signed and often addressed by IP, so a certificate authority
and a host name prove nothing there. What a dialling node can know is the certificate itself: an
invite (``hive swarm invite``) carries its SHA-256 fingerprint, and the node pins it. The client
context here therefore trusts no authority and checks no host name; ``check_pin`` runs right
after the connection opens and compares the fingerprint of the certificate the peer actually
presented. A mismatch drops the connection before any Waggle frame is sent and raises
``TlsPinMismatchError``; the dial never retries it, because a different certificate is a different
machine or an attacker, not a flaky link. Everything here is standard-library ``ssl`` plus the one
websockets connection type the check reads the certificate from.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. ``server_ssl_context`` is called by the Hive Stand's
    composition root for the listener; ``pinned_client_ssl_context``, ``is_valid_pin`` and
    ``check_pin`` by waggle.transport.websocket_client on every pinned dial; calls into the
    standard library, the websockets library and waggle.errors.

Key invariants:
    - A pinned dial never completes a connection to a certificate other than the pinned one: the
      check either returns, or the connection is aborted and TlsPinMismatchError is raised.
    - A pin is exactly 64 lowercase hexadecimal characters, the SHA-256 of the certificate in DER
      form; no other spelling (uppercase, colons) is accepted, so two pins are equal as strings.
    - Both contexts speak TLS 1.2 or later; neither is ever built from user text.
    - The pin says which certificate the peer presented; the TLS handshake itself proves the peer
      holds that certificate's private key (it signs the handshake with it, whatever the verify
      mode). Waggle's signatures remain the identity of every frame, not the certificate.

See Also:
    - docs/waggle/spec.md sections 6 (confidentiality is the link's) and 9 (TLS pinning).
    - waggle.transport.websocket_client for the dial that applies a pin, and
      waggle.transport.websocket_server for the listener that serves the context.
    - waggle.uris for the rule that ws:// is valid only on loopback or an onion service.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import re
import ssl
from pathlib import Path

from websockets.asyncio.connection import Connection

from waggle.errors import TlsPinMismatchError

PIN_HEX_CHARS = 64  # A SHA-256 digest is 32 bytes, 64 hexadecimal characters.
ABORT_WAIT_S = 2.0  # After an abort the stream is gone at once; this only bounds a stuck loop.
_PIN_RE = re.compile(rf"[0-9a-f]{{{PIN_HEX_CHARS}}}")  # Compiled once; lowercase hex only.

__all__ = [
    "ABORT_WAIT_S",
    "PIN_HEX_CHARS",
    "certificate_sha256",
    "check_pin",
    "is_valid_pin",
    "peer_certificate_sha256",
    "pinned_client_ssl_context",
    "server_ssl_context",
]


def server_ssl_context(cert_path: Path | str, key_path: Path | str) -> ssl.SSLContext:
    """Build the TLS context a ``wss://`` listener serves with.

    Reads two small files, so call it at startup (a composition root), not on a hot path.

    Args:
        cert_path: A PEM file with the listener's certificate, and any chain behind it.
        key_path: A PEM file with that certificate's private key, unencrypted: the file's own
            mode (0600) is its protection.

    Returns:
        A server-side context, TLS 1.2 or later, presenting that certificate. It asks clients for
        no certificate: a node proves who it is with Waggle signatures, not with TLS.

    Raises:
        FileNotFoundError: A path does not exist.
        ssl.SSLError: The files are not a PEM certificate and the key that matches it.
    """
    # The stdlib's own server preset: compression off and a modern cipher list. The minimum
    # version is set again on purpose, so the floor is in this file rather than in a default
    # that a future Python could change.
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


def pinned_client_ssl_context() -> ssl.SSLContext:
    """Build the TLS context a pinned dial uses: no authority, no host name, the pin instead.

    Returns:
        A client-side context, TLS 1.2 or later, that accepts whatever certificate the peer
        presents. It is safe only because ``check_pin`` then compares that certificate with the
        pin before the connection is used; never use it without one.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # check_hostname must be switched off before verify_mode can be: the pair is the whole
    # point, since the pin names the exact certificate and a self-signed one has no authority
    # or reliable name to verify against.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def certificate_sha256(der: bytes) -> str:
    """Return the pin form of a certificate: the SHA-256 of its DER bytes, lowercase hex.

    Args:
        der: The certificate in DER form, as ``SSLObject.getpeercert(binary_form=True)`` returns
            it or ``Certificate.public_bytes(Encoding.DER)`` produces it.

    Returns:
        64 lowercase hexadecimal characters.
    """
    return hashlib.sha256(der).hexdigest()


def is_valid_pin(pin: str) -> bool:
    """Return whether ``pin`` is spelled as a pin must be: 64 lowercase hexadecimal characters.

    Args:
        pin: The candidate fingerprint.

    Returns:
        True only for the exact form ``certificate_sha256`` produces.
    """
    return _PIN_RE.fullmatch(pin) is not None


def peer_certificate_sha256(connection: Connection) -> str | None:
    """Return the fingerprint of the certificate the peer presented on an open TLS connection.

    Args:
        connection: A websockets connection whose TLS handshake has completed.

    Returns:
        The SHA-256 of the peer's certificate in DER form, or None when the connection is not
        TLS or the peer presented no certificate.
    """
    # asyncio hands the TLS object to the transport's extra info; a plaintext connection has
    # none, and a handshake without a certificate (an anonymous cipher) yields no DER bytes.
    ssl_object = connection.transport.get_extra_info("ssl_object")
    if not isinstance(ssl_object, ssl.SSLObject | ssl.SSLSocket):
        return None
    der = ssl_object.getpeercert(binary_form=True)
    return None if der is None else certificate_sha256(der)


async def check_pin(connection: Connection, pin: str) -> None:
    """Return when the peer's certificate is the pinned one; otherwise drop the link and raise.

    Args:
        connection: A websockets connection whose TLS and WebSocket handshakes have completed
            and over which no Waggle frame has yet been sent.
        pin: The expected fingerprint, in the form ``is_valid_pin`` accepts.

    Raises:
        TlsPinMismatchError: The peer presented another certificate, or none. The connection has
            been aborted by the time this is raised.
    """
    presented = peer_certificate_sha256(connection)
    # compare_digest is constant-time: a fingerprint is public, but nothing here should ever
    # teach a caller to compare secrets with ==.
    if presented is not None and hmac.compare_digest(presented.encode(), pin.encode()):
        return
    # Read before the abort, because websockets forgets the peer's address once the socket is
    # gone and the address is what an operator most wants in this message.
    peer = _peer(connection)
    # Abort rather than run the close handshake: this peer has not proved itself, so it gets no
    # further bytes, not even a polite close frame, and the stream is gone at once.
    connection.transport.abort()
    # Local wait for the loop to run the connection-lost callback: instant in practice. A hang
    # must not mask the mismatch, so a timeout is swallowed and the error below still raises.
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(ABORT_WAIT_S):
            await connection.wait_closed()
    shown = "no certificate" if presented is None else f"a certificate with sha256 {presented}"
    raise TlsPinMismatchError(
        f"The TLS peer ({peer}) presented {shown}, not the pinned sha256 {pin}; "
        "the connection was dropped before any frame was sent."
    )


def _peer(connection: Connection) -> str:
    """Render the connection's remote address as ``host:port`` for an error message."""
    address = connection.remote_address
    # A TCP peername is (host, port) for IPv4 and a longer tuple for IPv6; None once gone.
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return "address unknown"
