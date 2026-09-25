"""Hold the login tables: each device's consecutive login failures and the networks it has used.

A valid device proof with a wrong password at the Hive Entrance (the Hive's one HTTP door) is a
failure of that device, and ``lockout_attempts`` in a row lock it; the travel lock asks whether a
device has used a network before (ADR-0041). Both facts are persisted, so a restart neither resets
an attacker's guesses nor forgets where a device has been. ``protocol`` defines ``LoginTable``;
``sqlite`` keeps it in the Entrance tables (``entrance_login_failures``,
``entrance_device_networks``, migration 0002); ``memory`` in dicts.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Used by the
    login and step-up flows and the travel lock; reached through ``EntranceStore.logins``. Calls
    into ``hivemind.common`` and ``hivemind.entrance.auth.network``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Lockout, rate
      limits, travel lock".

Public API:
    - LoginTable, check_network: the protocol and its rule (protocol).
    - SqliteLoginTable: the durable table (sqlite).
    - MemoryLoginTable: the in-memory table (memory).
"""

from hivemind.entrance.store.logins.memory import MemoryLoginTable
from hivemind.entrance.store.logins.protocol import LoginTable, check_network
from hivemind.entrance.store.logins.sqlite import SqliteLoginTable

__all__ = ["LoginTable", "MemoryLoginTable", "SqliteLoginTable", "check_network"]
