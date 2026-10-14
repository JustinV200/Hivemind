"""Build a Hive that watches on a compressed schedule, and the scripted model that reviews it.

The end-to-end scenarios of roadmap step 11.10 (watch mode and the hourly Patrol) all need the same
few things. A Hive whose `[supervision.watch]` table asks for a sample every fifth of a second and
a Patrol every second, instead of every five minutes and every hour. A model for the Warden's slot
that decides from what its prompt actually shows it, so a Patrol shown nothing notable cannot be
made to raise an Alarm by a canned answer. A real CPU hog to plant on the machine the Warden
watches. A way to fail the test the moment watching writes to the Cell. And reads of what the
Warden kept, by the Warden that kept it. They live here so each scenario states only the fact
under test.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by `tests/e2e/
    test_watch_and_patrol.py`; depends on `hivemind.cli.compose`, `hivemind.wardens.watch` and the
    e2e kernel and swarm helpers.

Key invariants:
    - The Hive is the real one: the real Queen and Wardens, a real SQLite file and, for the Hive
      Stand, this machine as its Cell. Only the model is scripted.
    - The scripted model alarms only when the hog's name is in the facts a Patrol was shown.
    - Nothing here touches the Cell: the hog is a child of the test, living in the test's own
      directory, and ends with the block that planted it.

See Also:
    - tests/e2e/kernel_helpers.py for HaikuScript, wait_until and text_response.
    - tests/e2e/swarm/ for the packet and Hive rig the device scenario uses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from builders.swarm import swarm_manifest
from e2e.kernel_helpers import HaikuScript, text_response
from e2e.swarm.hive import BEAT_S

from hivemind.cell import HoneyClearance
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cli.compose import Hive, build_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.memory import MemoryContext, MemoryIdentity
from hivemind.memory.bee_bread import BeeBreadEntry, BeeBreadEntryKind
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.wardens.watch.store import KeptObservation, load_observations
from waggle.clock import SystemClock

HOG_NAME = "beehog_zq7"  # What the planted process is called: recognisable, and 10 characters.
WAIT_S = 30.0  # Each wait; a Patrol is a second away and a device enrols in a couple of seconds.
RETENTION_S = 86_400.0  # A day: nothing is purged by age during a test, only by a Patrol.
HOG_CORE_FRACTION = 0.25  # A process using a quarter of one core counts as a hog in these Hives.
_PATROL_HEADING = "# The Patrol"  # How the Patrol prompt begins: how the model knows its turn.

__all__ = [
    "HOG_NAME",
    "RETENTION_S",
    "WAIT_S",
    "PatrolModel",
    "as_found",
    "events_of",
    "everything_said",
    "forbid_session_writes",
    "kept_by",
    "patrolled",
    "planted_hog",
    "stored_entries",
    "swarm_watching_hive",
    "watch_table",
    "watched_directory",
    "watching_hive",
]


class PatrolModel:
    """A scripted model for the Warden's slot: it decides only from the facts it is shown."""

    def __init__(self, others: HaikuScript | None = None) -> None:
        """Build the model.

        Args:
            others: Answers every other slot (the Queen's plan, a Drone's turns, the judge) when a
                scenario runs a goal as well; without it they answer an empty object.
        """
        self._others = others
        self.shown: list[str] = []  # The user turn of every Patrol, as the model saw it.

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Answer a Patrol from its facts, and every other request as the kernel suite does."""
        if request.slot is ModelSlot.WARDEN and (request.system or "").startswith(_PATROL_HEADING):
            facts = "".join(
                part.text for part in request.messages[0].parts if hasattr(part, "text")
            )
            self.shown.append(facts)
            # The signal line is the pre-filter's own wording: the hog shows up in it by name.
            sees_hog = f'signal NEW_PROCESS_CPU: name="{HOG_NAME}"' in facts
            choice = "ALARM" if sees_hog else "NOTHING"
            reason = "A new process pins the CPU." if sees_hog else "An ordinary interval."
            return text_response(json.dumps({"choice": choice, "reason": reason}))
        if self._others is not None:
            return self._others.responder(request)
        return text_response("{}")


def watch_table(roots: Sequence[Path] = ()) -> str:
    """Return the compressed `[supervision.watch]` body: a sample every 0.2 s, a Patrol every 1 s.

    Args:
        roots: Directories the Hive Stand's Warden reads logs and changes from. A device's Warden
            names none, whatever this says.

    Returns:
        The TOML body, without its table header.
    """
    share = min(1.0, HOG_CORE_FRACTION / (os.cpu_count() or 1))  # A share of all cores together.
    names = ", ".join(f"'{root.as_posix()}'" for root in roots)
    return (
        f"interval_s = 0.2\nretention_s = {RETENTION_S}\npatrol_interval_s = 1.0\n"
        f"cpu_share_alarm = {share}\ndisk_fill_alarm = 1.0\nroots = [{names}]\n"
    )


def watching_hive(tmp_path: Path, model: PatrolModel, watched: Path) -> Hive:
    """Build a Hive that watches `watched` on the compressed schedule, its models scripted.

    Args:
        tmp_path: The test's own directory; the manifest, database and scratch go under it.
        model: The scripted model every slot of the Hive answers through.
        watched: The directory the Hive Stand's Warden is told to read logs and changes from.

    Returns:
        A Hive, not yet started.
    """
    tuning = ManifestTuning(watch_table=watch_table((watched,)))
    manifest = load_manifest(fake_manifest(tmp_path, tuning=tuning), {})
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": model.responder}
    )


def swarm_watching_hive(tmp_path: Path, port: int, model: PatrolModel) -> Hive:
    """Build a Hive with the Swarm on at loopback `port` that watches on the compressed schedule.

    Args:
        tmp_path: The test's own directory.
        port: A free loopback TCP port for the swarm listener.
        model: The scripted model every slot of the Hive answers through.

    Returns:
        A Hive, not yet started, whose Swarm is on and whose Wardens name no directory.
    """
    path = swarm_manifest(tmp_path, listen_port=port, heartbeat_interval_s=BEAT_S)
    # TOML lets a sub-table of `[supervision]` come after `[swarm]`, so the table is appended.
    text = f"{path.read_text(encoding='utf-8')}\n[supervision.watch]\n{watch_table()}"
    path.write_text(text, encoding="utf-8")
    return build_hive(
        load_manifest(path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": model.responder},
    )


def watched_directory(tmp_path: Path) -> Path:
    """Make a directory with a log and a file in it: something to read, never to touch."""
    watched = tmp_path / "watched"
    (watched / "sub").mkdir(parents=True)
    (watched / "app.log").write_text("started\nlistening on 8080\n")
    (watched / "sub" / "notes.txt").write_text("not a log")
    return watched


def as_found(root: Path) -> dict[str, tuple[int, int]]:
    """Map every file and directory under `root` to its size and modification time in nanoseconds.

    A watched directory must come out of a watch exactly as it went in. Unlike a size-only
    snapshot, this catches a rewrite to the same length, a rename and a mere `touch`.
    """
    state: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        state[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return state


def forbid_session_writes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every write method of the real session record itself and fail.

    Args:
        monkeypatch: The test's monkeypatch, which puts the methods back after the test.

    Returns:
        The names of the write methods that were called; empty when watching only looked.
    """
    called: list[str] = []

    def refuse(name: str) -> object:
        async def write(*args: object, **kwargs: object) -> None:
            called.append(name)
            raise AssertionError(f"watch mode called {name} on the Hive Stand's session")

        return write

    for name in ("put_file", "delete_file", "start", "stop"):
        monkeypatch.setattr(LocalProcessSession, name, refuse(name))
    return called


@contextmanager
def planted_hog(directory: Path) -> Iterator[subprocess.Popen[bytes]]:
    """Run a real process called `beehog_zq7` that spins on a CPU core, and kill it after.

    Args:
        directory: Where the copy of the shell that gives the process its name is made.

    Yields:
        The running process.
    """
    program = directory / HOG_NAME
    shutil.copy2(shutil.which("sh") or "/bin/sh", program)
    process = subprocess.Popen([str(program), "-c", "while :; do :; done"])  # noqa: S603
    try:
        yield process
    finally:
        process.kill()
        process.wait(timeout=10)


def memory_context(hive: Hive) -> MemoryContext:
    """Return a context over the Hive's own memory store, to read what its Wardens kept."""
    identity = hive.manifest.hive
    return MemoryContext(
        store=hive.stores.memory,
        identity=MemoryIdentity(identity.id, identity.node_id, "human"),
        clock=hive.clock,
    )


async def kept_by(hive: Hive, warden_id: str) -> tuple[KeptObservation, ...]:
    """Return every Observation the Warden `warden_id` still keeps, oldest first."""
    now = hive.clock.now()
    window = (now - timedelta(days=2), now + timedelta(days=1))
    return await load_observations(memory_context(hive), warden_id, *window)


async def stored_entries(hive: Hive) -> list[BeeBreadEntry]:
    """Return every watch-observation entry in the Hive's store, whoever kept it, oldest first."""
    now = hive.clock.now()
    return list(
        await hive.stores.memory.list_bee_bread_of_kind(
            BeeBreadEntryKind.WATCH_OBSERVATION,
            now - timedelta(days=2),
            now + timedelta(days=1),
            HoneyClearance.C2,
        )
    )


async def events_of(hive: Hive, kind: str) -> list[PheromoneEvent]:
    """Return every trail event of `kind`, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def patrolled(hive: Hive, outcome: str, warden_id: str | None = None) -> bool:
    """Return whether a Patrol ended with `outcome` (for `warden_id`, when one is named)."""
    events = await events_of(hive, "warden.patrol")
    return any(
        event.payload.get("outcome") == outcome and warden_id in (None, event.subject_id)
        for event in events
    )


def everything_said(events: Sequence[PheromoneEvent]) -> str:
    """Return the events' kinds, subjects and payloads as one string, to search for an absence."""
    return json.dumps([[e.kind, e.subject_id, dict(e.payload)] for e in events], default=str)
