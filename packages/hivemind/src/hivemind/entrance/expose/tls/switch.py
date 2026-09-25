"""Swap the remote listener's TLS context without restarting it: how a revocation takes effect.

A listening socket keeps the TLS context it was started with, and a revocation (ADR-0041) must
reach the very next handshake without dropping the listener. ``ContextSwitch`` holds the context
the listener was started with and the current one, and installs itself as the listener context's
``sni_callback``: OpenSSL calls it on every ClientHello, with or without a server name, before the
client's certificate is checked, and it moves the connection to the current context, whose trust
store carries the newest revocation list. ``rebuild`` builds a fresh context from the plan, the
authority and a new list in a worker thread and swaps it in; the same call picks up a renewed
server certificate. Handshakes already completed are not touched: an established connection from
a device revoked since is ended by the login layer, which refuses a revoked device on its next
request and closes its sessions and sockets in the same step as the revocation.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.tls``. Built by
    the Entrance's composition root around the context it starts the remote listener with;
    ``rebuild`` is called on every revocation (and certificate renewal). Calls into
    ``server_context``.

Key invariants:
    - Every handshake on the listener uses whichever context is current when its ClientHello
      arrives; a swap never affects a handshake already past that point.
    - Every context swapped in must come from ``server_context`` with the same ``ListenerTls``:
      OpenSSL keeps the listener context's verify mode and flags for the connection and takes
      only the certificate and the trust store (authority and list) from the current one.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "a revocation
      rebuilds the TLS context, which new handshakes pick up through an sni_callback".
    - hivemind.entrance.expose.tls.context for the contexts swapped here.
"""

from __future__ import annotations

import asyncio
import ssl

from cryptography import x509

from hivemind.entrance.expose.plan import ListenerTls
from hivemind.entrance.expose.tls.authority import HiveAuthority
from hivemind.entrance.expose.tls.context import server_context

__all__ = ["ContextSwitch"]


class ContextSwitch:
    """The remote listener's TLS context, swappable while the listener runs."""

    def __init__(self, listener_context: ssl.SSLContext) -> None:
        """Take over the context the remote listener is started with.

        Args:
            listener_context: From ``server_context``; the listener must be started with
                ``listener_context`` (this switch's property of that name) and nothing else.
        """
        self._listener = listener_context
        self._current = listener_context
        listener_context.sni_callback = self._select

    @property
    def listener_context(self) -> ssl.SSLContext:
        """The context to start the remote listener with; it never changes."""
        return self._listener

    @property
    def current(self) -> ssl.SSLContext:
        """The context the next handshake will use."""
        return self._current

    def swap(self, context: ssl.SSLContext) -> None:
        """Make every handshake from now on use ``context``.

        Args:
            context: A context from ``server_context`` for the same ``ListenerTls``.
        """
        # A plain attribute store: the callback reads it on the event loop's thread, and a
        # reference assignment is atomic, so no handshake can see half a swap.
        self._current = context

    async def rebuild(
        self, tls: ListenerTls, authority: HiveAuthority, crl: x509.CertificateRevocationList
    ) -> None:
        """Build a context over ``crl`` (and today's certificate files) and swap it in.

        Args:
            tls: The plan's TLS settings, the same ones the listener context was built from.
            authority: The Hive's certificate authority.
            crl: The new revocation list, including the certificate just revoked.

        Raises:
            OSError: A certificate file could not be read; the current context stays in place.
            ssl.SSLError: OpenSSL rejected the files; the current context stays in place.
        """
        # Latency: two small file reads and a parse, milliseconds; blocking, so off the loop.
        context = await asyncio.to_thread(server_context, tls, authority, crl)
        self.swap(context)

    def _select(
        self, connection: ssl.SSLObject, server_name: str | None, context: ssl.SSLContext
    ) -> None:
        """Move a connection in mid-ClientHello to the current context (the ``sni_callback``)."""
        # Called for every ClientHello, a server name or not, so no client can dodge the swap.
        connection.context = self._current
