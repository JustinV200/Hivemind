"""Tests for the Guard Bee's injection correlation: a flag, then a denial, in the same episode.

Roadmap 10.6 and the 10.6b finding: outside text the scanner flagged for a bee, followed by that
same bee being refused, in the same episode (one worker id is one spawned attempt). A denial is a
guard.denied about the bee, or a capping.rejected at the ALLOWLIST rung, which is all a lease
boundary refusal records; the rejection names only its proposal, and the episode index joins it to
the bee through the proposal's task. A denial in another episode (the task's retry, another bee),
a denial before the flag, or a rejection at another rung never correlates, and one burst is filed
once.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/evaluate.py (codingrules section 3), split by
    feature from test_evaluate.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.rules.toml, `injection_then_denial`.
    - tests.e2e.test_guard_bee_on_hive_stand for the same correlation over a real Drone.
"""

from __future__ import annotations

from builders.guard_bee import Episode, GuardBeeRig, make_guard_bee, seed_episode

from hivemind.guard import GuardAction, GuardConfidence, GuardReport

_RULE = "injection_then_denial"


async def _reject(rig: GuardBeeRig, episode: Episode, failing_check: str) -> str:
    """Record a proposal of `episode` to write outside scratch, rejected at `failing_check`."""
    proposal = await rig.seed.proposed(episode.task, episode.cell, "OUTSIDE_SCRATCH_WRITE")
    await rig.seed.capping(
        proposal, "capping.rejected", tier="OUTSIDE_SCRATCH_WRITE", failing_check=failing_check
    )
    return proposal


async def _filed(rig: GuardBeeRig) -> list[GuardReport]:
    """Run a round and return the correlation's requests filed through the Queen's door."""
    await rig.bee.tick()
    return [report for report in rig.door.filed if report.rule == _RULE]


async def test_a_flag_then_a_denial_by_the_same_bee_files_one_quarantine_request() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    flag = await rig.seed.injection(episode.bee, episode.task)
    rig.clock.advance(2.0)
    denial = await rig.seed.denied(episode.bee)

    [report] = await _filed(rig)

    assert report.recommended is GuardAction.QUARANTINE_BEE
    assert report.confidence is GuardConfidence.HIGH
    assert report.event_ids == (flag.id, denial.id)  # The flag first: memory is suspect from it.
    assert (report.bee_ids, report.task_ids) == ((episode.bee,), (episode.task,))
    assert (report.cell_id, report.grant_ids) == (episode.cell, (episode.grant,))


async def test_a_lease_boundary_refusal_recorded_only_as_an_allowlist_rejection_correlates() -> (
    None
):
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await _reject(rig, episode, "ALLOWLIST")

    [report] = await _filed(rig)

    assert report.bee_ids == (episode.bee,) and report.cell_id == episode.cell
    assert len(report.event_ids) == 2  # The flag and the rejection; no guard.denied exists.
    assert await rig.kinds("guard.denied") == []


async def test_a_denial_in_the_tasks_next_episode_does_not_correlate() -> None:
    rig = make_guard_bee()
    first = await seed_episode(rig.seed)
    await rig.seed.injection(first.bee, first.task)
    rig.clock.advance(30.0)
    retry = await seed_episode(rig.seed, task=first.task)  # The same task, a new attempt.
    await rig.seed.denied(retry.bee)
    await _reject(rig, retry, "ALLOWLIST")

    assert await _filed(rig) == []


async def test_a_denial_of_another_bee_does_not_correlate() -> None:
    rig = make_guard_bee()
    flagged, other = await seed_episode(rig.seed), await seed_episode(rig.seed)
    await rig.seed.injection(flagged.bee, flagged.task)
    await rig.seed.denied(other.bee)

    assert await _filed(rig) == []


async def test_a_denial_before_the_flag_is_not_its_effect() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.denied(episode.bee)
    rig.clock.advance(1.0)
    await rig.seed.injection(episode.bee, episode.task)

    assert await _filed(rig) == []


async def test_a_rejection_at_any_other_rung_is_not_a_denial() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await _reject(rig, episode, "SIZE_CAP")

    assert await _filed(rig) == []


async def test_one_burst_is_filed_once_however_many_denials_follow() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)
    await _filed(rig)

    for _ in range(3):
        await rig.seed.denied(episode.bee)
        await rig.round()

    assert len([r for r in rig.door.filed if r.rule == _RULE]) == 1
