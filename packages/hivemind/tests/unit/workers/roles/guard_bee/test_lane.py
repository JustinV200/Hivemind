"""Tests for hivemind.workers.roles.guard_bee.lane: awake episodes one at a time, beside the tick.

At most one episode is in flight; a finished one is reaped once; a full queue refuses the offer;
an episode that raises is reaped as no verdict; draining runs every queued episode in order; and
closing cancels the one in flight, leaving no task behind.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/lane.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.lane for JudgeLane.
"""

from __future__ import annotations

import asyncio

from hivemind.guard import GuardAction, GuardConfidence
from hivemind.workers.roles.guard_bee import (
    Finding,
    GuardRule,
    JudgeCase,
    JudgeLane,
    Mark,
    Verdict,
)
from waggle.clock import FakeClock
from waggle.ids import new_worker_id

_CLOCK = FakeClock()  # Mints ids and times.
_VERDICT = Verdict(GuardConfidence.HIGH, GuardAction.OBSERVE, judged=True)


class _Judge:
    """A GuardJudge that answers each case in turn: a verdict, an error, or never."""

    def __init__(self, *answers: Verdict | Exception | None) -> None:
        self.answers = list(answers)
        self.judged: list[JudgeCase] = []
        self.release = asyncio.Event()

    async def judge(self, case: JudgeCase) -> Verdict | None:
        self.judged.append(case)
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            await self.release.wait()  # Blocks until the test lets it go (or cancels it).
        return answer


def _case(key: str | None = None) -> JudgeCase:
    rule = GuardRule.model_validate(
        {
            "key": "denial_burst",
            "title": "a burst of Guard refusals for one bee",
            "counts": [{"kind": "guard.denied"}],
            "group_by": "bee",
            "window_s": 300.0,
            "threshold": 5,
            "confidence": "medium",
            "action": "quarantine_bee",
            "judgement": True,
        }
    )
    finding = Finding(rule, key or new_worker_id(_CLOCK), (), 5.0, Mark(_CLOCK.now()))
    return JudgeCase(finding=finding, allowed=frozenset({GuardAction.OBSERVE}))


async def test_one_episode_runs_at_a_time_and_is_reaped_once() -> None:
    judge = _Judge(_VERDICT, _VERDICT)
    lane = JudgeLane(judge)
    first, second = _case(), _case()
    lane.offer(first)
    lane.offer(second)

    lane.start()
    lane.start()  # Still one in flight: the second waits.
    await asyncio.sleep(0)
    reaped = lane.reap()

    assert [case for case, _ in reaped] == [first] and reaped[0][1] == _VERDICT
    assert judge.judged == [first]
    assert lane.reap() == []
    assert lane.pending() == {("denial_burst", second.finding.key)}


async def test_a_full_queue_refuses_the_offer() -> None:
    lane = JudgeLane(_Judge(), capacity=1)

    assert lane.offer(_case()) is True
    assert lane.offer(_case()) is False


async def test_an_episode_that_raised_is_reaped_as_no_verdict() -> None:
    lane = JudgeLane(_Judge(RuntimeError("a judge bug")))
    lane.offer(_case())

    [(_, verdict)] = await lane.drain()

    assert verdict is None


async def test_draining_runs_every_queued_episode_in_order() -> None:
    lane = JudgeLane(_Judge(_VERDICT, _VERDICT, _VERDICT))
    cases = [_case(), _case(), _case()]
    for case in cases:
        lane.offer(case)

    finished = await lane.drain()

    assert [case for case, _ in finished] == cases
    assert lane.pending() == frozenset()


async def test_closing_cancels_the_episode_in_flight_and_forgets_the_queue() -> None:
    judge = _Judge()  # Never answers.
    lane = JudgeLane(judge)
    lane.offer(_case())
    lane.offer(_case())
    lane.start()
    await asyncio.sleep(0)

    await lane.aclose()

    assert lane.pending() == frozenset() and lane.reap() == []
    assert len(judge.judged) == 1  # The second never started.
