"""Chaos: the model misbehaves (refusals, malformed JSON, rate limits) or goes down mid-run.

Roadmap 13.6's provider half, on the Hive Stand with the fake provider. Each fault in `_FAULTS` is
raised (or answered) once, on the first call of the slot it targets; the three-haiku goal must
still end explicitly with nothing left open. The outage scenario takes the provider down while a
Drone's reply is in flight: the Queen's own health poller must read it DOWN and cluster it (the
Drone checkpoints, its task pauses), then read it healthy again and resume the task from its
Handoff, with no operator order either way.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_clustering.py and tests/e2e/offline/test_offline_clustering.py for the
      operator-ordered Clustering round trips this one runs without an order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from e2e.chaos.rig import assert_explicit_ends, assert_nothing_left_open, bounded, hive_on
from e2e.kernel_helpers import HaikuScript, default_worker_turn, text_response, wait_until

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    FakeLLMProvider,
    LLMError,
    LLMRequest,
    LLMResponse,
    MalformedOutputError,
    RateLimitedError,
    RefusedError,
)
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from hivemind.queen.cluster.health import ClusterBackoff

pytestmark = [pytest.mark.e2e, pytest.mark.chaos]

_GOAL = "write three haiku about bees to separate files"
_RUN_S = 30.0  # run_goal's own bound; the hang bound in rig sits above it.
_FAST_PROBES = ClusterBackoff(initial_s=0.05, max_s=0.2, healthy_interval_s=0.05)

Fault = Callable[[], LLMError | LLMResponse]
_FAULTS: dict[str, tuple[ModelSlot, Fault]] = {
    "worker refusal": (ModelSlot.WORKER, lambda: RefusedError("fake", "policy")),
    "worker rate limit": (ModelSlot.WORKER, lambda: RateLimitedError("fake", retry_after_s=0.01)),
    "worker malformed tool json": (
        ModelSlot.WORKER,
        lambda: text_response('```tool\n{"name": "write_file", "arguments": {"path": \n```'),
    ),
    "plan malformed json": (ModelSlot.QUEEN, lambda: text_response('{"tasks": [{"key": ')),
    "plan exhausted ladder": (
        ModelSlot.QUEEN,
        lambda: MalformedOutputError("fake", raw="{", attempts=3),
    ),
}


class _OnceFaulty:
    """Answer through `script`, but misbehave on the first call of `slot`."""

    def __init__(self, script: HaikuScript, slot: ModelSlot, fault: Fault) -> None:
        self._script, self._slot, self._fault = script, slot, fault
        self.fired = False

    def __call__(self, request: LLMRequest) -> LLMResponse:
        """One model call: the fault once, the ordinary script after."""
        if request.slot is self._slot and not self.fired:
            self.fired = True
            fault = self._fault()
            if isinstance(fault, LLMError):
                raise fault
            return fault
        return self._script.responder(request)


@pytest.mark.parametrize("fault", sorted(_FAULTS))
def test_a_misbehaving_model_still_ends_the_goal_explicitly_with_nothing_left_open(
    tmp_path: Path, fault: str
) -> None:
    slot, make = _FAULTS[fault]
    responder = _OnceFaulty(HaikuScript(default_worker_turn), slot, make)
    hive = hive_on(fake_manifest(tmp_path), responder)

    report = asyncio.run(bounded(fault, _run(hive)))

    assert responder.fired, "the fault was never reached"
    if report is not None:  # None: the goal was refused at submission, which is explicit too.
        assert not report.timed_out, report
        asyncio.run(assert_explicit_ends(hive))
    asyncio.run(assert_nothing_left_open(hive))


async def _run(hive: Hive) -> GoalReport | None:
    """Run the goal inside `run_hive`; `run_hive` returning is when everything must be closed.

    Returns:
        The goal's report, or None when no plan could be made of it: `submit_goal` then raises
        the ladder's own error to the caller (what `hive run` prints) and no task exists.
    """
    async with run_hive(hive):
        try:
            return await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RUN_S)
        except LLMError:
            return None


def test_a_provider_down_mid_run_is_clustered_then_resumed_with_no_order(tmp_path: Path) -> None:
    hive = hive_on(fake_manifest(tmp_path), HaikuScript(default_worker_turn).responder)
    hive.queen_deps.health_poller.backoff = _FAST_PROBES

    report = asyncio.run(bounded("provider down", _down_mid_run(hive)))

    assert report.succeeded, report
    events = asyncio.run(hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT)))
    kinds = [event.kind for event in events]
    assert kinds.index("memory.checkpoint") < kinds.index("task.paused")
    assert kinds.index("queen.clustered") < kinds.index("task.resumed")
    asyncio.run(assert_nothing_left_open(hive))


async def _down_mid_run(hive: Hive) -> GoalReport:
    """Hold the Drone's first reply, take the provider down, and bring it back once paused."""
    provider = hive.registry.provider("fake")
    assert isinstance(provider, FakeLLMProvider)
    parked, release = asyncio.Event(), asyncio.Event()
    _park_first_worker_reply(provider, parked, release)
    async with run_hive(hive):
        goal = asyncio.ensure_future(
            run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RUN_S)
        )
        await wait_until(parked.is_set, timeout_s=_RUN_S)
        provider.set_outage(True)  # Health reads DOWN from here; no operator order is written.
        await wait_until(lambda: _handoff_asked(hive), timeout_s=_RUN_S)
        release.set()  # The parked reply lands; the Drone meets the handoff at its next call.
        await wait_until(lambda: _recorded(hive, "task.paused"), timeout_s=_RUN_S)
        provider.set_outage(False)  # Back: the poller must read it healthy and resume on its own.
        return await goal


def _park_first_worker_reply(
    provider: FakeLLMProvider, parked: asyncio.Event, release: asyncio.Event
) -> None:
    """Make the first WORKER call answer, then wait for `release` before handing the answer back.

    The answer is taken before the wait, so it is the reply that was in flight when the provider
    went down, not a call made into the outage.
    """
    complete = provider.complete

    async def held(request: LLMRequest) -> LLMResponse:
        response = await complete(request)
        if request.slot is ModelSlot.WORKER and not parked.is_set():
            parked.set()
            await release.wait()
        return response

    setattr(provider, "complete", held)  # noqa: B010 - an instance's method, swapped on purpose.


def _handoff_asked(hive: Hive) -> bool:
    """Whether the Queen's Intervene(HANDOFF) has reached a running Drone (offline suite's read)."""
    return any(bee.runtime._ctx.telemetry.handoff_requested for bee in hive.warden.sub_bees)


async def _recorded(hive: Hive, kind: str) -> bool:
    """Whether the trail holds a `kind` event yet."""
    return bool(await hive.stores.trail.query(TrailQuery(kind=kind)))
