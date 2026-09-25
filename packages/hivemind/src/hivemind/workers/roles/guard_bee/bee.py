"""Run the Guard Bee: read the trail on the Queen's tick, evaluate its rules, report what fires.

The Guard Bee (a Worker specialised in security and monitoring, roadmap step 10.6, ADR-0043)
watches from the Queen's process and never touches a Cell: it runs in the Queen's process on the
Hive Stand, driven by her tick like the House Bee's sweep, so no Cell action can take it down and it
always reads the central trail. One round, at most every `[guard.bee] interval_s`: read what is new
on the trail (the first round after a start rebuilds the windows and what was already reported),
reap the verdict of an awake episode that finished, evaluate every rule, report each finding on
the rule's own verdict, or queue it for one awake episode on the judge slot when the rule asks for
judgement, and start the next episode beside the tick. Its role set is fixed and small: it reads
the trail in-process, which no capability gates, and calls `llm:judge`, so it is built with a
judge only while the Guard policy's `guard_bee` role holds `llm:judge` (a rule's own verdict stands
otherwise). It is not a `hivemind.workers.Worker`: a Worker is handed a Cell and a session, which is
exactly what this one must never hold.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.guard_bee`. Built by the
    composition root with `build_guard_bee` (the Queen's request door, her trail, her call gate)
    and held on `hivemind.queen.deps.QueenDeps.guard_bee`; `hivemind.queen.ticks.guard_bee` calls
    `tick` once per Queen tick, and the composition root calls `aclose` once the Queen has stopped.
    Calls into `hivemind.cell` (CellIdentity), `hivemind.forage` (ModelSlot), `hivemind.guard`,
    `hivemind.llm` (BoundModel, CallGate, UnresolvableSlotError),
    `hivemind.manifest.schema.security.guard` (GuardSection), `hivemind.pheromone`,
    `hivemind.supervision.capping` (TierTable), `hivemind.workers.errors` and every module of
    this package.

Key invariants:
    - It holds the trail, a clock, the Queen's request door, a C2 sink and a judge (a slot
      resolver and the Queen's call gate): no Cell, no session, no Waggle link, no grant.
    - A round raises nothing but `GuardBeeError`, so the Queen's tick is never taken down by it.
    - It never awaits a model inside a round: an episode runs in its lane, beside the tick.

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/guard-bee.md for the rules and what each recommends.
    - hivemind.queen.ticks.housekeeping for the House Bee's sweep, the shape this mirrors.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from hivemind.cell import CellIdentity
from hivemind.common.logging import get_logger
from hivemind.forage import ModelSlot
from hivemind.guard import (
    Capability,
    GuardConfidence,
    GuardPolicy,
    GuardRequestDoor,
    role_set,
    worker_role_name,
)
from hivemind.llm import BoundModel, CallGate, UnresolvableSlotError
from hivemind.manifest.schema.security.guard import GuardSection
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision.capping import TierTable
from hivemind.workers.errors import GuardBeeError
from hivemind.workers.roles.guard_bee.evaluate import Finding, allowed_actions, evaluate, targets_of
from hivemind.workers.roles.guard_bee.judge import JudgeCase, ModelGuardJudge, Verdict
from hivemind.workers.roles.guard_bee.lane import JudgeLane
from hivemind.workers.roles.guard_bee.requests import CAP_WINDOW, RequestLedger
from hivemind.workers.roles.guard_bee.respond import (
    AuditRaise,
    FindingResponder,
    ResponderDeps,
    Response,
)
from hivemind.workers.roles.guard_bee.rules import GuardRules, load_guard_rules
from hivemind.workers.roles.guard_bee.sink import GuardReportSink, InMemoryGuardReportSink
from hivemind.workers.roles.guard_bee.watch import TrailWatch
from waggle.clock import Clock
from waggle.messages.task import WorkerRole

GUARD_BEE_ROLE = worker_role_name(WorkerRole.GUARD_BEE)  # Its policy role: "guard_bee".
JUDGE_NEED = Capability.parse(f"llm:{ModelSlot.JUDGE.manifest_key}")  # What its episodes need.

log = get_logger(__name__)

__all__ = [
    "GUARD_BEE_ROLE",
    "JUDGE_NEED",
    "GuardBee",
    "GuardBeeInputs",
    "GuardBeeParts",
    "build_guard_bee",
]


@dataclass(frozen=True, slots=True)
class GuardBeeInputs:
    """What the composition root hands `build_guard_bee` (codingrules 5.1's argument group).

    Attributes:
        trail: The Queen's own trail: the central trail every node's segment merges into. With
            a Virtual side, the Night Veil boundary's `VeiledTrail`: the watch also reads every
            living Night Veil Cell's segment through it, and an alert about such a Cell is kept
            in that segment and dies with it.
        clock: The Hive's clock.
        identity: The Hive, the Queen's node and the actor ("system") its events carry.
        door: The Queen's door for a Guard request (`GuardRequestDoor`), her own implementation.
        guard: The manifest's `[guard]` section: the request floor and cap, and `[guard.bee]`.
        tiers: The Hive's Capping tier table, the rates a raise starts from.
        policy: The Guard policy; its `guard_bee` role decides whether episodes may run.
        bound_for: Resolves a model slot (the Queen's `QueenDeps.bound_for`).
        call_gate: The Queen's own call gate (`QueenDeps.call_gate`): the Royal Reserve's seats.
        sink: The C2 seam every report is deposited through; in memory until phase 7.
    """

    trail: PheromoneTrail
    clock: Clock
    identity: CellIdentity
    door: GuardRequestDoor
    guard: GuardSection
    tiers: TierTable
    policy: GuardPolicy
    bound_for: Callable[[ModelSlot], BoundModel]
    call_gate: CallGate
    sink: GuardReportSink = field(default_factory=InMemoryGuardReportSink)


@dataclass(frozen=True, slots=True)
class GuardBeeParts:
    """The Guard Bee's working parts, built by `build_guard_bee` (or by a test directly)."""

    trail: PheromoneTrail  # The trail it reads.
    clock: Clock  # Its clock.
    rules: GuardRules  # What it evaluates.
    watch: TrailWatch  # What it has read.
    responder: FindingResponder  # How it reports, and what it has reported.
    lane: JudgeLane | None  # Its awake episodes; None when it runs on rule verdicts alone.
    interval_s: float  # [guard.bee] interval_s.


class GuardBee:
    """Read the trail, evaluate the rules, report each finding: one round per Queen tick when due.

    Owns its own small state (codingrules 8.5): when its last round began. Everything else it
    remembers lives in its parts, each owning its own.
    """

    def __init__(self, parts: GuardBeeParts) -> None:
        """Build a Guard Bee whose first round is due at once.

        Args:
            parts: Its trail, clock, rules, watch, responder, lane and cadence.
        """
        self._parts = parts
        self._last_round: datetime | None = None

    @property
    def rules(self) -> GuardRules:
        """The rules it evaluates, overrides applied."""
        return self._parts.rules

    async def tick(self) -> tuple[Response, ...]:
        """Run one round when one is due; the Queen's tick calls this on every tick.

        Returns:
            Every report the round made, and what became of each; nothing when not due.

        Raises:
            GuardBeeError: The round failed; the next due round tries again.
        """
        now = self._parts.clock.now()
        last = self._last_round
        if last is not None and (now - last).total_seconds() < self._parts.interval_s:
            return ()  # Not due: the Queen's next tick asks again.
        # Advanced first, so a failing round is retried an interval later, not on every tick.
        self._last_round = now
        try:
            return await self._round(now)
        except GuardBeeError:
            raise
        except Exception as exc:
            # SAFETY: the top of the Guard Bee's run loop, one of codingrules 10's three places: it
            # runs inside the Queen's tick, so a failed round becomes the one typed error her tick
            # catches by name (hivemind.queen.ticks.guard_bee), never an exception ending her loop.
            raise GuardBeeError(f"A Guard Bee round failed: {type(exc).__name__}.") from exc

    async def flush(self) -> tuple[Response, ...]:
        """Run every queued awake episode to its end now, and report each finding.

        Returns:
            The reports made from those verdicts.
        """
        lane = self._parts.lane
        finished = await lane.drain() if lane is not None else []
        return tuple([await self._answer(case, verdict) for case, verdict in finished])

    async def aclose(self) -> None:
        """Cancel an episode still in flight; its finding is found again after a restart."""
        if self._parts.lane is not None:
            await self._parts.lane.aclose()

    async def _round(self, now: datetime) -> tuple[Response, ...]:
        """Read, reap, evaluate, report or queue for judgement, and start the next episode."""
        parts = self._parts
        own_alerts = await parts.watch.read(parts.trail, now)
        parts.responder.restore(own_alerts)
        lane = parts.lane
        finished = lane.reap() if lane is not None else []
        responses = [await self._answer(case, verdict) for case, verdict in finished]
        pending = lane.pending() if lane is not None else frozenset()
        for finding in evaluate(parts.rules.enabled, parts.watch, now, parts.responder.consumed):
            if (finding.rule.key, finding.key) in pending or self._judge_later(finding):
                continue  # Its verdict comes from an episode, on a later round.
            responses.append(await parts.responder.respond(finding, Verdict.of(finding.rule)))
        if lane is not None:
            lane.start()
        return tuple(responses)

    def _judge_later(self, finding: Finding) -> bool:
        """Queue `finding` for an episode when its rule asks and the lane has room."""
        lane = self._parts.lane
        if lane is None or not finding.rule.judgement:
            return False
        return lane.offer(JudgeCase(finding=finding, allowed=allowed_actions(targets_of(finding))))

    async def _answer(self, case: JudgeCase, verdict: Verdict | None) -> Response:
        """Report a judged finding on the episode's verdict, or on its rule's when there is none."""
        chosen = verdict if verdict is not None else Verdict.of(case.finding.rule)
        return await self._parts.responder.respond(case.finding, chosen)


def build_guard_bee(inputs: GuardBeeInputs) -> GuardBee:
    """Build the Guard Bee from the Queen's parts and the manifest's `[guard]` section.

    Args:
        inputs: The trail, clock, identity, the Queen's door, `[guard]`, the tier table, the
            Guard policy, her slot resolver and her call gate, and the C2 sink.

    Returns:
        A Guard Bee whose first round (on the Queen's next tick) rebuilds its windows.

    Raises:
        GuardRulesError: The shipped rules or a `[guard.bee.rules]` override is invalid.
    """
    section, bee = inputs.guard, inputs.guard.bee
    rules = load_guard_rules(bee.rules)
    longest = max((rule.window_s for rule in rules.enabled), default=0.0)
    alert_horizon_s = max(longest, bee.coalesce_window_s, CAP_WINDOW.total_seconds())
    ledger = RequestLedger(
        GuardConfidence(section.request_confidence),
        section.requests_per_hour,
        bee.coalesce_window_s,
    )
    audit = AuditRaise(tiers=inputs.tiers, step=bee.audit_raise_step, hold_s=bee.audit_raise_hold_s)
    deps = ResponderDeps(
        inputs.trail, inputs.clock, inputs.identity, inputs.door, inputs.sink, audit
    )
    parts = GuardBeeParts(
        trail=inputs.trail,
        clock=inputs.clock,
        rules=rules,
        watch=TrailWatch(rules, inputs.identity.node_id, alert_horizon_s),
        responder=FindingResponder(deps, ledger),
        lane=_judge_lane(inputs, rules),
        interval_s=bee.interval_s,
    )
    return GuardBee(parts)


def _judge_lane(inputs: GuardBeeInputs, rules: GuardRules) -> JudgeLane | None:
    """Build the episode lane, or None when no rule asks or the policy withholds the judge slot."""
    if not any(rule.judgement for rule in rules.enabled):
        return None  # Nothing to judge: every finding is reported on its rule's own verdict.
    if not role_set(inputs.policy, GUARD_BEE_ROLE).allows(JUDGE_NEED):
        # Its role set is the policy's to fix: without llm:judge, rules decide alone.
        log.warning("guard_bee.judge_withheld_by_policy", role=GUARD_BEE_ROLE)
        return None
    judge = ModelGuardJudge(
        _JudgeSlot(inputs.bound_for), inputs.call_gate, inputs.guard.bee.judge_timeout_s
    )
    return JudgeLane(judge)


class _JudgeSlot:
    """Resolve `ModelSlot.JUDGE` for one episode; None, logged once, when the manifest binds none.

    Owns one flag (codingrules 8.5): whether the missing binding was already logged.
    """

    def __init__(self, bound_for: Callable[[ModelSlot], BoundModel]) -> None:
        """Wrap the Queen's slot resolver."""
        self._bound_for = bound_for
        self._warned = False

    def __call__(self) -> BoundModel | None:
        """Return the judge binding, or None when it cannot be resolved."""
        try:
            return self._bound_for(ModelSlot.JUDGE)
        except (UnresolvableSlotError, KeyError):
            if not self._warned:
                # Logged once: an operator who never bound [llm.slots.judge] is told, not spammed.
                log.warning("guard_bee.judge_slot_unbound")
                self._warned = True
            return None
