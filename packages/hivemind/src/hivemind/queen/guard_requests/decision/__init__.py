"""Decide a Guard request on the Queen's tick: by rule, by one awake episode, or by the fallback.

ADR-0035 (roadmap step 10.6a): a Guard Bee only requests; the Queen decides. `decide` is the one
entry her tick calls for every GUARD_REQUEST item: it finds the request's target (`target`), rules
on it (`hivemind.queen.autopilot.guard`: a dire pattern isolates without a model), judges the rest
in one awake episode with the report's facts attached (`judge`), falls back to isolation when no
episode can decide, records `queen.decided`, carries the decision out (`act`: the one isolation
path, the Hive Stand's fallback in `hive_stand`, a quarantine order in `quarantine`, or nothing),
stamps the request's row with what came of it (`outcome`) and puts an acted-on or CRITICAL report
in front of the human as a SECURITY Alarm.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package; imported by `hivemind.queen.queen` directly, never by the
    guard_requests face (which `hivemind.queen.deps` imports). Calls into `hivemind.brood_chamber`,
    `hivemind.guard`, `hivemind.llm.errors`, `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.queen.autopilot`, `hivemind.queen.awake`, `hivemind.queen.cluster`,
    `hivemind.queen.isolation`, `hivemind.queen.quarantine`, `hivemind.queen.trail`,
    `hivemind.supervision` and waggle.

Key invariants:
    - A Guard request never isolates or quarantines anything before `queen.decided` is recorded.
    - Each request is decided once; a decided row is never decided again.

See Also:
    - docs/guard/isolation.md for the request path end to end.

Public API (roadmap step 10.6a):
    - decide_guard_item, DECIDED_KIND: the one entry (decide).
    - judge_guard_request, DECISION_HINT, TRIGGER_KIND: the awake episode (judge).
    - carry_out: the decision's effects (act); ActOutcome: what they came to (outcome).
    - hive_stand_fallback, FALLBACK_OUTCOME: the Hive Stand's fallback (hive_stand).
    - quarantine_tasks: the 10.6c orders (quarantine).
    - target_cell, implicated_tasks: what a request is aimed at (target).
"""

from hivemind.queen.guard_requests.decision.act import carry_out
from hivemind.queen.guard_requests.decision.decide import DECIDED_KIND, decide_guard_item
from hivemind.queen.guard_requests.decision.hive_stand import FALLBACK_OUTCOME, hive_stand_fallback
from hivemind.queen.guard_requests.decision.judge import (
    DECISION_HINT,
    TRIGGER_KIND,
    judge_guard_request,
)
from hivemind.queen.guard_requests.decision.outcome import ActOutcome
from hivemind.queen.guard_requests.decision.quarantine import quarantine_tasks
from hivemind.queen.guard_requests.decision.target import implicated_tasks, target_cell

__all__ = [
    "DECIDED_KIND",
    "DECISION_HINT",
    "FALLBACK_OUTCOME",
    "TRIGGER_KIND",
    "ActOutcome",
    "carry_out",
    "decide_guard_item",
    "hive_stand_fallback",
    "implicated_tasks",
    "judge_guard_request",
    "quarantine_tasks",
    "target_cell",
]
