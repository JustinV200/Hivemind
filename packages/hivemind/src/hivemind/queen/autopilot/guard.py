"""Decide a Guard request by rule: a dire pattern isolates (or cuts) at once, else needs judgement.

ADR-0043 (roadmap step 10.6a): a Guard request is the Queen's to decide, by autopilot first and by
an awake episode second, like every item in her inbox (codingrules 8.8). The rule is short on
purpose. A report whose rule the manifest lists among `[guard] dire_patterns` is decided without a
model: ISOLATE_CELL when the report's target Cell is known, QUARANTINE_BEE when it names only a
bee's task on no Cell the Queen can reach. A report whose rule `[guard.sting_cut] dire_patterns`
lists, with its target Cell known, is decided STING_CUT (roadmap step 13.4a): isolate the Cell, then
cut it off, under every condition the cut path keeps. Every other report needs judgement, and her
awake episode may answer with one of `AWAKE_ACTIONS` only. When no episode can run (her model is
clustered, it fails, or it answers outside that set), `fallback_action` is ISOLATE_CELL, because
isolation only removes access: it pauses and cuts off, destroys nothing, and the human can lift it.
Whether the target is the Hive Stand is not decided here: the `isolation` enforcement point refuses
the Queen there, and her decision falls back to quarantine and a placement hold.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package, which never imports `hivemind.llm`. Called by
    `hivemind.queen.guard_requests.decision`. Calls into `hivemind.guard` (GuardReport) and the
    sub-package's own actions only.

Key invariants:
    - Pure: the same report, patterns and target always give the same action.
    - Never returns DISMISS: only a judgement dismisses a request; a rule never does.
    - STING_CUT only with a known target Cell; a Sting Cut pattern without one falls back.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - hivemind.manifest.schema.security.guard for `[guard] dire_patterns`.
"""

from __future__ import annotations

from collections.abc import Set

from hivemind.guard import GuardReport
from hivemind.queen.autopilot.actions import QueenAction

# What her awake episode may decide on a Guard request; anything else falls back. STING_CUT is
# roadmap 13.4a's "by awake decision with the report attached otherwise", capped like her rule's.
AWAKE_ACTIONS = frozenset(
    {
        QueenAction.ISOLATE_CELL,
        QueenAction.STING_CUT,
        QueenAction.QUARANTINE_BEE,
        QueenAction.DISMISS,
    }
)
# The actions that act on the report's Cell and are unusable without one.
CELL_ACTIONS = frozenset({QueenAction.ISOLATE_CELL, QueenAction.STING_CUT})

__all__ = ["AWAKE_ACTIONS", "CELL_ACTIONS", "decide_guard_request", "fallback_action"]


def decide_guard_request(
    report: GuardReport,
    dire_patterns: Set[str],
    has_target_cell: bool,
    sting_cut_patterns: Set[str] = frozenset(),
) -> QueenAction:
    """Return the rule's action on `report`, or NEEDS_JUDGEMENT for an awake episode.

    Args:
        report: The Guard request's report.
        dire_patterns: `[guard] dire_patterns`: the rule keys decided without a model.
        has_target_cell: Whether the report's Cell is known (named, or its task placed on one).
        sting_cut_patterns: `[guard.sting_cut] dire_patterns`: the keys decided STING_CUT.

    Returns:
        STING_CUT for a Sting Cut pattern on a known Cell; ISOLATE_CELL or QUARANTINE_BEE for a
        dire pattern; NEEDS_JUDGEMENT otherwise.
    """
    if report.rule in sting_cut_patterns and has_target_cell:
        return QueenAction.STING_CUT
    if report.rule not in dire_patterns and report.rule not in sting_cut_patterns:
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
