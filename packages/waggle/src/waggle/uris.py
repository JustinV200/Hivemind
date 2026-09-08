"""Check a WebSocket URI Waggle may dial: wss:// anywhere, ws:// on a loopback host only.

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
      loopback host.

See Also:
    - docs/waggle/spec.md section 6 ("Confidentiality is the link's") for the rule.
    - waggle.messages.control.hive for QueenMoved, the message that carries an address.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

WAGGLE_URI_PATTERN = r"^wss?://\S+$"  # A WebSocket URI; ws:// is further restricted to loopback.
LOOPBACK_HOST_NAME = "localhost"  # The one loopback host that is a name rather than an address.
_PLAINTEXT_SCHEME = "ws"  # No TLS on the link; the scheme that needs the loopback rule.

__all__ = ["LOOPBACK_HOST_NAME", "WAGGLE_URI_PATTERN", "check_waggle_uri", "is_loopback_host"]

_URI_RE = re.compile(WAGGLE_URI_PATTERN)  # Compiled once; every check matches against it.


def check_waggle_uri(uri: str) -> str:
    """Return ``uri`` when Waggle may dial it: a wss:// URI, or a ws:// URI on a loopback host.

    Args:
        uri: The candidate endpoint, e.g. ``"wss://queen.example.org:8443/waggle"`` or
            ``"ws://127.0.0.1:9000"``.

    Returns:
        ``uri``, unchanged, now known to satisfy spec section 6's link rule.

    Raises:
        ValueError: ``uri`` does not match WAGGLE_URI_PATTERN, cannot be parsed, or uses ws://
            with a host that is not loopback.
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
    if parts.scheme == _PLAINTEXT_SCHEME and (host is None or not is_loopback_host(host)):
        raise ValueError(
            f"URI {uri!r} uses ws:// with host {host!r}; a plaintext endpoint is allowed only "
            "on a loopback host."
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
