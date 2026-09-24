"""Tell the human's devices that something is waiting: the Queen's channel and security notices.

The Hive Entrance (the Hive's one HTTP door) pushes "something is waiting" to the human's devices
(ADR-0034) for two callers: the Queen, through her ``HumanChannel`` seam (a reply, a question, an
Alarm, a goal's outcome), and the Entrance's own flows, through enrolment's ``SecurityNotifier``
seam (a device asking to join, a lock, a held request, a reduction). Both only queue: ``outbox``
holds the ``PushOutbox`` the Entrance runs in the background, delivering through the
``PushDispatcher`` with per-ref ordering so a withdrawal never overtakes its notice; ``human`` is
``PushHumanChannel`` and ``security`` is ``PushSecurityNotifier``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Built by the
    Entrance's composition (``hive serve``); called by the Queen's tick and by the Entrance's
    flows. Calls into ``hivemind.entrance.push`` and the Entrance tables.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here awaits a delivery on a caller's path, and no notice carries content.

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the notice and its audiences.
    - hivemind.queen.chat.channel for the HumanChannel seam.

Public API:
    - PushOutbox, DeviceSource, OUTBOX_CAPACITY, MAX_DELIVERIES_IN_FLIGHT: the queue (outbox).
    - PushHumanChannel: the Queen's HumanChannel over push (human).
    - PushSecurityNotifier: enrolment's SecurityNotifier over push (security).
"""

from hivemind.entrance.notify.human import PushHumanChannel
from hivemind.entrance.notify.outbox import (
    MAX_DELIVERIES_IN_FLIGHT,
    OUTBOX_CAPACITY,
    DeviceSource,
    PushOutbox,
)
from hivemind.entrance.notify.security import PushSecurityNotifier

__all__ = [
    "MAX_DELIVERIES_IN_FLIGHT",
    "OUTBOX_CAPACITY",
    "DeviceSource",
    "PushHumanChannel",
    "PushOutbox",
    "PushSecurityNotifier",
]
