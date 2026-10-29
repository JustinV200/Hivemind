"""Define GrantWait and GrantWaits: the fresh tasks the dispatcher holds PENDING for a grant.

A fresh task whose grant a passing shortfall zeroes is held PENDING and tried again on later
dispatch passes (`hivemind.queen.dispatcher.zero_grant`); this book keeps when each such wait
began. It lives here beside the dispatcher's other holds, rather than in `hivemind.queen.deps`
(whose `DispatchBook` owns one), for the same reason they do: `deps` may import nothing that
imports it back.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's holds
    sub-package. Held by `hivemind.queen.deps.DispatchBook`; read and written by
    `hivemind.queen.dispatcher.zero_grant`; built by `hivemind.cli.compose.hive.build`. Calls into
    `hivemind.forage` (GrantBound) and waggle only.

Key invariants:
    - In memory only: a restarted Queen starts each wait afresh rather than trust its old clock.

See Also:
    - hivemind.queen.dispatcher.zero_grant for the wait-or-deny rule this book serves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hivemind.forage import GrantBound
from waggle.ids import TaskId

DEFAULT_ZERO_GRANT_PATIENCE_S = 300.0  # Matches the manifest's own [forage] default.

__all__ = ["DEFAULT_ZERO_GRANT_PATIENCE_S", "GrantWait", "GrantWaits"]


@dataclass(frozen=True, slots=True)
class GrantWait:
    """One fresh task's wait for a grant: the limit it waits on, and since when.

    Attributes:
        bound: The limit that left its grant with no sub-bee when this wait began
            (`hivemind.forage.GrantBound`).
        since: When this wait began; a wait on the Cell's live figures keeps its clock whichever
            of them is tightest from pass to pass, and a wait on the goal's allowance runs its own.
    """

    bound: GrantBound
    since: datetime


@dataclass(slots=True)
class GrantWaits:
    """The Queen's book of fresh tasks waiting for a grant, and how long such a wait may last.

    A fresh task whose grant a passing shortfall zeroes stays PENDING and is tried again on every
    later dispatch pass (`hivemind.queen.dispatcher.zero_grant`); this is where each such wait's
    start is kept, so the first pass records the one `forage.denied` that says so, later passes
    record nothing, and a wait on a passing figure (the Cell's live load or free memory, or busy
    model seats) past `patience_s` fails the task with the figures instead of waiting for ever.
    Held in `DispatchBook`, for the same reason `Housekeeping` is held on `QueenDeps`. Owns its own
    mutable state in place (codingrules section 8.5): `waits` changes on every dispatch pass that
    starts, changes or ends a wait. In memory only: a restarted Queen starts each wait afresh (and
    says so on the trail) rather than trusting a clock it did not keep.

    Attributes:
        patience_s: `[forage] zero_grant_patience_s`: the longest a task waits on its Cell's live
            figures or on busy seats, in seconds.
        waits: Every fresh task waiting for a grant right now, by task id.
    """

    patience_s: float = DEFAULT_ZERO_GRANT_PATIENCE_S
    waits: dict[TaskId, GrantWait] = field(default_factory=dict)
