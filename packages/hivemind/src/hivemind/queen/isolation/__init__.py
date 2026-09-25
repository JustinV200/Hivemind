"""Isolate a Cell, the Queen's action alone, and lift it again, the human's (roadmap step 10.6a).

ADR-0043, "Only the Queen isolates a Cell": a Guard Bee requests, a Warden escalates, the human
orders; the Queen carries every isolation out through one code path (`path.isolate_cell`), checked
at the Guard's `isolation` enforcement point (`authority`). An isolation revokes the Cell's Warden
grants and writes a BLOCK Cell Wax note (`access`), checkpoints and pauses every bee on it with a
bounded wait for their answers (`pause`), cuts a Virtual Cell's egress to its Waggle link, records
`cell.isolated` (`record`, where the Cell's two states live), taints its memory from the first
cited event and orders its Warden to taint the store it keeps inside the Cell (`taint`), and tells
the human (`site`). The lease and its scratch stay intact for
forensics. The Hive Stand's own lease is isolated only by the human, and only the human lifts an
isolation (`lift`), through the Queen's door (`door`); a lift leaves tainted memory tainted.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by the Queen's Guard request decision (`hivemind.queen.guard_requests.decision`), her Alarm
    handling (`hivemind.queen.ticks.alarms`) and the Hive Entrance's isolation routes (through
    `IsolationDoor`, which the Queen inherits). Calls into `hivemind.brood_chamber`,
    `hivemind.cell`, `hivemind.forage`, `hivemind.guard`, `hivemind.hive`, `hivemind.memory`,
    `hivemind.pheromone`, `hivemind.queen.chat`, `hivemind.queen.forage`,
    `hivemind.queen.placement`, `hivemind.supervision` and waggle.

Key invariants:
    - Every isolation goes through `isolate_cell`; nothing composes its steps by hand elsewhere.
    - Nothing but the human's door lifts an isolation.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/isolation.md for the operator's view.
    - .claude/codingrules.md Appendix C, "Cell isolation" row.

Public API:
    - isolate_cell: the one path (path).
    - lift_isolation: the human's lift (lift).
    - IsolationDoor: both levers as Queen methods (door).
    - IsolationSite, alert_human: what a path runs against, and its SECURITY Alarm (site).
    - IsolationOrder, IsolationOutcome, LiftOutcome, Isolator, IsolationRefusal,
      MAX_ISOLATION_REASON_CHARS, MAX_EVIDENCE_EVENTS: the order and its answers (order).
    - IsolationState, IsolationRecord, TRANSITIONS, can_transition, read_isolation,
      ISOLATED_KIND, LIFTED_KIND: a Cell's two states, on the trail (record).
    - authorize_isolation, cell_capability, is_hive_stand: the enforcement point (authority).
    - resend_taint_order: the Warden's taint order sent again when it attaches while its Cell's
      isolation stands (taint).
"""

from hivemind.queen.isolation.authority import authorize_isolation, cell_capability, is_hive_stand
from hivemind.queen.isolation.door import IsolationDoor
from hivemind.queen.isolation.lift import lift_isolation
from hivemind.queen.isolation.order import (
    MAX_EVIDENCE_EVENTS,
    MAX_ISOLATION_REASON_CHARS,
    IsolationOrder,
    IsolationOutcome,
    IsolationRefusal,
    Isolator,
    LiftOutcome,
)
from hivemind.queen.isolation.path import isolate_cell
from hivemind.queen.isolation.record import (
    ISOLATED_KIND,
    LIFTED_KIND,
    TRANSITIONS,
    IsolationRecord,
    IsolationState,
    can_transition,
    read_isolation,
)
from hivemind.queen.isolation.site import IsolationSite, alert_human
from hivemind.queen.isolation.taint import resend_taint_order

__all__ = [
    "ISOLATED_KIND",
    "LIFTED_KIND",
    "MAX_EVIDENCE_EVENTS",
    "MAX_ISOLATION_REASON_CHARS",
    "TRANSITIONS",
    "IsolationDoor",
    "IsolationOrder",
    "IsolationOutcome",
    "IsolationRecord",
    "IsolationRefusal",
    "IsolationSite",
    "IsolationState",
    "Isolator",
    "LiftOutcome",
    "alert_human",
    "authorize_isolation",
    "can_transition",
    "cell_capability",
    "is_hive_stand",
    "isolate_cell",
    "lift_isolation",
    "read_isolation",
    "resend_taint_order",
]
