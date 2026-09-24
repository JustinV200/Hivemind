"""Define DeviceOffboarder: the seam that cuts a device off when it stops being approved.

ADR-0033: leaving APPROVED (a lock, a revocation, an expiry) ends every session the device holds
and deletes its push subscriptions in the same step, and every WebSocket of those sessions closes.
Enrolment decides *when* a device leaves; the sessions (roadmap step 10.5e) and the push
subscriptions (roadmap step 10.5b) are later steps' tables. ``DeviceOffboarder`` is the seam
between them: enrolment calls ``offboard`` right after the status change is recorded, and those
steps implement it. ``NullDeviceOffboarder`` is the documented no-op until then, which is safe
because until then no device can hold a session or a subscription to end.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Called by
    ``hivemind.entrance.enrol.record`` whenever a device leaves APPROVED, or a locked one is
    revoked or expires; implemented by roadmap steps 10.5e and 10.5b, and by
    ``hivemind.entrance.enrol.deps.fake`` for tests. Calls into ``hivemind.entrance.enrol.state``
    and waggle (ids) only.

Key invariants:
    - ``offboard`` is idempotent: a locked device was offboarded when it was locked, and is
      offboarded again, harmlessly, when it is revoked or expires.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "leaving APPROVED
      ends the device's sessions and deletes its push subscriptions in the same step".
"""

from __future__ import annotations

from typing import Protocol

from hivemind.entrance.enrol.state import DeviceStatus
from waggle.ids import DeviceId

__all__ = ["DeviceOffboarder", "NullDeviceOffboarder"]


class DeviceOffboarder(Protocol):
    """End every session and delete every push subscription of a device that left APPROVED."""

    async def offboard(self, device_id: DeviceId, reason: DeviceStatus) -> None:
        """Cut ``device_id`` off: its sessions (and their sockets) end, its subscriptions go.

        Args:
            device_id: The device that left APPROVED.
            reason: The status it moved to: LOCKED, REVOKED or EXPIRED.
        """
        ...


class NullDeviceOffboarder:
    """The no-op DeviceOffboarder: until sessions and subscriptions exist there is nothing to end.

    Roadmap steps 10.5e (sessions) and 10.5b (push subscriptions) replace this in the composition
    root with an offboarder over their own tables.
    """

    async def offboard(self, device_id: DeviceId, reason: DeviceStatus) -> None:
        """Do nothing; see the class docstring.

        Args:
            device_id: The device, ignored.
            reason: The status it moved to, ignored.
        """
