"""Expose the Hive Entrance beyond loopback only as ADR-0033 allows: never on the open internet.

The Hive Entrance (the Hive's one HTTP door) always runs its loopback listener, where approval
lives; ``[entrance] expose`` may add a remote listener: ``vpn`` (recommended: an overlay address on
the overlay's own interface), ``lan`` or ``tunnel`` (both behind mutual TLS), each with TLS on a
DNS name, and there is no public mode. This package decides and enforces that. ``gather`` reads
the host (``interfaces`` over ``psutil``, the TLS files) into frozen ``facts``; ``plan`` is the pure
check that turns the section and the facts into an ``ExposurePlan`` or refuses with one of the
``rules`` (``names`` answers its DNS questions); ``tls`` runs the Hive's own certificate authority
and builds the remote listener's context, swapping a rebuilt one in on every revocation;
``tunnel`` supervises the tunnel client, the one Entrance module that starts a process;
``loopback`` keeps anything from fronting the loopback listener; ``errors`` holds the refusals.
This file is the face.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the entrance package. Called by the
    Entrance's composition root when it starts its listeners, by the loopback listener's
    middleware, by approval (device certificates) and by revocation. Calls into
    ``hivemind.manifest`` (the section and the tunnel variables), ``hivemind.common`` (the
    secret store, logging), waggle, ``cryptography``, ``psutil`` and the standard library.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No listener starts that ``plan_exposure`` did not plan, and every remote listener has TLS
      on a DNS name; lan and tunnel also have mutual TLS.
    - No private key, passphrase or tunnel token appears in a log line, an error or a ``repr``.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Exposure never
      means the open internet".
    - .claude/codingrules.md section 8.15 ("Never on the open internet").

Public API:
    - plan_exposure, ExposurePlan, ListenerPlan, ListenerTls, DEFAULT_OVERLAY_INTERFACES: the
      pure mode check and its plan.
    - ExposureRule: every refusal the check can make.
    - ExposureFacts, TlsFacts, FileState, HostPlatform, host_platform: the facts it reads.
    - gather_facts, read_tls_facts, PathResolver, MAX_TLS_FILE_BYTES: reading them from the host.
    - LocalInterfaces, InterfaceAddresses, SystemInterfaces, FakeInterfaces: the interface seam
      (the rest via hivemind.entrance.expose.interfaces).
    - public_host, public_origin, certificate_covers, is_same_or_parent_domain, is_dns_name: the
      name rules.
    - loopback_request_allowed: the loopback listener's Host and forwarding-header check.
    - TunnelSupervisor, tunnel_environment: the supervised tunnel client.
    - HiveAuthority, load_or_create_authority, issue_client_certificate, issue_pkcs12,
      check_certificate_request, DeviceCertificate, DeviceBundle, build_crl, RevokedSerial,
      server_context, ContextSwitch: mutual TLS (the rest via hivemind.entrance.expose.tls).
    - ExposeError, ExposureRefusedError, CertificateAuthorityError, CertificateRequestError,
      CertificateIssueError: every refusal this package makes on purpose.
"""

from hivemind.entrance.expose.errors import (
    CertificateAuthorityError,
    CertificateIssueError,
    CertificateRequestError,
    ExposeError,
    ExposureRefusedError,
)
from hivemind.entrance.expose.facts import (
    ExposureFacts,
    FileState,
    HostPlatform,
    TlsFacts,
    host_platform,
)
from hivemind.entrance.expose.gather import (
    MAX_TLS_FILE_BYTES,
    PathResolver,
    gather_facts,
    read_tls_facts,
)
from hivemind.entrance.expose.interfaces import (
    FakeInterfaces,
    InterfaceAddresses,
    LocalInterfaces,
    SystemInterfaces,
)
from hivemind.entrance.expose.loopback import loopback_request_allowed
from hivemind.entrance.expose.names import (
    certificate_covers,
    is_dns_name,
    is_same_or_parent_domain,
    public_host,
    public_origin,
)
from hivemind.entrance.expose.plan import (
    DEFAULT_OVERLAY_INTERFACES,
    ExposurePlan,
    ListenerPlan,
    ListenerTls,
    plan_exposure,
)
from hivemind.entrance.expose.rules import ExposureRule
from hivemind.entrance.expose.tls import (
    ContextSwitch,
    DeviceBundle,
    DeviceCertificate,
    HiveAuthority,
    RevokedSerial,
    build_crl,
    check_certificate_request,
    issue_client_certificate,
    issue_pkcs12,
    load_or_create_authority,
    server_context,
)
from hivemind.entrance.expose.tunnel import TunnelSupervisor, tunnel_environment

__all__ = [
    "DEFAULT_OVERLAY_INTERFACES",
    "MAX_TLS_FILE_BYTES",
    "CertificateAuthorityError",
    "CertificateIssueError",
    "CertificateRequestError",
    "ContextSwitch",
    "DeviceBundle",
    "DeviceCertificate",
    "ExposeError",
    "ExposureFacts",
    "ExposurePlan",
    "ExposureRefusedError",
    "ExposureRule",
    "FakeInterfaces",
    "FileState",
    "HiveAuthority",
    "HostPlatform",
    "InterfaceAddresses",
    "ListenerPlan",
    "ListenerTls",
    "LocalInterfaces",
    "PathResolver",
    "RevokedSerial",
    "SystemInterfaces",
    "TlsFacts",
    "TunnelSupervisor",
    "build_crl",
    "certificate_covers",
    "check_certificate_request",
    "gather_facts",
    "host_platform",
    "is_dns_name",
    "is_same_or_parent_domain",
    "issue_client_certificate",
    "issue_pkcs12",
    "load_or_create_authority",
    "loopback_request_allowed",
    "plan_exposure",
    "public_host",
    "public_origin",
    "read_tls_facts",
    "server_context",
    "tunnel_environment",
]
