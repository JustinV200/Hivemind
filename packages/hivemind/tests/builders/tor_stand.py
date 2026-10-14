"""Build the Hive Stand's side of a Night Veil Cell's link: a signed listener behind a fake Tor.

A Night Veil Cell dials the Queen's onion service through the SOCKS proxy on its own loopback, so
a test of anything on that link needs the same three things: a listener that signs as the Queen
(a real `WebSocketServer` on loopback standing in for the Hive Stand), a `FakeSocksProxy` playing
Tor that routes the onion name to it, and the environment a Night Veil bootstrap renders
(`CellBootstrap.environment()`) naming both. `tor_stand` makes the first two and `TorStand.environ`
the third; the tests then play whichever Queen they need on the listener, and kill or restart Tor
by closing the proxy and starting a new one on the port it had.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the in-Cell Night Veil
    unit tests (`tests/unit/cli/in_cell`).

Key invariants:
    - Closing the stand closes the proxy and the listener, whatever a test did to either.
    - The proxy's one route is the onion name to the listener, so a Cell that reached the
      listener at all reached it through the proxy by that name.

See Also:
    - waggle.transport.socks for FakeSocksProxy.
    - tests/e2e/test_night_veil_link.py for the same link through a whole Hive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.ids import HiveId, NodeId, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer
from waggle.transport.socks import FakeSocksProxy
from waggle.transport.websocket_server import WebSocketServer

ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
ONION_PORT = 8710  # The onion service's virtual port; the fake proxy maps it to the listener.
CELL_ID = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"

__all__ = ["CELL_ID", "ONION", "ONION_PORT", "TorStand", "tor_stand"]


@dataclass
class TorStand:
    """The Hive Stand's side: its signed listener, its identity, and the fake Tor in front."""

    server: WebSocketServer
    proxy: FakeSocksProxy
    signer: Ed25519Signer
    hive_id: HiveId
    node_id: NodeId

    def environ(self, scratch_root: Path, **extra: str) -> dict[str, str]:
        """The environment a Night Veil Cell's bootstrap renders, with `extra` laid over it.

        Args:
            scratch_root: Where the Cell may make its scratch and outbox directories.
            **extra: Variables that replace or add to the rendered ones, by their full names.

        Returns:
            The mapping a Cell's `run_in_cell_warden` is started with.
        """
        environ = {
            "HIVEMIND_QUEEN_WAGGLE_URL": f"ws://{ONION}:{ONION_PORT}",
            "HIVEMIND_SOCKS_PROXY_URL": self.proxy.url(),
            "HIVEMIND_COMB_SHIELD": "NIGHT_VEIL",
            "HIVEMIND_CELL_ID": CELL_ID,
            "HIVEMIND_HIVE_ID": self.hive_id,
            "HIVEMIND_QUEEN_NODE_ID": self.node_id,
            "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
            "HIVEMIND_QUEEN_VERIFY_KEY": self.signer.public_key_bytes.hex(),
            "HIVEMIND_SCRATCH_ROOT": str(scratch_root),
        }
        environ.update(extra)
        return environ


@asynccontextmanager
async def tor_stand() -> AsyncIterator[TorStand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    clock = SystemClock()
    signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=signer))
    await server.start()
    proxy = FakeSocksProxy({ONION: ("127.0.0.1", server.port)})
    await proxy.start()
    try:
        yield TorStand(server, proxy, signer, new_hive_id(clock), new_node_id(clock))
    finally:
        await proxy.close()
        await server.close()
