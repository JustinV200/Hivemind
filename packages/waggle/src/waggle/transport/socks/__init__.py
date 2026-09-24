"""Reach a host through a loopback SOCKS proxy that resolves its name: the socks package.

A Night Veil Cell's Waggle link reaches the Hive Stand only at its Tor onion service, through
Tor's SOCKS port on the Cell's own loopback (ADR-0030, codingrules 8.7). `client` is that exchange
(SOCKS5 with the destination named, `socks5h`, or SOCKS4a), handing back a connected socket the
WebSocket client dials over; `fake` is a loopback proxy that plays Tor's part in tests.

Fits into the Hive:
    Its own layer, inside `waggle.transport`. Used by `waggle.transport.websocket_client`.

Key invariants:
    - The destination's name always goes to the proxy; nothing here resolves it.

See Also:
    - waggle.transport.websocket_client for the dial that uses it.

Public API:
    - SocksProxy, SocksTimeouts, DEFAULT_SOCKS_TIMEOUTS, open_socks_connection and the scheme,
      timeout and length constants: the client (client).
    - FakeSocksProxy, FakeSocksBehaviour: the loopback proxy tests dial (fake).
"""

from waggle.transport.socks.client import (
    DEFAULT_SOCKS_TIMEOUTS,
    MAX_NAME_BYTES,
    PROXY_CONNECT_TIMEOUT_S,
    PROXY_REPLY_TIMEOUT_S,
    SOCKS4A_SCHEME,
    SOCKS5H_SCHEME,
    SOCKS_SCHEMES,
    SocksProxy,
    SocksTimeouts,
    open_socks_connection,
)
from waggle.transport.socks.fake import FakeSocksBehaviour, FakeSocksProxy

__all__ = [
    "DEFAULT_SOCKS_TIMEOUTS",
    "MAX_NAME_BYTES",
    "PROXY_CONNECT_TIMEOUT_S",
    "PROXY_REPLY_TIMEOUT_S",
    "SOCKS4A_SCHEME",
    "SOCKS5H_SCHEME",
    "SOCKS_SCHEMES",
    "FakeSocksBehaviour",
    "FakeSocksProxy",
    "SocksProxy",
    "SocksTimeouts",
    "open_socks_connection",
]
