"""Build the remote listener's TLS context from its plan, the Hive's authority and the revocations.

Every remote mode speaks TLS on a DNS name, with the operator's certificate (``tailscale cert``,
or their own domain's), and under mutual TLS the remote listener is the outer gate of two
(ADR-0041): a handshake completes only with a client certificate that chains to the Hive's own
authority and is not on its revocation list; login is the inner gate. ``server_context`` builds
that context from the plan's ``ListenerTls``. With mutual TLS it requires a certificate, checks
the leaf against the list (``VERIFY_CRL_CHECK_LEAF``, strict X.509 rules on top), speaks TLS 1.3
only and issues no session tickets, so no handshake can resume a session and skip the check (see
``ListenerTls.minimum_version``); without it, TLS 1.2 is the floor. The standard library can load
a revocation list only from a file, never from memory, so the authority's certificate and the
list are written to a private temporary directory, loaded, and deleted before this returns.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.tls``. Called by
    the Entrance's composition root when it starts the remote listener, and by
    ``ContextSwitch.rebuild`` on every revocation. Calls into ``ssl``, ``tempfile`` and
    ``cryptography``. Blocking (it reads the certificate files): call it off the event loop.

Key invariants:
    - A mutual-TLS context always holds exactly one trust anchor (the Hive's authority, never the
      system store) and one revocation list; an empty list still counts.
    - Nothing here writes a private key anywhere: only the authority's certificate and the list,
      both public, touch the temporary file.

See Also:
    - hivemind.entrance.expose.plan.ListenerTls for the settings applied here.
    - hivemind.entrance.expose.tls.switch for swapping a rebuilt context in.
"""

from __future__ import annotations

import ssl
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from hivemind.entrance.expose.plan import ListenerTls
from hivemind.entrance.expose.tls.authority import HiveAuthority

_TRUST_FILE = "hive-authority.pem"  # The authority's certificate and its list, for one load.

__all__ = ["server_context"]


def server_context(
    tls: ListenerTls, authority: HiveAuthority, crl: x509.CertificateRevocationList
) -> ssl.SSLContext:
    """Build the remote listener's server-side TLS context.

    Args:
        tls: The plan's TLS settings: certificate, key, and whether client certificates are
            required.
        authority: The Hive's certificate authority, the only trust anchor for client
            certificates.
        crl: The current revocation list from ``build_crl``; unused without mutual TLS.

    Returns:
        A context for ``PROTOCOL_TLS_SERVER`` with the configured certificate chain loaded.

    Raises:
        OSError: A certificate file could not be read.
        ssl.SSLError: The certificate chain or key is rejected by OpenSSL (the exposure check
            has already refused the usual causes).
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = tls.minimum_version
    context.load_cert_chain(tls.cert_path, tls.key_path)
    # Renegotiation lets a client restart a handshake mid-connection: no client needs it, and it
    # is a known denial-of-service lever.
    context.options |= ssl.OP_NO_RENEGOTIATION
    if tls.client_certificate_required:
        _require_client_certificates(context, authority, crl)
    return context


def _require_client_certificates(
    context: ssl.SSLContext, authority: HiveAuthority, crl: x509.CertificateRevocationList
) -> None:
    """Make ``context`` demand a non-revoked certificate from the Hive's authority."""
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF | ssl.VERIFY_X509_STRICT
    # No tickets (TLS 1.3 resumption) and no TLS 1.2 at all (session-id resumption): a resumed
    # session never re-checks the certificate, so it could outlive a revocation.
    context.options |= ssl.OP_NO_TICKET
    context.num_tickets = 0
    trust = authority.certificate_pem + crl.public_bytes(serialization.Encoding.PEM)
    # load_verify_locations reads a revocation list only from a file (cadata takes certificates
    # alone), so both go through a private directory that is gone before this returns.
    with tempfile.TemporaryDirectory(prefix="hive-tls-") as directory:
        path = Path(directory) / _TRUST_FILE
        path.write_bytes(trust)
        context.load_verify_locations(cafile=path)
