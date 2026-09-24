"""End-to-end: a Night Veil Cell leaves only its skeleton on the Queen's trail, on every end path.

Codingrules section 12 run through a whole Hive, the way `test_night_veil_link` runs one: a human's
goal request naming NIGHT_VEIL is planned by the Queen, placed on a fresh Virtual Cell the phase 5
suite's container-spawning fake backend provisions, and worked by a real in-Cell Warden that
reaches the Hive Stand's listener through a fake Tor. While the Cell lives, its whole record (its
Warden's shipped segments, the Queen's own detail about it and its task) waits in its ephemeral
segment on the Queen's side and none of it reaches her durable trail; once it ends, the durable
trail holds, for that Cell and its task, only the skeleton section 12 names, cut to its skeleton
payloads, plus the purge's own `cell.purged`, and the ephemeral store is empty. Each end path is
its own scenario: a normal release after the task succeeds, a provision that fails its Night Veil
attestation after the Cell existed, an Absconding, and a restarted Queen that finds the Cell gone.
A MEADOW Cell's whole local trail still merges into the durable trail exactly as it was recorded.

"About the Cell or its task" is read by id: an event is about the Night Veil work when its subject
or payload names the Cell, the task, the task's grant or the Cell's Warden, or when a node other
than the Queen's recorded it. The goal request's own `queen.goal_request_*` records are the
human's request rather than the Cell's work: they name the goal they planned by id and carry no
words (`hivemind.pheromone.events.families.supervisors`), so they are the one family besides the
skeleton allowed to name the task.

The Absconding and the restart need a Cell still working when its Queen stops: its Worker's model
call never returns (`_hold_work`), and the Cell is ended from outside, the way a crashed Queen
leaves one behind.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention for the segments, the skeleton and the purge.
    - hivemind.hive.night_veil.boundary for where every end path runs the purge.
    - tests.e2e.test_night_veil_link for the same Hive, its Tor link and its binding.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import builders.virtual_cells as virtual_cells_builders
import pytest
from builders.human import is_planning, make_goal_request
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    abscond_now,
    single_haiku_plan,
    virtual_cells_manifest,
    without_shutdown_retire,
)
from e2e.kernel_helpers import (
    default_worker_turn,
    judge_approve_response,
    plan_response,
    wait_until,
)

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.cell import Cell, CombShieldLevel
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.cli.compose.deps import build_ledger
from hivemind.cli.in_cell.main import run_in_cell_warden as real_run_in_cell_warden
from hivemind.cli.readback.virtual_abscond import AbscondSummary
from hivemind.cli.stores import open_cluster_orders
from hivemind.forage import ModelSlot
from hivemind.hive.night_veil import (
    CheckResult,
    CheckStatus,
    FakeNightVeilProbe,
    NightVeilBoundary,
    NightVeilProbe,
)
from hivemind.llm import LLMRequest, LLMResponse, text_response
from hivemind.manifest import load_manifest
from hivemind.pheromone import (
    MAX_QUERY_LIMIT,
    SKELETON_KINDS,
    MemoryPheromoneTrail,
    PheromoneEvent,
    TrailQuery,
    skeleton_event,
)
from hivemind.wardens.deps import WardenDeps
from waggle.clock import Clock, SystemClock
from waggle.ids import CellId
from waggle.transport.socks import FakeSocksProxy

pytestmark = pytest.mark.e2e

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION_PORT = 8710  # The hidden service's virtual port; the fake Tor maps it to the listener.
_TIMEOUT_S = 20.0  # Generous: each scenario finishes in a few seconds on loopback.
_DECISION = '{"action": "RECORD", "reason": "Nothing to decide."}'  # Any awake episode's answer.
_PLAN = single_haiku_plan("haiku_1.txt")
_TASK_WORDS = ("Write haiku 1", "Write a haiku about bees to haiku_1.txt")  # Title, objective.
_EVERYTHING = TrailQuery(limit=MAX_QUERY_LIMIT)
_PURGED = "cell.purged"
_PURGE_COUNTS = {"events_purged", "side_channel_records_purged"}
_REQUEST_RECORDS = "queen.goal_request_"  # The human request's own family (module docstring).
_RED = CheckResult(status=CheckStatus.FAIL, detail="no circuit built")
_DETAIL = {"worker.spawned", "llm.call", "capping.proposed", "capping.capped"}
_WORKING = {"warden.started", "queen.assigned", "worker.spawned", "worker.started"}
_QUEEN_DETAIL = {"cell.provisioning", "cell.ready", "cell.granted", "cell.destroying"}
# What each end path leaves about the Cell itself, in order; the task's rows are checked apart.
_ENDED_WHILE_WORKING = [
    "cell.provisioned",
    "cell.attested",
    "forage.plan_written",
    "cell.destroyed",
    _PURGED,
]
_RELEASED = [*_ENDED_WHILE_WORKING[:-1], "capping.summary", _PURGED]
_FAILED_ATTESTATION = ["cell.provisioned", "cell.attested", "cell.destroyed", _PURGED]
_ENDS = ("cell.destroyed", _PURGED)
_SUCCEEDED_TASK = [
    "task.submitted",
    "queen.placed",
    "task.assigned",
    "task.started",
    "task.succeeded",
]

ProbeFactory = Callable[[Cell], NightVeilProbe]
Snapshots = dict[str, tuple[tuple[PheromoneEvent, ...], tuple[PheromoneEvent, ...]]]


def _responder(request: LLMRequest) -> LLMResponse:
    """Answer the Hive Stand's one fake provider: the plan, any awake episode, and the Judge."""
    if request.slot is ModelSlot.QUEEN:
        return plan_response(request, _PLAN) if is_planning(request) else text_response(_DECISION)
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return default_worker_turn(request)


def _green_probe(_cell: Cell) -> NightVeilProbe:
    """The attestation probe this Hive runs in place of production's fail-closed one."""
    return FakeNightVeilProbe()


def _red_then_green() -> ProbeFactory:
    """A probe factory whose first Cell fails `tor_healthy` and every later one passes."""
    probes = iter([FakeNightVeilProbe({"tor_healthy": _RED})])
    return lambda _cell: next(probes, FakeNightVeilProbe())


@dataclass
class _Run:
    """One Hive under test, with the parts of it this module watches."""

    hive: Hive
    manifest_path: Path
    tor: FakeSocksProxy
    in_cell: list[WardenDeps]  # Every in-Cell Warden's deps, across every Hive of the test.

    @property
    def backend(self) -> ContainerSpawningFakeCellBackend:
        """The Hive's one registered backend: the same instance for the Hive's whole life."""
        assert self.hive.virtual_cells is not None
        backend = self.hive.virtual_cells.registry.get("fake")
        assert isinstance(backend, ContainerSpawningFakeCellBackend)
        return backend

    @property
    def night_veil(self) -> NightVeilBoundary:
        """The Hive's Night Veil boundary: its segments, its trail and its purge."""
        assert self.hive.virtual_cells is not None
        return self.hive.virtual_cells.night_veil

    def route(self) -> None:
        """Route the onion name to the listener, as Tor would, once `run_hive` has bound it."""
        assert self.hive.virtual_cells is not None
        port = urlsplit(self.hive.virtual_cells.listener.uri).port
        assert port is not None
        self.tor.routes[_ONION] = ("127.0.0.1", port)

    async def rebuilt(self) -> _Run:
        """A new Hive over the same manifest and stores: a Queen restart, a fresh backend."""
        return _Run(await _build(self.manifest_path), self.manifest_path, self.tor, self.in_cell)


@asynccontextmanager
async def _hive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    probes: ProbeFactory = _green_probe,
    hold_work: bool = False,
    tuning: VirtualCellsTuning | None = None,
) -> AsyncIterator[_Run]:
    """Build a Hive whose Virtual side runs real in-Cell Wardens, over a fake Tor."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    monkeypatch.setattr("hivemind.cli.compose.virtual_cells._fail_closed_night_veil_probe", probes)
    in_cell = _watch_in_cell(monkeypatch, hold_work=hold_work)
    tor = FakeSocksProxy()
    await tor.start()
    try:
        manifest_path = _manifest(tmp_path, tor, tuning)
        yield _Run(await _build(manifest_path), manifest_path, tor, in_cell)
    finally:
        await tor.close()


@asynccontextmanager
async def _running(run: _Run) -> AsyncIterator[None]:
    """Run `run.hive` with its onion routed, and stop every container it started afterwards."""
    try:
        async with run_hive(run.hive):
            run.route()
            yield
    finally:
        await run.backend.aclose()


def _manifest(tmp_path: Path, tor: FakeSocksProxy, tuning: VirtualCellsTuning | None) -> Path:
    """The phase 5 suite's fake-backend manifest, plus a Night Veil tier profile using `tor`."""
    manifest_path = virtual_cells_manifest(tmp_path, tuning=tuning)
    profile = (
        "\n[security.tiers.NIGHT_VEIL]\n"
        'egress_profile = "vpn_tor"\ncontrol_channel = "tor_hidden_service"\n'
        f'hidden_service_address = "{_ONION}:{_ONION_PORT}"\ntor_socks = "{tor.url()}"\n'
        'locale_profile = "C.UTF-8"\n'
    )
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(profile)
    return manifest_path


async def _build(manifest_path: Path) -> Hive:
    """Build the Hive off this loop: `build_hive` runs its own `asyncio.run` seams."""
    manifest = load_manifest(manifest_path, {})
    return await asyncio.to_thread(
        build_hive, manifest, environ={}, clock=SystemClock(), responders={"fake": _responder}
    )


def _watch_in_cell(monkeypatch: pytest.MonkeyPatch, *, hold_work: bool) -> list[WardenDeps]:
    """Record every in-Cell Warden's deps as the backend builds it; optionally hold its work."""
    built: list[WardenDeps] = []

    async def run_in_cell_warden(
        environ: Mapping[str, str],
        clock: Clock,
        *,
        on_deps_built: Callable[[WardenDeps], None] | None = None,
    ) -> None:
        def watch(deps: WardenDeps) -> None:
            if on_deps_built is not None:
                on_deps_built(deps)  # The backend's own script first, as it runs alone.
            built.append(deps)
            if hold_work:
                _hold_work(deps, monkeypatch)

        await real_run_in_cell_warden(environ, clock, on_deps_built=watch)

    monkeypatch.setattr(virtual_cells_builders, "run_in_cell_warden", run_in_cell_warden)
    return built


def _hold_work(deps: WardenDeps, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the Cell's Worker model calls wait until the Cell is destroyed around them.

    Before it waits, a held call ships the Cell's trail to the Queen, as its Warden's next
    heartbeat would: the scenario reads the Cell at work without waiting out that cadence.
    """
    provider, trail_sync = deps.bound.provider, deps.trail_sync
    assert trail_sync is not None  # Every in-Cell Warden ships its own trail.
    real_complete = provider.complete

    async def complete(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.WORKER:
            await trail_sync.sync()
            await asyncio.Event().wait()  # Never set: the container's cancel ends the wait.
        return await real_complete(request)

    monkeypatch.setattr(provider, "complete", complete)


def _watch_teardown(run: _Run, monkeypatch: pytest.MonkeyPatch) -> Snapshots:
    """Snapshot a held segment and the durable trail the moment its Cell's teardown starts."""
    assert run.hive.virtual_cells is not None
    lifecycle = run.hive.virtual_cells.lifecycle
    real_teardown = lifecycle.teardown
    seen: Snapshots = {}

    async def teardown(cell_id: CellId) -> None:
        segments = run.night_veil.segments
        if segments.holds(cell_id):
            seen[cell_id] = (await segments.query(cell_id, _EVERYTHING), await _durable(run))
        await real_teardown(cell_id)

    monkeypatch.setattr(lifecycle, "teardown", teardown)
    return seen


async def _request(run: _Run, tier: CombShieldLevel = CombShieldLevel.NIGHT_VEIL) -> None:
    """Ask for the one-task goal at `tier`, as a human would."""
    await run.hive.queen.request_goal(make_goal_request(SystemClock(), comb_shield=tier))


async def _abscond(hive: Hive) -> AbscondSummary:
    """Run `hive cells abscond --yes`'s own pass over `hive`, its stores opened as the CLI does."""
    database = hive.manifest.resolve_path(hive.manifest.hive.db)
    # Off this loop: both open their store under their own asyncio.run, as the command does.
    ledger = await asyncio.to_thread(build_ledger, hive.manifest, hive.manifest.forage.reserve)
    orders = await asyncio.to_thread(open_cluster_orders, database)
    return await abscond_now(hive, ledger, orders)


async def _tasks(run: _Run) -> tuple[Task, ...]:
    """Every task in the Hive's Brood Chamber: the one the goal was planned into."""
    return tuple(await run.hive.stores.chamber.list(TaskFilter()))


async def _succeeded(run: _Run) -> bool:
    """Whether every task of the goal succeeded; fails at once, naming why, if one ended otherwise.

    Failing fast turns a task that ended early (a grant a loaded host denies, say) into its own
    outcome in the report, rather than a timeout further on that names nothing.
    """
    tasks = await _tasks(run)
    for task in tasks:
        assert task.status is TaskStatus.SUCCEEDED or not is_terminal(task.status), task.outcome
    return bool(tasks) and all(task.status is TaskStatus.SUCCEEDED for task in tasks)


async def _durable(run: _Run) -> tuple[PheromoneEvent, ...]:
    """Every event on the Queen's durable trail (the boundary's reads pass straight through)."""
    return await run.hive.stores.trail.query(_EVERYTHING)


async def _counted(run: _Run, kind: str, expected: int) -> bool:
    """Whether the durable trail holds exactly `expected` events of `kind`."""
    query = TrailQuery(kind=kind, limit=MAX_QUERY_LIMIT)
    return len(await run.hive.stores.trail.query(query)) == expected


async def _working(run: _Run) -> bool:
    """Whether a held segment shows its Cell's Worker at work; fails fast if it never can."""
    for task in await _tasks(run):
        assert not is_terminal(task.status), task.outcome  # Ended before its Worker could hold.
    segments = run.night_veil.segments
    for cell_id in segments.held_cells():
        if await segments.query(cell_id, TrailQuery(kind="worker.started")):
            return True
    return False


def _night_veil_cells(events: Iterable[PheromoneEvent]) -> list[str]:
    """Every Night Veil Cell the skeleton's `cell.provisioned` names, in provisioning order."""
    return [
        e.subject_id
        for e in events
        if e.kind == "cell.provisioned" and e.payload.get("comb_shield") == "NIGHT_VEIL"
    ]


def _world(events: Sequence[PheromoneEvent], tasks: Iterable[Task], run: _Run) -> frozenset[str]:
    """Every id the Night Veil work goes by: its Cells, tasks, grants and in-Cell Wardens."""
    task_ids = {task.id for task in tasks}
    grants = {
        e.subject_id
        for e in events
        if e.kind == "forage.granted" and e.payload.get("task_id") in task_ids
    }
    wardens = {deps.hop.sender for deps in run.in_cell}
    return frozenset({*_night_veil_cells(events), *task_ids, *grants, *wardens})


def _skeletal(event: PheromoneEvent, queen_node: str) -> bool:
    """Whether `event` may outlive a Night Veil Cell: a skeleton row, cut, from the Queen."""
    if event.node_id != queen_node:
        return False  # A row the Cell's own node recorded: its local detail.
    if event.kind == _PURGED:
        return set(event.payload) == _PURGE_COUNTS
    if event.kind.startswith(_REQUEST_RECORDS):
        return True  # The human's request's own records (module docstring).
    return event.kind in SKELETON_KINDS and skeleton_event(event) == event


def _leaks(events: Sequence[PheromoneEvent], tasks: Iterable[Task], run: _Run) -> list[str]:
    """Every durable event about the Night Veil work that is more than its skeleton."""
    world = _world(events, tasks, run)
    queen_node = str(run.hive.manifest.hive.node_id)
    about = [
        e
        for e in events
        if e.node_id != queen_node
        or e.subject_id in world
        or any(member in json.dumps(e.payload) for member in world)
    ]
    return [f"{e.kind} {e.subject_id}" for e in about if not _skeletal(e, queen_node)]


def _kinds_about(events: Iterable[PheromoneEvent], subject: str) -> list[str]:
    """The kinds of every event about `subject`, in trail order."""
    return [e.kind for e in events if e.subject_id == subject]


async def _assert_working_behind_the_veil(run: _Run) -> None:
    """While the Cell works: its record is in its segment, and none of it is on the trail."""
    [cell_id] = run.night_veil.segments.held_cells()
    held = await run.night_veil.segments.query(cell_id, _EVERYTHING)
    assert {e.kind for e in held} >= _WORKING
    assert _leaks(await _durable(run), await _tasks(run), run) == []


async def _assert_only_the_skeleton_is_left(run: _Run) -> tuple[PheromoneEvent, ...]:
    """After every end: skeleton rows only, no task words, no segment, no in-Cell store left."""
    events = await _durable(run)
    assert _leaks(events, await _tasks(run), run) == []
    assert [e.kind for e in events if any(w in json.dumps(e.payload) for w in _TASK_WORDS)] == []
    assert run.night_veil.segments.held_cells() == ()
    # Each in-Cell trail was its Warden's memory alone, gone with the container.
    assert run.in_cell
    assert all(isinstance(deps.trail, MemoryPheromoneTrail) for deps in run.in_cell)
    return events


async def test_a_released_night_veil_cell_leaves_only_its_skeleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _hive(tmp_path, monkeypatch) as run:
        at_teardown = _watch_teardown(run, monkeypatch)
        async with _running(run):
            await _request(run)
            await wait_until(lambda: _succeeded(run), timeout_s=_TIMEOUT_S)
            await wait_until(lambda: _counted(run, _PURGED, 1), timeout_s=_TIMEOUT_S)

    events = await _assert_only_the_skeleton_is_left(run)
    [cell_id] = _night_veil_cells(events)
    [task] = await _tasks(run)
    assert task.status is TaskStatus.SUCCEEDED
    # Until its teardown began, its whole record waited in its segment, none of it on the trail.
    held, durable_then = at_teardown[cell_id]
    assert {e.kind for e in held} >= _DETAIL
    [deps] = run.in_cell
    assert {e.id for e in await deps.trail.query(_EVERYTHING)} <= {e.id for e in held}
    assert _leaks(durable_then, [task], run) == []
    # Then the skeleton alone: one summary for its one tier, and the purge's own counts.
    assert _kinds_about(events, cell_id) == _RELEASED
    assert _kinds_about(events, task.id) == _SUCCEEDED_TASK
    [summary] = [e for e in events if e.kind == "capping.summary"]
    assert summary.payload == {
        "tier": "SCRATCH_WRITE",
        "approved": 3,
        "rejected": 0,
        "rolled_back": 0,
    }
    [purged] = [e for e in events if e.kind == _PURGED]
    events_purged = purged.payload["events_purged"]
    assert isinstance(events_purged, int)
    assert events_purged > len(held)  # Its teardown's own rows went too.


async def test_a_failed_attestation_purges_the_night_veil_cell_it_had_provisioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _hive(tmp_path, monkeypatch, probes=_red_then_green()) as run, _running(run):
        await _request(run)
        await wait_until(lambda: _succeeded(run), timeout_s=_TIMEOUT_S)
        await wait_until(lambda: _counted(run, _PURGED, 2), timeout_s=_TIMEOUT_S)

    events = await _assert_only_the_skeleton_is_left(run)
    failed, worked = _night_veil_cells(events)
    assert _kinds_about(events, failed) == _FAILED_ATTESTATION
    assert _kinds_about(events, worked) == _RELEASED
    # The verdict survives per check, PASS or FAIL, never the check's own words.
    attested = next(e for e in events if e.kind == "cell.attested" and e.subject_id == failed)
    assert attested.payload["passed"] is False
    assert attested.payload["red"] == ["tor_healthy"]
    assert attested.payload["tor_healthy"] == {"status": "FAIL"}
    [task] = await _tasks(run)
    assert task.status is TaskStatus.SUCCEEDED


async def test_an_absconding_purges_a_night_veil_cell_it_finds_still_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _hive(tmp_path, monkeypatch, hold_work=True) as run:
        # A crashed Queen: nothing retires the Cell, which is still working when she stops.
        run.hive = without_shutdown_retire(run.hive)
        async with _running(run):
            await _request(run)
            await wait_until(lambda: _working(run), timeout_s=_TIMEOUT_S)
            await _assert_working_behind_the_veil(run)
        summary = await _abscond(run.hive)

    assert summary.left_as_found
    assert summary.containers_destroyed == 1
    events = await _assert_only_the_skeleton_is_left(run)
    [cell_id] = _night_veil_cells(events)
    assert _kinds_about(events, cell_id) == _ENDED_WHILE_WORKING
    destroyed = next(e for e in events if e.kind == "cell.destroyed" and e.subject_id == cell_id)
    assert set(destroyed.payload) == {"grants_revoked", "wax_retired", "leavings_removed"}


async def test_a_restarted_queen_purges_a_night_veil_cell_it_finds_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _hive(tmp_path, monkeypatch, hold_work=True) as first:
        first.hive = without_shutdown_retire(first.hive)
        # The Queen stops with her Cell working, and its host goes with her: a fresh backend
        # below lists nothing of it.
        async with _running(first):
            await _request(first)
            await wait_until(lambda: _working(first), timeout_s=_TIMEOUT_S)
            await _assert_working_behind_the_veil(first)
        # Its record was held in her memory alone, which a real crash takes with her process.
        [gone] = first.night_veil.segments.held_cells()
        restarted = await first.rebuilt()
        async with _running(restarted):
            # Starting the Hive reconciled it: the sweep found the Cell gone and purged it.
            assert await _counted(restarted, _PURGED, 1)

    events = await _assert_only_the_skeleton_is_left(restarted)
    assert _night_veil_cells(events) == [gone]
    assert _kinds_about(events, gone) == _ENDED_WHILE_WORKING
    # Recorded by the sweep: its destruction (never recorded before) and a purge with nothing held.
    ends = [e.payload for e in events if e.subject_id == gone and e.kind in _ENDS]
    assert ends == [{}, {"events_purged": 0, "side_channel_records_purged": 0}]


async def test_a_meadow_cells_whole_local_trail_still_merges_as_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tuning = VirtualCellsTuning(overwinter_enabled=False)  # Torn down at once, like Night Veil.
    async with _hive(tmp_path, monkeypatch, tuning=tuning) as run, _running(run):
        await _request(run, CombShieldLevel.MEADOW)
        await wait_until(lambda: _succeeded(run), timeout_s=_TIMEOUT_S)
        await wait_until(lambda: _counted(run, "cell.destroyed", 1), timeout_s=_TIMEOUT_S)

    events = await _durable(run)
    [task] = await _tasks(run)
    assert task.status is TaskStatus.SUCCEEDED
    # Every row the Cell recorded reached the Queen's trail exactly as recorded, detail and all.
    [deps] = run.in_cell
    local = await deps.trail.query(_EVERYTHING)
    assert {e.kind for e in local} >= _DETAIL
    by_id = {e.id: e for e in events}
    assert [by_id.get(e.id) for e in local] == list(local)
    # So does the Queen's own detail about the Cell and its task, which a Night Veil Cell withholds.
    [cell_id] = [e.subject_id for e in events if e.kind == "cell.provisioned"]
    assert set(_kinds_about(events, cell_id)) >= _QUEEN_DETAIL
    submitted = next(e for e in events if e.kind == "task.submitted")
    assert submitted.payload["title"] == "Write haiku 1"
    assert [e.kind for e in events if e.kind in (_PURGED, "capping.summary")] == []
    assert run.night_veil.segments.held_cells() == ()
