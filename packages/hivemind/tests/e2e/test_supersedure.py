"""End to end: two Queens on temp stores, one Supersedure handed over and one rolled back.

Roadmap 13.2a's proof in one process (ADR-0065). The old Hive runs a goal whose Drone waits on a
question on the Hive Stand. The human's order reaches `Queen.supersede` (the point, the preflight,
the freeze, the start), and the rest runs on her loop. The candidate is a second Hive over its own
directory with the same Hive id and key and its own node: the archive is checked and restored
there the way `hive restore` does it, and its Queen comes up REQUEENING and held. A sampler
watches both Queens the whole time and fails if both are ever active. Once handed over, the old
Queen is SUPERSEDED, the candidate places the goal's task afresh, asks again, and finishes it.
In the second test the candidate dies before it is ready; the old Queen rolls back inside the
window, points the devices back at herself, and the goal still finishes on her.

The real exit (supersede onto an enrolled Nuc while a goal runs on the Hive Stand, a container
and the Nuc) needs that hardware; it is the operator's run (docs/runbooks/supersedure.md).

Fits into the Hive:
    Whole-Hive scenario (codingrules 14.2, `e2e`). Exercises hivemind.queen.supersedure through
    the Queen's door, hivemind.cli.compose.hive.supersedure (the start rules and the hold) and
    Requeening's held first half.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/queen/supersedure for each piece over fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.entrance.goals import goal_responder
from builders.entrance.stand import stand_manifest
from builders.supersedure import FakeSender, endpoint
from builders.swarm.parties import make_swarm_node
from e2e.kernel_helpers import wait_until

from hivemind.brood_chamber import AnswerSource, Task, TaskFilter, TaskStatus
from hivemind.cell import AccessLevel, HoneyClearance
from hivemind.cli.backup import BASKET_TREE, backup_source, check_destination, record_restored
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.common.backup import open_backup, restore_backup
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import TrailQuery
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure import (
    ABORTED_KIND,
    STARTED_KIND,
    SUPERSEDED_KIND,
    Endpoint,
    ProbeReport,
    Recorder,
    SupersedureWiring,
    was_ready,
)
from hivemind.swarm import SwarmNode
from waggle.clock import SystemClock
from waggle.ids import DeviceId, MessageId, NodeId, new_node_id
from waggle.messages.swarm import RuntimeLevel

pytestmark = pytest.mark.e2e

_QUESTION = "Which season?"
_GOAL = "write a haiku about bees"
_WAIT_S = 60.0
_VERSION = "1.0.0"
_HUMAN = DeviceId("device_01ARZ3NDEKTSV4RRFFQ69G5FAV")


class _Candidate:
    """The candidate machine: a second Hive restored from the archive, or one that dies first."""

    def __init__(self, manifest: HiveManifest, *, dies: bool) -> None:
        self.manifest, self.dies = manifest, dies
        self.hive: Hive | None = None
        self._archive = manifest.resolve_path(Path("incoming.hive.tgz"))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def send_backup(self, archive: Path, sha256: str) -> None:
        await asyncio.to_thread(shutil.copyfile, archive, self._archive)

    async def restore(self, sha256: str) -> None:
        db = self.manifest.resolve_path(self.manifest.hive.db)
        work = self.manifest.resolve_path(Path("restore-work"))
        opened = await asyncio.to_thread(open_backup, self._archive, work, sha256)
        await check_destination(opened.index, self.manifest)
        trees = {BASKET_TREE: self.manifest.basket_root()}
        written = await asyncio.to_thread(restore_backup, opened, db, trees)
        await asyncio.to_thread(record_restored, self.manifest, opened, written, SystemClock())

    async def start_queen(self) -> None:
        if self.dies:
            return  # Killed before it was ready: its Queen never comes up.
        self.hive = await _build(self.manifest)
        self._task = asyncio.ensure_future(self._serve(self.hive))

    async def is_ready(self, started_id: str) -> bool:
        return self.hive is not None and await was_ready(self.hive.stores.trail, started_id)

    async def acknowledge(self, started_id: str) -> bool:
        assert self.hive is not None
        await _recorder(self.hive).record(
            SUPERSEDED_KIND, self.hive.manifest.hive.id, started_id=started_id, acknowledged=True
        )
        return True

    async def discard(self, started_id: str) -> bool:
        return False  # Dead, or never started: nothing answers.

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _serve(self, hive: Hive) -> None:
        async with run_hive(hive):
            await self._stop.wait()


async def test_a_ready_candidate_takes_the_hive_and_two_queens_never_run(tmp_path: Path) -> None:
    old, candidate, sender = await _setup(tmp_path, dies=False)
    active_both: list[tuple[QueenMode, QueenMode]] = []
    try:
        async with run_hive(old), _sampler(old, candidate, active_both):
            await old.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
            blocked = await _task_in(old, TaskStatus.BLOCKED)
            await old.queen.supersede(candidate.manifest.hive.node_id, _HUMAN)
            await wait_until(lambda: _mode_is(old, QueenMode.SUPERSEDED), timeout_s=_WAIT_S)
            new = await _started(candidate)
            await wait_until(lambda: _mode_is(new, QueenMode.RUNNING), timeout_s=_WAIT_S)
            await _answer_on(new, blocked)
            finished = await _task_in(new, TaskStatus.SUCCEEDED)
    finally:
        await candidate.stop()

    assert active_both == []
    assert finished.id == blocked.id
    for hive in (old, new):
        assert len(await hive.stores.trail.query(TrailQuery(kind=SUPERSEDED_KIND))) == 1
        assert len(await hive.stores.trail.query(TrailQuery(kind=STARTED_KIND))) == 1
    [notice] = sender.notices
    assert notice.new_node_id == candidate.manifest.hive.node_id and not notice.is_rollback


async def test_a_candidate_killed_before_it_is_ready_is_rolled_back_and_the_goal_finishes(
    tmp_path: Path,
) -> None:
    old, candidate, sender = await _setup(tmp_path, dies=True)
    async with run_hive(old):
        await old.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
        blocked = await _task_in(old, TaskStatus.BLOCKED)
        await old.queen.supersede(candidate.manifest.hive.node_id, _HUMAN)
        await wait_until(lambda: _mode_is(old, QueenMode.RUNNING), timeout_s=_WAIT_S)
        await _answer_on(old, blocked)
        finished = await _task_in(old, TaskStatus.SUCCEEDED)

    assert finished.id == blocked.id
    [aborted] = await old.stores.trail.query(TrailQuery(kind=ABORTED_KIND))
    assert aborted.payload["cause"] == "NOT_READY"
    assert await old.stores.trail.query(TrailQuery(kind=SUPERSEDED_KIND)) == ()
    [notice] = sender.notices
    assert notice.is_rollback and notice.new_node_id == old.manifest.hive.node_id


async def _setup(tmp_path: Path, *, dies: bool) -> tuple[Hive, _Candidate, FakeSender]:
    """Build the old Hive, the candidate's directory beside it, and wire the old Queen."""
    manifest = load_manifest(stand_manifest(tmp_path / "old"), {})
    old = await _build(manifest)
    candidate = _Candidate(_candidate_manifest(manifest, tmp_path / "new"), dies=dies)
    sender = FakeSender([])
    source = await backup_source(manifest)
    node = make_swarm_node(
        None,
        None,
        node_id=candidate.manifest.hive.node_id,
        level=RuntimeLevel.COLONIZED,
        access_level=AccessLevel.FULL,
        runtime_version=_VERSION,
    )
    probe = ProbeReport(source.secret_names, source.identity_key, 0.0, reachable=True)

    async def node_for(_node: str) -> SwarmNode | None:
        return node

    async def probe_for(_node: str) -> ProbeReport:
        return probe

    async def link_for(_node: str) -> _Candidate:
        return candidate

    async def endpoint_for(node_id: str) -> Endpoint:
        return endpoint(node_id, 9001)

    old.queen_deps.supersedure.wiring = SupersedureWiring(
        own=endpoint(manifest.hive.node_id, 9000),
        node_for=node_for,
        link_for=link_for,
        probe_for=probe_for,
        sender=sender,
        source=lambda: backup_source(manifest),
        endpoint_for=endpoint_for,
        runtime_version=_VERSION,
        workdir=tmp_path / "work",
        grace_s=8.0,
        poll_s=0.1,
    )
    return old, candidate, sender


def _candidate_manifest(manifest: HiveManifest, root: Path) -> HiveManifest:
    """The same Hive on another node: its manifest with a new node id, and the same secrets."""
    old_root = manifest.resolve_path(Path("."))
    root.mkdir(parents=True)
    text = (old_root / "hive.toml").read_text(encoding="utf-8")
    node: NodeId = new_node_id(SystemClock())
    # Absolute paths in the old manifest name the old machine's disk; the candidate has its own.
    text = text.replace(manifest.hive.node_id, node).replace(old_root.as_posix(), root.as_posix())
    (root / "hive.toml").write_text(text, encoding="utf-8")
    secrets = manifest.resolve_path(manifest.hive.secrets_dir)
    shutil.copytree(secrets, root / secrets.relative_to(old_root))
    return load_manifest(root / "hive.toml", {})


async def _build(manifest: HiveManifest) -> Hive:
    """Build a Hive over `manifest`; its own event loops, so never inside the test's."""
    return await asyncio.to_thread(
        build_hive,
        manifest,
        environ={},
        clock=SystemClock(),
        responders={"fake": goal_responder(_QUESTION)},
    )


def _recorder(hive: Hive) -> Recorder:
    """A Recorder writing on `hive`'s trail as its own node."""
    ids = hive.manifest.hive
    return Recorder(hive.stores.trail, ids.id, ids.node_id, SystemClock())


@contextlib.asynccontextmanager
async def _sampler(
    old: Hive, candidate: _Candidate, both: list[tuple[QueenMode, QueenMode]]
) -> AsyncIterator[None]:
    """Watch both Queens every 10 ms; note any instant where both are active."""

    async def watch() -> None:
        while True:
            new = candidate.hive
            if new is not None and _active(old) and _active(new):
                both.append((_mode(old), _mode(new)))
            await asyncio.sleep(0.01)

    task = asyncio.ensure_future(watch())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _mode(hive: Hive) -> QueenMode:
    return hive.queen_deps.cluster_state.mode


def _active(hive: Hive) -> bool:
    """A Queen that may place work: RUNNING or CLUSTERED, and not held as a candidate."""
    held = hive.queen_deps.requeening.hold is not None
    return _mode(hive) in (QueenMode.RUNNING, QueenMode.CLUSTERED) and not held


async def _mode_is(hive: Hive, mode: QueenMode) -> bool:
    return _mode(hive) is mode


async def _started(candidate: _Candidate) -> Hive:
    async def up() -> bool:
        return candidate.hive is not None

    await wait_until(up, timeout_s=_WAIT_S)
    assert candidate.hive is not None
    return candidate.hive


async def _task_in(hive: Hive, status: TaskStatus) -> Task:
    """Wait until a task reaches `status`, and return it."""
    found: list[Task] = []

    async def reached() -> bool:
        found[:] = [t for t in await hive.stores.chamber.list(TaskFilter()) if t.status is status]
        return bool(found)

    await wait_until(reached, timeout_s=_WAIT_S)
    return found[0]


async def _answer_on(hive: Hive, task: Task) -> None:
    """Answer the newest question `task`'s Drone is waiting on, as the human."""
    pending: list[MessageId] = []

    async def asked() -> bool:
        questions = await hive.stores.chamber.pending_questions(task.id)
        pending[:] = [q.id for q in questions]
        return bool(pending)

    await wait_until(asked, timeout_s=_WAIT_S)
    await hive.queen.answer_question(
        pending[-1],
        "spring",
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
    )
