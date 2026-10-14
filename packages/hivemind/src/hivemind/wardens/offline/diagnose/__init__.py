"""Search for why a cut-off Warden's link to the Queen went, with a Drone from its own pool.

A Warden (the always-on supervisor of one Cell, a unit of compute) whose floor when cut off is to
retry the link with backoff and keep working (ADR-0053) does more where it can think: a Nuc, a
colonized device that also runs a model server of its own, sends one short-lived Drone with the
objective "diagnose and, if possible, restore the link to the Queen" (roadmap 11.9a, ADR-0056).
The Drone does ordinary Capping-gated scratch work through its Cell's session, bound by the
Cell's access level and the Guard like any bee; it is the Warden's own task on the Warden's own
local pool, so nothing is asked of the Queen and nothing about it is sent up as a task of hers. A
search is bounded by `[supervision] reconnect_budget`, attempts and wall-clock, carved out of
`max_offline_s` and never added to it. It ends FIXED when the link is back and a cause was named,
DIAGNOSED when a cause is known and the link is still down, and NO_CAUSE otherwise; the outcome is
`warden.diagnosed` on the Warden's trail and the findings are Nectar, queued behind the link. A
Warden with no model of its own sends no Drone and records nothing. A package, because the pure
judgement, the task a Drone is given, the Drone's life, the record and the tick that drives them
are five concepts that together outgrow one file (codingrules 5.2).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Called by `hivemind.wardens.offline.resume.reconcile` once per tick.
    Calls into `hivemind.wardens.spawn` (the local spawn), `hivemind.wardens.ticks` (the Cell
    context, retiring a sub-bee, sending Nectar), `hivemind.wardens.offline.freeze` (recording an
    event), `hivemind.forage` and waggle only.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.
    - A Drone never touches the Royal Jelly Lab or the Quarantine Comb, asks nobody a question and
      is never given more than the Cell's access level allows a connected bee.
    - The budget is carved out of the offline limit; Clustering keeps its deadline whatever the
      search is doing.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the decision.
    - hivemind.wardens.spawn.local for the spawn of a task the Warden minted itself.
    - hivemind.queen.swarm_gate.unreachable.hive_stand for the other end's search.

Public API (roadmap step 11.9a):
    - investigate: the tick's one call; drives the search a notch (step).
    - Verdict, Phase, Move, Finding, Snapshot, LocalBinding, decide_move, decide_verdict,
      local_binding, read_finding, and the waits VERIFY_S and LINGER_S: the pure judgement (decide).
    - Investigation: the record of one outage's search (state).
    - CEILING, LocalPlan, diagnosis_objective, local_assignment: the Drone's task and grant
      (assign).
    - Start, start_drone, has_ended, collect, stop_drone: the Drone's life (drone).
    - Judged, findings_document, deposit_findings, record_verdict: the outcome's record (report).
"""

from hivemind.wardens.offline.diagnose.assign import (
    CEILING,
    LocalPlan,
    diagnosis_objective,
    local_assignment,
)
from hivemind.wardens.offline.diagnose.decide import (
    LINGER_S,
    VERIFY_S,
    Finding,
    LocalBinding,
    Move,
    Phase,
    Snapshot,
    Verdict,
    decide_move,
    decide_verdict,
    local_binding,
    read_finding,
)
from hivemind.wardens.offline.diagnose.drone import (
    Start,
    collect,
    has_ended,
    start_drone,
    stop_drone,
)
from hivemind.wardens.offline.diagnose.report import (
    Judged,
    deposit_findings,
    findings_document,
    record_verdict,
)
from hivemind.wardens.offline.diagnose.state import Investigation
from hivemind.wardens.offline.diagnose.step import investigate

__all__ = [
    "CEILING",
    "LINGER_S",
    "VERIFY_S",
    "Finding",
    "Investigation",
    "Judged",
    "LocalBinding",
    "LocalPlan",
    "Move",
    "Phase",
    "Snapshot",
    "Start",
    "Verdict",
    "collect",
    "decide_move",
    "decide_verdict",
    "deposit_findings",
    "diagnosis_objective",
    "findings_document",
    "has_ended",
    "investigate",
    "local_assignment",
    "local_binding",
    "read_finding",
    "record_verdict",
    "start_drone",
    "stop_drone",
]
