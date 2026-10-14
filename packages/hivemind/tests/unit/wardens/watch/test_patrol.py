"""Tests for hivemind.wardens.watch.patrol: one Patrol, its four outcomes, and SKIPPED.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/patrol.py (codingrules section 3). The port is a
    `ScriptedPatrolPort`, so these tests are about what the Patrol does with a decision: which
    window it reviews, what it keeps and discards, and how it ends. The awake episode the real port
    runs is tested beside it (tests/unit/wardens/awake/test_patrol.py) and the real port in
    test_bridge.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.patrol for the module under test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from builders.watch import (
    ScriptedPatrolPort,
    StubProbe,
    make_memory_context,
    make_observation,
    make_process,
    make_resources,
    make_watch_deps,
)

from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.memory import MemoryContext
from hivemind.wardens.watch import PatrolChoice, PatrolDecision, PatrolOutcome, store
from hivemind.wardens.watch import patrol as patrol_module
from hivemind.wardens.watch.deps import WatchDeps
from hivemind.wardens.watch.patrol import Patrol
from hivemind.wardens.watch.signals import SignalKind
from waggle.clock import FakeClock

_OWNER = "warden_owner"


def _decision(choice: PatrolChoice, **extra: object) -> PatrolDecision:
    """A decision of `choice` with a reason."""
    return PatrolDecision(choice=choice, reason="because", **extra)


class _Rig:
    """A Patrol over an in-memory store, with a scripted port and a clock a test moves."""

    def __init__(self, port: ScriptedPatrolPort) -> None:
        self.clock = FakeClock()
        self.ctx, _trail = make_memory_context(self.clock)
        self.watch: WatchDeps = make_watch_deps(StubProbe(), retention_s=86_400.0)
        self.port = port
        self.patrol = Patrol(self.watch, self.ctx, port, _OWNER)

    async def keep(
        self, name: str, share: float = 0.01, *, wait: bool = True, **parts: object
    ) -> None:
        """Keep one sample `name` now, then (unless `wait` is off) let a minute pass."""
        processes = [make_process(1, name, share, started_at=self.clock.now())]
        observation = make_observation(self.clock.now(), processes=processes, **parts)  # type: ignore[arg-type]
        await store.save(observation, self.ctx, _OWNER, 86_400.0)
        if wait:
            self.clock.advance(60.0)

    async def kept(self) -> list[str]:
        """The names of the samples still kept."""
        span = timedelta(days=30)
        found = await store.load_observations(
            self.ctx, _OWNER, self.clock.now() - span, self.clock.now() + span
        )
        return [item.observation.processes[0].name for item in found]


# ──────────────────────────────────────────────────────────────────────────────
# The four outcomes
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("choice", "outcome"),
    [
        (PatrolChoice.ALARM, PatrolOutcome.ALARM),
        (PatrolChoice.NECTAR, PatrolOutcome.NECTAR),
        (PatrolChoice.WAX, PatrolOutcome.WAX),
    ],
)
async def test_a_patrol_that_acts_delivers_the_decision_and_keeps_what_it_covered(
    choice: PatrolChoice, outcome: PatrolOutcome
) -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(choice, text="why it matters")))
    await rig.keep("a")
    await rig.keep("b")

    result = await rig.patrol.run()

    assert result is outcome
    assert [d.choice for d in rig.port.delivered] == [choice]
    assert [o for o, _ in rig.port.recorded] == [outcome]
    assert await rig.kept() == ["a", "b"]  # Evidence stays until its retention ends.
    # The cursor is when the newest observation reviewed was kept: "b", a minute ago.
    assert rig.watch.schedule.patrolled_until == rig.clock.now() - timedelta(seconds=60)


async def test_a_patrol_that_finds_nothing_discards_exactly_what_it_covered() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.NOTHING)))
    await rig.keep("a")
    await rig.keep("b")

    result = await rig.patrol.run()
    await rig.keep("c")  # Taken after the Patrol: not covered.

    assert result is PatrolOutcome.NOTHING
    assert await rig.kept() == ["c"]
    assert rig.port.recorded[0][0] is PatrolOutcome.NOTHING


async def test_the_next_patrol_reviews_only_what_was_kept_since_the_last_one() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.ALARM)))
    await rig.keep("a")
    await rig.patrol.run()
    await rig.keep("b")
    await rig.keep("c")

    await rig.patrol.run()

    first, second = rig.port.windows
    assert [o.processes[0].name for o in first.observations] == ["a"]
    assert [o.processes[0].name for o in second.observations] == ["b", "c"]
    assert second.since > first.until  # Windows never overlap.
    assert (second.since, second.until) == (
        rig.clock.now() - timedelta(seconds=120),
        rig.clock.now() - timedelta(seconds=60),
    )


async def test_a_sample_kept_at_the_very_instant_a_patrol_ended_is_not_lost_or_reviewed_twice() -> (
    None
):
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.ALARM)))
    await rig.keep("a", wait=False)
    await rig.patrol.run()
    await rig.keep("b", wait=False)  # The clock has not moved: the same instant as "a".

    await rig.patrol.run()
    await rig.patrol.run()

    names = [[o.processes[0].name for o in w.observations] for w in rig.port.windows]
    assert names == [["a"], ["b"]]  # Each reviewed once; the third Patrol found nothing new.


async def test_the_window_carries_the_prefilters_signals() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.ALARM)))
    await rig.keep("quiet")
    await rig.keep("miner", share=0.95)

    await rig.patrol.run()

    (window,) = rig.port.windows
    assert [(s.kind, s.subject) for s in window.signals] == [(SignalKind.NEW_PROCESS_CPU, "miner")]


async def test_a_process_that_started_between_two_patrols_is_flagged_in_the_second() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.NOTHING)))
    await rig.keep("a", wait=False)
    await rig.patrol.run()  # Reviews "a", and discards it: nothing is left to compare against.
    started = rig.clock.now() + timedelta(seconds=100)
    rig.clock.advance(300.0)  # The next sample comes five minutes on; the job began in between.
    late = make_process(9, "late-job", 0.95, started_at=started)
    await store.save(make_observation(rig.clock.now(), processes=[late]), rig.ctx, _OWNER, 86_400.0)

    await rig.patrol.run()

    (window,) = rig.port.windows[1:]
    assert [(s.kind, s.subject) for s in window.signals] == [
        (SignalKind.NEW_PROCESS_CPU, "late-job")
    ]


async def test_the_window_spans_the_oldest_to_the_newest_observation_it_covers() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.NOTHING)))
    await rig.keep("a", wait=False)
    rig.clock.advance(23 * 3_600.0)  # Still inside the day the observations are kept for.
    await rig.keep("b", wait=False)
    first, last = rig.clock.now() - timedelta(hours=23), rig.clock.now()

    await rig.patrol.run()

    (window,) = rig.port.windows
    assert (window.since, window.until) == (first, last)
    assert [o.processes[0].name for o in window.observations] == ["a", "b"]


async def test_a_patrol_reviews_and_discards_only_its_own_wardens_observations() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.NOTHING)))
    await rig.keep("mine", wait=False)
    # Every Warden of a Hive Stand keeps its samples in the one store: this one is another's.
    others = [make_process(1, "theirs", 0.01, started_at=rig.clock.now())]
    await store.save(
        make_observation(rig.clock.now(), processes=others), rig.ctx, "warden_other", 86_400.0
    )
    rig.clock.advance(60.0)

    result = await rig.patrol.run()

    assert result is PatrolOutcome.NOTHING
    (window,) = rig.port.windows
    assert [o.processes[0].name for o in window.observations] == ["mine"]
    span = timedelta(days=30)
    left = await store.load_observations(
        rig.ctx, "warden_other", rig.clock.now() - span, rig.clock.now() + span
    )
    assert [item.observation.processes[0].name for item in left] == ["theirs"]
    assert await rig.kept() == []


# ──────────────────────────────────────────────────────────────────────────────
# Nothing to review, and SKIPPED
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_patrol_with_nothing_in_its_window_runs_no_episode_and_records_nothing() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.ALARM)))

    result = await rig.patrol.run()

    assert result is None
    assert rig.port.windows == [] and rig.port.recorded == []
    assert rig.watch.schedule.patrolled_until is None  # Nothing was reviewed, so nothing moved.


@pytest.mark.parametrize(
    "error",
    [ProviderUnavailableError("fake-warden"), RateLimitedError("fake-warden", retry_after_s=1.0)],
)
async def test_a_model_that_cannot_be_reached_skips_the_patrol_and_keeps_everything(
    error: Exception,
) -> None:
    rig = _Rig(ScriptedPatrolPort(error))
    await rig.keep("a")

    result = await rig.patrol.run()

    assert result is PatrolOutcome.SKIPPED
    assert await rig.kept() == ["a"]
    assert rig.watch.schedule.patrolled_until is None  # Not advanced: the next Patrol covers "a".
    assert [o for o, _ in rig.port.recorded] == [PatrolOutcome.SKIPPED]
    assert rig.port.delivered == []


async def test_a_skipped_patrols_observations_are_reviewed_by_the_next_one() -> None:
    rig = _Rig(ScriptedPatrolPort(ProviderUnavailableError("fake-warden")))
    await rig.keep("a")
    await rig.patrol.run()
    rig.port._decision = _decision(PatrolChoice.NOTHING)
    await rig.keep("b")

    result = await rig.patrol.run()

    assert result is PatrolOutcome.NOTHING
    assert [o.processes[0].name for o in rig.port.windows[-1].observations] == ["a", "b"]
    assert await rig.kept() == []


async def test_a_model_that_never_answers_skips_the_patrol(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hung(ScriptedPatrolPort):
        async def decide(self, window: object) -> PatrolDecision:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    monkeypatch.setattr(patrol_module, "PATROL_TIMEOUT_S", 0.01)
    rig = _Rig(Hung(_decision(PatrolChoice.ALARM)))
    await rig.keep("a")

    assert await rig.patrol.run() is PatrolOutcome.SKIPPED
    assert await rig.kept() == ["a"]


@pytest.mark.parametrize("choice", [PatrolChoice.ALARM, PatrolChoice.NECTAR, PatrolChoice.WAX])
async def test_a_decision_that_could_not_be_delivered_skips_the_patrol(
    choice: PatrolChoice,
) -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(choice, text="t"), delivers=False))
    await rig.keep("a")

    result = await rig.patrol.run()

    assert result is PatrolOutcome.SKIPPED
    assert await rig.kept() == ["a"]
    assert rig.watch.schedule.patrolled_until is None
    assert [o for o, _ in rig.port.recorded] == [PatrolOutcome.SKIPPED]


async def test_a_bug_in_the_episode_is_not_mistaken_for_an_unreachable_model() -> None:
    rig = _Rig(ScriptedPatrolPort(KeyError("a bug")))
    await rig.keep("a")

    with pytest.raises(KeyError):
        await rig.patrol.run()


async def test_every_patrol_that_ran_records_its_counts() -> None:
    rig = _Rig(ScriptedPatrolPort(_decision(PatrolChoice.NOTHING)))
    await rig.keep("a")
    await rig.keep("b", share=0.9)

    await rig.patrol.run()

    ((outcome, window),) = rig.port.recorded
    assert (outcome, len(window.observations), len(window.signals)) == (
        PatrolOutcome.NOTHING,
        2,
        1,
    )


def test_the_patrol_is_built_from_a_context_and_a_port_only() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    watch = make_watch_deps(StubProbe(make_observation(clock.now(), resources=make_resources())))

    patrol = Patrol(watch, ctx, ScriptedPatrolPort(_decision(PatrolChoice.NOTHING)), _OWNER)

    assert isinstance(ctx, MemoryContext) and isinstance(patrol, Patrol)
    assert isinstance(clock.now(), datetime)
