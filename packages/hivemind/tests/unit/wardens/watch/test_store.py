"""Tests for hivemind.wardens.watch.store: keeping observations in Bee Bread, and reading them back.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/store.py (codingrules section 3). The store is the real
    in-memory memory store; what is checked is the label, the retention window, the oldest-first
    read and that nothing the machine said reaches the trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.store for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.watch import make_memory_context, make_observation, make_process, make_resources

from hivemind.cell import HoneyClearance
from hivemind.memory import MemoryContext
from hivemind.memory.bee_bread import MAX_ENTRY_PAYLOAD_CHARS, BeeBreadEntryKind
from hivemind.pheromone import TrailQuery
from hivemind.wardens.watch.store import (
    discard_before,
    encode_observation,
    load_observations,
    save,
)
from waggle.clock import FakeClock
from waggle.messages.swarm import FileChange, FileChangeKind, LogLine, Observation

_DAY = 86_400.0
_OWNER = "warden_owner"  # Any id: the real one is the Warden's own.


def _big(clock: FakeClock, lines: int = 200) -> Observation:
    """An Observation with so many long log lines that it will not fit one Bee Bread entry."""
    return make_observation(
        clock.now(),
        processes=[make_process(pid=n, name=f"p{n}", cpu_share=1 / (n + 1)) for n in range(1, 40)],
        log_lines=[LogLine(path="/r/a.log", line=f"{n:04d}" + "x" * 995) for n in range(lines)],
        file_changes=[
            FileChange(
                path=f"/r/f{n}", change=FileChangeKind.MODIFIED, at=clock.now(), size_bytes=n
            )
            for n in range(100)
        ],
    )


async def _kept(ctx: MemoryContext, clock: FakeClock) -> list[Observation]:
    """Every Observation kept, read back across a window that holds them all."""
    start, end = clock.now() - timedelta(days=30), clock.now() + timedelta(days=30)
    return [item.observation for item in await load_observations(ctx, _OWNER, start, end)]


def test_a_small_observation_is_encoded_whole() -> None:
    observation = make_observation(
        FakeClock().now(), processes=[make_process(name="a")], resources=make_resources()
    )

    text = encode_observation(observation)

    assert Observation.model_validate_json(text) == observation


def test_an_observation_too_big_for_one_entry_is_halved_until_it_fits() -> None:
    clock = FakeClock()
    big = _big(clock)
    assert len(big.model_dump_json()) > MAX_ENTRY_PAYLOAD_CHARS

    shrunk = Observation.model_validate_json(encode_observation(big))

    assert len(shrunk.model_dump_json()) <= MAX_ENTRY_PAYLOAD_CHARS
    assert shrunk.is_truncated is True  # Whoever reads it knows it is a lower bound.
    assert len(shrunk.log_lines) < len(big.log_lines)
    assert shrunk.log_lines[-1] == big.log_lines[-1]  # The newest lines are the ones kept.
    assert shrunk.processes == big.processes[: len(shrunk.processes)]  # The busiest are kept.


async def test_save_keeps_a_c2_entry_with_no_task_and_no_text_on_the_trail() -> None:
    clock = FakeClock()
    ctx, trail = make_memory_context(clock)
    observation = make_observation(
        clock.now(), log_lines=[LogLine(path="/r/a.log", line="password=hunter2")]
    )

    await save(observation, ctx, _OWNER, _DAY)

    (entry,) = await ctx.store.list_bee_bread_of_kind(
        BeeBreadEntryKind.WATCH_OBSERVATION,
        clock.now() - timedelta(seconds=1),
        clock.now() + timedelta(seconds=1),
        HoneyClearance.C2,
    )
    assert entry.clearance is HoneyClearance.C2 and entry.task_id is None
    events = await trail.query(TrailQuery())
    assert "hunter2" not in repr([event.payload for event in events])


async def test_save_sweeps_the_observations_past_their_retention() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    await save(
        make_observation(clock.now(), processes=[make_process(name="old")]), ctx, _OWNER, 3_600.0
    )
    clock.advance(3_601.0)

    await save(
        make_observation(clock.now(), processes=[make_process(name="new")]), ctx, _OWNER, 3_600.0
    )

    assert [o.processes[0].name for o in await _kept(ctx, clock)] == ["new"]


async def test_save_keeps_an_observation_still_inside_its_retention() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    await save(
        make_observation(clock.now(), processes=[make_process(name="a")]), ctx, _OWNER, 3_600.0
    )
    clock.advance(3_599.0)

    await save(
        make_observation(clock.now(), processes=[make_process(name="b")]), ctx, _OWNER, 3_600.0
    )

    assert [o.processes[0].name for o in await _kept(ctx, clock)] == ["a", "b"]


async def test_load_observations_returns_the_window_oldest_first_with_the_time_kept() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    kept_at = []
    for name in ("a", "b", "c"):
        await save(
            make_observation(clock.now(), processes=[make_process(name=name)]), ctx, _OWNER, _DAY
        )
        kept_at.append(clock.now())
        clock.advance(60.0)

    window = await load_observations(ctx, _OWNER, kept_at[1], kept_at[2])

    assert [(i.kept_at, i.observation.processes[0].name) for i in window] == [
        (kept_at[1], "b"),
        (kept_at[2], "c"),
    ]
    assert len({i.entry_id for i in window}) == 2  # Each names its own entry.


async def test_an_entry_that_does_not_parse_is_skipped_not_raised() -> None:
    from hivemind.memory.bee_bread import deposit_watch_observation

    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    await deposit_watch_observation("this is not an observation", _OWNER, ctx)
    await deposit_watch_observation('{"processes": "wrong"}', _OWNER, ctx)
    clock.advance(1.0)
    await save(
        make_observation(clock.now(), processes=[make_process(name="ok")]), ctx, _OWNER, _DAY
    )

    assert [o.processes[0].name for o in await _kept(ctx, clock)] == ["ok"]


async def test_discard_before_deletes_the_observations_older_than_the_cutoff_only() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    for name in ("a", "b", "c"):
        await save(
            make_observation(clock.now(), processes=[make_process(name=name)]), ctx, _OWNER, _DAY
        )
        clock.advance(60.0)

    deleted = await discard_before(ctx, _OWNER, clock.now() - timedelta(seconds=90))

    assert deleted == 2
    assert [o.processes[0].name for o in await _kept(ctx, clock)] == ["c"]


async def _names(ctx: MemoryContext, owner: str, clock: FakeClock) -> list[str]:
    """The process names in each Observation `owner` still keeps, oldest first."""
    start, end = clock.now() - timedelta(days=30), clock.now() + timedelta(days=30)
    kept = await load_observations(ctx, owner, start, end)
    return [item.observation.processes[0].name for item in kept]


async def test_wardens_sharing_one_store_each_read_only_their_own_observations() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    await save(
        make_observation(clock.now(), processes=[make_process(name="stand")]), ctx, "a", _DAY
    )
    await save(
        make_observation(clock.now(), processes=[make_process(name="laptop")]), ctx, "b", _DAY
    )

    assert await _names(ctx, "a", clock) == ["stand"]
    assert await _names(ctx, "b", clock) == ["laptop"]
    assert await _names(ctx, "c", clock) == []


async def test_one_wardens_discard_leaves_the_others_observations_alone() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    await save(
        make_observation(clock.now(), processes=[make_process(name="stand")]), ctx, "a", _DAY
    )
    await save(
        make_observation(clock.now(), processes=[make_process(name="laptop")]), ctx, "b", _DAY
    )
    clock.advance(60.0)

    deleted = await discard_before(ctx, "a", clock.now())

    assert deleted == 1
    assert await _names(ctx, "a", clock) == []
    assert await _names(ctx, "b", clock) == ["laptop"]  # Not yet reviewed by its own Patrol.


async def test_the_retention_sweep_clears_every_wardens_expired_observations() -> None:
    clock = FakeClock()
    ctx, _trail = make_memory_context(clock)
    await save(
        make_observation(clock.now(), processes=[make_process(name="old-a")]), ctx, "a", 100.0
    )
    await save(
        make_observation(clock.now(), processes=[make_process(name="old-b")]), ctx, "b", 100.0
    )
    clock.advance(101.0)

    await save(
        make_observation(clock.now(), processes=[make_process(name="new-a")]), ctx, "a", 100.0
    )

    assert await _names(ctx, "a", clock) == ["new-a"]
    assert await _names(ctx, "b", clock) == []  # Whoever saves sweeps: one retention for all.
