"""Carry out the Queen's two answers to a Nurse Bee rejection: send it back, or escalate it.

ADR-0061: a Warden's Nurse Bee that still asks for changes fails the attempt with its review
attached, and the Queen's autopilot table (`hivemind.queen.autopilot.table`, reading wire fields
only) decides `RETRY_TASK` while the task may still be sent back and `ESCALATE_TO_HUMAN` once it
may not. `send_back` counts the round in the Brood Chamber and sends a fresh Worker to the same
Cell with the findings; `escalate_unresolved` puts the findings in front of the human and closes
the task out. A package because `hivemind.queen.ticks` already holds the ten modules codingrules
5.6 allows.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen._act_on_task_result`. Calls into this package's
    `send_back` and `unresolved` only.

Key invariants:
    - This file holds re-exports and __all__ only (codingrules section 5.4).

See Also:
    - docs/adr/0061-the-nurse-bee-reviews-an-attempt-in-a-fresh-context.md for the decision.
    - hivemind.wardens.ticks.results for the Warden side, which sends the rejection.

Public API (ADR-0061):
    - send_back: count a Nurse round and retry the task with the findings (send_back).
    - escalate_unresolved: the human inbox gets the findings, and the task fails (unresolved).
"""

from hivemind.queen.ticks.nurse.send_back import send_back
from hivemind.queen.ticks.nurse.unresolved import escalate_unresolved

__all__ = ["escalate_unresolved", "send_back"]
