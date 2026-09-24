"""Check a WebSocket URI Waggle may dial: wss:// anywhere, ws:// on loopback or an onion service.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) provides
authenticity and integrity through signatures, never confidentiality: the link does that (spec
section 6), which is TLS on a ``wss://`` endpoint, the Cell tier's VPN, or Tor. A plaintext
``ws://`` endpoint is therefore valid only when both ends are the same machine, that is when its
host is a loopback address. The rule is stated once here because three places apply it: the
``control.queen_moved`` notice that carries the Queen's new address (the one URL that crosses
Waggle), the WebSocket client transport that dials an address, and the Hive Manifest (the TOML
file that configures one Hive) that names the listener. Only ``localhost`` and a literal loopback
IP count: a DNS name that happens to resolve to loopback would need a lookup, which is a side
effect a validator must not have, and a name is not proof of where the traffic goes.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.messages.control.hive (QueenMoved's
    validator), by the WebSocket client transport before it dials, and by the manifest loader;
    calls into the standard library only.

Key invariants:
    - check_waggle_uri returns its input unchanged or raises ValueError with a full sentence;
      it never performs a name lookup or any other I/O.
    - Every URI it accepts matches WAGGLE_URI_PATTERN, and every ws:// URI it accepts has a
      loopback host or a v3 onion service host, unless allow_virtual_cell_gateway_host widens
      that (see below).

Roadmap step 5.6 (this branch): a Virtual Cell's control link is the one deliberate exception to
"ws:// only on loopback". A container reaches the Hive Stand through a host-gateway alias
(``host.docker.internal`` on Docker Desktop, ``10.0.2.2`` on QEMU's own SLIRP networking) or a
private address the operator's own network assigns it, never through loopback -- loopback inside
the container is the container itself. Signing every frame (mandatory, unconditionally) already
gives the link authenticity and integrity; what loopback-only normally buys beyond that is "no
other process on this machine, or reachable from it over an untrusted network, can pretend to be
the peer", a guarantee a private, operator-controlled network segment (RFC 1918, link-local, or
one of the two documented gateway aliases) still gives in practice, and the public internet does
not. ``check_waggle_uri``'s new ``allow_virtual_cell_gateway_host`` keyword, default ``False``,
is that widened rule -- opt-in, per call site, never the default -- and ``queen.cell_gate`` and
the Docker/QEMU backends are its only intended callers.

Roadmap step 10.3a: a Tor v3 onion service is the other place a plaintext ``ws://`` link keeps
Waggle's promise, always and with no opt-in. A Night Veil Cell reaches the Hive Stand only at the
Hive Stand's onion service (ADR-0030), and an onion address is the service's own public key: Tor
encrypts the connection end to end and authenticates the service by that key, so no one between
the Cell and the Hive Stand can read the link or answer in the service's place, which is exactly
what TLS on ``wss://`` would add. ``is_onion_service_host`` accepts only a well-formed v3 address
(56 base32 characters encoding the key, a checksum and version 3, then ``.onion``), so a typo or a
look-alike name is refused rather than dialled; an onion service is reachable only through Tor's
SOCKS proxy, which ``waggle.transport.websocket_client`` requires for one and never bypasses.

See Also:
    - docs/waggle/spec.md section 6 ("Confidentiality is the link's") for the rule.
    - waggle.messages.control.hive for QueenMoved, the message that carries an address.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why a Virtual
      Cell dials out at all, and hivemind.queen.cell_gate for this rule's one production caller.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

WAGGLE_URI_PATTERN = r"^wss?://\S+$"  # A WebSocket URI; ws:// is further restricted by host.
LOOPBACK_HOST_NAME = "localhost"  # The one loopback host that is a name rather than an address.
ONION_SUFFIX = ".onion"  # Tor's reserved top-level name (RFC 7686): reachable only through Tor.
ONION_V3_ID_CHARS = 56  # base32 of a 32-byte key, a 2-byte checksum and a version byte.
_PLAINTEXT_SCHEME = "ws"  # No TLS on the link; the scheme that needs the host rule.
_ONION_V3_VERSION = 3  # The address version this module accepts (rend-spec-v3).
_ONION_KEY_BYTES = 32  # The service's ed25519 public key, first in the decoded address.
_ONION_CHECKSUM_BYTES = 2  # The truncated SHA3-256 checksum that follows it.
_ONION_CHECKSUM_TAG = b".onion checksum"  # rend-spec-v3's constant prefix for the checksum.
_BASE32_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz234567")  # RFC 4648, lowercase.

# The documented host-gateway alias names a Virtual Cell backend resolves to reach the Hive Stand
# from inside a container: Docker Desktop's own DNS entry for the host, and the fixed IP the same
# alias also answers to on Docker's Linux bridge networking (both point at the host, never at
# another container or the outside world). QEMU's own SLIRP gateway (10.0.2.2) is already an IP,
# so it needs no name entry here -- it is covered by the RFC 1918 check in
# is_virtual_cell_gateway_host below.
VIRTUAL_CELL_GATEWAY_HOST_NAMES = frozenset({"host.docker.internal"})

__all__ = [
    "LOOPBACK_HOST_NAME",
    "ONION_SUFFIX",
    "ONION_V3_ID_CHARS",
    "VIRTUAL_CELL_GATEWAY_HOST_NAMES",
    "WAGGLE_URI_PATTERN",
    "check_waggle_uri",
    "is_loopback_host",
    "is_onion_service_host",
    "is_virtual_cell_gateway_host",
]

_URI_RE = re.compile(WAGGLE_URI_PATTERN)  # Compiled once; every check matches against it.


def check_waggle_uri(uri: str, *, allow_virtual_cell_gateway_host: bool = False) -> str:
    """Return ``uri`` when Waggle may dial it: a wss:// URI, or an allowed ws:// URI.

    A ws:// URI's host must be loopback or a v3 onion service (module docstring: Tor encrypts and
    authenticates that link end to end), unless ``allow_virtual_cell_gateway_host`` is set, in
    which case a documented gateway alias or a private (RFC 1918/link-local) address is accepted
    too (module docstring: the roadmap step 5.6 Virtual Cell control-link exception). Signatures
    remain mandatory on every frame regardless; this only widens which *unencrypted* transport
    hosts are dialable, never whether a frame needs to be signed.

    Args:
        uri: The candidate endpoint, e.g. ``"wss://queen.example.org:8443/waggle"`` or
            ``"ws://127.0.0.1:9000"``.
        allow_virtual_cell_gateway_host: When True, a ws:// URI whose host is a documented
            Virtual Cell gateway alias or a private/link-local address is accepted too, not only
            loopback. False (the default) is the ordinary Waggle rule, unchanged.

    Returns:
        ``uri``, unchanged, now known to satisfy the link rule this call applies.

    Raises:
        ValueError: ``uri`` does not match WAGGLE_URI_PATTERN, cannot be parsed, or uses ws://
            with a host neither loopback nor (when allowed) a Virtual Cell gateway host.
    """
    # The pattern first, so a value that is not a WebSocket URI at all gets that message rather
    # than a host complaint; the same pattern is what a pydantic field declares.
    if _URI_RE.fullmatch(uri) is None:
        raise ValueError(f"URI {uri!r} is not a WebSocket URI (ws:// or wss://).")
    # urlsplit raises on a malformed IPv6 bracket ("ws://[::1"); that is a bad URI, not a crash.
    try:
        parts = urlsplit(uri)
        host = parts.hostname
    except ValueError as exc:
        raise ValueError(f"URI {uri!r} cannot be parsed.") from exc
    if parts.scheme == _PLAINTEXT_SCHEME and not _plaintext_host_allowed(
        host, allow_virtual_cell_gateway_host
    ):
        allowed = (
            "loopback, a v3 onion service, a documented Virtual Cell gateway alias, or a "
            "private/link-local address"
            if allow_virtual_cell_gateway_host
            else "a loopback host or a v3 onion service"
        )
        raise ValueError(
            f"URI {uri!r} uses ws:// with host {host!r}; a plaintext endpoint is allowed only on "
            f"{allowed}."
        )
    return uri


def is_loopback_host(host: str) -> bool:
    """Return True for ``localhost`` or a literal loopback IP (127.0.0.0/8, ::1), else False.

    Args:
        host: A host as urlsplit reports it: a name, or an IP literal without brackets.

    Returns:
        True only for the loopback forms that can be judged without a lookup.
    """
    if host == LOOPBACK_HOST_NAME:
        return True
    # Any other name is not trusted, even one that would resolve to loopback: judging it needs a
    # lookup, and a lookup is a side effect a validator must not have.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_onion_service_host(host: str) -> bool:
    """Return True for a well-formed Tor v3 onion service address, judged by its spelling alone.

    The address is the service's identity: 56 base32 characters encoding its 32-byte ed25519
    public key, a 2-byte checksum and the version byte 3, then ``.onion`` (rend-spec-v3). The
    checksum (SHA3-256 over a fixed tag, the key and the version) is verified, so a mistyped
    address is refused rather than dialled. A subdomain of an onion address is not accepted.

    Args:
        host: A host as urlsplit reports it, in any case.

    Returns:
        True only for a v3 onion address whose version and checksum hold; never a lookup.
    """
    lowered = host.lower()
    if not lowered.endswith(ONION_SUFFIX):
        return False
    service_id = lowered.removesuffix(ONION_SUFFIX)
    # The shape first: exactly one label of the right length, in the base32 alphabet.
    if len(service_id) != ONION_V3_ID_CHARS or not set(service_id) <= _BASE32_ALPHABET:
        return False
    try:
        decoded = base64.b32decode(service_id.upper())
    except binascii.Error:
        return False  # Unreachable for 56 alphabet characters; kept so a decode never raises.
    key = decoded[:_ONION_KEY_BYTES]
    checksum = decoded[_ONION_KEY_BYTES : _ONION_KEY_BYTES + _ONION_CHECKSUM_BYTES]
    version = decoded[-1]
    if version != _ONION_V3_VERSION:
        return False
    digest = hashlib.sha3_256(_ONION_CHECKSUM_TAG + key + bytes((version,))).digest()
    return checksum == digest[:_ONION_CHECKSUM_BYTES]


def is_virtual_cell_gateway_host(host: str) -> bool:
    """Return True for a documented Virtual Cell gateway alias or a private/link-local address.

    Never true for a public address: only the two documented aliases (module docstring) and
    RFC 1918/link-local ranges, which is exactly what a container's own host-gateway networking
    and an operator's own LAN segment use. Python's ``ipaddress.is_private`` also covers loopback,
    so this may return True for a loopback literal too; harmless, since ``check_waggle_uri`` (this
    function's one caller) already accepts loopback through ``is_loopback_host`` first.

    Args:
        host: A host as urlsplit reports it: a name, or an IP literal without brackets.

    Returns:
        True for ``host.docker.internal``, or an RFC 1918/link-local/loopback IP literal (which
        already covers QEMU's own ``10.0.2.2`` SLIRP gateway, itself inside 10.0.0.0/8); False
        otherwise, including for any name lookup this function refuses to perform.
    """
    if host in VIRTUAL_CELL_GATEWAY_HOST_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # Any other name needs a lookup this function must not perform.
    # is_private covers RFC 1918 (and loopback, already handled by is_loopback_host); link-local
    # is checked separately since ipaddress does not fold it into is_private.
    return address.is_private or address.is_link_local


def _plaintext_host_allowed(host: str | None, allow_virtual_cell_gateway_host: bool) -> bool:
    """Return whether a ws:// URI's host clears the rule this call applies."""
    if host is None:
        return False
    # Loopback stays on one machine; an onion service's link is Tor's, encrypted and
    # authenticated end to end (module docstring). Either keeps a plaintext frame private.
    if is_loopback_host(host) or is_onion_service_host(host):
        return True
    return allow_virtual_cell_gateway_host and is_virtual_cell_gateway_host(host)
