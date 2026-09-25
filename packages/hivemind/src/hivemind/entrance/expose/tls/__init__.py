"""Run the Hive's own certificate authority for mutual TLS, and the remote listener's TLS context.

Under mutual TLS (``lan`` and ``tunnel`` always, ``vpn`` when ``mutual_tls`` is on) the remote
listener admits only devices holding a client certificate from the Hive's own authority, and
login is the gate behind that one (ADR-0041). ``authority`` creates the authority once (its key in
the secret store) and loads it after; ``issue`` signs a device's certificate from its request, or
seals a fresh key and certificate into a PKCS#12 bundle for a browser; ``revocation`` builds the
list of certificates taken back; ``context`` builds the listener's TLS context from the plan, the
authority and the list; ``switch`` swaps a rebuilt context in on every revocation through the
listener context's ``sni_callback``. This file is the face.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Built by the
    Entrance's composition root; ``issue`` is called at approval, ``revocation`` and ``switch`` on
    revocation. Calls into ``cryptography``, ``ssl`` and the secret store.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - The authority's private key lives in the secret store and inside ``HiveAuthority`` only.
    - A mutual-TLS context trusts only the Hive's authority, always carries a revocation list,
      speaks TLS 1.3 only and never resumes a session.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "lan and tunnel:
      mutual TLS on top".
    - hivemind.entrance.expose.plan for the TLS settings a context is built from.

Public API:
    - HiveAuthority, load_or_create_authority, CA_KEY_NAME, CA_CERT_NAME, CA_VALIDITY,
      NOT_BEFORE_SKEW: the authority and where it is kept.
    - issue_client_certificate, issue_pkcs12, check_certificate_request, DeviceCertificate,
      DeviceBundle, CLIENT_CERT_VALIDITY, MIN_PASSPHRASE_CHARS, MIN_RSA_BITS, MAX_CSR_BYTES,
      PKCS12_KDF_ROUNDS: device certificates.
    - build_crl, RevokedSerial: the revocation list.
    - server_context: the remote listener's TLS context.
    - ContextSwitch: swaps a rebuilt context in without restarting the listener.
"""

from hivemind.entrance.expose.tls.authority import (
    CA_CERT_NAME,
    CA_KEY_NAME,
    CA_VALIDITY,
    NOT_BEFORE_SKEW,
    HiveAuthority,
    load_or_create_authority,
)
from hivemind.entrance.expose.tls.context import server_context
from hivemind.entrance.expose.tls.issue import (
    CLIENT_CERT_VALIDITY,
    MAX_CSR_BYTES,
    MIN_PASSPHRASE_CHARS,
    MIN_RSA_BITS,
    PKCS12_KDF_ROUNDS,
    DeviceBundle,
    DeviceCertificate,
    check_certificate_request,
    issue_client_certificate,
    issue_pkcs12,
)
from hivemind.entrance.expose.tls.revocation import RevokedSerial, build_crl
from hivemind.entrance.expose.tls.switch import ContextSwitch

__all__ = [
    "CA_CERT_NAME",
    "CA_KEY_NAME",
    "CA_VALIDITY",
    "CLIENT_CERT_VALIDITY",
    "MAX_CSR_BYTES",
    "MIN_PASSPHRASE_CHARS",
    "MIN_RSA_BITS",
    "NOT_BEFORE_SKEW",
    "PKCS12_KDF_ROUNDS",
    "ContextSwitch",
    "DeviceBundle",
    "DeviceCertificate",
    "HiveAuthority",
    "RevokedSerial",
    "build_crl",
    "check_certificate_request",
    "issue_client_certificate",
    "issue_pkcs12",
    "load_or_create_authority",
    "server_context",
]
