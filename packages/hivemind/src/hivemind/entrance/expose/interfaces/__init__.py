"""List this host's interfaces and addresses for the exposure check: protocol, adapter, fake.

``vpn`` and ``lan`` exposure (ADR-0041) are decided against the interfaces of the host the Hive
Entrance runs on: ``vpn`` binds only to an address on the overlay's own interface, ``lan`` only to
an address this host really has. ``protocol`` defines ``LocalInterfaces`` and the snapshot value;
``system`` is the adapter over ``psutil`` (the standard library cannot list interface addresses
portably across Windows and Linux); ``fake`` answers from a table. This file is the face.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose``. Read by
    ``hivemind.entrance.expose.gather``; ``SystemInterfaces`` is built by the Entrance's
    composition root.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No address in a snapshot carries an IPv6 zone.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the rules.
    - hivemind.entrance.expose.plan for the check that reads a snapshot.

Public API:
    - LocalInterfaces, InterfaceAddresses, IPAddress, unscoped: the protocol, its snapshot value,
      the address type and the zone rule.
    - SystemInterfaces, interfaces_from_records, AddressRecord: the psutil adapter and its pure
      conversion.
    - FakeInterfaces: the table-driven fake.
"""

from hivemind.entrance.expose.interfaces.fake import FakeInterfaces
from hivemind.entrance.expose.interfaces.protocol import (
    InterfaceAddresses,
    IPAddress,
    LocalInterfaces,
    unscoped,
)
from hivemind.entrance.expose.interfaces.system import (
    AddressRecord,
    SystemInterfaces,
    interfaces_from_records,
)

__all__ = [
    "AddressRecord",
    "FakeInterfaces",
    "IPAddress",
    "InterfaceAddresses",
    "LocalInterfaces",
    "SystemInterfaces",
    "interfaces_from_records",
    "unscoped",
]
