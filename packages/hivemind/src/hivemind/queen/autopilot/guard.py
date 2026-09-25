"""Decide a Guard request by rule: a dire pattern isolates at once, anything else needs judgement.

ADR-0043 (roadmap step 10.6a): a Guard request is the Queen's to decide, by autopilot first and by
an awake episode second, like every item in her inbox (codingrules 8.8). The rule is short on
purpose. A report whose rule the manifest lists among `[guard] dire_patterns` is decided without a
model: ISOLATE_CELL when the report's target Cell is known, QUARANTINE_BEE when it names only a
bee's task on no Cell the Queen can reach. Every other report needs judgement, and her awake
episode may answer with one of `AWAKE_ACTIONS` only. When no episode can run (her model is
clustered, it fails, or it answers outside that set), `fallback_action` is ISOLATE_CELL, because
isolation only removes access: it pauses and cuts off, destroys nothing, and the human can lift
it. Whether the target is the Hive Stand is not decided here: the `isolation` enforcement point
refuses the Queen there, and her decision falls back to quarantine and a placement hold.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package, which never imports `hivemind.llm`. Called by
    `hivemind.queen.guard_requests.decision`. Calls into `hivemind.guard` (GuardReport) and the
    sub-package's own actions only.

Key invariants:
    - Pure: the same report, patterns and target always give the same action.
    - Never returns DISMISS: only a judgement dismisses a request; a rule never does.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.manifest.schema.security.guard for `[guard] dire_patterns`.
"""

from __future__ import annotations

from collections.abc import Set

from hivemind.guard import GuardReport
from hivemind.queen.autopilot.actions import QueenAction

# What her awake episode may decide on a Guard request; anything else falls back.
AWAKE_ACTIONS = frozenset(
    {QueenAction.ISOLATE_CELL, QueenAction.QUARANTINE_BEE, QueenAction.DISMISS}
)

__all__ = ["AWAKE_ACTIONS", "decide_guard_request", "fallback_action"]


def decide_guard_request(
    report: GuardReport, dire_patterns: Set[str], has_target_cell: bool
) -> QueenAction:
    """Return the rule's action on `report`, or NEEDS_JUDGEMENT for an awake episode.

    Args:
        report: The Guard request's report.
        dire_patterns: `[guard] dire_patterns`: the rule keys decided without a model.
        has_target_cell: Whether the report's Cell is known (named, or its task placed on one).

    Returns:
        ISOLATE_CELL or QUARANTINE_BEE for a dire pattern; NEEDS_JUDGEMENT otherwise.
    """
    if report.rule not in dire_patterns:
        return QueenAction.NEEDS_JUDGEMENT
    return fallback_action(has_target_cell)


def fallback_action(has_target_cell: bool) -> QueenAction:
    """Return what the Queen does on a Guard request without a judgement: isolate when she can.

    Args:
        has_target_cell: Whether the report's Cell is known.

    Returns:
        ISOLATE_CELL with a target Cell; QUARANTINE_BEE when the report names only bees or tasks
        on no Cell she can reach (a report's validator guarantees such a request names one).
    """
    return QueenAction.ISOLATE_CELL if has_target_cell else QueenAction.QUARANTINE_BEE
