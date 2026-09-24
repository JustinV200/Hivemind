"""Define AuthTables: the tables that login, sessions, step-up and the Entrance Reducer keep.

Roadmap step 10.5e adds four tables to the Entrance's own (migration 0002): sessions and the nonces
their requests have spent, each device's login failures and known networks, the pending
confirmations waiting for a person, and the Entrance's mode. Each is its own concept with its own
protocol and two implementations (``hivemind.entrance.store.sessions``, ``.logins``, ``.pending``,
``.mode``). ``AuthTables`` gathers them as four read-only properties, and ``EntranceStore``
extends it, so one store object reaches every Entrance table through one connection while each
table stays a small class of its own.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Extended by
    ``hivemind.entrance.store.protocol.EntranceStore``; implemented by ``SqliteEntranceStore`` and
    ``MemoryEntranceStore``. Calls into the four tables' protocols only.

Key invariants:
    - Each property returns the same table object for the life of its store.

See Also:
    - hivemind.entrance.store.protocol for EntranceStore.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for what they hold.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.entrance.store.logins.protocol import LoginTable
from hivemind.entrance.store.mode.protocol import ModeTable
from hivemind.entrance.store.pending.protocol import PendingTable
from hivemind.entrance.store.sessions.protocol import SessionTable

__all__ = ["AuthTables"]


class AuthTables(Protocol):
    """The four tables login, sessions, step-up and the Reducer keep, beside the device tables."""

    @property
    def sessions(self) -> SessionTable:
        """Sessions by token hash, and the nonces their requests have spent."""
        ...

    @property
    def logins(self) -> LoginTable:
        """Each device's consecutive login failures and the networks it has used."""
        ...

    @property
    def pending(self) -> PendingTable:
        """Requests held until a person confirms them."""
        ...

    @property
    def entrance_mode(self) -> ModeTable:
        """The Entrance's persisted mode, OPEN or REDUCED."""
        ...
