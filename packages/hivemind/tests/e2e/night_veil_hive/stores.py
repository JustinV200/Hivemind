"""Seed and read what a Night Veil run leaves in the Hive's stores beside its trail.

Codingrules section 12 lets nothing of a Night Veil Cell outlive it but its skeleton on the
Queen's trail, and four stores besides the trail hold rows about one while it lives: the Queen's
memory tables, the Brood Chamber, the Forage ledger (beside the boundary's own checkpoint table)
and a backend's snapshot images. `SnapshotHost` is a Docker daemon beside the Hive's own backend:
the container fake (`FakeDockerClient`) behind a `DockerCellBackend` with no room for a Cell, so
no placement ever lands there, on which a Cell's Capping snapshots could have left images. Each
Hive of a scenario registers it, as a restarted Queen's registry names the same daemon again.
`seed` leaves one row the purge must find in each of the two stores a run of this suite leaves
empty on its own: a Queen episode naming the Night Veil work, and a snapshot image of its Cell.
`left_about` reads every store fresh (the memory, ledger and checkpoint tables row by row, straight
from the Hive's file, the chamber through its own reads, the images from the daemon) and names
whatever still names the Night Veil work: every end path must leave nothing.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests.e2e.test_night_veil_boundary.

Key invariants:
    - `left_about` opens the Hive's file read-only: reading it never changes what it holds.

See Also:
    - hivemind.cli.compose.night_veil.side_channels for the purge's side channels.
    - tests.e2e.night_veil_hive.reads for what the trail keeps.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from builders.cells import make_cell
from builders.memory import make_episode, make_trigger_event
from e2e.night_veil_hive.reads import tasks
from e2e.night_veil_hive.rig import NightVeilRun

from hivemind.brood_chamber import is_terminal
from hivemind.cell import CellKind
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker import (
    ContainerSpec,
    DockerBackendConfig,
    DockerCellBackend,
    FakeDockerClient,
)
from hivemind.hive.backends.docker.backend import container_name
from hivemind.hive.backends.fake import FakeReadinessGate
from hivemind.hive.snapshot import DockerSnapshotter, SnapshotLedger
from hivemind.pheromone import MemoryEvent
from waggle.clock import SystemClock
from waggle.ids import CellId, new_event_id, new_node_id

__all__ = ["TASK_WORDS", "SnapshotHost", "left_about", "seed"]

TASK_WORDS = ("Write haiku 1", "Write a haiku about bees to haiku_1.txt")  # Title, objective.
_DOCKER = "docker"  # The daemon's backend name in each Hive's registry.
_SNAPSHOT_OF = "hivemind.snapshot_of"  # The label every snapshot commit stamps on its image.
# Every table of the stores read row by row: the memory tables, the ledger's and the checkpoint.
_TABLE_PREFIXES = ("memory_", "forage_ledger_", "night_veil_checkpoints")


class SnapshotHost:
    """A Docker daemon (the container fake) holding snapshot images, beside every Hive's own."""

    def __init__(self) -> None:
        """Start with no image and no container."""
        self.client = FakeDockerClient()

    def register(self, run: NightVeilRun) -> None:
        """Register a Docker backend over this daemon on `run`'s Virtual side, with no room."""
        clock = SystemClock()
        endpoint = QueenEndpoint(
            waggle_url="ws://localhost:8710",
            queen_node_id=new_node_id(clock),
            queen_verify_key_hex="00" * 32,
        )
        backend = DockerCellBackend(
            self.client,
            FakeReadinessGate(clock),
            endpoint,
            clock,
            config=DockerBackendConfig(max_cells=0),  # Never a placement's: only its images count.
        )
        assert run.hive.virtual_cells is not None
        run.hive.virtual_cells.registry.register(_DOCKER, lambda: backend)

    async def leave_snapshot(self, cell_id: CellId) -> None:
        """Leave one snapshot image of `cell_id`, committed as a Capping snapshot commits it."""
        name = container_name(cell_id)
        await self.client.create_container(_spec(name))
        cell = make_cell(kind=CellKind.VIRTUAL, id=CellId(cell_id))
        await DockerSnapshotter(self.client, SnapshotLedger(), SystemClock()).snapshot(cell)
        # The container goes (as the Cell's would); the image stays until something removes it.
        await self.client.remove_container(name, force=True)

    async def images_of(self, cell_id: str) -> tuple[str, ...]:
        """Every image this daemon still holds of `cell_id`."""
        return tuple(await self.client.list_images({_SNAPSHOT_OF: cell_id}))


async def seed(
    run: NightVeilRun, host: SnapshotHost, cell_id: CellId, named: Iterable[str]
) -> None:
    """Leave a Queen episode naming `named` and the Cell, and a snapshot image of the Cell."""
    clock, identity = SystemClock(), run.hive.manifest.hive
    summary = f"Checked on {cell_id} for {' '.join(named)}."
    episode = make_episode(clock, principal="queen", trigger=make_trigger_event(summary=summary))
    event = MemoryEvent(
        id=new_event_id(clock),
        hive_id=identity.id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind="memory.episode",
        subject_id=episode.id,
        payload={},
    )
    await run.hive.stores.memory.put_episode(episode, event)
    await host.leave_snapshot(cell_id)


async def left_about(
    run: NightVeilRun, host: SnapshotHost, cells: Iterable[str], ids: frozenset[str]
) -> list[str]:
    """Name everything any store still holds about the Night Veil work; empty when nothing.

    Args:
        run: The Hive whose stores are read.
        host: The daemon whose snapshot images are read.
        cells: The Night Veil Cells the run provisioned.
        ids: Every id the Night Veil work goes by (`reads.world`).

    Returns:
        One line per table row, live or wordy task, or image still naming the work.
    """
    left = _rows_naming(run.hive.manifest.resolve_path(run.hive.manifest.hive.db), ids)
    for task in await tasks(run):
        if task.id not in ids:
            continue  # Another tier's task keeps its words (the MEADOW scenario checks it).
        if not is_terminal(task.status):
            left.append(f"live task {task.id}")
        if any(word in json.dumps(task.model_dump(mode="json")) for word in TASK_WORDS):
            left.append(f"task words {task.id}")
    for cell in cells:
        left.extend(f"image {image}" for image in await host.images_of(cell))
    return left


def _rows_naming(database: Path, ids: frozenset[str]) -> list[str]:
    """Name each store table row that names one of `ids`, read straight from the file."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        names = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        found: list[str] = []
        for table in (name for name in names if name.startswith(_TABLE_PREFIXES)):
            # SAFETY: `table` is a name read from the file's own schema, never outside input.
            for row in connection.execute(f"SELECT * FROM {table}"):  # noqa: S608
                text = " ".join(str(value) for value in row)
                found.extend(f"{table} row naming {i}" for i in sorted(ids) if i in text)
        return found
    finally:
        connection.close()


def _spec(name: str) -> ContainerSpec:
    """A minimal container, never started: only something for a snapshot to commit."""
    return ContainerSpec(
        name=name,
        image="base-ubuntu",
        environment={},
        labels={},
        network_name="none",
        extra_hosts={},
        volume_name=None,
        volume_mount_path="/scratch",
        nano_cpus=500_000_000,
        mem_limit_bytes=64 * 1024 * 1024,
        pids_limit=16,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges:true",),
        read_only_rootfs=False,
    )
