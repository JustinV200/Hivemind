"""Build a running device Warden with an outage over fakes, and drive its link by hand (11.9).

`start_offline_scene` starts a real `Warden` on a Swarm device's deps (`DeviceDeps` with an
`Outage`, and, for a Warden that dials the Queen from the device, a `RemoteDeps` over a link the
test flips), gives it one task, and waits until its sub-bee runs. The sub-bee runs `pausable`, a
role that works until it is told to hand off, then writes a Handoff and stops, and, started again
from that Handoff, records what it resumed from and works on: exactly the two halves of a pause
and a resume. `SwitchLink` stands in for the reconnecting link as the Warden reads it
(`is_online`), and the scene's `cut` and `restore` flip it and wake the Warden's tick as the real
link's watcher does. Everything waits on state, never on a timer.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the Warden-level tests
    under packages/hivemind/tests/unit/wardens/offline.

Key invariants:
    - The Warden's own Queen link stays the in-memory pair of `builders.wardens`; only the state
      the Warden reads (`SwitchLink.is_online`) is switched, which is all `reconcile` looks at.
    - Time moves only when a test advances the shared FakeClock.

See Also:
    - hivemind.wardens.offline for the freeze and the reconcile these scenes exercise.
    - builders.wardens for the Queen's end and the deps this builds on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from builders.memory import make_handoff
from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import RunScript, ScriptedWorker, make_assignment, make_outcome

from hivemind.memory import Handoff
from hivemind.wardens import DeviceDeps, WardenDeps
from hivemind.wardens.offline import Outage, RemoteDeps
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.ids import TaskId
from waggle.messages.task import TaskAssign

WORKER_BEAT_S = 1.0  # Each sub-bee's own heartbeat cadence.
WARDEN_BEAT_S = 10.0  # The Warden's own: the tick that follows a quiet return rides this one.
_HUNG_S = 1_000_000.0  # A role that works on, and never finishes by itself within a test.
_WINDOW = 1_000  # The context window a bee reports against.
_FULL = 950  # Tokens used: past the 0.66 handoff threshold the test deps carry.
_STOP_TIMEOUT_S = 5.0  # A hung stop fails the test instead of blocking the suite.

__all__ = [
    "WARDEN_BEAT_S",
    "WORKER_BEAT_S",
    "OfflineScene",
    "SwitchLink",
    "pausable",
    "start_offline_scene",
]


class SwitchLink:
    """The reconnecting link as the Warden reads it: whether it is online, which a test flips."""

    def __init__(self) -> None:
        """Start online, as a link that has connected."""
        self.is_online = True


def pausable(resumed: list[Handoff], *, full_context: bool = False) -> RunScript:
    """A role that works until told to hand off, then stops at a Handoff; resumed, it carries on.

    Args:
        resumed: Appended with the Handoff each bee of the task starts from, so a test reads what
            the task was resumed from.
        full_context: Report a context past the handoff threshold, so the Warden's own context
            check would order a handoff of this bee if nothing stopped it.

    Returns:
        The script for a `ScriptedWorker`.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        if resume_from is not None:
            resumed.append(resume_from)
            await ctx.clock.sleep(_HUNG_S)  # The resumed bee carries on with the work.
        if full_context:
            ctx.telemetry.record_tokens(_FULL, _WINDOW)
        # ASYNC110: the runtime sets a plain bool flag on the lever; no Event exists to wait on.
        while not ctx.telemetry.handoff_requested:  # noqa: ASYNC110
            await asyncio.sleep(0)
        return make_outcome(claimed=False, handoff=make_handoff(written_by="the first bee"))

    return script


@dataclass
class OfflineScene:
    """A running device Warden, the Queen's end of its link, and the outage it carries."""

    warden: Warden
    queen: QueenEnd
    clock: FakeClock
    run_task: asyncio.Task[None]
    deps: WardenDeps
    outage: Outage
    link: SwitchLink
    assignment: TaskAssign

    def rows(self, task_id: TaskId | None = None) -> list[SubBee]:
        """Every sub-bee row the Warden holds for the scene's task (or `task_id`)."""
        wanted = task_id if task_id is not None else self.assignment.task_id
        return [row for row in self.warden.sub_bees if row.task_id == wanted]

    def cut(self) -> None:
        """Take the link down and wake the tick, as the reconnecting link's watcher does."""
        self.link.is_online = False
        self.outage.nudge()

    def restore(self) -> None:
        """Bring the link back and wake the tick."""
        self.link.is_online = True
        self.outage.nudge()

    async def wait_paused(self, bee: SubBee | None = None) -> SubBee:
        """Wait until the bee has obeyed a pause: Handoff written, stopped, its row retired.

        Args:
            bee: The bee to wait for, when the test took it earlier; the task's one row otherwise.

        Returns:
            The bee that was paused, for its last Handoff.
        """
        if bee is None:
            [bee] = self.rows()
        await settle(lambda: bee.runtime.state is WorkerState.DONE and bee.last_handoff is not None)
        self.clock.advance(WORKER_BEAT_S)  # Its DONE Heartbeat: the report that it has ended.
        await settle(lambda: self.rows() == [])
        return bee

    async def stop(self) -> None:
        """Stop the Warden and wait for its loop to end, bounded."""
        await self.warden.stop()
        await asyncio.wait_for(self.run_task, timeout=_STOP_TIMEOUT_S)


async def start_offline_scene(
    script: RunScript,
    *,
    remote: bool = True,
    max_offline_s: float = 600.0,
    assign: bool = True,
    **overrides: object,
) -> OfflineScene:
    """Start a device Warden with an outage, give it one task and wait until its bee runs.

    Args:
        script: What the sub-bee does (normally `pausable`).
        assign: Give the Warden its task; False leaves it idle (the scene's `assignment` unsent).
        remote: Whether the Warden dials the Queen from the device (Level 1), with a link the
            test flips; False builds a Level 0 device Warden, whose Cell the test loses and finds.
        max_offline_s: The limit past which a remote Warden clusters fully.
        **overrides: Field values for `make_warden_deps` (a `local_providers`, say).

    Returns:
        The running scene.
    """
    clock = FakeClock()
    outage, link = Outage(), SwitchLink()
    remote_deps = RemoteDeps(link, max_offline_s) if remote else None
    deps, queen, warden_id = make_warden_deps(
        clock,
        worker_factory=lambda role: ScriptedWorker(script, role=role),
        heartbeat_interval_s=WARDEN_BEAT_S,
        worker_heartbeat_interval_s=WORKER_BEAT_S,
        device=DeviceDeps(outage=outage, remote=remote_deps),
        **overrides,  # type: ignore[arg-type]  # test-only overrides
    )
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    assignment = make_assignment(clock=clock)
    scene = OfflineScene(warden, queen, clock, run_task, deps, outage, link, assignment)
    if not assign:
        return scene
    await queen.send(make_grant_issued(clock, assignment))
    await queen.send(assignment)
    await settle(lambda: scene.rows() != [])
    [bee] = scene.rows()
    await settle(lambda: bee.runtime.state is WorkerState.RUNNING)
    return scene
