"""Keep the remote listener's TLS context current: built on start, rebuilt on every revocation.

In every remote mode the remote listener speaks TLS on a DNS name, and under mutual TLS it demands
a client certificate chaining to the Hive's own authority and not revoked (ADR-0033). The context
is built inside a ``ContextSwitch`` whose ``listener_context`` uvicorn is started with; every
ClientHello moves to the switch's current context, so a rebuilt one (with a fresh revocation
list) reaches the next handshake without restarting the listener. ``RemoteTls`` builds the context
with a fresh ``build_crl`` when the listener starts (a restart never serves a stale list), and
rebuilds it after every revocation. The revoked serials come from a ``RevokedSerials`` source; no
device certificate is issued by the Entrance yet (approval over mutual TLS is a later step), so
``NoCertificates`` is the source until then and the list names nothing.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Used by the
    Entrance's listeners. Calls into ``hivemind.entrance.expose`` (the context, the switch, the
    revocation list).

Key invariants:
    - The listener is always started with ``ContextSwitch.listener_context``, never another.
    - A failed rebuild leaves the current context in place.

See Also:
    - hivemind.entrance.expose.tls for the context, the switch and the list.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Iterable
from typing import Protocol

from hivemind.common.logging import get_logger
from hivemind.entrance.expose import (
    ContextSwitch,
    HiveAuthority,
    ListenerTls,
    RevokedSerial,
    build_crl,
    server_context,
)
from waggle.clock import Clock

log = get_logger(__name__)

__all__ = ["NoCertificates", "RemoteTls", "RevokedSerials"]


class RevokedSerials(Protocol):
    """Every revoked device certificate the Hive issued."""

    async def revoked(self) -> Iterable[RevokedSerial]:
        """Return the revoked serials, in any order.

        Returns:
            Every revoked device certificate.
        """
        ...


class NoCertificates:
    """The source while the Entrance issues no device certificate: nothing is revoked."""

    async def revoked(self) -> Iterable[RevokedSerial]:
        """Return nothing: no certificate was issued, so none is revoked.

        Returns:
            An empty tuple.
        """
        return ()


class RemoteTls:
    """The remote listener's TLS context, behind a switch rebuilt on start and on revocation."""

    def __init__(
        self,
        tls: ListenerTls,
        authority: HiveAuthority,
        serials: RevokedSerials,
        clock: Clock,
    ) -> None:
        """Hold what the context is built from; nothing is built until ``listener_context``.

        Args:
            tls: The exposure plan's TLS settings for the remote listener.
            authority: The Hive's certificate authority, the client certificates' only anchor.
            serials: The revoked device certificates.
            clock: Dates each revocation list.
        """
        self._tls = tls
        self._authority = authority
        self._serials = serials
        self._clock = clock
        self._switch: ContextSwitch | None = None

    async def listener_context(self) -> ssl.SSLContext:
        """Return the context to start the remote listener with, over a fresh revocation list.

        Returns:
            The switch's listener context (built on the first call, rebuilt after that).

        Raises:
            OSError: A certificate file could not be read.
            ssl.SSLError: OpenSSL rejected the certificate chain or key.
        """
        crl = build_crl(self._authority, await self._serials.revoked(), self._clock.now())
        if self._switch is None:
            # Latency: two small file reads and a parse, milliseconds; blocking, so off the loop.
            context = await asyncio.to_thread(server_context, self._tls, self._authority, crl)
            self._switch = ContextSwitch(context)
        else:
            await self._switch.rebuild(self._tls, self._authority, crl)
        return self._switch.listener_context

    async def rebuild(self) -> None:
        """Rebuild the current context over a fresh revocation list (after a revocation)."""
        if self._switch is None:
            return
        crl = build_crl(self._authority, await self._serials.revoked(), self._clock.now())
        try:
            await self._switch.rebuild(self._tls, self._authority, crl)
        except (OSError, ssl.SSLError) as error:
            # The current context stays in place; the next start rebuilds from the files again.
            log.error("entrance.tls_rebuild_failed", error=type(error).__name__)
