"""Provide the Pollen Packet, the lightweight connector on a borrowed Real Cell device.

The Pollen Packet is a gateway: it dials the Queen (the Hive's central orchestrator), proves who it
is with a key it generated, and then does exactly what a Warden (the per-Cell supervisor) or the
Queen asks of the device, inside a lease (the Hive's temporary hold on it), checking every request
against the capabilities it was granted before it runs anything. It holds no model, writes no
audit trail, supervises nothing and never listens. It depends only on waggle (and pydantic and
psutil), so it can install on hardware with no Docker, no SQLite extensions and no LLM SDK, such as
a Raspberry Pi. ``pollen enrol`` joins a device to the Swarm with an invite, ``pollen run`` is the
packet, and ``pollen install`` keeps it running at login.

Fits into the Hive:
    Its own package, parallel to hivemind rather than inside its layer table; called by nothing
    in the workspace (it runs standalone on a borrowed device) and calls into waggle only, so it
    installs on hardware with no Docker, no SQLite extensions and no LLM SDK.

Key invariants:
    - pollen imports nothing from hivemind, and reads no ``HIVEMIND_*`` environment variable.
    - Nothing is acted on until it is verified, addressed here, from the Queen or the Warden,
      fresh and not a duplicate; nothing outside a lease's scratch is changed without being
      recorded first.
    - Every operating-system difference lives behind ``pollen.platform``.

See Also:
    - .claude/codingrules.md section 4 for the single-dependency rule this package follows.
    - docs/waggle/gateway-contract.md for the contract the packet meets, and
      docs/waggle/enrolment.md for how a device joins.
    - waggle for the protocol and primitives this package links against.

Public API:
    - PollenPacket, build_packet: the packet's loop, and the composition root that assembles it.
    - PacketState, DataLayout: what the packet persists, and where.
    - PollenError: the root of every error pollen raises on purpose.
"""

from pollen.agent import PollenPacket
from pollen.build import build_packet
from pollen.errors import PollenError
from pollen.state import DataLayout, PacketState

__all__ = ["DataLayout", "PacketState", "PollenError", "PollenPacket", "build_packet"]
