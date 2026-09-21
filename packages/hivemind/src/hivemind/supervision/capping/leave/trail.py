"""Define leave_decided_payload: the capping.leave_decided trail payload for one leave decision.

Split out of `hivemind.supervision.capping.gate` (codingrules section 5.1 file-size limit; that
module's own `_record_event` still does the actual trail write, this function only builds the
payload it writes). Roadmap step 5.0c: "record the decision... with the path, the verdict, the
rule that fired and, for HUMAN, the answer; never file contents" -- `path_class` is "the rule that
fired" (which row of `leave-policy.toml` decided this), and `human_answer` (roadmap step 5.0d) is
present only once `hivemind.supervision.capping.checks.human.HumanCheck.resolve` actually asked.
Never `decision.reason`: codingrules section 12 keeps human-readable text off the trail.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Called by `hivemind.supervision.capping.gate` once per `ApplyResult.leave_decisions`
    entry. Calls into this package's own `.persist` module only.

Key invariants:
    - Every value is an id, an enum's own `.value` or a bool -- never `decision.reason`, matching
      `hivemind.supervision.capping.gate`'s own "Key invariants" for every other `capping.*` event.

See Also:
    - .claude/roadmap.md step 5.0c for the trail sentence this function implements, verbatim.
    - hivemind.supervision.capping.gate for _record_event, this function's one caller's caller.
    - hivemind.supervision.capping.leave.persist for LeaveDecisionRecord.
"""

from __future__ import annotations

from hivemind.supervision.capping.leave.persist import LeaveDecisionRecord

__all__ = ["leave_decided_payload"]


def leave_decided_payload(decision: LeaveDecisionRecord) -> dict[str, str | bool]:
    """Build the capping.leave_decided trail payload for one path's own leave decision.

    Args:
        decision: One `ApplyResult.leave_decisions` entry.

    Returns:
        `path`, `path_class`, `verdict`, `persisted`, and `human_answer` when a HumanCheck ran.
    """
    payload: dict[str, str | bool] = {
        "path": decision.path,
        "path_class": decision.path_class.value,
        "verdict": decision.verdict.value,
        "persisted": decision.persisted,
    }
    if decision.human_answer is not None:
        payload["human_answer"] = decision.human_answer.value
    return payload
