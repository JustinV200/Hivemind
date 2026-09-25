"""Build Virtual Cell e2e test data: a container-spawning fake backend, plans, wax, manifests.

Roadmap phase 5's own e2e slice (`tests.e2e.test_virtual_cells`): every scenario there runs the
REAL Queen, `CellListener`, `LifecycleVirtualCellProvider`, `CellLifecycle` and `OverwinterPool`
against `hivemind.hive.backends.fake.FakeCellBackend` -- but that fake never starts a real
container the way `DockerCellBackend`/`QemuCellBackend` do, so this module's
`ContainerSpawningFakeCellBackend` stands in for "the container runtime" itself: on every
`provision()`, once the fake backend has
minted a real `hivemind.hive.backends.bootstrap.CellBootstrap` for the Cell (which it only does when
built with an `endpoint`, `hivemind.cli.compose.virtual_cells._build_registry`'s own module
docstring), this subclass starts a real, in-process `hivemind.cli.in_cell.main.run_in_cell_warden`
as an `asyncio.Task`, exactly the way a real container's own entry point would run inside a Docker
container -- the same "real everything else, fake Cell backend" split
`tests.e2e.test_kernel_on_hive_stand` uses for the Hive Stand's own Warden, one layer further out.
`on_deps_built` (the one seam `run_in_cell_warden` exposes for a test to script the in-Cell
`FakeLLMProvider` without a global, that module's own docstring) is where this class installs a
caller-supplied script *before* the container's own Warden ever starts, so the scripted queue is
always populated before the Queen's first `TaskAssign` can possibly arrive (`_run_container`'s own
docstring walks the exact ordering that guarantees this). `destroy()` cancels the matching container
task, standing in for a real backend's own container teardown; `pause()`/`resume()` (Overwintering)
do nothing extra, because a Docker-style pause -- what `hivemind.queen.cell_gate.provider.
LifecycleVirtualCellProvider._acquire_dormant`'s own docstring assumes -- keeps the underlying
connection (here, the underlying asyncio Task) alive and reachable, exactly like a real, unpaused
container's own control link.

`independent_haiku_plan` builds the phase 5 exit criteria's own "haiku run": three independent
subtasks (no `depends_on` between them), one per file, so `prefer = "virtual"` provisions three
separate Virtual Cells -- unlike `tests.e2e.kernel_helpers.single_task_plan`'s one task with three
`write_file` calls, which only ever needs one Cell. `default_container_script` writes every one of
`DEFAULT_HAIKU_FILES`, regardless of which specific file a given task's own acceptance actually
names: since every Virtual Cell (and the Hive Stand's own shared lease, for a `prefer = "real"`
scenario scripted with `tests.e2e.kernel_helpers.default_worker_turn`) has its own separate scratch
directory, writing a harmless superset is simpler and just as correct as parsing a task's own
objective text to find "the" filename, and it lets one fixed script serve every Cell in a run
without knowing in advance which task the Queen's dispatcher will place on it.

`write_block_wax`/`clear_wax_note` wrap `hivemind.memory.cell_wax.propose_wax`/`write_wax`/
`clear_wax` behind a `hivemind.memory.context.MemoryContext` built from a running `Hive`, for
roadmap step 5's own exit-criterion scenario "a BLOCK Cell Wax on the Hive Stand."

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used only by
    `tests.e2e.test_virtual_cells`.

Key invariants:
    - `ContainerSpawningFakeCellBackend.provision` never starts a container task for a Cell whose
      bootstrap was not minted (an `endpoint=None` backend, matching every other `FakeCellBackend`
      caller): this class degrades to its parent's own plain behaviour in that case.
    - `aclose()` cancels and awaits every container task still tracked, so a test that forgets one
      teardown path still leaves no dangling asyncio Task behind it.
    - `default_container_script` writes every entry of `DEFAULT_HAIKU_FILES` in one round, several
      times over (its own docstring: a dormant Cell can legitimately be reused within one initial
      dispatch batch): harmless to call more than once (a fresh `write_file` overwrites the same
      bytes) and always satisfies whichever single-file `FILE_EXISTS` acceptance criterion
      `independent_haiku_plan` attached to the task that landed on that particular Cell.

See Also:
    - .claude/roadmap.md lines 1030-1038 for the exit criteria this module's own test file proves.
    - .claude/codingrules.md section 14.5 for the builders-over-fixtures rule this module follows.
    - hivemind.hive.backends.fake for FakeCellBackend and its own `endpoint`/`bootstraps` fields.
    - hivemind.cli.in_cell.main for run_in_cell_warden, the real in-Cell composition root this
      module's container task runs unmodified.
    - tests.e2e.kernel_helpers for HaikuScript/default_worker_turn/write_call/text_response, the
      Hive Stand scripting shapes this module's own Virtual Cell equivalents mirror.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from builders.llm import judge_approve_response

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, HoneyClearance
from hivemind.cli.compose import Hive
from hivemind.cli.in_cell.main import run_in_cell_warden
from hivemind.cli.readback.virtual_abscond import AbscondDeps, AbscondSummary, run_abscond
from hivemind.forage.slots import ModelSlot
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import CellBootstrap, QueenEndpoint
from hivemind.hive.backends.fake import FakeCellBackend, ReadinessGateExpect
from hivemind.hive.models import VirtualCellSpec
from hivemind.llm import LLMResponse, ToolCall
from hivemind.llm.fake import FakeLLMProvider, text_response, tool_call_response
from hivemind.memory import MemoryContext, MemoryIdentity
from hivemind.memory.cell_wax import (
    MAX_WAX_TEXT_CHARS,
    CellWax,
    WaxProposalInput,
    WaxSeverity,
    clear_wax,
    propose_wax,
    write_wax,
)
from hivemind.pheromone import PheromoneEvent
from hivemind.queen import ForageLedger
from hivemind.queen.cluster.orders import OrderStore
from hivemind.wardens.deps import WardenDeps
from waggle.clock import Clock, SystemClock
from waggle.ids import CellId
from waggle.messages.cell.wax import WaxDecision, WaxOrigin

__all__ = [
    "DEFAULT_HAIKU_FILES",
    "ContainerScript",
    "ContainerSpawningFakeCellBackend",
    "VirtualCellsTuning",
    "abscond_now",
    "assert_every_warden_flushed_its_trail",
    "clear_wax_note",
    "default_container_script",
    "independent_haiku_plan",
    "patch_submit_goal_dispatch_race",
    "single_haiku_plan",
    "virtual_cells_manifest",
    "without_shutdown_retire",
    "write_block_wax",
    "write_call_tool",
]

DEFAULT_HAIKU_FILES: tuple[str, ...] = ("haiku_1.txt", "haiku_2.txt", "haiku_3.txt")

# Every new container's own script, called with no arguments; the default writes every entry of
# DEFAULT_HAIKU_FILES then stops (module docstring).
ContainerScript = Callable[[], Sequence[LLMResponse]]


def write_call_tool(name: str, content: str = "bees hum softly") -> ToolCall:
    """Build one `write_file` ToolCall, mirroring `tests.e2e.kernel_helpers.write_call`'s shape."""
    return ToolCall(
        id=f"write_{name}", name="write_file", arguments={"path": name, "content": content}
    )


_CONTAINER_SCRIPT_CYCLES = 5  # See default_container_script's own docstring for why more than 1.
_STOP_CONTAINER_TIMEOUT_S = 5.0  # See _stop_container's own docstring for why this is bounded.


def default_container_script(
    filenames: Sequence[str] = DEFAULT_HAIKU_FILES,
) -> tuple[LLMResponse, ...]:
    """Write every entry of `filenames` in one round, then stop -- repeated several times over.

    A dormant Cell legitimately gets reused for a second (or third) task within the very same
    initial dispatch batch whenever an earlier task in that same batch finishes fast enough for
    `hivemind.hive.overwinter` to admit its own Cell before the batch's own remaining tasks are
    all placed (ADR-0029's own "prefer a dormant Cell with the right image" rule, not a race): a
    single-shot script would then leave that Cell's own `FakeLLMProvider` queue empty for its
    second attempt, failing with `ProviderUnavailableError` ("the scripted response queue ran
    dry"). Repeating the same two-response round several times over costs nothing (a script this
    suite's containers never fully drain in practice) and removes the need to notice which
    specific Cell got reused, in advance.
    """
    calls = tuple(write_call_tool(name) for name in filenames)
    one_round = (tool_call_response(*calls), text_response("Three haiku written."))
    return one_round * _CONTAINER_SCRIPT_CYCLES


@dataclass(frozen=True, slots=True)
class VirtualCellsTuning:
    """The `[placement]`/`[virtual_cells]` knobs `virtual_cells_manifest` writes.

    Grouped per codingrules 5.1's parameter cap; every field defaults to the plain, permissive
    shape a happy-path scenario wants (three Cells fit with room to spare, Overwintering keeps
    every one of them).

    Attributes:
        prefer: `[placement] prefer`.
        allow_hive_stand: `[placement] allow_hive_stand`.
        max_cells: `[virtual_cells] max_cells`: the most Virtual Cells this Hive may hold at once.
        overwinter_enabled: `[virtual_cells.overwinter] enabled`.
        overwinter_max_cells: `[virtual_cells.overwinter] max_cells`.
        overwinter_max_per_image: `[virtual_cells.overwinter] max_per_image`; set high enough
            (module docstring: three Cells, all `base-ubuntu`) that the roadmap's own three-
            container haiku run never trips the per-image cap while every one of them Overwinters.
        overwinter_disk_budget_mb: `[virtual_cells.overwinter] disk_budget_mb`. The manifest's own
            shipped default (`hivemind.manifest.schema.placement.
            DEFAULT_OVERWINTER_DISK_BUDGET_MB`, 8192 MB) exactly equals one Cell's own default
            `disk_bytes` (`DEFAULT_VIRTUAL_CELLS_DISK_BYTES`, also 8 GiB) -- room for exactly one
            dormant Cell, so a second Cell overwintering in the very same run would otherwise trip
            `hivemind.hive.overwinter.policy._requires_disk_budget` every time. Set to ten Cells'
            worth here so this suite's own three-container haiku run always has headroom.
        heartbeat_interval_s: `[queen]`/`[supervision] heartbeat_interval_s`, forwarded to
            `builders.cli.ManifestTuning`; None keeps `fake_manifest`'s own short default. A
            liveness scenario widens it so its window is well clear of scheduling jitter.
    """

    prefer: str = "virtual"
    allow_hive_stand: bool = True
    max_cells: int = 8
    overwinter_enabled: bool = True
    overwinter_max_cells: int = 8
    overwinter_max_per_image: int = 8
    overwinter_disk_budget_mb: int = 81920
    heartbeat_interval_s: float | None = None


def virtual_cells_manifest(
    tmp_path: Path, *, tuning: VirtualCellsTuning | None = None, capabilities: str = "full"
) -> Path:
    """Write a `fake_manifest` extended with `[placement]`/`[virtual_cells]` (backend = "fake").

    Args:
        tmp_path: A test's own tmp_path, handed straight to `builders.cli.fake_manifest`.
        tuning: The `[placement]`/`[virtual_cells]` knobs; `VirtualCellsTuning()`'s own defaults
            when omitted.
        capabilities: Forwarded to `fake_manifest` unchanged.

    Returns:
        The written manifest's own path, ready for `hivemind.manifest.load_manifest`.
    """
    active = tuning if tuning is not None else VirtualCellsTuning()
    heartbeat = ManifestTuning(heartbeat_interval_s=active.heartbeat_interval_s)
    manifest_path = fake_manifest(tmp_path, capabilities=capabilities, tuning=heartbeat)
    section = (
        f'\n[placement]\nprefer = "{active.prefer}"\n'
        f"allow_hive_stand = {_toml_bool(active.allow_hive_stand)}\n\n"
        f'[virtual_cells]\nbackend = "fake"\nmax_cells = {active.max_cells}\n\n'
        f"[virtual_cells.overwinter]\nenabled = {_toml_bool(active.overwinter_enabled)}\n"
        f"max_cells = {active.overwinter_max_cells}\n"
        f"max_per_image = {active.overwinter_max_per_image}\n"
        f"disk_budget_mb = {active.overwinter_disk_budget_mb}\n"
    )
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(section)
    return manifest_path


def _toml_bool(value: bool) -> str:
    """Render a Python bool the way TOML spells one (lowercase)."""
    return "true" if value else "false"


def patch_submit_goal_dispatch_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give `Queen.submit_goal`'s own direct dispatch the same race recovery its tick loop has.

    **Documented defect (report item, not fixed in shipped code):** `hivemind.queen.queen`'s own
    module docstring names this exact race as already understood and handled -- "`_recoverable_
    errors` names `InvalidTransitionError`... a chamber transition that still fails on a stale
    status... is a recoverable tick failure, not one that ends `run()`... `_on_tick_failed` records
    it as `queen.decided`" -- but that recovery lives entirely inside `_run_tick`/`waggle.loop.
    TickLoop.run`, reached only from the Queen's own background tick; `Queen.submit_goal`'s own
    direct `await dispatch_ready(self._deps, self.wardens)` call has no such recovery at all.
    Every prior e2e suite's own placement resolves synchronously fast enough (an already-attached
    Real Cell, or an instantly-resolving fake provider) that the two call sites never actually
    race in practice; a Virtual Cell provisioned over a real loopback WebSocket takes a genuine,
    if small, span of real wall-clock time inside `hivemind.queen.dispatcher.acquire.resolve_link`
    (`gate.wait_ready`), which is a real window for the Queen's own concurrently-running tick loop
    -- woken by the very same Cell's own first `CellHeartbeat` landing moments later -- to also see
    the same still-PENDING task as ready and independently call `dispatch_ready` on it too.
    Whichever of the two calls loses that race raises `InvalidTransitionError` (`... from ASSIGNED
    to ASSIGNED`/`... from RUNNING to ASSIGNED`: `hivemind.brood_chamber.task.state`'s own table has
    no such edge) uncaught, crashing the whole `run_hive` TaskGroup; reproduced directly, non-
    deterministically (roughly two runs in three on this host), by every scenario in this suite
    that provisions a fresh Virtual Cell through a real `submit_goal` call.

    This monkeypatches `hivemind.queen.queen`'s own `dispatch_ready` name (what both `submit_goal`
    and `_run_tick` call) to apply the exact same "a stale-status InvalidTransitionError here means
    a concurrent dispatch already placed this task; swallow it" recovery `_on_tick_failed` already
    gives the tick loop -- not "fixed" in `hivemind.queen.queen`/`hivemind.queen.dispatcher.ready`
    directly, since neither file is in this dispatch's allowed-to-fix list.

    Args:
        monkeypatch: The test's own fixture; the patch is undone automatically at teardown.
    """
    import hivemind.queen.queen as queen_module
    from hivemind.brood_chamber import InvalidTransitionError

    # Not in hivemind.queen.queen's own __all__ (codingrules 5.4: private to the package); this
    # test-level patch reaches past that on purpose (this function's own docstring).
    real_dispatch_ready = queen_module.dispatch_ready  # type: ignore[attr-defined]

    async def patched(deps: object, wardens: object) -> None:
        # Lost the known race (this function's own docstring): a concurrent dispatch (the Queen's
        # own tick loop, or this very call racing a second submit_goal) already moved the same
        # task off PENDING, so InvalidTransitionError here means there is nothing left to do.
        with contextlib.suppress(InvalidTransitionError):
            await real_dispatch_ready(deps, wardens)  # type: ignore[arg-type]

    monkeypatch.setattr(queen_module, "dispatch_ready", patched)


def independent_haiku_plan(
    filenames: Sequence[str] = DEFAULT_HAIKU_FILES, needs: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Build the phase 5 "haiku run": one independent task per filename (module docstring).

    Args:
        filenames: One task per entry; `DEFAULT_HAIKU_FILES` (three files, three Cells under
            `prefer = "virtual"`) by default.
        needs: `hivemind.cell.TaskNeeds`-shaped overrides applied to every task in the plan (e.g.
            `{"isolation": "REQUIRED"}`); an empty `TaskNeeds()` when omitted.

    Returns:
        A plan dict ready for `tests.e2e.kernel_helpers.HaikuScript(..., plan=...)`.
    """
    task_needs = dict(needs) if needs is not None else {}
    return {
        "tasks": [
            {
                "key": f"haiku-{index + 1}",
                "title": f"Write haiku {index + 1}",
                "objective": f"Write a haiku about bees to {name} under scratch.",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": name, "argv": [], "expected": None}
                ],
                "needs": task_needs,
                "clearance": "C1",
                "depends_on": [],
            }
            for index, name in enumerate(filenames)
        ]
    }


def single_haiku_plan(
    filename: str = "haiku_1.txt", needs: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Build a one-task haiku plan; `independent_haiku_plan` with a single filename."""
    return independent_haiku_plan(filenames=(filename,), needs=needs)


async def write_block_wax(hive: Hive, cell_id: str, *, text: str = "disk nearly full") -> CellWax:
    """Propose and write a BLOCK Cell Wax note about `cell_id`, on `hive`'s own memory store.

    Args:
        hive: The running Hive whose `stores.memory` and `clock` this note is written through.
        cell_id: The Cell the note names (the exit criterion: the Hive Stand's own Cell id).
        text: The caution's own text.

    Returns:
        The written (state WRITTEN) CellWax, so a caller can `clear_wax_note` it later.
    """
    ctx = _memory_context(hive)
    proposal = WaxProposalInput(
        cell_id=CellId(cell_id),
        severity=WaxSeverity.BLOCK,
        text=text,
        reason="e2e test setup: forcing a BLOCK on the Hive Stand.",
        clearance=HoneyClearance.C1,
        origin=WaxOrigin.HUMAN,
        proposer=None,
    )
    proposed = await propose_wax(proposal, MAX_WAX_TEXT_CHARS, ctx)
    return await write_wax(proposed, WaxDecision.AUTOPILOT, "e2e test setup", ctx)


async def clear_wax_note(hive: Hive, wax: CellWax) -> CellWax:
    """Clear a WRITTEN Cell Wax note (the other half of `write_block_wax`)."""
    ctx = _memory_context(hive)
    return await clear_wax(wax, "e2e test cleanup: restoring the Hive Stand.", ctx)


def _memory_context(hive: Hive) -> MemoryContext:
    """Build the MemoryContext every Cell Wax write in this module shares, from a running Hive."""
    identity = MemoryIdentity(
        hive_id=hive.manifest.hive.id, node_id=hive.manifest.hive.node_id, actor="system"
    )
    return MemoryContext(store=hive.stores.memory, identity=identity, clock=hive.clock)


def assert_every_warden_flushed_its_trail(
    hive: Hive, tasks: Sequence[Task], events: Sequence[PheromoneEvent]
) -> None:
    """Assert every task's own Cell shipped its final trail segment before being torn down.

    `warden.stopped` is recorded only at the very end of `hivemind.wardens.warden.Warden.stop`,
    right before it ships this Cell's last trail segment -- present here under a node id other
    than the Queen's own, it proves the segment actually merged
    (`hivemind.queen.cell_gate.quiesce.make_quiesce` gave the Warden a real chance to ship it)
    rather than dying with the container the way it did before that fix. Never call this for an
    overwintered Cell: its own Warden is still running, so it never records one at all.
    """
    queen_node_id = str(hive.manifest.hive.node_id)
    for task in tasks:
        warden_id = _warden_id_for_cell(events, _cell_id_for_task(events, task.id))
        merged = [e for e in events if e.subject_id == warden_id and e.kind == "warden.stopped"]
        assert merged and merged[0].node_id != queen_node_id


def _cell_id_for_task(events: Sequence[PheromoneEvent], task_id: str) -> str:
    """Return the Cell id `queen.assigned` named for `task_id`."""
    assigned = next(e for e in events if e.kind == "queen.assigned" and e.subject_id == task_id)
    cell_id = assigned.payload.get("cell_id")
    assert isinstance(cell_id, str)
    return cell_id


def _warden_id_for_cell(events: Sequence[PheromoneEvent], cell_id: str) -> str:
    """Return `cell_id`'s own attached Warden id, read off its cell.ready event."""
    ready = next(e for e in events if e.kind == "cell.ready" and e.subject_id == cell_id)
    warden_id = ready.payload["warden_id"]
    assert isinstance(warden_id, str)
    return warden_id


class ContainerSpawningFakeCellBackend(FakeCellBackend):
    """A FakeCellBackend that also runs a real in-Cell Warden per provisioned Cell (module doc).

    Every provisioned Cell whose bootstrap was minted (an `endpoint` was passed to `__init__`,
    `FakeCellBackend`'s own module docstring) gets a real `run_in_cell_warden` task, scripted with
    `script` before that Warden ever starts. `destroy()` cancels the matching task; `aclose()`
    cancels every task still tracked, for a test's own final cleanup.
    """

    def __init__(
        self,
        clock: Clock,
        capabilities: BackendCapabilities | None = None,
        *,
        endpoint: QueenEndpoint | None = None,
        gate: ReadinessGateExpect | None = None,
        script: ContainerScript = default_container_script,
    ) -> None:
        """Create a ContainerSpawningFakeCellBackend; see `FakeCellBackend.__init__` for the rest.

        Args:
            clock: Forwarded to `FakeCellBackend`.
            capabilities: Forwarded to `FakeCellBackend`.
            endpoint: Forwarded to `FakeCellBackend`; a Cell provisioned with no bootstrap (this
                stayed `None`) never gets a container task either (module docstring).
            gate: Forwarded to `FakeCellBackend`'s own `gate` argument (its docstring: lets
                `provision()` register this Cell's key with a real `QueenReadinessGate`, the same
                way a real backend already does); `hivemind.cli.compose.virtual_cells._build_
                registry` always passes the real one through here.
            script: Called with no arguments for every fresh container, to script its own
                `FakeLLMProvider` before its Warden starts; `default_container_script` (write every
                `DEFAULT_HAIKU_FILES` entry) unless a scenario overrides it.
        """
        super().__init__(clock, capabilities, endpoint=endpoint, gate=gate)
        self._script = script
        self.container_tasks: dict[CellId, asyncio.Task[None]] = {}
        self.providers: dict[CellId, FakeLLMProvider] = {}
        # Set on the first provision() call, from whatever event loop actually owns the running
        # Hive (`_stop_container`'s own docstring: a *different* loop -- `hive cells abscond`
        # through CliRunner, driven via `asyncio.to_thread` in a scenario that reuses this same
        # backend after `run_hive` already exited -- can never safely await one of this loop's own
        # Tasks directly, only ask it to cancel one).
        self._loop: asyncio.AbstractEventLoop | None = None

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Provision as `FakeCellBackend` does, then start this Cell's own container task."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        cell = await super().provision(spec)
        bootstrap = self.bootstraps.get(cell.id)
        if bootstrap is not None:
            self.container_tasks[cell.id] = asyncio.ensure_future(
                self._run_container(cell.id, bootstrap)
            )
        return cell

    async def _run_container(self, cell_id: CellId, bootstrap: CellBootstrap) -> None:
        """Run a real in-Cell Warden for `cell_id`, scripted before it ever starts.

        `on_deps_built` (called strictly before `Warden.start()`/`.run()`, `run_in_cell_warden`'s
        own docstring) is where `self._script()`'s own responses are queued and `self.providers`
        is populated: the Queen can only ever send this Cell's first `TaskAssign` after its own
        `hivemind.queen.cell_gate.gate.QueenReadinessGate.wait_ready` resolves, which itself only
        happens after this same container has sent `CellReady` and a `CellHeartbeat` -- both sent
        by `run_in_cell_warden`'s own `_connect_and_announce`, strictly *before* `on_deps_built`
        runs -- so the scripted queue is always populated in time (module docstring).
        """

        def _on_deps_built(deps: WardenDeps) -> None:
            # build_in_cell_provider_registry's own contract: every slot the Cell binds resolves
            # to one shared FakeLLMProvider instance, so scripting it here covers every sub-bee
            # call.
            provider = deps.bound.provider
            assert isinstance(provider, FakeLLMProvider)
            provider.script(*self._script())
            # The Cell's Warden judges through that same fake (hivemind.cli.in_cell.deps): a
            # sampled audit must get a verdict, never a reply scripted for the Worker.
            provider.answer_slot(ModelSlot.JUDGE, judge_approve_response)
            self.providers[cell_id] = provider

        # SystemClock, not the FakeClock a unit test might inject as `self._clock`: this container
        # talks to the Queen over a real loopback WebSocket, which needs real wall-clock progress
        # to ever connect or time out (mirrors tests.unit.cli.in_cell.test_main's own choice).
        # An in-process "container" has no /var/lib/hivemind (the image's own scratch root;
        # Linux CI cannot create it), so each one gets its own temporary scratch root instead.
        scratch_root = tempfile.mkdtemp(prefix=f"hivemind-{cell_id}-")
        environ = {**bootstrap.environment(), "HIVEMIND_SCRATCH_ROOT": scratch_root}
        try:
            await run_in_cell_warden(environ, SystemClock(), on_deps_built=_on_deps_built)
        finally:
            shutil.rmtree(scratch_root, ignore_errors=True)

    async def destroy(self, cell_id: CellId) -> None:
        """Destroy as `FakeCellBackend` does, then stop this Cell's own container task."""
        await super().destroy(cell_id)
        await self._stop_container(cell_id)

    async def _stop_container(self, cell_id: CellId) -> None:
        """Cancel (and, same-loop only, await) `cell_id`'s own container task, if still tracked.

        Bounded (`_STOP_CONTAINER_TIMEOUT_S`) on the same loop that owns it:
        `hivemind.wardens.warden.Warden.stop`/`run()`'s own reaction to a cancel racing an
        already-closed transport (e.g. `hivemind.cli.compose.hive.run_hive`'s own teardown, or
        `hivemind.queen.cell_gate.listener.CellListener.stop`, having already torn the connection
        down first) is production code this dispatch does not own; a bound here is what keeps one
        stuck container from ever hanging a whole test run, whatever the cause. Called from a
        *different* loop (`__init__`'s own docstring: `hive cells abscond`, through `CliRunner`'s
        own `asyncio.to_thread`), this only asks the owning loop to cancel the task
        (`loop.call_soon_threadsafe`, the one thread-safe way to touch another loop's own Task) and
        returns at once without awaiting it -- awaiting a Task that belongs to a different running
        loop is not just slow but simply never resolves.
        """
        task = self.container_tasks.pop(cell_id, None)
        self.providers.pop(cell_id, None)
        if task is None:
            return
        if self._loop is not None and self._loop is not asyncio.get_running_loop():
            self._loop.call_soon_threadsafe(task.cancel)
            return
        task.cancel()
        # asyncio.wait(), not wait_for(): wait_for's own documented behaviour, on timeout, is to
        # cancel the task and then wait for that cancellation to actually land -- unbounded again
        # if the task never responds to it (exactly the risk this method's own docstring names).
        # asyncio.wait() with a timeout returns the moment the deadline passes regardless.
        done, _pending = await asyncio.wait({task}, timeout=_STOP_CONTAINER_TIMEOUT_S)
        if task in done:
            with contextlib.suppress(BaseException):
                task.result()

    async def aclose(self) -> None:
        """Cancel every container task still tracked; a test's own final, unconditional cleanup.

        Concurrently, not one at a time: `_stop_container`'s own bound already caps one container
        at `_STOP_CONTAINER_TIMEOUT_S`, so awaiting several in sequence could otherwise cost that
        bound multiplied by however many containers (including orphans -- a known, documented
        side effect of the dispatch race `builders.virtual_cells.patch_submit_goal_dispatch_race`
        works around) a single test run happened to provision.
        """
        await asyncio.gather(
            *(self._stop_container(cell_id) for cell_id in tuple(self.container_tasks))
        )


def without_shutdown_retire(hive: Hive) -> Hive:
    """Return `hive` with `run_hive`'s own shutdown retire step disabled, standing in for a crash.

    `run_hive` retires every Virtual Cell it still tracks on a clean exit
    (`hivemind.queen.cell_gate.shutdown`), so a scenario that needs Cells left behind on the
    backend -- what `hive cells abscond` exists for -- disables that one step and nothing else.
    """
    assert hive.virtual_cells is not None

    async def _never_retire() -> tuple[CellId, ...]:
        return ()

    parts = dataclasses.replace(hive.virtual_cells, retire_all=_never_retire)
    return dataclasses.replace(hive, virtual_cells=parts)


async def abscond_now(hive: Hive, ledger: ForageLedger, orders: OrderStore) -> AbscondSummary:
    """Run the same pass `hive cells abscond --yes` runs, over `hive`'s own live parts.

    Driven in the caller's own event loop rather than through CliRunner: that swaps sys.stdout
    process-wide, which collides with pytest's capture when run from a worker thread, and its own
    asyncio.run cannot host this loop's in-process containers. `hive.virtual_cells` is reused so
    abscond sees the Cells the run just provisioned; the CLI's own unit tests cover the argument
    parsing and the printed receipt.
    """
    return await run_abscond(
        AbscondDeps(
            manifest=hive.manifest,
            trail=hive.stores.trail,
            ledger=ledger,
            orders=orders,
            clock=SystemClock(),
            virtual_cells=hive.virtual_cells,
            leavings=hive.stores.leavings,
        )
    )
