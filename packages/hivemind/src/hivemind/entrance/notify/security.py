"""Provide PushSecurityNotifier: every Entrance security event, pushed to every other device.

Codingrules 8.15: every enrolment, approval, denial, revocation, lockout, step-up, held request and
reduction is a ``guard.*`` trail event pushed to every other enrolled device, so a device that was
stolen or an approval the operator never made is noticed at once. Enrolment, step-up, the pending
confirmations and the Entrance Reducer hand a ``SecurityNotice`` to their ``SecurityNotifier`` seam
(a no-op until now); this implementation queues a ``security_event`` notice about the trail event on
the ``PushOutbox``. A notice about one device leaves that device out; a broadcast (a reduction, a
reopening) reaches every approved device holding ``entrance:push``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.notify``. Installed as
    ``EnrolmentSeams.notifier`` and ``ReducerSeams.notifier`` by the Entrance's composition. Calls
    into the outbox only.

Key invariants:
    - ``notify`` only queues and returns, as the seam's contract requires.
    - The notice's ref is the trail event's id: a device reads what happened itself, over its
      authenticated session.

See Also:
    - hivemind.entrance.enrol.deps.notifier for the seam and the notice.
    - docs/adr/0034-landing-board-versioning-and-push.md for ``security_event``'s audience.
"""

from __future__ import annotations

from hivemind.entrance.enrol.deps import SecurityNotice
from hivemind.entrance.notify.outbox import PushOutbox
from hivemind.entrance.push import NoticeKind

__all__ = ["PushSecurityNotifier"]


class PushSecurityNotifier:
    """The SecurityNotifier over the push outbox."""

    def __init__(self, outbox: PushOutbox) -> None:
        """Build the notifier.

        Args:
            outbox: Where every notice is queued.
        """
        self._outbox = outbox

    async def notify(self, notice: SecurityNotice) -> None:
        """Queue a ``security_event`` notice about the notice's trail event.

        Args:
            notice: What happened, to which device (None: to the Hive itself).
        """
        self._outbox.push(NoticeKind.SECURITY_EVENT, notice.event_id, concerning=notice.device_id)
