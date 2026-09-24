"""Name every rule the exposure check enforces, each with the one sentence a refusal quotes.

``[entrance] expose`` chooses how the Hive Entrance (the Hive's one HTTP door) is reached from
other machines: ``loopback`` (not at all), ``vpn``, ``lan`` or ``tunnel`` (ADR-0033). Each remote
mode has prerequisites the manifest schema cannot check on its own, because they depend on this
host (its interfaces, its TLS files) or on several fields at once; ``plan_exposure`` checks them
when the Entrance starts and refuses with exactly one ``ExposureRule``. The rules live here, apart
from the check, so a test can walk every one of them and an operator reading a refusal gets the
same sentence every time. Each member's value is its stable, lowercase code.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Read by
    ``hivemind.entrance.expose.plan`` (which raises them) and ``hivemind.entrance.expose.errors``
    (which quotes them). Imports nothing.

Key invariants:
    - Every member has a requirement sentence; a test asserts none is missing.
    - A sentence names settings and rules only, never a value read from a file or the
      environment, so a refusal can never quote a secret.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Exposure never
      means the open internet".
    - hivemind.entrance.expose.plan for the order the rules are checked in.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["ExposureRule"]


class ExposureRule(Enum):
    """Every reason the Entrance refuses to start exposed; a refusal names exactly one."""

    # The mode's own switches.
    MUTUAL_TLS_REQUIRED = "mutual_tls_required"
    TUNNEL_COMMAND_MISSING = "tunnel_command_missing"
    # Where the remote listener binds.
    REMOTE_BIND_MISSING = "remote_bind_missing"
    REMOTE_BIND_LOOPBACK = "remote_bind_loopback"
    VPN_BIND_GLOBAL = "vpn_bind_global"
    VPN_BIND_OUTSIDE_CIDRS = "vpn_bind_outside_cidrs"
    VPN_INTERFACE_UNNAMED = "vpn_interface_unnamed"
    VPN_INTERFACE_ABSENT = "vpn_interface_absent"
    VPN_BIND_NOT_ON_INTERFACE = "vpn_bind_not_on_interface"
    LAN_BIND_NOT_PRIVATE = "lan_bind_not_private"
    LAN_BIND_NOT_LOCAL = "lan_bind_not_local"
    TUNNEL_BIND_NOT_LOOPBACK = "tunnel_bind_not_loopback"
    TUNNEL_BIND_CLASHES = "tunnel_bind_clashes"
    # The name devices reach it by.
    PUBLIC_URL_MISSING = "public_url_missing"
    PUBLIC_URL_NOT_A_NAME = "public_url_not_a_name"
    RP_ID_OUTSIDE_PUBLIC_URL = "rp_id_outside_public_url"
    RP_ID_PUBLIC_SUFFIX = "rp_id_public_suffix"
    # TLS on that name.
    TLS_NOT_CONFIGURED = "tls_not_configured"
    TLS_CERT_UNREADABLE = "tls_cert_unreadable"
    TLS_CERT_UNPARSEABLE = "tls_cert_unparseable"
    TLS_KEY_UNREADABLE = "tls_key_unreadable"
    TLS_KEY_UNPARSEABLE = "tls_key_unparseable"
    TLS_KEY_MISMATCH = "tls_key_mismatch"
    TLS_CERT_NOT_CURRENT = "tls_cert_not_current"
    TLS_NAME_MISMATCH = "tls_name_mismatch"

    @property
    def requirement(self) -> str:
        """The rule as one sentence an operator can act on, without a trailing full stop."""
        return _REQUIREMENTS[self]


# One sentence per rule. Kept beside the enum rather than in the values so the value stays a
# short, stable code a test or a log filter can match on.
_REQUIREMENTS: dict[ExposureRule, str] = {
    ExposureRule.MUTUAL_TLS_REQUIRED: (
        "lan and tunnel require mutual_tls = true, a client certificate from the Hive's own "
        "authority on top of login"
    ),
    ExposureRule.TUNNEL_COMMAND_MISSING: (
        "tunnel needs tunnel_command, the tunnel client's argv: a program name first, and no "
        "NUL character anywhere"
    ),
    ExposureRule.REMOTE_BIND_MISSING: (
        "every remote mode needs remote_bind, the remote listener's address as host:port"
    ),
    ExposureRule.REMOTE_BIND_LOOPBACK: (
        "vpn and lan bind the remote listener to a real interface, never to loopback: only "
        "tunnel binds loopback, and only behind mutual TLS"
    ),
    ExposureRule.VPN_BIND_GLOBAL: (
        "vpn binds an overlay address the open internet cannot route to, never a globally "
        "routable one: exposure never means the open internet"
    ),
    ExposureRule.VPN_BIND_OUTSIDE_CIDRS: (
        "vpn needs remote_bind inside one of vpn_cidrs, the overlay's address ranges"
    ),
    ExposureRule.VPN_INTERFACE_UNNAMED: (
        "vpn needs vpn_interface named on this platform: only Linux (tailscale0) and Windows "
        "(Tailscale) have a default, so macOS names its utun interface"
    ),
    ExposureRule.VPN_INTERFACE_ABSENT: (
        "vpn needs its overlay interface to exist on this host, so the VPN must be up first"
    ),
    ExposureRule.VPN_BIND_NOT_ON_INTERFACE: (
        "vpn needs remote_bind assigned to the overlay interface itself: an address inside "
        "vpn_cidrs on any other interface may be carrier-grade NAT space on the open internet"
    ),
    ExposureRule.LAN_BIND_NOT_PRIVATE: (
        "lan binds a private-network address (RFC 1918, link-local, or an IPv6 unique local "
        "address in fd00::/8): a globally routable address, or carrier-grade NAT space on a WAN "
        "port, is reachable from beyond the local network"
    ),
    ExposureRule.LAN_BIND_NOT_LOCAL: (
        "lan needs remote_bind assigned to one of this host's network interfaces"
    ),
    ExposureRule.TUNNEL_BIND_NOT_LOOPBACK: (
        "tunnel binds the remote listener to loopback, where only the local tunnel client "
        "reaches it"
    ),
    ExposureRule.TUNNEL_BIND_CLASHES: (
        "tunnel needs remote_bind on a different port from bind, since both listeners are on "
        "loopback"
    ),
    ExposureRule.PUBLIC_URL_MISSING: (
        "every remote mode needs public_url, the https URL devices reach the Entrance by"
    ),
    ExposureRule.PUBLIC_URL_NOT_A_NAME: (
        "public_url must name a DNS host: browsers need TLS on a name, and WebAuthn refuses an "
        "IP address or a loopback name as its relying party"
    ),
    ExposureRule.RP_ID_OUTSIDE_PUBLIC_URL: (
        "rp_id must be public_url's host or a parent domain of it, or every passkey ceremony "
        "on the remote listener fails"
    ),
    ExposureRule.RP_ID_PUBLIC_SUFFIX: (
        "rp_id must be a domain you control, never a public suffix such as ts.net, which "
        "browsers refuse as a relying party; use the full MagicDNS name or your own domain"
    ),
    ExposureRule.TLS_NOT_CONFIGURED: (
        "every remote mode needs [entrance.tls] cert and key, because remote always means TLS "
        "on a DNS name"
    ),
    ExposureRule.TLS_CERT_UNREADABLE: "[entrance.tls] cert must be a readable file",
    ExposureRule.TLS_CERT_UNPARSEABLE: (
        "[entrance.tls] cert must hold a PEM certificate chain, the server's own certificate first"
    ),
    ExposureRule.TLS_KEY_UNREADABLE: "[entrance.tls] key must be a readable file",
    ExposureRule.TLS_KEY_UNPARSEABLE: (
        "[entrance.tls] key must hold an unencrypted PEM private key"
    ),
    ExposureRule.TLS_KEY_MISMATCH: (
        "[entrance.tls] key must be the private key of the certificate's public key"
    ),
    ExposureRule.TLS_CERT_NOT_CURRENT: (
        "the TLS certificate must be valid now; renew it (tailscale cert, or your own CA)"
    ),
    ExposureRule.TLS_NAME_MISMATCH: (
        "the TLS certificate's DNS names must cover public_url's host"
    ),
}
