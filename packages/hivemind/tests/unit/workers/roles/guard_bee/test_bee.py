"""Tests for hivemind.workers.roles.guard_bee.bee: the Guard Bee's round, whole.

A round runs at most every interval; a restart neither re-files what was filed nor misses a burst
that straddles it; a rule that asks for judgement is judged beside the tick, and the episode may
change the report while an outage leaves the rule's verdict; a policy without `llm:judge` for the
guard_bee role runs no episode at all; a failed round is one typed error; and the Guard Bee never
widens anything and never holds a Cell, a session or a Waggle link.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/bee.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.bee for GuardBee and build_guard_bee.
    - tests.unit.workers.roles.guard_bee.test_bee_entrance for a reduction over a real Entrance.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from builders.guard_bee import (
    SHIPPED_RULE_SEEDERS,
    GuardBeeRig,
    RigOptions,
    judge_reply,
    make_guard_bee,
    restart_guard_bee,
    seed_episode,
)

from hivemind.guard import GuardAction, GuardConfidence, load_guard_policy
from hivemind.llm import ProviderUnavailableError
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from hivemind.supervision.capping import AuditRateRaise
from hivemind.workers.roles import guard_bee
from hivemind.workers.roles.guard_bee import Disposition, GuardBeeError

# Where a Cell, a session, a lease, a device link or a supervisor lives: none may be reachable.
_HANDLE_MODULES = (
    "hivemind.cell.local",
    "hivemind.cell.fake",
    "hivemind.cell.session",
    "hivemind.cell.lease",
    "hivemind.cell.in_cell",
    "hivemind.hive",
    "hivemind.swarm",
    "hivemind.wardens",
    "hivemind.queen",
    "hivemind.entrance",
    "waggle.transport",
    "waggle.outbox",
)
_OWN_KINDS = {"guard.alert", "guard.audit_rate_raised", "guard.reduce_ordered"}
_EVERYTHING = TrailQuery(limit=10_000)  # More than any test here records.
_ABUSER = "203.0.113.9"  # The address an invite-abuse burst comes from.


async def _denial_burst(rig: GuardBeeRig) -> None:
    """Seed one bee refused five times: the judged denial_burst rule fires on it."""
    episode = await seed_episode(rig.seed)
    for _ in range(5):
        await rig.seed.denied(episode.bee)


async def _redeem_failures(rig: GuardBeeRig, times: int) -> None:
    """Seed `times` failed invite redemptions from one address."""
    for _ in range(times):
        await rig.seed.entrance(
            "guard.entrance_redeem_failed",
            rig.identity.hive_id,
            reason="unknown_code",
            address=_ABUSER,
        )


async def test_a_round_runs_at_most_every_interval() -> None:
    rig = make_guard_bee()
    await rig.bee.tick()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)

    early = await rig.bee.tick()
    due = await rig.round()

    assert early == ()
    assert [response.disposition for response in due] == [Disposition.FILED]


async def test_a_restart_never_refiles_what_was_filed() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)
    await rig.bee.tick()

    restarted = restart_guard_bee(rig)
    restarted.clock.advance(60.0)
    responses = await restarted.bee.tick()

    assert responses == ()
    assert restarted.door.filed == [] and len(rig.door.filed) == 1
    assert len(await rig.alerts()) == 1


async def test_a_burst_that_straddles_a_restart_is_still_found() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await _redeem_failures(rig, 3)
    assert await rig.bee.tick() == ()  # Half of each burst: nothing yet.

    restarted = restart_guard_bee(rig)
    restarted.clock.advance(30.0)
    await restarted.seed.denied(episode.bee)
    await _redeem_failures(restarted, 2)
    responses = await restarted.bee.tick()

    rules = sorted(response.report.rule for response in responses)
    assert rules == ["injection_then_denial", "invite_abuse"]
    assert len(restarted.door.filed) == 1


async def test_an_episode_may_change_the_report_before_it_is_filed() -> None:
    rig = make_guard_bee()
    rig.judge.script(judge_reply("high", "quarantine_bee"))
    await _denial_burst(rig)

    queued = await rig.bee.tick()
    [response] = await rig.bee.flush()

    assert queued == ()  # Judged beside the tick: nothing reported by the round itself.
    assert response.report.confidence is GuardConfidence.HIGH
    assert response.disposition is Disposition.FILED and rig.door.filed == [response.report]
    [alert] = await rig.alerts()
    assert alert.payload["judged"] is True
    assert (alert.payload["rule_action"], alert.payload["rule_confidence"]) == (
        "quarantine_bee",
        "medium",
    )


async def test_an_episode_that_ends_between_rounds_is_reported_by_the_next_round() -> None:
    rig = make_guard_bee()
    rig.judge.script(judge_reply("low", "observe"))
    await _denial_burst(rig)
    await rig.bee.tick()  # Queues the finding and starts its episode beside the tick.

    for _ in range(20):
        await asyncio.sleep(0)  # The Queen's loop sleeps between ticks; the episode ends.
    [response] = await rig.round()
    again = await rig.round()

    assert (response.report.recommended, response.report.confidence) == (
        GuardAction.OBSERVE,
        GuardConfidence.LOW,
    )
    assert response.disposition is Disposition.OBSERVED and again == ()
    assert len(rig.judge.calls) == 1


async def test_a_judge_outage_leaves_the_rules_own_verdict() -> None:
    rig = make_guard_bee()
    rig.judge.script(ProviderUnavailableError("judge-provider", "down for the test"))
    await _denial_burst(rig)

    await rig.bee.tick()
    [response] = await rig.bee.flush()

    assert response.report.confidence is GuardConfidence.MEDIUM
    assert response.disposition is Disposition.BELOW_FLOOR and rig.door.filed == []
    [alert] = await rig.alerts()
    assert alert.payload["judged"] is False


async def test_a_policy_without_the_judge_slot_runs_no_episode() -> None:
    withheld = GuardSection(roles={"guard_bee": GuardRoleSection(allow=("spend:*",))})
    rig = make_guard_bee(RigOptions(policy=load_guard_policy(section=withheld)))
    await _denial_burst(rig)

    [response] = await rig.bee.tick()

    assert response.report.confidence is GuardConfidence.MEDIUM
    assert rig.judge.calls == []


async def test_a_failed_round_is_one_typed_error_and_the_next_round_runs() -> None:
    class _BrokenTrail(MemoryPheromoneTrail):
        broken = True

        async def query(self, query: TrailQuery) -> tuple[PheromoneEvent, ...]:
            if self.broken:
                raise OSError("disk gone")
            return await super().query(query)

    rig = make_guard_bee()
    trail = _BrokenTrail(rig.clock)
    broken = make_guard_bee(RigOptions(clock=rig.clock, trail=trail))

    with pytest.raises(GuardBeeError, match="OSError"):
        await broken.bee.tick()
    trail.broken = False
    assert await broken.round() == ()


async def test_the_guard_bee_records_only_narrowing_and_alerts_and_files_only_requests() -> None:
    rig = make_guard_bee()
    for seeder in SHIPPED_RULE_SEEDERS.values():
        await seeder(rig, True)
    before = {event.id for event in await rig.trail.query(_EVERYTHING)}

    await rig.bee.tick()
    await rig.bee.flush()

    recorded = [e for e in await rig.trail.query(_EVERYTHING) if e.id not in before]
    raises = [AuditRateRaise.from_event(e) for e in recorded if e.kind == "guard.audit_rate_raised"]
    assert {event.kind for event in recorded} == _OWN_KINDS  # Alerts, raises, reductions: all.
    assert raises and all(r is not None and r.to_rate > r.from_rate for r in raises)
    assert rig.door.filed and all(report.is_request for report in rig.door.filed)


def test_the_guard_bee_imports_nothing_that_holds_a_cell_or_a_link() -> None:
    package = Path(guard_bee.__file__).parent
    imported = set()
    for module in package.glob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    assert not [name for name in imported if name.startswith(_HANDLE_MODULES)]


async def test_the_guard_bee_holds_no_cell_session_or_link() -> None:
    rig = make_guard_bee()
    for seeder in SHIPPED_RULE_SEEDERS.values():
        await seeder(rig, True)
    await rig.bee.tick()

    reachable = set(_reachable_modules(rig.bee))

    assert "hivemind.workers.roles.guard_bee.watch" in reachable  # The walk does reach its parts.
    assert not [module for module in reachable if module.startswith(_HANDLE_MODULES)]


def _reachable_modules(root: object) -> Iterator[str]:
    """Walk every object reachable from `root`'s attributes and yield each one's module."""
    seen: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if id(current) in seen or isinstance(current, str | bytes | int | float | bool | type):
            continue
        seen.add(id(current))
        yield type(current).__module__
        stack.extend(_children(current))


def _children(value: object) -> list[object]:
    """Every object one step from `value`: fields, items, slots, closure cells."""
    if isinstance(value, dict):
        return [*value.keys(), *value.values()]
    if isinstance(value, list | tuple | set | frozenset):
        return list(value)
    children: list[object] = []
    if dataclasses.is_dataclass(value):
        children += [getattr(value, f.name) for f in dataclasses.fields(value)]
    children += list(getattr(value, "__dict__", {}).values())
    for klass in type(value).__mro__:
        for name in getattr(klass, "__slots__", ()):
            if isinstance(name, str) and hasattr(value, name):
                children.append(getattr(value, name))
    closure = getattr(value, "__closure__", None) or ()
    return children + [cell.cell_contents for cell in closure]
