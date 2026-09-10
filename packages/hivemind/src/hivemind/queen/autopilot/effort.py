"""Define effort_for: the Effort an awake episode gets, keyed by the inbox item's own event class.

Codingrules section 8.8: "sets the effort for any awake episode it hands off by event class."
`effort_for` is that one table: an Alarm reaching `NEEDS_JUDGEMENT` gets `Effort.HIGH` (the Queen
is the last chance to resolve it before it reaches the human), a blocking Question gets
`Effort.MEDIUM` (routine but worth a real answer), and everything else -- a Heartbeat, a routine
task report, an unrecognised kind -- gets `Effort.LOW`. A table, not a chain of `if`s, so a new
event class is one row, not a new branch.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package (which never imports `hivemind.llm`). Called by
    `hivemind.queen.queen.Queen`'s tick once `hivemind.queen.autopilot.table.decide` returns
    `NEEDS_JUDGEMENT`, to pick the `Effort` `hivemind.queen.awake.episode.decide_awake` runs the
    model at. Calls into `hivemind.forage.slots` only.

Key invariants:
    - `effort_for` is total: every `hivemind.supervision.attendant.InboxKind` value it is given
      resolves to a member of `Effort`, via the table's own default for anything not listed.

See Also:
    - .claude/codingrules.md section 8.8 for "sets the effort for any awake episode... by event
      class".
    - hivemind.forage.slots for Effort, the enum this table's values are drawn from.
    - hivemind.supervision.attendant for InboxKind, the closed set `kind` names a member of.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.forage.slots import Effort
from hivemind.supervision.attendant import InboxKind

# Alarms are the Queen's last chance before an issue reaches the human: worth the strongest
# thinking she can afford. Questions are routine but deserve a real answer, not a shrug. Every
# other event class (Heartbeat, routine progress, an unrecognised kind) gets the cheapest rung.
_EFFORT_BY_KIND: Mapping[InboxKind, Effort] = {
    InboxKind.ALARM: Effort.HIGH,
    InboxKind.QUESTION: Effort.MEDIUM,
}
_DEFAULT_EFFORT = Effort.LOW  # Everything else: routine traffic, never worth more than this.

__all__ = ["effort_for"]


def effort_for(kind: InboxKind) -> Effort:
    """Return the Effort an awake episode should run at for `kind`'s own event class.

    Args:
        kind: The InboxKind of the item that triggered NEEDS_JUDGEMENT.

    Returns:
        Effort.HIGH for an Alarm, Effort.MEDIUM for a Question, Effort.LOW for everything else.
    """
    return _EFFORT_BY_KIND.get(kind, _DEFAULT_EFFORT)
