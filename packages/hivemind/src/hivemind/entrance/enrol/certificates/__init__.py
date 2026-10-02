"""A device's mutual-TLS client certificate: issued at approval, renewed, re-issued on loopback.

``issuing`` holds what the device's record keeps of its certificate and the certifier that signs
one from a request or seals one into a browser's bundle (ADR-0041). ``renewal`` holds the two flows
that give an approved device a fresh certificate later (ADR-0058): ``renew_certificate``, which an
enrolled program calls with its still-valid certificate and a request for its own key, and
``reissue_certificate``, which the operator calls on loopback for a device approved while the Hive
ran loopback only (it has none) or one whose certificate lapsed. This file is the face of
``issuing`` only: ``models`` imports it, and ``renewal`` records through ``record``, which imports
``models``, so the flows are re-exported by ``hivemind.entrance.enrol`` instead, after ``models``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Calls into
    ``hivemind.entrance.expose.tls``; ``renewal`` also into the enrolment records.

Key invariants:
    - This file holds re-exports and ``__all__`` only, and never imports ``renewal`` (the import
      cycle above).

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "receives its
      client certificate at approval".
    - docs/adr/0058-device-certificates-are-renewed-by-their-key-and-re-issued-on-loopback.md.

Public API:
    - CertificateRecord, CertificateChange, DeviceCertifier, IssuedBundle, withdrawn,
      new_bundle_passphrase, MAX_CERTIFICATE_PEM_CHARS, MAX_CERTIFICATE_REQUEST_CHARS,
      MAX_SUPERSEDED_CERTIFICATES, PASSPHRASE_GROUPS: the certificate a device's record keeps,
      a change to it, and what issues it (issuing).
"""

from hivemind.entrance.enrol.certificates.issuing import (
    MAX_CERTIFICATE_PEM_CHARS,
    MAX_CERTIFICATE_REQUEST_CHARS,
    MAX_SUPERSEDED_CERTIFICATES,
    PASSPHRASE_GROUPS,
    CertificateChange,
    CertificateRecord,
    DeviceCertifier,
    IssuedBundle,
    new_bundle_passphrase,
    withdrawn,
)

__all__ = [
    "MAX_CERTIFICATE_PEM_CHARS",
    "MAX_CERTIFICATE_REQUEST_CHARS",
    "MAX_SUPERSEDED_CERTIFICATES",
    "PASSPHRASE_GROUPS",
    "CertificateChange",
    "CertificateRecord",
    "DeviceCertifier",
    "IssuedBundle",
    "new_bundle_passphrase",
    "withdrawn",
]
