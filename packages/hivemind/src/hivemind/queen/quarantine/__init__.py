"""The Queen's side of a quarantine: order one, and hold the task when its Warden reports it held.

ADR-0035's quarantine (roadmap step 10.6c) is one intervention carried out by one code path in the
Warden (`hivemind.wardens.quarantine`). The Queen, the Hive's orchestrator, only orders it and then
keeps her task store true: `order` sends a Warden the `Intervene(QUARANTINE)` (a
`QueenAction.QUARANTINE_BEE` decision, or a Guard report she acts on) and `hold` moves the task to
PAUSED in the Brood Chamber when the Warden reports it held (a `QueenAction.PAUSE_TASK` decision on
a `task.progress` at stage PAUSED). She is told of every quarantine, hers or a Warden's own, by the
SECURITY Alarm the Warden raises, which her escalation policy sends on to the human.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called by
    `hivemind.queen.queen`'s tick and `hivemind.queen.ticks.alarms`. Calls into
    `hivemind.brood_chamber`, `hivemind.queen.deps`, `hivemind.queen.trail`,
    `hivemind.supervision` and waggle only.

Key invariants:
    - The Queen never quarantines a bee herself: she has no session and no sub-bee, only the order.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.wardens.quarantine for the one code path.

Public API (roadmap step 10.6c):
    - order_quarantine, lever_from_alarm: send a Warden the order, and read one off an Alarm
      (order).
    - hold_task, MAX_HOLD_REASON_CHARS: move a task its Warden reports held to PAUSED (hold).
"""

from hivemind.queen.quarantine.hold import MAX_HOLD_REASON_CHARS, hold_task
from hivemind.queen.quarantine.order import lever_from_alarm, order_quarantine

__all__ = ["MAX_HOLD_REASON_CHARS", "hold_task", "lever_from_alarm", "order_quarantine"]
