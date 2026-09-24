"""Reach one Hive Entrance: its origin, the TLS that proves it is the Hive, and an HTTP client.

A CLI device talks to exactly one Entrance (the Hive's HTTP door): the loopback listener on the
Hive Stand (``http://localhost:<port>``) or a remote listener on a VPN overlay (``https://`` on a
DNS name, ADR-0033). ``EntranceAddress`` is that choice, validated once: plain ``http`` only for a
loopback host (the one place TLS is not needed, because the traffic never leaves the machine), and
``https`` everywhere else, verified either against the system's trust store or, for a Hive that
runs its own certificate authority, against only the CA file the operator named. A device holding
a mutual-TLS client certificate carries it in its address, and every HTTPS request and WebSocket
presents it (``hivemind.cli.landing.certificate``); plain loopback ``http`` has no TLS to present
it in. Nothing here
consults the environment's proxy settings: the Entrance is reached directly (on loopback, or on the
overlay's own address), and a proxy in the path is exactly what the loopback listener refuses.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Used by the console
    (``hivemind.cli.entrance``) and the remote device (``hivemind.cli.remote``) to open their
    clients, and by ``hivemind.cli.landing.stream`` for its sockets. Calls into ``httpx``, the
    standard library's ``ssl`` and ``waggle.uris`` (the loopback rule).

Key invariants:
    - An ``EntranceAddress`` is either ``https`` or a loopback ``http`` origin, with no path,
      query or fragment.
    - Certificates are always verified; there is no switch that turns verification off.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Exposure never
      means the open internet, and remote always means TLS on a name".
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from hivemind.cli.landing.certificate import ClientCertificate, present
from hivemind.cli.landing.errors import LandingError
from waggle.uris import is_loopback_host

REQUEST_TIMEOUT_S = 30.0  # One request, login included: two Argon2id checks take under a second.
_SECURE = "https"  # Every remote Entrance speaks TLS on a DNS name.
_PLAIN = "http"  # Only the loopback listener, which never leaves the machine.
_SOCKET_SCHEMES = {_SECURE: "wss", _PLAIN: "ws"}  # The same origin's WebSocket scheme.

__all__ = ["REQUEST_TIMEOUT_S", "EntranceAddress", "entrance_address", "open_http"]


@dataclass(frozen=True, slots=True)
class EntranceAddress:
    """Where one Entrance answers, and what its TLS certificate must chain to.

    Attributes:
        origin: ``scheme://host[:port]``, nothing more.
        ca_file: A PEM file of the only authorities trusted for this Entrance (a Hive's own CA),
            or None to trust the system's store; unused for plain loopback ``http``.
        client: The device's mutual-TLS client certificate, presented on every handshake; None
            for a device holding none. Unused for plain loopback ``http``.
    """

    origin: str
    ca_file: Path | None = None
    client: ClientCertificate | None = field(default=None, repr=False)

    @property
    def socket_origin(self) -> str:
        """The same origin with its WebSocket scheme (``ws`` or ``wss``)."""
        scheme, rest = self.origin.split("://", 1)
        return f"{_SOCKET_SCHEMES[scheme]}://{rest}"

    def ssl_context(self) -> ssl.SSLContext | None:
        """Return the TLS context that verifies this Entrance, or None for loopback ``http``.

        Returns:
            A verifying client context: the CA file alone when one was named, else the system
            store; hostname checking on; the device's client certificate when it holds one. None
            when the origin is plain ``http``.

        Raises:
            LandingError: The CA file cannot be read as PEM certificates, or the client
                certificate does not match its key.
        """
        if self.origin.startswith(f"{_PLAIN}://"):
            return None
        try:
            # create_default_context verifies the chain and the host name; with a cafile it trusts
            # that file alone, which is what pinning a Hive's own authority means.
            context = ssl.create_default_context(cafile=self.ca_file)
        except (OSError, ssl.SSLError) as exc:
            raise LandingError(
                f"The CA file {self.ca_file} could not be read as PEM certificates ({exc})."
            ) from exc
        if self.client is not None:
            present(context, self.client)
        return context


def entrance_address(url: str, ca_file: Path | None = None) -> EntranceAddress:
    """Validate an Entrance URL and keep only its origin.

    Args:
        url: What the operator was given: an origin, or an invite link (its path and fragment are
            dropped here; the caller reads the fragment itself).
        ca_file: A PEM file of the authorities to trust, or None for the system store.

    Returns:
        The address.

    Raises:
        LandingError: The URL is not ``https``, or is ``http`` to a host that is not loopback, or
            has no host.
    """
    parts = urlsplit(url.strip())
    host = parts.hostname
    if host is None or parts.scheme not in _SOCKET_SCHEMES:
        raise LandingError(f"{url!r} is not an http(s) URL of a Hive Entrance.")
    # Plain http is the loopback listener only: anything else would cross a network in the clear.
    if parts.scheme == _PLAIN and not is_loopback_host(host):
        raise LandingError(
            f"{url!r} is plain http to a host that is not loopback; a remote Entrance is https."
        )
    return EntranceAddress(origin=f"{parts.scheme}://{parts.netloc}", ca_file=ca_file)


@asynccontextmanager
async def open_http(address: EntranceAddress) -> AsyncIterator[httpx.AsyncClient]:
    """Open an HTTP client for ``address``, verifying its TLS, closed when the block exits.

    Args:
        address: The Entrance.

    Yields:
        A client whose base URL is the Entrance's origin.

    Raises:
        LandingError: The CA file cannot be read.
    """
    context = address.ssl_context()
    # trust_env=False: no environment proxy and no environment CA bundle; the Entrance is
    # reached directly and verified only as the address says (module docstring).
    async with httpx.AsyncClient(
        base_url=address.origin,
        verify=context if context is not None else True,
        trust_env=False,
        timeout=REQUEST_TIMEOUT_S,
    ) as http:
        yield http
