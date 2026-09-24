"""Define SecurityNotifier: the seam that tells every other device a security event happened.

Codingrules 8.15 and ADR-0034: every Entrance security event (a device asking to join, an
approval, a lock, a revocation) is pushed to every other approved device, as a notice that says
only that something happened, never what, because pushes transit third-party services. Enrolment
decides *when* that happens; roadmap step 10.5b's push channels decide *how*. This module is the
seam between the two: ``SecurityNotice`` is what enrolment hands over (the device it happened to
and the trail event that records it, which becomes the push's ``ref``), ``SecurityNotifier`` the
protocol step 10.5b implements, and ``NullSecurityNotifier`` the documented no-op the Entrance
runs with until then.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.deps``. Called by
    every enrolment flow after its trail event is written; implemented by ``hivemind.entrance.
    push`` (roadmap 10.5b) and by ``hivemind.entrance.enrol.deps.fake`` for tests. Calls into
    waggle (ids) only.

Key invariants:
    - A notice carries identifiers only: the device, the trail event's id and kind, and when.
    - ``notify`` is called after the event is on the trail, so a notice never points at an
      event that does not exist; implementations queue and return, they never block a flow on
      delivery.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the notice a push carries.
    - hivemind.entrance.enrol.record for where notices are sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from waggle.ids import DeviceId, EventId

__all__ = ["NullSecurityNotifier", "SecurityNotice", "SecurityNotifier"]


@dataclass(frozen=True, slots=True)
class SecurityNotice:
    """Something security-relevant happened to one device; every other approved device hears it.

    Attributes:
        device_id: The device it happened to; the push goes to every approved device but this one.
        event_id: The ``guard.entrance_*`` trail event recording it: the push notice's ``ref``,
            which a device fetches over an authenticated session to learn what happened.
        kind: That event's kind, e.g. ``"guard.entrance_pending"`` (a device is asking to join).
        at: When it happened.
    """

    device_id: DeviceId
    event_id: EventId
    kind: str
    at: datetime


class SecurityNotifier(Protocol):
    """Hand a security notice to whatever pushes it to every other approved device."""

    async def notify(self, notice: SecurityNotice) -> None:
        """Queue ``notice`` for every other approved device and return without waiting for delivery.

        Args:
            notice: What happened to which device, by trail event.
        """
        ...


class NullSecurityNotifier:
    """The no-op SecurityNotifier: until the push channels exist (roadmap 10.5b), nobody is told.

    The trail still records every event, so nothing is lost: a device that logs in later reads
    the security events it missed. Roadmap step 10.5b replaces this in the composition root.
    """

    async def notify(self, notice: SecurityNotice) -> None:
        """Do nothing; see the class docstring.

        Args:
            notice: The notice, ignored.
        """
