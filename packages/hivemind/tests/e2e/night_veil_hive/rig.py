"""Build and run a whole Hive whose Night Veil Cells run real in-Cell Wardens over a fake Tor.

The Hive is the phase 5 suite's container-spawning fake backend under a Night Veil tier profile
whose control channel is a fake Tor (`FakeSocksProxy`), which routes the onion name to the Queen's
listener once it binds. `night_veil_hive` builds it, optionally holding each Cell's Worker once it
has answered a number of model calls, so a Queen can stop (or an Absconding run) with the Cell
still at work; `running` runs it with the onion routed; `watch_teardown` snapshots a held segment
and the durable trail the moment its Cell's teardown starts, and runs a hook there first;
`request` asks for the goal as a human would, and `abscond` runs `hive cells abscond --yes`'s own
pass. Moved out of `tests.e2e.test_night_veil_boundary` once its scenarios outgrew one module.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests.e2e.test_night_veil_boundary.

Key invariants:
    - Every container a run started is stopped once `running` exits.
    - A held Worker call ships its Cell's trail before it waits, so the Queen's segment holds
      everything the Cell recorded up to the hold.

See Also:
    - tests.e2e.test_night_veil_link for the same Hive, its Tor link and its binding.
    - builders.virtual_cells for the fake backend and the Absconding pass.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
)
from e2e.kernel_helpers import default_worker_turn, judge_approve_response, plan_response

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
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from hivemind.wardens.deps import WardenDeps
from waggle.clock import Clock, SystemClock
from waggle.ids import CellId
from waggle.transport.socks import FakeSocksProxy

__all__ = [
    "EVERYTHING",
    "NightVeilRun",
    "ProbeFactory",
    "Snapshots",
    "TeardownHook",
    "abscond",
    "green_probe",
    "night_veil_hive",
    "red_then_green",
    "request",
    "running",
    "watch_teardown",
]

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION_PORT = 8710  # The hidden service's virtual port; the fake Tor maps it to the listener.
_DECISION = '{"action": "RECORD", "reason": "Nothing to decide."}'  # Any awake episode's answer.
_PLAN = single_haiku_plan("haiku_1.txt")
_RED = CheckResult(status=CheckStatus.FAIL, detail="no circuit built")
EVERYTHING = TrailQuery(limit=MAX_QUERY_LIMIT)  # One read of a whole (test-sized) trail.

ProbeFactory = Callable[[Cell], NightVeilProbe]
Snapshots = dict[str, tuple[tuple[PheromoneEvent, ...], tuple[PheromoneEvent, ...]]]
TeardownHook = Callable[[CellId], Awaitable[None]]


def _responder(request: LLMRequest) -> LLMResponse:
    """Answer the Hive Stand's one fake provider: the plan, any awake episode, and the Judge."""
    if request.slot is ModelSlot.QUEEN:
        return plan_response(request, _PLAN) if is_planning(request) else text_response(_DECISION)
    if request.slot is ModelSlot.JUDGE:
        return judge_approve_response(request)
    return default_worker_turn(request)


def green_probe(_cell: Cell) -> NightVeilProbe:
    """The attestation probe this Hive runs in place of production's fail-closed one."""
    return FakeNightVeilProbe()


def red_then_green() -> ProbeFactory:
    """A probe factory whose first Cell fails `tor_healthy` and every later one passes."""
    probes = iter([FakeNightVeilProbe({"tor_healthy": _RED})])
    return lambda _cell: next(probes, FakeNightVeilProbe())


@dataclass
class NightVeilRun:
    """One Hive under test, with the parts of it this suite watches.

    Attributes:
        hive: The Hive, rebuilt in place by a scenario that restarts or strips it.
        manifest_path: The manifest every Hive of the test is built from.
        tor: The fake Tor every Cell's link rides.
        in_cell: Every in-Cell Warden's deps, across every Hive of the test.
        holding: Set once a Cell's Worker holds (its trail shipped), when the run holds work.
    """

    hive: Hive
    manifest_path: Path
    tor: FakeSocksProxy
    in_cell: list[WardenDeps]
    holding: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def backend(self) -> ContainerSpawningFakeCellBackend:
        """The Hive's own fake backend: the same instance for the Hive's whole life."""
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

    async def rebuilt(self) -> NightVeilRun:
        """A new Hive over the same manifest and stores: a Queen restart, a fresh backend."""
        hive = await _build(self.manifest_path)
        return NightVeilRun(hive, self.manifest_path, self.tor, self.in_cell, self.holding)


@asynccontextmanager
async def night_veil_hive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    probes: ProbeFactory = green_probe,
    hold_after: int | None = None,
    tuning: VirtualCellsTuning | None = None,
) -> AsyncIterator[NightVeilRun]:
    """Build a Hive whose Virtual side runs real in-Cell Wardens, over a fake Tor.

    Args:
        tmp_path: Where the manifest and every store live.
        monkeypatch: Swaps in the container-spawning backend, the probes and the Warden watch.
        probes: The attestation probe each Night Veil Cell gets.
        hold_after: Hold each Cell's Worker once it has answered this many model calls; None
            never holds.
        tuning: The Virtual side's own knobs, when a scenario needs other than the defaults.
    """
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    monkeypatch.setattr("hivemind.cli.compose.virtual_cells._fail_closed_night_veil_probe", probes)
    holding = asyncio.Event()
    in_cell = _watch_in_cell(monkeypatch, hold_after, holding)
    tor = FakeSocksProxy()
    await tor.start()
    try:
        manifest_path = _manifest(tmp_path, tor, tuning)
        yield NightVeilRun(await _build(manifest_path), manifest_path, tor, in_cell, holding)
    finally:
        await tor.close()


@asynccontextmanager
async def running(run: NightVeilRun) -> AsyncIterator[None]:
    """Run `run.hive` with its onion routed, and stop every container it started afterwards."""
    try:
        async with run_hive(run.hive):
            run.route()
            yield
    finally:
        await run.backend.aclose()


def watch_teardown(
    run: NightVeilRun, monkeypatch: pytest.MonkeyPatch, before: TeardownHook | None = None
) -> Snapshots:
    """Snapshot a held segment and the durable trail the moment its Cell's teardown starts.

    Args:
        run: The Hive whose lifecycle's teardown is watched.
        monkeypatch: Wraps that teardown.
        before: Awaited with the Cell's id first, before the snapshot, for every held Cell.

    Returns:
        Each held Cell's segment and the durable trail as its teardown began, filled as they end.
    """
    assert run.hive.virtual_cells is not None
    lifecycle = run.hive.virtual_cells.lifecycle
    real_teardown = lifecycle.teardown
    seen: Snapshots = {}

    async def teardown(cell_id: CellId) -> None:
        segments = run.night_veil.segments
        if segments.holds(cell_id):
            if before is not None:
                await before(cell_id)
            held = await segments.query(cell_id, EVERYTHING)
            seen[cell_id] = (held, await run.hive.stores.trail.query(EVERYTHING))
        await real_teardown(cell_id)

    monkeypatch.setattr(lifecycle, "teardown", teardown)
    return seen


async def request(run: NightVeilRun, tier: CombShieldLevel = CombShieldLevel.NIGHT_VEIL) -> None:
    """Ask for the one-task goal at `tier`, as a human would."""
    await run.hive.queen.request_goal(make_goal_request(SystemClock(), comb_shield=tier))


async def abscond(hive: Hive) -> AbscondSummary:
    """Run `hive cells abscond --yes`'s own pass over `hive`, its stores opened as the CLI does."""
    database = hive.manifest.resolve_path(hive.manifest.hive.db)
    # Off this loop: both open their store under their own asyncio.run, as the command does.
    ledger = await asyncio.to_thread(build_ledger, hive.manifest, hive.manifest.forage.reserve)
    orders = await asyncio.to_thread(open_cluster_orders, database)
    return await abscond_now(hive, ledger, orders)


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


def _watch_in_cell(
    monkeypatch: pytest.MonkeyPatch, hold_after: int | None, holding: asyncio.Event
) -> list[WardenDeps]:
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
            if hold_after is not None:
                _hold_work(deps, monkeypatch, hold_after, holding)

        await real_run_in_cell_warden(environ, clock, on_deps_built=watch)

    monkeypatch.setattr(virtual_cells_builders, "run_in_cell_warden", run_in_cell_warden)
    return built


def _hold_work(
    deps: WardenDeps, monkeypatch: pytest.MonkeyPatch, after: int, holding: asyncio.Event
) -> None:
    """Make the Cell's Worker calls past the first `after` wait until the Cell is destroyed.

    Before it waits, a held call ships the Cell's trail to the Queen, as its Warden's next
    heartbeat would, then sets `holding`: the scenario reads the Cell at work without waiting out
    that cadence, and with everything its Worker did first (its Capping included) on the Queen's
    side.
    """
    provider, trail_sync = deps.bound.provider, deps.trail_sync
    assert trail_sync is not None  # Every in-Cell Warden ships its own trail.
    real_complete = provider.complete
    answered = 0

    async def complete(request: LLMRequest) -> LLMResponse:
        nonlocal answered
        if request.slot is ModelSlot.WORKER:
            if answered >= after:
                await trail_sync.sync()
                holding.set()
                await asyncio.Event().wait()  # Never set: the container's cancel ends the wait.
            answered += 1
        return await real_complete(request)

    monkeypatch.setattr(provider, "complete", complete)
