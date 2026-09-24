"""End-to-end: the composed Hive's own Guard Bee judges a finding on the Royal Reserve's seats.

Roadmap step 10.6 on a real run of `hive run`'s composition (`build_hive`/`run_hive`, real SQLite,
the manifest's own `[guard]` section with quick rounds). Nothing hands the Queen a Guard Bee here:
the composition root built one and bound it to her door. A burst of Guard refusals for one bee
lands on her trail, as the Enforcer writes them; the Guard Bee's rule for it asks for judgement,
so one awake episode on the judge slot decides the report. That call goes through the Queen's own
call gate, a Fanner lane with no grant: it is charged to the Royal Reserve, recorded as `llm.call`
on the judge slot on the Queen's node with no grant id, and draws on no Worker's grant.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.guard for `with_guard`, the wiring under test.
    - tests.e2e.test_guard_bee_on_hive_stand for a real Drone's lure reaching the Queen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.guard_bee import GuardReviews, TrailSeeder, quick_rounds, seed_episode
from e2e.kernel_helpers import wait_until

from hivemind.cell import CellIdentity
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.llm import LLMRequest, LLMResponse, text_response
from hivemind.manifest import load_manifest
from hivemind.pheromone import LlmEvent, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 15.0  # Generous: the episode ends within a round or two.
_DENIALS = 5  # The shipped denial_burst threshold.


def _silent(request: LLMRequest) -> LLMResponse:
    """Every call that is not a Guard review: this Hive is given no work."""
    del request
    return text_response("{}")


async def _alerts(hive: Hive) -> list[PheromoneEvent]:
    """Every guard.alert on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind="guard.alert")))


async def _judged_burst(hive: Hive) -> list[PheromoneEvent]:
    """Record a burst of refusals for one bee, and wait for the Guard Bee's judged alert."""
    manifest = hive.manifest
    identity = CellIdentity(manifest.hive.id, manifest.hive.node_id, "system")
    seed = TrailSeeder(hive.stores.trail, hive.clock, identity)
    episode = await seed_episode(seed)
    for _ in range(_DENIALS):
        await seed.denied(episode.bee)
    await wait_until(lambda: _has_alert(hive), timeout_s=_TIMEOUT_S)
    return await _alerts(hive)


async def _has_alert(hive: Hive) -> bool:
    """Whether the Guard Bee has reported anything yet."""
    return bool(await _alerts(hive))


def test_the_composed_guard_bee_judges_on_the_royal_reserves_seats(tmp_path: Path) -> None:
    reviews = GuardReviews(_silent, "low", "observe")
    manifest = load_manifest(quick_rounds(fake_manifest(tmp_path)), {})
    hive = build_hive(manifest, environ={}, clock=SystemClock(), responders={"fake": reviews})

    async def scenario() -> tuple[list[PheromoneEvent], tuple[PheromoneEvent, ...]]:
        async with run_hive(hive):
            alerts = await _judged_burst(hive)
        return alerts, await hive.stores.trail.query(TrailQuery(kind="llm.call"))

    alerts, calls = asyncio.run(scenario())

    [alert] = alerts
    assert alert.payload["rule"] == "denial_burst" and alert.payload["judged"] is True
    assert (alert.payload["action"], alert.payload["confidence"]) == ("observe", "low")
    judge_calls = [c for c in calls if isinstance(c, LlmEvent) and c.slot == "JUDGE"]
    assert reviews.reviews == len(judge_calls) == 1
    # The Royal Reserve's seats: the Queen's own lane, on her node, drawing on no grant.
    [call] = judge_calls
    assert call.node_id == manifest.hive.node_id and "grant_id" not in call.payload
