"""Run two Queens on temp stores for a Supersedure: the old Hive, its candidate, and their reads.

Shared by `tests/e2e/test_supersedure.py` (roadmap 13.2a's in-process proof) and the chaos suite's
Supersedure scenarios (roadmap 13.6). The old Hive runs a goal whose Drone waits on a question on
the Hive Stand. The candidate is a second Hive over its own directory with the same Hive id and key
and its own node: the archive is checked and restored there the way `hive restore` does it, and
its Queen comes up REQUEENING and held. A candidate can die before it is ready (its Queen never
starts) or stall (its restore never returns, so the old Queen stays SUPERSEDING until something
ends her). `sampler` watches both Queens and notes any instant both are active.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped.

Key invariants:
    - The candidate never shares a directory, a database or a key file with the old Hive.

See Also:
    - docs/adr/0065-supersedure-moving-the-hive-stand.md for the protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

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
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure import (
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

QUESTION = "Which season?"
GOAL = "write a haiku about bees"
WAIT_S = 60.0
VERSION = "1.0.0"
HUMAN = DeviceId("device_01ARZ3NDEKTSV4RRFFQ69G5FAV")


class Candidate:
    """The candidate machine: a second Hive restored from the archive, or one that dies first."""

    def __init__(self, manifest: HiveManifest, *, dies: bool, stalls: bool = False) -> None:
        """A candidate over `manifest`: one that dies before it is ready, or stalls its restore."""
        self.manifest, self.dies, self.stalls = manifest, dies, stalls
        self.hive: Hive | None = None
        self._archive = manifest.resolve_path(Path("incoming.hive.tgz"))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def send_backup(self, archive: Path, sha256: str) -> None:
        """Receive the archive, as the candidate's session would."""
        await asyncio.to_thread(shutil.copyfile, archive, self._archive)

    async def restore(self, sha256: str) -> None:
        """Check and restore the archive the way `hive restore` does."""
        if self.stalls:
            await asyncio.Event().wait()  # The copy never finishes: the old Queen dies first.
        db = self.manifest.resolve_path(self.manifest.hive.db)
        work = self.manifest.resolve_path(Path("restore-work"))
        opened = await asyncio.to_thread(open_backup, self._archive, work, sha256)
        await check_destination(opened.index, self.manifest)
        trees = {BASKET_TREE: self.manifest.basket_root()}
        written = await asyncio.to_thread(restore_backup, opened, db, trees)
        await asyncio.to_thread(record_restored, self.manifest, opened, written, SystemClock())

    async def start_queen(self) -> None:
        """Start the candidate's Queen, unless it was killed first."""
        if self.dies:
            return  # Killed before it was ready: its Queen never comes up.
        self.hive = await build(self.manifest)
        self._task = asyncio.ensure_future(self._serve(self.hive))

    async def is_ready(self, started_id: str) -> bool:
        """Whether the candidate's Queen has said it is ready for `started_id`."""
        return self.hive is not None and await was_ready(self.hive.stores.trail, started_id)

    async def acknowledge(self, started_id: str) -> bool:
        """Record the old Queen's acknowledgement on the candidate's trail."""
        assert self.hive is not None
        await recorder(self.hive).record(
            SUPERSEDED_KIND, self.hive.manifest.hive.id, started_id=started_id, acknowledged=True
        )
        return True

    async def discard(self, started_id: str) -> bool:
        """Nothing answers a discard: the candidate is dead or never started."""
        return False  # Dead, or never started: nothing answers.

    async def stop(self) -> None:
        """Stop the candidate's Queen, if it ever ran."""
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _serve(self, hive: Hive) -> None:
        async with run_hive(hive):
            await self._stop.wait()


async def setup(
    tmp_path: Path, *, dies: bool, stalls: bool = False
) -> tuple[Hive, Candidate, FakeSender]:
    """Build the old Hive, the candidate's directory beside it, and wire the old Queen."""
    manifest = load_manifest(stand_manifest(tmp_path / "old"), {})
    old = await build(manifest)
    candidate = Candidate(candidate_manifest(manifest, tmp_path / "new"), dies=dies, stalls=stalls)
    sender = FakeSender([])
    source = await backup_source(manifest)
    node = make_swarm_node(
        None,
        None,
        node_id=candidate.manifest.hive.node_id,
        level=RuntimeLevel.COLONIZED,
        access_level=AccessLevel.FULL,
        runtime_version=VERSION,
    )
    probe = ProbeReport(source.secret_names, source.identity_key, 0.0, reachable=True)

    async def node_for(_node: str) -> SwarmNode | None:
        return node

    async def probe_for(_node: str) -> ProbeReport:
        return probe

    async def link_for(_node: str) -> Candidate:
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
        runtime_version=VERSION,
        workdir=tmp_path / "work",
        grace_s=8.0,
        poll_s=0.1,
    )
    return old, candidate, sender


def candidate_manifest(manifest: HiveManifest, root: Path) -> HiveManifest:
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


async def build(manifest: HiveManifest) -> Hive:
    """Build a Hive over `manifest`; its own event loops, so never inside the test's."""
    return await asyncio.to_thread(
        build_hive,
        manifest,
        environ={},
        clock=SystemClock(),
        responders={"fake": goal_responder(QUESTION)},
    )


def recorder(hive: Hive) -> Recorder:
    """A Recorder writing on `hive`'s trail as its own node."""
    ids = hive.manifest.hive
    return Recorder(hive.stores.trail, ids.id, ids.node_id, SystemClock())


@contextlib.asynccontextmanager
async def sampler(
    old: Hive, candidate: Candidate, both: list[tuple[QueenMode, QueenMode]]
) -> AsyncIterator[None]:
    """Watch both Queens every 10 ms; note any instant where both are active."""

    async def watch() -> None:
        while True:
            new = candidate.hive
            if new is not None and active(old) and active(new):
                both.append((queen_mode(old), queen_mode(new)))
            await asyncio.sleep(0.01)

    task = asyncio.ensure_future(watch())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def queen_mode(hive: Hive) -> QueenMode:
    return hive.queen_deps.cluster_state.mode


def active(hive: Hive) -> bool:
    """A Queen that may place work: RUNNING or CLUSTERED, and not held as a candidate."""
    held = hive.queen_deps.requeening.hold is not None
    return queen_mode(hive) in (QueenMode.RUNNING, QueenMode.CLUSTERED) and not held


async def mode_is(hive: Hive, mode: QueenMode) -> bool:
    return queen_mode(hive) is mode


async def started(candidate: Candidate) -> Hive:
    async def up() -> bool:
        return candidate.hive is not None

    await wait_until(up, timeout_s=WAIT_S)
    assert candidate.hive is not None
    return candidate.hive


async def task_in(hive: Hive, status: TaskStatus) -> Task:
    """Wait until a task reaches `status`, and return it."""
    found: list[Task] = []

    async def reached() -> bool:
        found[:] = [t for t in await hive.stores.chamber.list(TaskFilter()) if t.status is status]
        return bool(found)

    await wait_until(reached, timeout_s=WAIT_S)
    return found[0]


async def answer_on(hive: Hive, task: Task) -> None:
    """Answer the newest question `task`'s Drone is waiting on, as the human."""
    pending: list[MessageId] = []

    async def asked() -> bool:
        questions = await hive.stores.chamber.pending_questions(task.id)
        pending[:] = [q.id for q in questions]
        return bool(pending)

    await wait_until(asked, timeout_s=WAIT_S)
    await hive.queen.answer_question(
        pending[-1],
        "spring",
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
    )
