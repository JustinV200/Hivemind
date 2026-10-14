"""Build a Nuc over fakes and the scripts for its diagnostic Drone (roadmap 11.9a).

`start_nuc_scene` starts a real `Warden` on a Swarm device's deps with a link the test flips
(`builders.offline_warden`), and makes it a Nuc: it holds ceilings from the Queen and a model
server that serves one source, which the Warden's default `[llm.slots]` row for its `worker` slot
names, and its provider counts as local. Its Drone is a `ScriptedWorker`, so a test says what the
Drone does as a script: `says` ends at once with a closing text, `hangs` works until it is
stopped, `raises` fails, and `in_turn` gives each Drone of a search its own.
`start_thinking_nuc_scene` runs the real Drone role instead, on a `FakeLLMProvider` the test
scripts, for what only the real role can show: what its model is offered, told and refused. The
helpers read what a search left: the `warden.diagnosed` events on the Warden's trail, the Drone
rows the Warden holds, and the Nectar that reached the Queen's end, whole.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/wardens/offline/diagnose and `.../spawn`.

Key invariants:
    - The server is a stand-in with the one thing a search asks of it: it is serving a source. No
      process runs and no port opens.
    - Time moves only when a test advances the scene's FakeClock; every wait is on state.

See Also:
    - hivemind.wardens.offline.diagnose for the search these scenes exercise.
    - builders.offline_warden for the scene this builds on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

from builders.forage import make_source
from builders.hosting import make_server_config
from builders.offline_warden import OfflineScene, Searching, start_offline_scene
from builders.workers import RunScript, make_outcome

from hivemind.cell import BackgroundProcess
from hivemind.forage import Ceilings
from hivemind.llm import FakeLLMProvider
from hivemind.memory import Handoff
from hivemind.pheromone import TrailQuery, WardenEvent
from hivemind.wardens import Warden
from hivemind.wardens.local_pool.hosting import ModelServerHost, RunningServer
from hivemind.wardens.offline import ReconnectBudget
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles import worker_for
from waggle.messages.forage import SourceRemovalCause
from waggle.messages.task import TaskAssign

BUDGET = ReconnectBudget(attempts=2, wall_clock_s=120.0)  # Two Drones, two minutes of the cut.
PEER = "hive.example"  # The Queen's host, as the Drone's objective names it.
PROVIDER = "fake"  # The provider the Warden's default `worker` slot row names.
MODEL = "test-model"  # The model that row names: the one the stand-in server serves.
_CAP = 2  # The Cell's ceiling of sub-bees the Queen set.
_HUNG_S = 1_000_000.0  # A Drone that works on and never finishes by itself within a test.
_WAIT_S = 5.0  # A message that never arrives fails the test instead of hanging the suite.

__all__ = [
    "BUDGET",
    "MODEL",
    "PEER",
    "PROVIDER",
    "StubHost",
    "diagnosed",
    "drones",
    "hangs",
    "in_turn",
    "make_nuc",
    "nectar_text",
    "raises",
    "says",
    "says_when",
    "start_nuc_scene",
    "start_thinking_nuc_scene",
]


class StubHost:
    """Stands in for a Warden's `ModelServerHost`: servers that serve, all a search asks of one."""

    def __init__(self, running: dict[str, RunningServer]) -> None:
        """Hold the servers that are running."""
        self.running = running

    async def sweep(self) -> None:
        """Do nothing: a stand-in server never exits on its own."""

    async def stop_all(self, cause: SourceRemovalCause) -> None:
        """Forget the servers, as the real host does when the Warden stops."""
        del cause
        self.running.clear()


def make_nuc(warden: Warden, cap: int = _CAP) -> None:
    """Make `warden` a Nuc: ceilings from the Queen, and a server serving the `worker` model.

    Args:
        warden: The Warden, holding its lease and not yet running its tick loop.
        cap: The Queen's ceiling of sub-bees on its Cell.
    """
    warden._ceilings = Ceilings(
        max_sub_bees=cap,
        model_vram_bytes=0,
        model_disk_bytes=0,
        loadable_sources=(),
        exportable_seats=0,
    )
    source = make_source(source_id="small", provider=PROVIDER, model=MODEL)
    server = RunningServer(
        config=make_server_config(sources=(source,)),
        process=cast(BackgroundProcess, object()),
        provider=FakeLLMProvider(name=PROVIDER),
        sources=(source,),
    )
    warden._models = cast(ModelServerHost, StubHost({"box": server}))


async def start_nuc_scene(
    script: RunScript,
    *,
    budget: ReconnectBudget = BUDGET,
    assign: bool = False,
    prepare: Callable[[Warden], None] = make_nuc,
) -> OfflineScene:
    """Start a device Warden that searches for why its link went, with `script` as its Drone.

    Args:
        script: What each Drone it sends does.
        budget: What the search may spend.
        assign: Give it a task of the Queen's to run as well; the script runs for that too.
        prepare: Makes the Warden what it is; a Nuc by default. Pass a no-op for a Level 1 Warden,
            which holds no model of its own.

    Returns:
        The running scene, its link up.
    """
    searching = Searching(budget, peer=PEER, prepare=prepare)
    return await start_offline_scene(
        script, assign=assign, searching=searching, local_providers=frozenset({PROVIDER})
    )


async def start_thinking_nuc_scene(
    provider: FakeLLMProvider, *, budget: ReconnectBudget = BUDGET
) -> OfflineScene:
    """Start a Nuc whose Drone is the real role, thinking with the answers scripted on `provider`.

    Args:
        provider: What the Drone's model says; the test scripts it and reads the calls it saw.
        budget: What the search may spend.

    Returns:
        The running scene, its link up and no task of the Queen's given.
    """
    searching = Searching(budget, peer=PEER, prepare=make_nuc, drone=worker_for)
    return await start_offline_scene(
        hangs(),  # The scene's script, which the real role replaces.
        assign=False,
        searching=searching,
        local_providers=frozenset({PROVIDER}),
        fake_provider=provider,
    )


def says(text: str, log: list[TaskAssign] | None = None) -> RunScript:
    """A Drone that ends at once with `text` as its closing message.

    Args:
        text: The closing message; its first line is `CAUSE: ...` by the search's convention.
        log: Appended with the assignment each Drone is given, so a test reads its objective.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        if log is not None:
            log.append(assignment)
        return make_outcome(summary=text, claimed=True)

    return script


def says_when(go: asyncio.Event, text: str) -> RunScript:
    """A Drone that works until `go` is set, then ends with `text` as its closing message."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        await go.wait()
        return make_outcome(summary=text, claimed=True)

    return script


def hangs(log: list[TaskAssign] | None = None) -> RunScript:
    """A Drone that works on, and never finishes by itself."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        if log is not None:
            log.append(assignment)
        await ctx.clock.sleep(_HUNG_S)
        return make_outcome(summary="Never reached.", claimed=True)

    return script


def raises(log: list[TaskAssign] | None = None) -> RunScript:
    """A Drone that crashes."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        if log is not None:
            log.append(assignment)
        raise RuntimeError("The Drone crashed.")

    return script


def in_turn(*scripts: RunScript) -> RunScript:
    """A script that runs the first of `scripts` on its first call, the second on its second."""
    remaining = list(scripts)

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        return await remaining.pop(0)(ctx, assignment, resume_from)

    return script


async def diagnosed(scene: OfflineScene) -> list[WardenEvent]:
    """Every `warden.diagnosed` event on the Warden's trail, in the order it was recorded."""
    events = await scene.deps.trail.query(TrailQuery(kind="warden.diagnosed"))
    return [event for event in events if isinstance(event, WardenEvent)]


def drones(scene: OfflineScene) -> list[SubBee]:
    """Every row of the Warden's table that is a task it minted for itself."""
    return [row for row in scene.warden.sub_bees if row.local is not None]


async def nectar_text(scene: OfflineScene) -> str:
    """Read the Nectar that reaches the Queen's end whole, waiting (bounded) for its last chunk."""
    queen = scene.queen
    await asyncio.wait_for(
        queen.pump_until(lambda: bool(queen.nectar) and queen.nectar[-1].final), timeout=_WAIT_S
    )
    return b"".join(chunk.chunk for chunk in queen.nectar).decode("utf-8")
