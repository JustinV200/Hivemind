"""Tests for the Queen's decision on a Guard request: rule, judgement, fallback, trail and human.

Roadmap step 10.6a (ADR-0035). A request under a dire pattern isolates by rule, with no model
asked; any other is judged by one awake episode with the report's facts attached, which may
isolate, quarantine or dismiss; when no episode can decide (her model is clustered, it fails, or it
answers outside those three) the fallback isolates. Every decision is `queen.decided` with the
report id, recorded before anything it does, and the request's row is stamped so it is never
decided twice. A report she acted on, or a CRITICAL one, reaches the human as a SECURITY Alarm.

Fits into the Hive:
    Mirrors src/hivemind/queen/guard_requests/decision/ and src/hivemind/queen/autopilot/guard.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.guard_requests.decision for the one entry.
"""

from __future__ import annotations

import json

from builders.isolation import (
    DIRE_RULE,
    JUDGED_RULE,
    isolation_site,
    make_guard_report,
    place_running,
)
from builders.queen import make_queen_deps

from hivemind.guard import GuardAction, GuardConfidence, GuardReport
from hivemind.llm import FakeLLMProvider, LLMRequest, LLMResponse, text_response
from hivemind.pheromone import TrailQuery
from hivemind.queen.autopilot import QueenAction, decide_guard_request, fallback_action
from hivemind.queen.guard_requests import GuardBasis, GuardDeps, guard_door, guard_items
from hivemind.queen.guard_requests.decision import decide_guard_item
from hivemind.queen.isolation import ISOLATED_KIND, IsolationSite
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import FakeClock

_NO_WAIT = GuardDeps(pause_timeout_s=0.0)


def _judging(action: str) -> FakeLLMProvider:
    """A provider whose every structured answer is one decision naming `action`."""
    decision = {"action": action, "task_id": None, "reason": "Judged.", "binding": None}

    def responder(request: LLMRequest) -> LLMResponse:
        # Raw JSON on the schema rungs, fenced on the prompted one (the ladder's own contract).
        if request.response_schema is not None:
            return text_response(json.dumps(decision))
        return text_response(f"```json\n{json.dumps(decision)}\n```")

    return FakeLLMProvider(responder=responder)


async def _decide(site: IsolationSite, report: GuardReport) -> None:
    """File `report` through the Queen's door, then decide the one item her tick would drain."""
    await guard_door(site.deps).file_guard_request(report)
    [item] = await guard_items(site.deps)
    await decide_guard_item(site, item)


async def test_a_guard_request_never_isolates_without_a_queen_decision_on_the_trail() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=_NO_WAIT)
    report = make_guard_report(clock, cell_id=link.cell.id)

    await _decide(isolation_site(deps, link), report)

    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    assert kinds.index("queen.decided") < kinds.index(ISOLATED_KIND)
    [decided] = await deps.trail.query(TrailQuery(kind="queen.decided"))
    assert decided.payload["report_id"] == report.id
    assert decided.payload["action"] == QueenAction.ISOLATE_CELL.value
    [isolated] = await deps.trail.query(TrailQuery(kind=ISOLATED_KIND))
    assert isolated.payload["decision_event_id"] == decided.id
    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None
    assert (stamped.decision.event_id, stamped.decision.outcome) == (decided.id, "isolated")
    await warden_end.close()


async def test_the_dire_rule_isolates_without_an_awake_episode() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()  # Scripted with nothing: any call would fail loudly.
    deps, link, warden_end = make_queen_deps(clock, fake_provider=provider, guard=_NO_WAIT)
    report = make_guard_report(clock, cell_id=link.cell.id, rule=DIRE_RULE)

    await _decide(isolation_site(deps, link), report)

    assert provider.calls == []
    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None
    assert (stamped.decision.action, stamped.decision.basis) == (
        QueenAction.ISOLATE_CELL,
        GuardBasis.RULE,
    )
    await warden_end.close()


async def test_when_her_awake_mode_is_unavailable_the_fallback_isolates() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    deps, link, warden_end = make_queen_deps(clock, fake_provider=provider, guard=_NO_WAIT)
    deps.cluster_state.mark_clustered("fake")  # Her own model's provider is clustered.
    report = make_guard_report(clock, cell_id=link.cell.id, rule=JUDGED_RULE)

    await _decide(isolation_site(deps, link), report)

    assert provider.calls == []
    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None
    assert (stamped.decision.basis, stamped.decision.outcome) == (GuardBasis.FALLBACK, "isolated")
    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND))
    await warden_end.close()


async def test_a_failing_model_falls_back_to_isolation_too() -> None:
    clock = FakeClock()
    provider = FakeLLMProvider()
    provider.set_outage(True)
    deps, link, warden_end = make_queen_deps(clock, fake_provider=provider, guard=_NO_WAIT)
    report = make_guard_report(clock, cell_id=link.cell.id, rule=JUDGED_RULE)

    await _decide(isolation_site(deps, link), report)

    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None
    assert (stamped.decision.action, stamped.decision.basis) == (
        QueenAction.ISOLATE_CELL,
        GuardBasis.FALLBACK,
    )
    await warden_end.close()


async def test_an_awake_episode_with_the_reports_facts_may_dismiss_it() -> None:
    clock = FakeClock()
    provider = _judging("DISMISS")
    deps, link, warden_end = make_queen_deps(clock, fake_provider=provider, guard=_NO_WAIT)
    report = make_guard_report(clock, cell_id=link.cell.id, rule=JUDGED_RULE)
    site = isolation_site(deps, link)

    await _decide(site, report)

    shown = provider.calls[-1].model_dump_json()
    assert report.id in shown and JUDGED_RULE in shown  # The report's facts, by id.
    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None
    assert (stamped.decision.action, stamped.decision.outcome) == (QueenAction.DISMISS, "dismissed")
    assert stamped.decision.basis is GuardBasis.AWAKE
    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND)) == ()
    assert site.human_inbox.alarms == ()  # Not acted on, not CRITICAL: nothing to push.
    await warden_end.close()


async def test_an_episode_answering_outside_its_three_actions_falls_back() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(
        clock, fake_provider=_judging("RECORD"), guard=_NO_WAIT
    )
    report = make_guard_report(clock, cell_id=link.cell.id, rule=JUDGED_RULE)

    await _decide(isolation_site(deps, link), report)

    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None
    assert (stamped.decision.action, stamped.decision.basis) == (
        QueenAction.ISOLATE_CELL,
        GuardBasis.FALLBACK,
    )
    await warden_end.close()


async def test_a_critical_report_reaches_the_human_even_when_dismissed() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(
        clock, fake_provider=_judging("DISMISS"), guard=_NO_WAIT
    )
    report = make_guard_report(
        clock, cell_id=link.cell.id, rule=JUDGED_RULE, confidence=GuardConfidence.CRITICAL
    )
    site = isolation_site(deps, link)

    await _decide(site, report)

    [alarm] = site.human_inbox.alarms
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)
    assert report.id in alarm.detail
    await warden_end.close()


async def test_a_quarantine_decision_orders_the_implicated_tasks_warden() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(
        clock, fake_provider=_judging("QUARANTINE_BEE"), guard=_NO_WAIT
    )
    task = await place_running(deps, link)
    report = make_guard_report(
        clock,
        cell_id=None,
        rule=JUDGED_RULE,
        recommended=GuardAction.QUARANTINE_BEE,
        task_ids=(task.id,),
    )
    site = isolation_site(deps, link)

    await _decide(site, report)

    intervene = await warden_end.wait_for_intervene()
    assert (intervene.action.value, intervene.task_id) == ("QUARANTINE", task.id)
    assert intervene.suspect_episode_id == report.event_ids[0]
    [alarm] = site.human_inbox.alarms  # Acted on: the human hears of it.
    assert report.id in alarm.detail and alarm.severity is AlarmSeverity.WARNING
    await warden_end.close()


async def test_a_request_is_decided_once() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=_NO_WAIT)
    report = make_guard_report(clock, cell_id=link.cell.id)
    site = isolation_site(deps, link)
    await guard_door(deps).file_guard_request(report)
    [item] = await guard_items(deps)

    await decide_guard_item(site, item)
    await decide_guard_item(site, item)  # The same item twice in one tick: decided once.

    assert len(await deps.trail.query(TrailQuery(kind="queen.decided"))) == 1
    assert not await guard_items(deps)  # Stamped: nothing left for her next tick.
    await warden_end.close()


def test_the_rule_never_dismisses_and_needs_judgement_off_its_patterns() -> None:
    patterns = frozenset({DIRE_RULE})
    dire, judged = make_guard_report(rule=DIRE_RULE), make_guard_report(rule=JUDGED_RULE)

    assert decide_guard_request(dire, patterns, True) is QueenAction.ISOLATE_CELL
    assert decide_guard_request(dire, patterns, False) is QueenAction.QUARANTINE_BEE
    assert decide_guard_request(judged, patterns, True) is QueenAction.NEEDS_JUDGEMENT
    assert fallback_action(True) is QueenAction.ISOLATE_CELL
