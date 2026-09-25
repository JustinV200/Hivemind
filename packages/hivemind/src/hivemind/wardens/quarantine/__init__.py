"""Quarantine one sub-bee: ADR-0043's one intervention, and its one way out.

A bee that may have read an injected instruction is quarantined by its Warden (the always-on
supervisor of its Cell) through one code path (roadmap step 10.6c): checkpoint it, cancel it and
kill its tracked runtime (a command in flight dies with it), revoke its slice of the grant, record
`warden.intervened`, taint every checkpoint, Handoff, episode record and Bee Bread entry of the bee
and its task from the suspect episode on (`memory.tainted`, through
`hivemind.memory.taint.taint_memory`), hold the task PAUSED, and tell the Queen with a SECURITY
Alarm. Three things can order it: the Queen (the orchestrator) with `Intervene(QUARANTINE)`, this
Warden's own escalation policy row for a sub-bee's Alarm, and `Warden.intervene(child,
Quarantine(...))`. The only way out is a respawn from the checkpoint once a judge has cleared it
(`admit_respawn`).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package.
    Called by `hivemind.wardens.warden.Warden` (its tick's QUARANTINE and SPAWN dispatch, and its
    `intervene`). Calls into `hivemind.guard`, `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.supervision`, `hivemind.wardens.ticks.alarms`/`.trail_ship` and waggle; no tick
    module imports this package, so the dependency runs one way.

Key invariants:
    - Nothing else in the Hive composes a quarantine by hand: every caller goes through
      `quarantine_bee`, and only this package labels memory with `TaintSource.QUARANTINE`.
    - Checked at `EnforcementPoint.QUARANTINE` before anything is cut.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.supervision.intervention for the Quarantine lever and its wire form.
    - hivemind.queen.quarantine for the Queen's side: ordering one, and pausing the task.

Public API (roadmap step 10.6c):
    - QuarantineOrder, queen_order, own_policy_order, lever_order: one order, and the three ways
      a Warden gets one (order).
    - authorize, refuse_respawn, SUB_BEE_SCOPE, CHECKPOINT_SCOPE: the `quarantine` point
      (authority).
    - write_quarantine_checkpoint: the Handoff written before anything is cut (checkpoint).
    - QuarantineRecord: what the Warden holds about a quarantined task (record).
    - record_intervened, report_paused, raise_security_alarm: the trail row and the Queen's two
      reports (report).
    - quarantine_bee, carry_out, quarantine_child: the one path and its entry points (path).
    - admit_respawn: the only way out (gate).
"""

from hivemind.wardens.quarantine.authority import (
    CHECKPOINT_SCOPE,
    SUB_BEE_SCOPE,
    authorize,
    refuse_respawn,
)
from hivemind.wardens.quarantine.checkpoint import write_quarantine_checkpoint
from hivemind.wardens.quarantine.gate import admit_respawn
from hivemind.wardens.quarantine.order import (
    QuarantineOrder,
    lever_order,
    own_policy_order,
    queen_order,
)
from hivemind.wardens.quarantine.path import carry_out, quarantine_bee, quarantine_child
from hivemind.wardens.quarantine.record import QuarantineRecord
from hivemind.wardens.quarantine.report import (
    raise_security_alarm,
    record_intervened,
    report_paused,
)

__all__ = [
    "CHECKPOINT_SCOPE",
    "SUB_BEE_SCOPE",
    "QuarantineOrder",
    "QuarantineRecord",
    "admit_respawn",
    "authorize",
    "carry_out",
    "lever_order",
    "own_policy_order",
    "quarantine_bee",
    "quarantine_child",
    "queen_order",
    "raise_security_alarm",
    "record_intervened",
    "refuse_respawn",
    "report_paused",
    "write_quarantine_checkpoint",
]
