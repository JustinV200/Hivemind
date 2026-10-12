"""Test hivemind.entrance.reads.screens.metrics: the metrics snapshot read from a small Hive.

An idle Hive reads its one Cell, its Warden's place, every task status and zero counts; tasks
submitted and a tool promotion and a denial on the trail are counted; completed model calls sum
into the cost series.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/metrics.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import scene
from builders.observation import make_event, make_llm_call

from hivemind.entrance.reads.screens import read_metrics
from waggle.clock import FakeClock
from waggle.ids import new_task_id, new_tool_id


async def test_an_idle_hive_reads_its_cell_and_zero_counts() -> None:
    clock = FakeClock()
    hive = scene(clock)

    snapshot = await read_metrics(hive.reads, clock.now())

    assert [(c.kind, c.count) for c in snapshot.cells] == [("real", 1)]
    assert snapshot.summary.cells_total == 1
    assert (snapshot.tool_promotions, snapshot.denials, snapshot.llm) == (0, 0, [])
    assert {t.status: t.count for t in snapshot.tasks}["pending"] == 0


async def test_tasks_trail_counts_and_model_calls_are_counted() -> None:
    clock = FakeClock()
    hive = scene(clock)
    await hive.submit({"a": (), "b": ()})
    await hive.record(make_event("tool.promoted", new_tool_id(clock)))
    for _ in range(2):
        await hive.record(make_event("guard.denied", new_task_id(clock)))
    await hive.record(make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.5))

    snapshot = await read_metrics(hive.reads, clock.now())

    assert {t.status: t.count for t in snapshot.tasks}["pending"] == 2
    assert (snapshot.tool_promotions, snapshot.denials) == (1, 2)
    assert [(line.slot, line.provider, line.cost_usd) for line in snapshot.llm] == [
        ("WORKER", "cloud", 0.5)
    ]
