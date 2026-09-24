"""Tests for hivemind.workers.roles.guard_bee.facts: what an event becomes, and the episode joins.

A fact keeps an event's well-formed ids by kind, the payload values the rules read (and a
guard.denied's capability family), a Capping event's tier and an llm.call's spend, and nothing
else. The episode index joins a proposal to its task, Cell and tier, a task to the bee spawned
for it at the moment asked about, and a task to its Cell and grant.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/facts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.facts for TrailFact, fact_from_event and EpisodeIndex.
"""

from __future__ import annotations

from builders.guard_bee import TrailSeeder, make_guard_bee, seed_episode

from hivemind.pheromone import TrailQuery
from hivemind.workers.roles.guard_bee import EpisodeIndex, TrailFact, fact_from_event
from waggle.ids import new_task_id

_EVERYTHING = TrailQuery(limit=1_000)  # Every event a test here seeds, oldest first.
_NEWEST = TrailQuery(limit=1, newest_first=True)  # The event a test just seeded.
_WATCHED = frozenset({"principal_kind", "capability_family", "failing_check", "tier", "reason"})


def _seeder() -> TrailSeeder:
    return make_guard_bee().seed


async def test_a_denial_keeps_the_bee_and_the_capability_family_and_nothing_else() -> None:
    seed = _seeder()
    episode = await seed_episode(seed)

    event = await seed.denied(episode.bee, capability="net:example.org")
    fact = fact_from_event(event, _WATCHED)

    assert fact.ids == {"bee": episode.bee}
    assert fact.fields == {"principal_kind": "worker", "capability_family": "net"}
    assert fact.amount == 0.0


async def test_only_a_capping_events_tier_is_a_risk_tier() -> None:
    seed = _seeder()
    episode = await seed_episode(seed)
    proposal = await seed.proposed(episode.task, episode.cell, "OUTSIDE_SCRATCH_WRITE")

    rejected = await seed.capping(
        proposal, "capping.rejected", tier="OUTSIDE_SCRATCH_WRITE", failing_check="ALLOWLIST"
    )
    injection = await seed.injection(episode.bee, episode.task)

    assert fact_from_event(rejected, _WATCHED).ids == {
        "proposal": proposal,
        "tier": "OUTSIDE_SCRATCH_WRITE",
    }
    assert "tier" not in fact_from_event(injection, _WATCHED).ids


async def test_a_payload_value_that_only_looks_like_an_id_is_not_taken_for_one() -> None:
    seed = _seeder()
    episode = await seed_episode(seed)

    # The trail validates a subject, never a payload value: a forged task id stays out.
    event = await seed.injection(episode.bee, "task_not-a-ulid")

    assert fact_from_event(event, _WATCHED).ids == {"bee": episode.bee}


async def test_an_llm_call_carries_its_grant_and_its_spend() -> None:
    seed = _seeder()
    episode = await seed_episode(seed)

    fact = fact_from_event(await seed.llm_call(episode.grant, 0.75), _WATCHED)

    assert fact.ids == {"grant": episode.grant}
    assert fact.amount == 0.75


async def test_an_entrance_event_keeps_its_device_and_address() -> None:
    seed = _seeder()

    device = await seed.entrance("guard.entrance_redeem_failed", address="203.0.113.7")
    event = (await seed.trail.query(_NEWEST))[0]
    fact = fact_from_event(event, _WATCHED)

    assert fact.ids == {"device": device, "address": "203.0.113.7"}


async def test_a_rejection_joins_through_its_proposal_to_the_bee_of_that_moment() -> None:
    seed = _seeder()
    index = EpisodeIndex()
    first = await seed_episode(seed)
    proposal = await seed.proposed(first.task, first.cell, "OUTSIDE_SCRATCH_WRITE")
    rejected = await seed.capping(proposal, "capping.rejected", failing_check="ALLOWLIST")
    seed.clock.advance(60.0)
    retry = await seed_episode(seed, task=first.task)  # A second attempt: a new bee, same task.
    for event in await seed.trail.query(_EVERYTHING):
        index.learn(fact_from_event(event, _WATCHED))

    ids = index.resolve(fact_from_event(rejected, _WATCHED))

    assert ids["bee"] == first.bee != retry.bee
    assert (ids["task"], ids["cell"]) == (first.task, first.cell)
    assert ids["grant"] in {first.grant, retry.grant}
    assert ids["tier"] == "OUTSIDE_SCRATCH_WRITE"


async def test_a_bees_own_fact_joins_to_its_task_cell_and_grant() -> None:
    seed = _seeder()
    index = EpisodeIndex()
    episode = await seed_episode(seed)
    for event in await seed.trail.query(_EVERYTHING):
        index.learn(fact_from_event(event, _WATCHED))

    ids = index.resolve(fact_from_event(await seed.denied(episode.bee), _WATCHED))

    assert ids == {
        "bee": episode.bee,
        "task": episode.task,
        "cell": episode.cell,
        "grant": episode.grant,
    }


async def test_a_fact_never_has_an_id_of_its_own_replaced_by_a_join() -> None:
    seed = _seeder()
    index = EpisodeIndex()
    episode = await seed_episode(seed)
    for event in await seed.trail.query(_EVERYTHING):
        index.learn(fact_from_event(event, _WATCHED))
    other_task = new_task_id(seed.clock)

    ids = index.resolve(fact_from_event(await seed.injection(episode.bee, other_task), _WATCHED))

    assert ids["task"] == other_task


async def test_pruning_forgets_joins_learnt_before_the_horizon() -> None:
    seed = _seeder()
    index = EpisodeIndex()
    episode = await seed_episode(seed)
    for event in await seed.trail.query(_EVERYTHING):
        index.learn(fact_from_event(event, _WATCHED))
    seed.clock.advance(10.0)

    index.prune(seed.clock.now())
    fact = TrailFact(
        event_id="event_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        at=seed.clock.now(),
        kind="guard.denied",
        node_id=seed.identity.node_id,
        ids={"bee": episode.bee},
    )

    assert index.resolve(fact) == {"bee": episode.bee}
