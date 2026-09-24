"""Define LoginTable: each device's consecutive login failures and the networks it has used.

Two facts about a device's logins at the Hive Entrance (the Hive's one HTTP door) outlive any one
session (ADR-0033). The **consecutive failure count**: a valid device proof followed by a wrong
password is a failure of that device, and ``lockout_attempts`` of them in a row lock it; a success
resets the count. It is persisted, so restarting ``hive serve`` does not hand an attacker a fresh
set of guesses. The **known networks**: the travel lock asks whether a device is logging in from a
network it has used before, so every network a device has been cleared on is remembered with when
it was first and last seen. ``LoginTable`` is the seam, implemented by ``SqliteLoginTable`` and
``MemoryLoginTable``; ``check_network`` is the rule both apply.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.logins``. Used by
    the login and step-up flows and the travel lock (``hivemind.entrance.auth``); reached through
    ``EntranceStore.logins``. Calls into ``hivemind.entrance.auth.network`` only.

Key invariants:
    - ``count_failure`` is atomic: two failures at once count as two.
    - Only a canonical device network (``hivemind.entrance.auth.network``) is ever remembered.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Lockout, rate
      limits, travel lock".
    - hivemind.entrance.store.logins.sqlite for the tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.entrance.auth.network import is_device_network
from waggle.ids import DeviceId

__all__ = ["LoginTable", "check_network"]


class LoginTable(Protocol):
    """Count each device's consecutive login failures, and remember the networks it has used."""

    async def count_failure(self, device_id: DeviceId, at: datetime) -> int:
        """Add one consecutive failure to ``device_id`` and return the new count.

        Args:
            device_id: The device whose valid proof came with a wrong password.
            at: When.

        Returns:
            The consecutive failures now on record, at least 1.
        """
        ...

    async def failures(self, device_id: DeviceId) -> int:
        """Return how many consecutive failures ``device_id`` has on record.

        Args:
            device_id: The device.

        Returns:
            The count; 0 when it has none.
        """
        ...

    async def clear_failures(self, device_id: DeviceId) -> None:
        """Reset ``device_id``'s consecutive failures (a success, or a lock that used them up).

        Args:
            device_id: The device.
        """
        ...

    async def networks(self, device_id: DeviceId) -> frozenset[str]:
        """Return every network ``device_id`` has been cleared on.

        Args:
            device_id: The device.

        Returns:
            The canonical networks; empty when it has none.
        """
        ...

    async def remember_network(self, device_id: DeviceId, network: str, at: datetime) -> None:
        """Remember that ``device_id`` was seen on ``network`` at ``at``.

        Args:
            device_id: The device.
            network: A canonical device network.
            at: When; the first sighting keeps its own time, later ones move ``last_seen_at``.

        Raises:
            ValueError: ``network`` is not a canonical device network.
        """
        ...


def check_network(network: str) -> str:
    """Refuse a network that is not canonical, so one network is never remembered twice.

    Args:
        network: The candidate.

    Returns:
        ``network``, unchanged.

    Raises:
        ValueError: It is not a canonical /24, /64 or ``derp:<region>``.
    """
    if not is_device_network(network):
        raise ValueError("Only a canonical /24, /64 or derp:<region> network is remembered.")
    return network
