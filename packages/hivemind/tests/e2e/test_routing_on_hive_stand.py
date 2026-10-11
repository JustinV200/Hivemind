"""End-to-end tests for routing on the Hive Stand: roadmap step 8.1's tempo-to-binding mapping.

One Hive is built over a `builders.cli.fake_manifest` whose worker slot is bound to a grade-2 model
on one fake provider (`small`) with a named fallback binding (`local_worker`) on a second fake
provider (`fake`) at grade 4; both are free on the Forage map. The same three-haiku goal then runs
twice, its one subtask's tempo set in the plan: at CRITICAL (a grade floor of 4) routing must pass
over the grade-2 primary, so the Drone's `llm.call` events name the grade-4 provider and one
`llm.fallback` names both keys; at LOW the primary stays, so the calls name the grade-2 provider and
no `llm.fallback` exists. The weak model is grade 2, not 1, on purpose: the Queen's hosting plan
(which routing ranks by, and which the Warden receives before it spawns) lists only sources at the
NORMAL floor (grade 2) or better, so a grade-1 source is never planned and the strong planned one
would outrank it even at LOW. Each is a real Hive Stand lease, a real SQLite file and a real Warden
spawning a real Drone; only the models are scripted (`tests.e2e.kernel_helpers.HaikuScript`).

Fits into the Hive:
    Mirrors the roadmap 8.1 exit bar ("an urgent low-bar task lands on a fast local model at low
    effort and a critical one on the strongest slot"), beside `test_kernel_on_hive_stand.py`.

Key invariants:
    - A Drone's calls are told apart by `LlmEvent.provider`, the manifest's provider name, never
      by a model id.
    - Each scenario runs its Hive once and finishes well inside `_TIMEOUT_S`.

See Also:
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md for the policy.
    - hivemind.wardens.spawn.route for the Warden's routing step these scenarios exercise.
    - tests.e2e.test_kernel_on_hive_stand for the Hive-building style this module follows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from e2e.kernel_helpers import HaikuScript, default_worker_turn, single_task_plan

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import build_hive, run_goal, run_hive
from hivemind.manifest import load_manifest
from hivemind.pheromone import LlmEvent, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_GOAL = "write three haiku about bees to separate files"
_TIMEOUT_S = 15.0  # Generous: each scenario finishes in a second or two.
_FILES = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")
_WEAK_PROVIDER = "small"  # The grade-2 primary's provider name in the manifest below.
_STRONG_PROVIDER = "fake"  # The grade-4 fallback's provider name (fake_manifest's own).

# The original worker row and the row that replaces it: the same slot, now on the weak provider.
_FAKE_WORKER_ROW = '[llm.slots.worker]\nprovider = "fake"\nmodel = "test-model"\n'
_SMALL_WORKER_ROW = (
    f'[llm.slots.worker]\nprovider = "{_WEAK_PROVIDER}"\nmodel = "test-model-small"\n'
)
# What is added to the written manifest: a second fake provider and the two Forage-map sources
# (free: no cost keys). The shipped worker floor of 2 already lets the weak primary stand.
_ROUTING_TABLES = f"""
[llm.providers.{_WEAK_PROVIDER}]
kind = "fake"

[forage.map.small_default]
provider = "{_WEAK_PROVIDER}"
model = "test-model-small"
grade = 2
context_window = 8192
seats = 4

[forage.map.strong_default]
provider = "{_STRONG_PROVIDER}"
model = "test-model-strong"
grade = 4
context_window = 8192
seats = 4
"""


def _routing_manifest(tmp_path: Path) -> Path:
    """Write a manifest whose worker slot is grade 2 on `small`, falling back to grade 4 on `fake`.

    `fake_manifest(worker_fallback=True)` already writes `local_worker` (the strong model on the
    `fake` provider) as the worker row's fallback; this swaps the worker row onto the weak provider
    and appends the provider, floor and Forage-map tables, patching the written TOML the way
    `kernel_helpers.set_forage_reserve_seats` does since the builder has no knob for them.
    """
    path = fake_manifest(tmp_path, tuning=ManifestTuning(worker_fallback=True))
    text = path.read_text(encoding="utf-8")
    assert _FAKE_WORKER_ROW in text, "fake_manifest's worker row changed shape"
    path.write_text(text.replace(_FAKE_WORKER_ROW, _SMALL_WORKER_ROW, 1) + _ROUTING_TABLES)
    return path


def _plan_at(accuracy: str) -> dict[str, object]:
    """Return the one-task plan with its subtask's tempo set to `accuracy`."""
    plan = single_task_plan(*_FILES)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["needs"] = {"tempo": {"accuracy": accuracy}}
    return plan


def _run_goal_at(tmp_path: Path, accuracy: str) -> tuple[bool, tuple[PheromoneEvent, ...]]:
    """Run the haiku goal with its subtask at `accuracy`; return success and every trail event."""
    script = HaikuScript(default_worker_turn, plan=_plan_at(accuracy))
    manifest = load_manifest(_routing_manifest(tmp_path), {})
    # `build_hive` opens real SQLite through its own `asyncio.run`, so it runs outside any loop.
    hive = build_hive(
        manifest,
        environ={},
        clock=SystemClock(),
        responders={_STRONG_PROVIDER: script.responder, _WEAK_PROVIDER: script.responder},
    )

    async def run() -> tuple[bool, tuple[PheromoneEvent, ...]]:
        async with run_hive(hive):
            report = await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S)
        events = await hive.stores.trail.query(TrailQuery())
        done = report.succeeded and report.tasks[0].status is TaskStatus.SUCCEEDED
        return done, tuple(events)

    return asyncio.run(run())


def _drone_providers(events: tuple[PheromoneEvent, ...]) -> set[str | None]:
    """Return the provider name of every `llm.call` the Drone (the WORKER slot) made."""
    return {
        event.provider
        for event in events
        if isinstance(event, LlmEvent) and event.kind == "llm.call" and event.slot == "WORKER"
    }


def _routing_fallbacks(events: tuple[PheromoneEvent, ...]) -> list[PheromoneEvent]:
    """Return the `llm.fallback` events routing recorded (the ladders' own carry no `to_key`)."""
    return [e for e in events if e.kind == "llm.fallback" and "to_key" in e.payload]


def test_a_critical_subtask_passes_over_the_grade_two_primary_for_the_grade_four_fallback(
    tmp_path: Path,
) -> None:
    succeeded, events = _run_goal_at(tmp_path, "CRITICAL")

    assert succeeded
    assert _drone_providers(events) == {_STRONG_PROVIDER}  # Every Drone call went to grade 4.
    [fallback] = _routing_fallbacks(events)
    assert (fallback.payload["from_key"], fallback.payload["to_key"]) == ("worker", "local_worker")
    assert fallback.payload["passed_over"] == [{"key": "worker", "cause": "GRADE_BELOW_FLOOR"}]


def test_a_low_tempo_subtask_keeps_the_grade_two_primary_and_records_no_fallback(
    tmp_path: Path,
) -> None:
    succeeded, events = _run_goal_at(tmp_path, "LOW")

    assert succeeded
    assert _drone_providers(events) == {_WEAK_PROVIDER}  # Every Drone call stayed on grade 2.
    assert _routing_fallbacks(events) == []
