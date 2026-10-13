"""Read an Attendant view for the Queen or one Warden.

The Attendant views (roadmap 12.7) show the task graph a principal is concerned with and the
inbox behind it. This module reads the tasks (every unfinished one, and the newest finished ones),
the open Alarms and, for the Queen, the human messages still waiting in her chat log, and hands them
to the pure ``attendant_view``. The principal must be the Queen or an attached Warden.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads.screens``. Called
    by ``hivemind.entrance.routes.screens.attendant``. Calls into the shared gathers, the chat log
    and the pure builder.

Key invariants:
    - Only reads; an unknown principal raises ``PrincipalNotFoundError``.

See Also:
    - hivemind.observation.api.attendant for the view and how its inbox is reconstructed.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.entrance.errors import PrincipalNotFoundError
from hivemind.entrance.gate.reads import HiveReads
from hivemind.entrance.reads.screens.gather import alarm_views, recent_finished, unfinished_tasks
from hivemind.observation import QUEEN_PRINCIPAL, AttendantInputs, AttendantView, attendant_view

WAITING_MESSAGES = 100  # Most waiting human messages read: a long queue shows its head.

__all__ = ["WAITING_MESSAGES", "read_attendant"]


async def read_attendant(reads: HiveReads, principal: str, now: datetime) -> AttendantView:
    """Read the Attendant view for ``principal``.

    Args:
        reads: The stores and live tables the Entrance reads.
        principal: ``queen``, or the id of an attached Warden.
        now: The instant the view is built for.

    Returns:
        The reconstructed inbox, the task graph, what finished and what is next.

    Raises:
        PrincipalNotFoundError: It is neither the Queen nor an attached Warden.
    """
    queen = principal == QUEEN_PRINCIPAL
    if not queen and principal not in {link.warden_id for link in reads.census.wardens}:
        raise PrincipalNotFoundError(principal)
    tasks = [*await unfinished_tasks(reads), *await recent_finished(reads)]
    # Latency: one local indexed read of the chat log; only the Queen has a human inbox.
    waiting = await reads.chat.unhandled(WAITING_MESSAGES) if queen else ()
    inputs = AttendantInputs(
        principal=principal,
        tasks=tasks,
        alarms=await alarm_views(reads, tasks),
        waiting_messages=[(entry.id, entry.at) for entry in waiting],
        as_of=now,
    )
    return attendant_view(inputs)
