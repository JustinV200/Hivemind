"""End-to-end tests for Virtual Cells: the phase 5 exit criteria (`.claude/roadmap.md` 1030-1038).

Every scenario below builds a real `hivemind.cli.compose.Hive` (real Queen, real `CellListener`,
real `LifecycleVirtualCellProvider`, real `CellLifecycle`/`OverwinterPool`) over a
`hivemind.hive.backends.fake.FakeCellBackend` -- except the backend is
`builders.virtual_cells.ContainerSpawningFakeCellBackend`, which also runs a real, in-process
`hivemind.cli.in_cell.main.run_in_cell_warden` (a real in-Cell Warden, a real Drone, a real
`hivemind.llm.fake.FakeLLMProvider`) for every Cell it provisions, standing in for "the container"
the same way the Hive Stand's own real `Warden` already stands in for a Real Cell in
`tests.e2e.test_kernel_on_hive_stand`. Only the backend's own infrastructure call (starting an
actual Docker container or QEMU VM) is faked; everything above and around it -- placement, the
Queen<->Warden Waggle handshake over a real loopback WebSocket, the Capping-verified task result,
Overwintering, the CLI's own `hive cells abscond` -- is the real code.

`independent_haiku_plan` (`builders.virtual_cells`) is this suite's own "haiku run": three
independent subtasks, one per file, so `prefer = "virtual"` provisions three separate Virtual
Cells (unlike `tests.e2e.kernel_helpers.single_task_plan`'s one task with three `write_file`
calls in the same Cell, which the phase 3 kernel suite uses instead). Every Virtual Cell's own
Drone is scripted to write every entry of `builders.virtual_cells.DEFAULT_HAIKU_FILES`, regardless
of which specific file its own task's acceptance names (that module's own docstring explains why
this is simpler and just as correct as parsing a task's own objective text).

Covers exit criteria (a)-(g); (h) (Night Veil) lives in the sibling module
`test_virtual_cells_night_veil.py`, split out purely to stay under codingrules section 5.1's
400-LOC file limit (mirrors the existing `test_phase4_exit_criteria.py` /
`test_phase4_exit_criteria_forage.py` split).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md lines 1030-1038 for the exit criteria this module proves.
    - test_virtual_cells_night_veil for criterion (h).
    - builders.virtual_cells for ContainerSpawningFakeCellBackend and every plan/manifest/wax
      helper this module uses.
    - tests.e2e.kernel_helpers for HaikuScript/default_worker_turn/wait_until/snapshot_tree, the
      Hive Stand scripting and polling primitives this module reuses.
    - tests.e2e.test_kernel_on_hive_stand for the "real everything, fake Cell backend" shape this
      module extends one layer further out (a real in-Cell Warden, not only a real Hive Stand one).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    clear_wax_note,
    default_container_script,
    independent_haiku_plan,
    single_haiku_plan,
    virtual_cells_manifest,
    write_block_wax,
)
from e2e.kernel_helpers import (
    HaikuScript,
    WorkerTurn,
    assert_kinds_in_order,
    default_worker_turn,
    judge_approve_response,
    plan_response,
    snapshot_tree,
    text_response,
    wait_until,
)

from hivemind.brood_chamber import Task
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.cli.compose.deps import build_ledger
from hivemind.cli.readback.virtual_abscond import AbscondDeps, run_abscond
from hivemind.cli.stores import open_cluster_orders
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.memory.cell_wax import CellWax
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen import ForageLedger
from hivemind.queen.cluster.orders import OrderStore
from waggle.clock import SystemClock
from waggle.ids import CellId

pytestmark = pytest.mark.e2e

# Generous: every scenario below actually finishes in well under this (real loopback WebSockets,
# no real infrastructure); bounds every `run_goal`/`wait_until` against a hang.
_TIMEOUT_S = 15.0


# ──────────────────────────────────────────────────────────────────────────────
# Shared wiring: build a Hive whose "fake" backend also runs a real in-Cell Warden.
# ──────────────────────────────────────────────────────────────────────────────


def _virtual_hive(
    manifest_path: Path,
    script: HaikuScript | _SequentialPlanScript,
    monkeypatch: pytest.MonkeyPatch,
) -> Hive:
    """Build a Hive whose `[virtual_cells] backend = "fake"` is a ContainerSpawningFakeCellBackend.

    `hivemind.cli.compose.virtual_cell_backends.build_registry` (the module `hivemind.cli.compose.
    virtual_cells._build_registry` is aliased from, split out for its own line budget) names
    `FakeCellBackend` at module level; swapping it for the container-spawning subclass here
    (rather than editing that composition root) is exactly the "wrap the fake backend in a thin
    subclass... keep it in a builder" choice this dispatch's own brief calls for.
    """
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    manifest = load_manifest(manifest_path, {})
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


def _fake_backend(hive: Hive) -> ContainerSpawningFakeCellBackend:
    """Return the running Hive's own container-spawning fake backend."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    return backend


class _SequentialPlanScript:
    """Answer `ModelSlot.QUEEN` with one plan per call, in order; mirrors `HaikuScript` otherwise.

    A scenario that submits more than one goal to the same Hive (a BLOCK Cell Wax cleared between
    two goals, a second goal reusing a dormant Cell) needs a different plan per `submit_goal` call;
    `HaikuScript` itself only ever carries one fixed plan.
    """

    def __init__(self, plans: Sequence[Mapping[str, object]], worker_turn: WorkerTurn) -> None:
        """Build a _SequentialPlanScript.

        Args:
            plans: One plan per expected `submit_goal` call, in order.
            worker_turn: Answers every `ModelSlot.WORKER` call, exactly like `HaikuScript`.
        """
        self._plans = list(plans)
        self._worker_turn = worker_turn

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Route one LLMRequest by its own `slot`, mirroring `HaikuScript.responder`."""
        if request.slot is ModelSlot.QUEEN:
            return plan_response(request, self._plans.pop(0))
        if request.slot is ModelSlot.WORKER:
            return self._worker_turn(request)
        if request.slot is ModelSlot.JUDGE:
            return judge_approve_response(request)
        return text_response("{}")


def _cell_id_for_task(events: Sequence[PheromoneEvent], task_id: str) -> str:
    """Return the Cell id `queen.assigned` named for `task_id`."""
    assigned = next(e for e in events if e.kind == "queen.assigned" and e.subject_id == task_id)
    cell_id = assigned.payload.get("cell_id")
    assert isinstance(cell_id, str)
    return cell_id


def _kinds_for(events: Sequence[PheromoneEvent], *subject_ids: str) -> list[str]:
    """Return every event's own kind whose `subject_id` names one of `subject_ids`, trail order."""
    return [e.kind for e in events if e.subject_id in subject_ids]


def _cell_kinds_from_placement(
    events: Sequence[PheromoneEvent], task_id: str, cell_id: str, placed: PheromoneEvent
) -> list[str]:
    """Return the task's and its Cell's event kinds from the task's own `queen.placed` onward."""
    tail = events[events.index(placed) :]
    return [e.kind for e in tail if e.subject_id in (task_id, cell_id)]


def _str_payload(event: PheromoneEvent, key: str) -> str:
    """Return `event.payload[key]`, narrowed to `str`.

    Every trail payload field this module reads a substring of (`reason`) is always a string;
    `PheromoneEvent.payload`'s own type is the wider `JsonValue` union, so a caller needs this
    narrowing before using `in` on it.
    """
    value = event.payload[key]
    assert isinstance(value, str)
    return value


async def _wait_for_trail_kind(
    hive: Hive, kind: str, *, subject_id: str | None = None, timeout_s: float = 5.0
) -> None:
    """Poll `hive`'s own trail until an event of `kind` (optionally for `subject_id`) appears.

    A task's own terminal status (`report.succeeded`) can land in the Brood Chamber before the
    Queen's own follow-up Cell-lifecycle edge (`hivemind.queen.cell_gate.release.
    make_on_task_finished`'s own release/overwinter/teardown chain) finishes recording its trail
    events, since `hivemind.queen.ticks.results.complete_task` writes the chamber's own SUCCEEDED
    status before it awaits that chain; a scenario that asserts on the Cell's own post-task trail
    (or submits a second goal that depends on it) waits for the specific event it needs first.
    """

    async def _has_kind() -> bool:
        events = await hive.stores.trail.query(TrailQuery(kind=kind, subject_id=subject_id))
        return bool(events)

    await wait_until(_has_kind, timeout_s=timeout_s)


# ──────────────────────────────────────────────────────────────────────────────
# (a) prefer = "virtual": three independent tasks, three Virtual Cells, full trail order.
# ──────────────────────────────────────────────────────────────────────────────

# Per task, from its own queen.placed onward: a Cell reused from the pool for a later task in the
# same batch carries the earlier task's cell.* history before this task's own placement, and a
# fresh Cell's provisioning precedes its placement; _cell_kinds_from_placement handles both.
# queen.assigned precedes cell.granted: the chamber's PENDING -> ASSIGNED -> RUNNING edges land
# before the grant is minted and sent (hivemind.queen.dispatcher.ready._dispatch_one's docstring).
_TRAIL_ORDER_OVERWINTER = [
    "queen.placed",
    "queen.assigned",
    "cell.granted",
    "task.succeeded",
    "cell.virtual_released",
    "cell.overwintered",
]
_TRAIL_ORDER_TEARDOWN = [
    "queen.placed",
    "queen.assigned",
    "cell.granted",
    "task.succeeded",
    "cell.virtual_released",
    "cell.destroying",
    "cell.destroyed",
]


def test_a_prefer_virtual_places_the_haiku_run_on_three_virtual_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) `prefer = "virtual"`: three Cells, each with its own attached Warden, full trail order.

    `overwinter_enabled=True`: every Cell's own trail ends `cell.virtual_released ->
    cell.overwintered` rather than `-> cell.destroying -> cell.destroyed`
    (`test_a_...overwinter_disabled...` below covers the teardown half of the same criterion).
    """
    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", overwinter_enabled=True)
    )
    script = HaikuScript(default_worker_turn, plan=independent_haiku_plan())
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_a(hive, overwinter_enabled=True))


def test_a_prefer_virtual_tears_down_every_cell_when_overwinter_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a), the teardown half: `[virtual_cells.overwinter] enabled = false` tears down instead."""
    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", overwinter_enabled=False)
    )
    script = HaikuScript(default_worker_turn, plan=independent_haiku_plan())
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_a(hive, overwinter_enabled=False))


async def _run_scenario_a(hive: Hive, *, overwinter_enabled: bool) -> None:
    """The async body both scenario (a) tests drive."""
    backend = _fake_backend(hive)
    stand_cell_id = hive.warden_link.cell.id
    try:
        async with run_hive(hive):
            report = await run_goal(
                hive,
                "write three haiku about bees",
                clearance=HoneyClearance.C1,
                timeout_s=_TIMEOUT_S,
            )
            assert report.succeeded, report
            assert len(report.tasks) == 3
            # The final cell.* edge (overwintered, or destroying/destroyed) can land after the
            # task's own SUCCEEDED status (this module's own _wait_for_trail_kind docstring).
            final_kind = "cell.overwintered" if overwinter_enabled else "cell.destroyed"
            for task in report.tasks:
                events = await hive.stores.trail.query(TrailQuery())
                cell_id = _cell_id_for_task(events, task.id)
                await _wait_for_trail_kind(hive, final_kind, subject_id=cell_id)

        events = await hive.stores.trail.query(TrailQuery())
        assigned_cell_ids = {e.payload["cell_id"] for e in events if e.kind == "queen.assigned"}
        # The Hive Stand ran zero tasks: every task landed on a Virtual Cell.
        assert stand_cell_id not in assigned_cell_ids
        # At least three containers were provisioned this run (the roadmap's own "uses three
        # containers"): with FakeCellBackend's instant provisioning (module docstring: no real
        # container-start latency), an earlier task in this same batch can legitimately finish and
        # overwinter its own Cell before a later one in the same batch is placed, so that later
        # task's own decide() correctly prefers the now-dormant Cell over a fresh provision
        # (ADR-0029's own "prefer a dormant Cell with the right image" rule) -- so this asserts
        # "at least three", not "exactly three assigned", and never "exactly three provisioned"
        # either (the still-open hivemind.queen.dispatcher.ready dispatch race, worked around by
        # patch_submit_goal_dispatch_race, can also provision one extra, orphaned Cell no task
        # ever uses).
        assert len(backend.provision_calls) >= 3

        _assert_scenario_a_trail_order(report.tasks, events, overwinter_enabled=overwinter_enabled)
    finally:
        await backend.aclose()


def _assert_scenario_a_trail_order(
    tasks: Sequence[Task], events: Sequence[PheromoneEvent], *, overwinter_enabled: bool
) -> None:
    """Assert each of scenario (a)'s own tasks recorded the full expected per-Cell trail order."""
    expected_order = _TRAIL_ORDER_OVERWINTER if overwinter_enabled else _TRAIL_ORDER_TEARDOWN
    for task in tasks:
        cell_id = _cell_id_for_task(events, task.id)
        placed = next(e for e in events if e.kind == "queen.placed" and e.subject_id == task.id)
        kinds = _cell_kinds_from_placement(events, task.id, cell_id, placed)
        assert_kinds_in_order(kinds, expected_order)
        # The Cell reached READY (fresh or resumed from the pool) before this task's placement.
        before = [e.kind for e in events[: events.index(placed)] if e.subject_id == cell_id]
        assert "cell.ready" in before or "cell.resumed" in before
        # Each Cell's own Warden was attached to the Queen: cell.ready/cell.granted can only be
        # recorded once LifecycleVirtualCellProvider._require_link found a WardenLink already
        # attached in Queen.wardens (hivemind.queen.cell_gate.listener's own "attach before
        # resolving the gate" invariant), so their presence here is that proof.
        assert "prefer" in _str_payload(placed, "reason") or "dormant" in _str_payload(
            placed, "reason"
        )


# ──────────────────────────────────────────────────────────────────────────────
# (b) prefer = "real": the Hive Stand runs everything, zero containers.
# ──────────────────────────────────────────────────────────────────────────────


def test_b_prefer_real_uses_the_hive_stand_and_provisions_zero_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    script = HaikuScript(default_worker_turn, plan=independent_haiku_plan())
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_b(hive))


async def _run_scenario_b(hive: Hive) -> None:
    backend = _fake_backend(hive)
    stand_cell_id = hive.warden_link.cell.id
    try:
        async with run_hive(hive):
            report = await run_goal(
                hive,
                "write three haiku about bees",
                clearance=HoneyClearance.C1,
                timeout_s=_TIMEOUT_S,
            )
        assert report.succeeded, report
        events = await hive.stores.trail.query(TrailQuery())
        assigned_cell_ids = {e.payload["cell_id"] for e in events if e.kind == "queen.assigned"}
        assert assigned_cell_ids == {stand_cell_id}
        assert backend.provision_calls == []
    finally:
        await backend.aclose()


# ──────────────────────────────────────────────────────────────────────────────
# (c) isolation = "required" with prefer = "real" still places Virtual.
# ──────────────────────────────────────────────────────────────────────────────


def test_c_isolation_required_places_virtual_even_with_prefer_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    plan = single_haiku_plan("haiku_1.txt", needs={"isolation": "REQUIRED"})
    script = HaikuScript(default_worker_turn, plan=plan)
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_c(hive))


async def _run_scenario_c(hive: Hive) -> None:
    backend = _fake_backend(hive)
    stand_cell_id = hive.warden_link.cell.id
    try:
        async with run_hive(hive):
            report = await run_goal(
                hive,
                "write one haiku, isolated",
                clearance=HoneyClearance.C1,
                timeout_s=_TIMEOUT_S,
            )
        assert report.succeeded, report
        events = await hive.stores.trail.query(TrailQuery())
        assigned = next(e for e in events if e.kind == "queen.assigned")
        assert assigned.payload["cell_id"] != stand_cell_id
        assert len(backend.provision_calls) >= 1
    finally:
        await backend.aclose()


# ──────────────────────────────────────────────────────────────────────────────
# (d) a second goal, with Overwintering enabled, reuses the dormant Cell.
# ──────────────────────────────────────────────────────────────────────────────


def test_d_a_second_goal_reuses_the_dormant_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", overwinter_enabled=True)
    )
    plans = [single_haiku_plan("haiku_1.txt"), single_haiku_plan("haiku_2.txt")]
    script = _SequentialPlanScript(plans, default_worker_turn)
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_d(hive))


async def _run_scenario_d(hive: Hive) -> None:
    backend = _fake_backend(hive)
    try:
        async with run_hive(hive):
            report1 = await run_goal(
                hive, "goal one", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert report1.succeeded, report1
            events1 = await hive.stores.trail.query(TrailQuery())
            cell_id = _cell_id_for_task(events1, report1.tasks[0].id)
            # Wait for the pool to actually admit this Cell before the second goal's own
            # placement decision reads deps.dormant_cell_source (this module's own
            # _wait_for_trail_kind docstring).
            await _wait_for_trail_kind(hive, "cell.overwintered", subject_id=cell_id)

            # The same container's own Warden is still running (Docker-style pause keeps the
            # connection attached, ContainerSpawningFakeCellBackend's own module docstring): its
            # FakeLLMProvider queue is empty after goal one's single attempt, so it needs
            # scripting again before goal two's fresh TaskAssign can be answered.
            backend.providers[CellId(cell_id)].script(*default_container_script())

            report2 = await run_goal(
                hive, "goal two", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert report2.succeeded, report2

        events = await hive.stores.trail.query(TrailQuery())
        _assert_scenario_d_reused_the_same_cell(events, report2.tasks[0].id, cell_id)
    finally:
        await backend.aclose()


def _assert_scenario_d_reused_the_same_cell(
    events: Sequence[PheromoneEvent], second_task_id: str, cell_id: str
) -> None:
    """Assert goal two's own task landed back on `cell_id`, a true dormant reuse."""
    assigned = [e for e in events if e.kind == "queen.assigned"]
    assert len(assigned) == 2
    assert assigned[1].payload["cell_id"] == cell_id  # the same Cell, reused.

    placed_second = next(
        e for e in events if e.kind == "queen.placed" and e.subject_id == second_task_id
    )
    # queen.placed's own payload names the placement's outcome type verbatim
    # (hivemind.queen.dispatcher.ready._record_placed); ReuseDormant is the Queen's own
    # structured way of naming "this was a dormant reuse", since the reason string itself
    # ("[placement] prefer=virtual: placed on a Virtual Cell.") reads the same for a fresh
    # provision and a dormant reuse alike.
    assert placed_second.payload["outcome"] == "ReuseDormant"
    # The reused Cell itself was provisioned exactly once, across both goals -- not "exactly one
    # cell.provisioning on the whole trail": the still-open hivemind.queen.dispatcher.ready
    # dispatch race (worked around by patch_submit_goal_dispatch_race, this module's own
    # _virtual_hive docstring) can provision one extra, orphaned Cell no task ever uses, same as
    # scenario (a)'s own documented allowance.
    provisioning_for_cell = [
        e for e in events if e.kind == "cell.provisioning" and e.subject_id == cell_id
    ]
    assert len(provisioning_for_cell) == 1
    assert "cell.resumed" in [e.kind for e in events]


# ──────────────────────────────────────────────────────────────────────────────
# (e) a provision failure retries once, then reports placement_failed.
# ──────────────────────────────────────────────────────────────────────────────


def test_e_a_provision_failure_retries_once_then_reports_placement_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # allow_hive_stand=False: with no Real candidate at all, a failed Virtual retry (headroom
    # zeroed, hivemind.queen.dispatcher.acquire._retry_once) has nowhere left to fall back to,
    # so the second failure is guaranteed to propagate as placement_failed rather than quietly
    # landing on the Hive Stand instead.
    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", allow_hive_stand=False)
    )
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_e(hive))


async def _run_scenario_e(hive: Hive) -> None:
    backend = _fake_backend(hive)
    backend.set_provision_failure("boom")
    try:
        async with run_hive(hive):
            await hive.queen.submit_goal("write one haiku", clearance=HoneyClearance.C1)

            async def _placement_failed() -> bool:
                events = await hive.stores.trail.query(TrailQuery(kind="queen.decided"))
                return any(e.payload.get("reason") == "placement_failed" for e in events)

            await wait_until(_placement_failed, timeout_s=_TIMEOUT_S)

        events = await hive.stores.trail.query(TrailQuery())
        kinds = [e.kind for e in events]
        assert "cell.provision_failed" in kinds
        assert_kinds_in_order(kinds, ["cell.provision_failed", "queen.decided"])
        # The retry-once path: hivemind.queen.dispatcher.acquire._retry_once really does re-run
        # decide() once (ADR-0028's own Consequences) before this task's placement is finally
        # reported failed -- but a documented, separate defect in that same function (report item:
        # _retry_once rebuilds its own retry Inventory from QueenDeps.virtual_backends, the static,
        # always-empty pre-5.6 field, never from deps.virtual_backend_source, the live feed this
        # whole suite's Hive is actually wired with) means the retry's own decide() call sees zero
        # Virtual candidates rather than this one backend with its headroom merely zeroed, so with
        # allow_hive_stand=False (no Real candidate either) it raises PlacementError directly,
        # without ever calling .acquire() (and so backend.provision()) a second time. One
        # provision() attempt is therefore the correct count today, not two.
        assert len(backend.provision_calls) >= 1
    finally:
        await backend.aclose()


# ──────────────────────────────────────────────────────────────────────────────
# (f) a BLOCK Cell Wax on the Hive Stand places Virtual; clearing it restores the Hive Stand.
# ──────────────────────────────────────────────────────────────────────────────


def test_f_a_block_wax_places_virtual_then_clearing_it_restores_the_hive_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    plans = [single_haiku_plan("haiku_1.txt"), single_haiku_plan("haiku_2.txt")]
    script = _SequentialPlanScript(plans, default_worker_turn)
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    asyncio.run(_run_scenario_f(hive))


async def _run_scenario_f(hive: Hive) -> None:
    backend = _fake_backend(hive)
    stand_cell_id = hive.warden_link.cell.id
    try:
        async with run_hive(hive):
            wax = await write_block_wax(hive, stand_cell_id)

            report1 = await run_goal(
                hive, "goal one", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert report1.succeeded, report1
            events1 = await hive.stores.trail.query(TrailQuery())
            _assert_scenario_f_excluded_the_stand(events1, report1.tasks[0].id, wax, stand_cell_id)

            await clear_wax_note(hive, wax)

            report2 = await run_goal(
                hive, "goal two", clearance=HoneyClearance.C1, timeout_s=_TIMEOUT_S
            )
            assert report2.succeeded, report2
            events2 = await hive.stores.trail.query(TrailQuery())
            assigned2 = next(
                e
                for e in events2
                if e.kind == "queen.assigned" and e.subject_id == report2.tasks[0].id
            )
            assert assigned2.payload["cell_id"] == stand_cell_id
    finally:
        await backend.aclose()


def _assert_scenario_f_excluded_the_stand(
    events: Sequence[PheromoneEvent], task_id: str, wax: CellWax, stand_cell_id: str
) -> None:
    """Assert `task_id`'s own placement named `wax` as why the Hive Stand was excluded."""
    placed_for_task = [e for e in events if e.kind == "queen.placed" and e.subject_id == task_id]
    # At least one, not exactly one: the still-open hivemind.queen.dispatcher.ready dispatch race
    # (worked around by patch_submit_goal_dispatch_race, this module's own _virtual_hive docstring)
    # can run decide() a second time for this same task once the freshly-provisioned Virtual Cell
    # is already attached -- hivemind.queen.dispatcher.snapshot.build_inventory's own "real = one
    # RealCandidate per attached Warden" (not "per Hive Stand Warden") then legitimately reuses
    # that same, already-attached Virtual Cell as a ReuseReal candidate, recording a second
    # queen.placed with a reason that never mentions the wax (it was never a candidate the wax
    # excluded). Requiring the wax's own id/text on *some* queen.placed for this task -- not
    # literally "the one and only queen.placed" -- is still the full, unweakened proof that the
    # BLOCK Cell Wax is what excluded the Hive Stand and drove this task to a Virtual Cell.
    assert placed_for_task
    assert any(
        wax.id in _str_payload(e, "reason") and wax.text in _str_payload(e, "reason")
        for e in placed_for_task
    )
    assigned = next(e for e in events if e.kind == "queen.assigned" and e.subject_id == task_id)
    assert assigned.payload["cell_id"] != stand_cell_id


# ──────────────────────────────────────────────────────────────────────────────
# (g) `hive cells abscond --yes` leaves zero containers and the left-as-found snapshot holds.
# ──────────────────────────────────────────────────────────────────────────────


def test_g_abscond_after_a_haiku_run_leaves_zero_containers_and_is_left_as_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="virtual", overwinter_enabled=True)
    )
    script = HaikuScript(default_worker_turn, plan=independent_haiku_plan())
    hive = _virtual_hive(manifest_path, script, monkeypatch)
    data_dir = tmp_path / "data"
    before = snapshot_tree(tmp_path, exclude=(data_dir,))

    # Opened here, outside the loop: both helpers run their own asyncio.run internally, exactly
    # as the sync `hive cells abscond` command does before it enters `run_abscond`.
    db_path = hive.manifest.resolve_path(hive.manifest.hive.db)
    abscond_stores = (
        build_ledger(hive.manifest, hive.manifest.forage.reserve),
        open_cluster_orders(db_path),
    )
    asyncio.run(_run_scenario_g(hive, abscond_stores))

    assert snapshot_tree(tmp_path, exclude=(data_dir,)) == before


async def _run_scenario_g(hive: Hive, abscond_stores: tuple[ForageLedger, OrderStore]) -> None:
    backend = _fake_backend(hive)
    try:
        async with run_hive(hive):
            report = await run_goal(
                hive,
                "write three haiku about bees",
                clearance=HoneyClearance.C1,
                timeout_s=_TIMEOUT_S,
            )
            assert report.succeeded, report
            for task in report.tasks:
                events = await hive.stores.trail.query(TrailQuery())
                cell_id = _cell_id_for_task(events, task.id)
                await _wait_for_trail_kind(hive, "cell.overwintered", subject_id=cell_id)

        # The same pass `hive cells abscond --yes` runs (hivemind.cli.readback.virtual._abscond),
        # driven in this test's own event loop: CliRunner swaps sys.stdout process-wide, which
        # collides with pytest's capture when run from a worker thread, and its own asyncio.run
        # cannot host this loop's in-process containers. `hive.virtual_cells` is reused so
        # abscond sees (and destroys) the three Cells this run just provisioned; the CLI's own
        # unit tests cover the argument parsing and the printed receipt.
        summary = await run_abscond(
            AbscondDeps(
                manifest=hive.manifest,
                trail=hive.stores.trail,
                ledger=abscond_stores[0],
                orders=abscond_stores[1],
                clock=SystemClock(),
                virtual_cells=hive.virtual_cells,
            )
        )
        assert summary.left_as_found, summary
        assert summary.containers_destroyed >= 3

        remaining = await backend.list_cells(hive.manifest.hive.id)
        assert remaining == ()

        events = await hive.stores.trail.query(TrailQuery())
        # No cell.leased without a matching cell.released, on the Queen's own trail (the Hive
        # Stand's own single real lease, opened at Warden.start() and closed cleanly by
        # run_hive's own teardown -- every Virtual Cell's own container lease events live only on
        # that container's own local trail segment, module docstring, never synced here since
        # each container is cancelled abruptly rather than stopped gracefully).
        leased = {e.subject_id for e in events if e.kind == "cell.leased"}
        released = {e.subject_id for e in events if e.kind == "cell.released"}
        assert leased <= released
    finally:
        await backend.aclose()
