"""Judge a Guard request no rule decides: one awake episode with the report's facts attached.

Codingrules 8.8: an item autopilot cannot decide runs one stateless awake episode, assembled from
durable state plus the triggering event. For a Guard request (roadmap step 10.6a, ADR-0043) the
event is the report's facts: its id, rule, confidence, recommendation, the Cell, bees, tasks and
grants it names, how many trail events it cites and the rule's own sentence built from ids and
counts. No content reaches the episode, because the trail the report was built from carries none.
The episode may answer ISOLATE_CELL, QUARANTINE_BEE or DISMISS only (`AWAKE_ACTIONS`). When her
awake mode is unavailable (her model's provider is clustered), when the episode fails on every
rung, or when it answers anything else, the decision is the autopilot fallback, ISOLATE_CELL
(QUARANTINE_BEE with no Cell to isolate), because isolation only removes access.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package, which may import `hivemind.llm` (only `autopilot/` may
    not). Called by `.decide`. Calls into `hivemind.cell`, `hivemind.guard`, `hivemind.llm.errors`,
    `hivemind.memory`, `hivemind.queen.autopilot`, `hivemind.queen.awake`, `hivemind.queen.cluster`,
    `hivemind.queen.isolation` (IsolationSite), `hivemind.queen.trail`,
    `hivemind.supervision.attendant` and the sub-package's own model only.

Key invariants:
    - Never raises for a model failure: every failure is the fallback, never a crashed tick.
    - The episode's prompt carries ids, enum values, counts and the rule's own sentence only.

See Also:
    - hivemind.queen.autopilot.guard for the rule and the fallback.
    - hivemind.queen.awake.episode for decide_awake, the one episode runner.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
from hivemind.guard import GuardReport
from hivemind.llm.errors import LLMError
from hivemind.memory import ContextOverflowError, TriggerEvent
from hivemind.queen.autopilot import AWAKE_ACTIONS, QueenAction, effort_for, fallback_action
from hivemind.queen.awake import EpisodeExtras, QueenSources, decide_awake
from hivemind.queen.cluster import awake_available
from hivemind.queen.guard_requests.model import GuardBasis
from hivemind.queen.isolation import IsolationSite
from hivemind.queen.trail import record_event
from hivemind.supervision.attendant import InboxKind
from waggle.ids import CellId

TRIGGER_KIND = "guard.request"  # The episode's trigger kind, as `queen.awake` records it.
# The episode's standing instruction: the three answers and what each one does.
DECISION_HINT = (
    "A Guard request is a security finding the Guard Bee cannot act on alone. Decide exactly one "
    "action: ISOLATE_CELL cuts the Cell off (its grant revoked, its bees paused, nothing placed "
    "there, its egress cut, its memory tainted from the first cited event; only the human can "
    "lift it), QUARANTINE_BEE holds only the implicated bees' tasks and taints their memory, "
    "DISMISS leaves everything as it is (the report stays on the trail and the human sees it). "
    "Put why in `reason`, naming ids only."
)
_EPISODE_FAILURES: tuple[type[Exception], ...] = (LLMError, ContextOverflowError)

__all__ = ["DECISION_HINT", "TRIGGER_KIND", "judge_guard_request"]


async def judge_guard_request(
    site: IsolationSite, report: GuardReport, target: CellId | None
) -> tuple[QueenAction, GuardBasis]:
    """Decide `report` by one awake episode, or by the fallback when none can decide it.

    Args:
        site: The running Queen's collaborators, attached Wardens and human inbox.
        report: The Guard request's report.
        target: The Cell an isolation would cut off; None when none is known.

    Returns:
        The action and whether it was judged (AWAKE) or fell back (FALLBACK).
    """
    deps = site.deps
    fallback = (fallback_action(target is not None), GuardBasis.FALLBACK)
    # Roadmap step 4.9: while her own model's provider is clustered, no episode is attempted.
    if not awake_available(deps.cluster_state, deps):
        return fallback
    event = TriggerEvent(
        kind=TRIGGER_KIND,
        summary=_facts(report, target),
        payload_ref=report.id,
        clearance=HoneyClearance.C1,  # Ids, enum values, counts and a rule-written sentence.
    )
    extras = EpisodeExtras(
        cells_in_play=frozenset({target}) if target is not None else frozenset(),
        system_hint=DECISION_HINT,
    )
    sources = QueenSources(deps.chamber, deps.memory, site.human_inbox)
    try:
        # External await: one model call through the ladder and its fallback chain, seconds.
        effort = effort_for(InboxKind.GUARD_REQUEST)
        decision = await decide_awake(deps, event, sources, effort, extras)
    except _EPISODE_FAILURES:
        return fallback  # Every rung and binding failed: never a crashed tick over a request.
    await record_event(deps, "queen.awake", deps.identity.hive_id, event_kind=TRIGGER_KIND)
    usable = decision.action in AWAKE_ACTIONS and not (
        decision.action is QueenAction.ISOLATE_CELL and target is None
    )
    return (decision.action, GuardBasis.AWAKE) if usable else fallback


def _facts(report: GuardReport, target: CellId | None) -> str:
    """The report's facts for the episode: ids, enum values, counts, the rule's own sentence."""
    events = report.event_ids
    span = events[0] if len(events) == 1 else f"{events[0]} to {events[-1]}"
    return (
        f"Guard request {report.id}: rule {report.rule} fired at {report.confidence.value} "
        f"confidence on {len(events)} trail event(s) ({span}) and recommends "
        f"{report.recommended.value}. Cell: {target or 'none known'}; bees: "
        f"{', '.join(report.bee_ids) or 'none'}; tasks: {', '.join(report.task_ids) or 'none'}; "
        f"grants: {', '.join(report.grant_ids) or 'none'}. The rule's own summary: "
        f"{report.summary}"
    )
