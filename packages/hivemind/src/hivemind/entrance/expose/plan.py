"""Decide which listeners the Hive Entrance runs, where, and with what TLS, or refuse to start.

The Hive Entrance (the Hive's one HTTP door) always runs its loopback listener, where approval
lives, and runs a remote listener only when ``[entrance] expose`` asks for one (ADR-0041):
``loopback`` runs nothing else; ``vpn`` (recommended) binds a specific address that must be inside
``vpn_cidrs`` and assigned to the overlay's own interface, because Tailscale's IPv4 range is also
carrier-grade NAT space a WAN port can hold; ``lan`` binds a private-network address this host
really has; ``tunnel`` binds loopback, where only the supervised tunnel client reaches it. Neither
``vpn`` nor ``lan`` ever binds a globally routable address, even one this host holds. Every remote
mode needs TLS on a DNS name (``public_url``'s host, covered by the configured certificate, with
``rp_id`` that host or a parent domain), and ``lan`` and ``tunnel`` need mutual TLS on top.
``plan_exposure`` is the pure check (codingrules 8.3): it reads the section and the gathered facts
and returns an ``ExposurePlan`` naming each listener's address and TLS settings, or raises
``ExposureRefusedError`` naming the one rule that failed. There is no public mode to plan: the
schema has no such value.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Called by the
    Entrance's composition root with facts from ``hivemind.entrance.expose.gather``; its plan
    drives the listeners, ``hivemind.entrance.expose.tls.server_context`` and the tunnel child.
    Calls into the sibling rule, name and fact modules and the manifest schema only.

Key invariants:
    - Every plan has the loopback listener, without TLS; only vpn, lan and tunnel have a remote
      listener, and it always has TLS.
    - A remote listener with mutual TLS requires a client certificate and TLS 1.3 (see
      ``ListenerTls.minimum_version`` for why not 1.2).
    - Rules are checked in one fixed order and the first broken one is raised: the mode's own
      switches, the remote listener's address, public_url and rp_id, then the TLS files.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Exposure never
      means the open internet".
    - hivemind.entrance.expose.rules for every rule and its sentence.
"""

from __future__ import annotations

import ipaddress
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn

from hivemind.entrance.expose.errors import ExposureRefusedError
from hivemind.entrance.expose.facts import ExposureFacts, FileState, HostPlatform
from hivemind.entrance.expose.interfaces import IPAddress, unscoped
from hivemind.entrance.expose.names import (
    certificate_covers,
    describe_public_host,
    is_public_suffix,
    is_same_or_parent_domain,
    public_host,
    public_origin,
)
from hivemind.entrance.expose.rules import ExposureRule
from hivemind.manifest.schema import EntranceExposure, EntranceSection
from hivemind.manifest.schema.entrance import split_host_port

# Tailscale's interface on the platforms where its name is fixed; macOS numbers its utun
# interfaces as they are created, so there (and anywhere else) vpn_interface must be named.
DEFAULT_OVERLAY_INTERFACES: Mapping[HostPlatform, str] = MappingProxyType(
    {HostPlatform.LINUX: "tailscale0", HostPlatform.WINDOWS: "Tailscale"}
)
# The modes the ADR puts behind mutual TLS whatever the manifest says.
_MUTUAL_TLS_MODES = frozenset({EntranceExposure.LAN, EntranceExposure.TUNNEL})
_NUL = "\x00"  # No operating system can pass this inside an argument to a new process.
# Each TLS file's pair of rules: unreadable first, then unparseable.
_CERT_FILE_RULES = (ExposureRule.TLS_CERT_UNREADABLE, ExposureRule.TLS_CERT_UNPARSEABLE)
_KEY_FILE_RULES = (ExposureRule.TLS_KEY_UNREADABLE, ExposureRule.TLS_KEY_UNPARSEABLE)

__all__ = [
    "DEFAULT_OVERLAY_INTERFACES",
    "ExposurePlan",
    "ListenerPlan",
    "ListenerTls",
    "plan_exposure",
]


@dataclass(frozen=True, slots=True)
class ListenerTls:
    """How a remote listener speaks TLS: its certificate, the name it serves, client certificates.

    Attributes:
        cert_path: The PEM certificate chain, already resolved against the manifest.
        key_path: The PEM private key for it.
        server_name: ``public_url``'s host, which the certificate was checked to cover.
        client_certificate_required: True under mutual TLS: a client certificate chaining to
            the Hive's own authority, and not revoked, is required on every handshake.
    """

    cert_path: Path
    key_path: Path
    server_name: str
    client_certificate_required: bool

    @property
    def minimum_version(self) -> ssl.TLSVersion:
        """TLS 1.3 under mutual TLS, TLS 1.2 otherwise.

        WHY: a TLS 1.2 client can resume a cached session by id, and a resumed handshake never
        re-checks the client certificate, so a device revoked since then would be let back in
        past the rebuilt revocation list. TLS 1.3 resumes only with tickets, which the mutual
        TLS context never issues; every current browser and TLS library speaks 1.3.
        """
        if self.client_certificate_required:
            return ssl.TLSVersion.TLSv1_3
        return ssl.TLSVersion.TLSv1_2


@dataclass(frozen=True, slots=True)
class ListenerPlan:
    """Where one listener binds and whether it speaks TLS.

    Attributes:
        host: The address or loopback name to bind, as the manifest wrote it.
        port: The TCP port; 0 lets the operating system choose (tests).
        tls: The TLS settings; None only on the loopback listener.
    """

    host: str
    port: int
    tls: ListenerTls | None


@dataclass(frozen=True, slots=True)
class ExposurePlan:
    """Every listener the Entrance runs and what the remote one is known by.

    Attributes:
        mode: The ``[entrance] expose`` value this plan honours.
        loopback: The loopback listener, present in every mode.
        remote: The remote listener, or None in loopback mode.
        public_origin: The one origin CORS allows (``https://host[:port]``), or None.
        rp_id: The WebAuthn relying party id for devices enrolled on the remote listener, or
            None in loopback mode (the loopback listener's is ``localhost``).
        tunnel_argv: The tunnel client's argv in tunnel mode; empty otherwise.
    """

    mode: EntranceExposure
    loopback: ListenerPlan
    remote: ListenerPlan | None
    public_origin: str | None
    rp_id: str | None
    tunnel_argv: tuple[str, ...]


def plan_exposure(section: EntranceSection, facts: ExposureFacts) -> ExposurePlan:
    """Check ``[entrance]`` against this host and plan the listeners, or refuse.

    Args:
        section: The manifest's ``[entrance]`` section.
        facts: What ``gather_facts`` found on this host (or what a test says it found).

    Returns:
        The plan: always the loopback listener; in a remote mode also the remote listener with
        its TLS settings, the CORS origin, the relying party and, in tunnel mode, the argv.

    Raises:
        ExposureRefusedError: The mode's prerequisites do not hold; its ``rule`` names the first
            one broken, in the order the module docstring gives.

    Example:
        ``plan_exposure(EntranceSection(), facts).remote`` is None: the default manifest is
        loopback only, whatever the host looks like.
    """
    loopback_host, loopback_port = split_host_port(section.bind)
    loopback = ListenerPlan(host=loopback_host, port=loopback_port, tls=None)
    # Loopback is the default and needs nothing else: no remote listener, no TLS, no name.
    if section.expose is EntranceExposure.LOOPBACK:
        return ExposurePlan(
            mode=section.expose,
            loopback=loopback,
            remote=None,
            public_origin=None,
            rp_id=None,
            tunnel_argv=(),
        )
    _check_switches(section)
    remote_host, remote_port = _remote_address(section, facts)
    host, rp_id = _public_name(section)
    remote = ListenerPlan(remote_host, remote_port, _listener_tls(section, facts, host))
    tunnel_argv = section.tunnel_command if section.expose is EntranceExposure.TUNNEL else ()
    return ExposurePlan(
        mode=section.expose,
        loopback=loopback,
        remote=remote,
        public_origin=public_origin(section.public_url),
        rp_id=rp_id,
        tunnel_argv=tunnel_argv,
    )


# ──────────────────────────────────────────────────────────────────────────────
# The mode's switches and the remote listener's address
# ──────────────────────────────────────────────────────────────────────────────


def _check_switches(section: EntranceSection) -> None:
    """Refuse a mode whose own settings are off: mutual TLS for lan and tunnel, a tunnel argv."""
    if section.expose in _MUTUAL_TLS_MODES and not section.mutual_tls:
        _refuse(section, ExposureRule.MUTUAL_TLS_REQUIRED, "mutual_tls is false.")
    # The argv is never echoed: it goes to a process, and an operator may have misplaced a token.
    if section.expose is EntranceExposure.TUNNEL and not _usable_argv(section.tunnel_command):
        _refuse(
            section,
            ExposureRule.TUNNEL_COMMAND_MISSING,
            f"tunnel_command has {len(section.tunnel_command)} entries.",
        )


def _usable_argv(argv: Sequence[str]) -> bool:
    """Return whether ``argv`` can start a process: a program name, and no NUL anywhere."""
    return bool(argv) and bool(argv[0]) and not any(_NUL in argument for argument in argv)


def _remote_address(section: EntranceSection, facts: ExposureFacts) -> tuple[str, int]:
    """Check ``remote_bind`` against the mode's address rule and return its host and port."""
    if not section.remote_bind:
        _refuse(section, ExposureRule.REMOTE_BIND_MISSING, "remote_bind is empty.")
    host, port = split_host_port(section.remote_bind)
    # The schema already proved the host is a specific IP literal; only its zone is dropped.
    address = unscoped(ipaddress.ip_address(host))
    match section.expose:
        case EntranceExposure.VPN:
            _check_vpn_address(section, facts, address)
        case EntranceExposure.LAN:
            _check_lan_address(section, facts, address)
        case _:
            _check_tunnel_address(section, address, port)
    return host, port


def _check_vpn_address(section: EntranceSection, facts: ExposureFacts, address: IPAddress) -> None:
    """Refuse a vpn address that is loopback, global, outside the overlay or off its interface."""
    where = f"remote_bind is {section.remote_bind}"
    if address.is_loopback:
        _refuse(section, ExposureRule.REMOTE_BIND_LOOPBACK, f"{where}.")
    # Checked before the ranges, since vpn_cidrs is the operator's to widen: whatever it says, an
    # address the whole internet can route to is never an overlay-only door (ADR-0041).
    if address.is_global:
        _refuse(section, ExposureRule.VPN_BIND_GLOBAL, f"{where}.")
    if not any(address in ipaddress.ip_network(cidr) for cidr in section.vpn_cidrs):
        ranges = ", ".join(section.vpn_cidrs) or "empty"
        _refuse(section, ExposureRule.VPN_BIND_OUTSIDE_CIDRS, f"{where}; vpn_cidrs is {ranges}.")
    name = section.vpn_interface or DEFAULT_OVERLAY_INTERFACES.get(facts.platform)
    if name is None:
        detail = f"This host is {facts.platform.value} and vpn_interface is empty."
        _refuse(section, ExposureRule.VPN_INTERFACE_UNNAMED, detail)
    interface = facts.interface(name)
    if interface is None:
        _refuse(section, ExposureRule.VPN_INTERFACE_ABSENT, f"No interface is named {name!r}.")
    # The check that counts (ADR-0041): the address must be the overlay's own, not merely in
    # its range, because 100.64.0.0/10 is also carrier-grade NAT space on ordinary WAN ports.
    if address not in interface.addresses:
        held = ", ".join(sorted(str(item) for item in interface.addresses)) or "no address"
        _refuse(section, ExposureRule.VPN_BIND_NOT_ON_INTERFACE, f"{where}; {name} has {held}.")


def _check_lan_address(section: EntranceSection, facts: ExposureFacts, address: IPAddress) -> None:
    """Refuse a lan address that is loopback, not private, or not assigned to this host."""
    where = f"remote_bind is {section.remote_bind}"
    if address.is_loopback:
        _refuse(section, ExposureRule.REMOTE_BIND_LOOPBACK, f"{where}.")
    # A VPS's public address, a LAN's global IPv6 address or a CGNAT WAN address is on an
    # interface of this host too, but reachable from beyond the local network: never lan's.
    if not address.is_private:
        _refuse(section, ExposureRule.LAN_BIND_NOT_PRIVATE, f"{where}.")
    if not any(address in entry.addresses for entry in facts.interfaces):
        _refuse(section, ExposureRule.LAN_BIND_NOT_LOCAL, f"{where}; no interface here has it.")


def _check_tunnel_address(section: EntranceSection, address: IPAddress, port: int) -> None:
    """Refuse a tunnel address off loopback, or on the loopback listener's own port."""
    if not address.is_loopback:
        detail = f"remote_bind is {section.remote_bind}."
        _refuse(section, ExposureRule.TUNNEL_BIND_NOT_LOOPBACK, detail)
    _, loopback_port = split_host_port(section.bind)
    # Port 0 asks the operating system for a free port, so two of them never collide.
    if port != 0 and port == loopback_port:
        detail = f"remote_bind is {section.remote_bind} and bind is {section.bind}."
        _refuse(section, ExposureRule.TUNNEL_BIND_CLASHES, detail)


# ──────────────────────────────────────────────────────────────────────────────
# The name devices use, and TLS on it
# ──────────────────────────────────────────────────────────────────────────────


def _public_name(section: EntranceSection) -> tuple[str, str]:
    """Return ``public_url``'s host and the effective relying party, or refuse."""
    if not section.public_url:
        _refuse(section, ExposureRule.PUBLIC_URL_MISSING, "public_url is empty.")
    host = public_host(section.public_url)
    if host is None:
        detail = describe_public_host(section.public_url)
        _refuse(section, ExposureRule.PUBLIC_URL_NOT_A_NAME, detail)
    # An empty rp_id means public_url's host itself (the schema's documented default).
    rp_id = section.rp_id.lower().removesuffix(".") or host
    if not is_same_or_parent_domain(rp_id, host):
        detail = f"rp_id is {section.rp_id!r} and public_url's host is {host!r}."
        _refuse(section, ExposureRule.RP_ID_OUTSIDE_PUBLIC_URL, detail)
    # A parent domain can still be one every browser refuses: a suffix shared by strangers.
    if is_public_suffix(rp_id):
        _refuse(section, ExposureRule.RP_ID_PUBLIC_SUFFIX, f"rp_id is {rp_id!r}.")
    return host, rp_id


def _listener_tls(section: EntranceSection, facts: ExposureFacts, host: str) -> ListenerTls:
    """Check the TLS files' facts in order and build the remote listener's TLS settings."""
    tls = facts.tls
    cert_path, key_path = tls.cert_path, tls.key_path
    if cert_path is None or key_path is None:
        detail = "[entrance.tls] cert or key is empty."
        _refuse(section, ExposureRule.TLS_NOT_CONFIGURED, detail)
    _check_file(section, tls.cert, cert_path, _CERT_FILE_RULES)
    _check_file(section, tls.key, key_path, _KEY_FILE_RULES)
    if not tls.key_matches_cert:
        detail = f"cert is {cert_path} and key is {key_path}."
        _refuse(section, ExposureRule.TLS_KEY_MISMATCH, detail)
    _check_validity(section, facts)
    if not certificate_covers(tls.dns_names, host):
        names = ", ".join(tls.dns_names) or "no DNS name"
        detail = f"public_url's host is {host!r}; the certificate names {names}."
        _refuse(section, ExposureRule.TLS_NAME_MISMATCH, detail)
    return ListenerTls(cert_path, key_path, host, section.mutual_tls)


def _check_file(
    section: EntranceSection,
    state: FileState,
    path: Path,
    rules: tuple[ExposureRule, ExposureRule],
) -> None:
    """Refuse a TLS file that could not be read, or read but not parsed (``rules`` in order)."""
    unreadable, unparseable = rules
    if state is FileState.UNREADABLE:
        _refuse(section, unreadable, f"The file is {path}.")
    if state is FileState.UNPARSEABLE:
        _refuse(section, unparseable, f"The file is {path}.")


def _check_validity(section: EntranceSection, facts: ExposureFacts) -> None:
    """Refuse a server certificate that has expired or is not valid yet."""
    start, end = facts.tls.not_before, facts.tls.not_after
    if start is None or end is None or not start <= facts.now <= end:
        detail = f"It is valid from {start} to {end}, and it is now {facts.now}."
        _refuse(section, ExposureRule.TLS_CERT_NOT_CURRENT, detail)


def _refuse(section: EntranceSection, rule: ExposureRule, detail: str) -> NoReturn:
    """Raise the refusal for ``rule`` in ``section``'s mode."""
    raise ExposureRefusedError(section.expose, rule, detail)
