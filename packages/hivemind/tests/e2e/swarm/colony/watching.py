"""Let a colonized device's own Warden watch it, and read what that Warden kept on the device.

Roadmap step 11.10 makes watching what the Warden of every Real Cell does while no bee works, and
`tests/e2e/test_phase11_exit_watch.py` proves it for a Level 0 device, whose Warden runs on the Hive
Stand and asks the packet. This is the same story for a device whose Warden has moved onto it (Level
1): a container is enrolled on a whole Hive, the operator's `hive swarm promote` moves the Warden
onto it, and that Warden, local to the machine, looks through a session that holds no lease and
keeps what it sees in its own Bee Bread on the device. The story, told in order on one machine: the
idle device is Patrolled on the Warden's beat and every Patrol is quiet; a CPU hog planted on it
makes the next Patrol an ALARM and a `PATROL_ANOMALY` Alarm reaches the Queen, while the
observations that Patrol covered hold no file, because the operator has named no directory; the
operator then names one (`hive swarm watch`), the Queen finds that the Warden runs under the old
list and restarts it under the new one, and a second hog's Patrol covers observations that hold the
log lines of that directory and of no other; once the hog is gone the Patrols are quiet again. The
scripted model decides each Patrol from the facts it is shown (`Scenario.alarm_on`), so a quiet
Patrol cannot be turned into an Alarm by a canned reply. `run_watch_scenario` tells the story and
returns a `WatchRun`, the record `tests/e2e/test_phase11_exit_watch_colonized.py` makes its claims
over.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 watch
    scenario for a colonized device; builds on the rest of `e2e.swarm.colony`, `e2e.swarm.bridge`,
    `e2e.watch_helpers` and the operator's own `hive swarm promote` and `hive swarm watch`.

Key invariants:
    - The operator's two wishes are the real commands, and everything after them is the running
      Hive acting on its own beat; nothing here moves a Warden or changes its terms by hand.
    - What the Warden kept is read from the device's own SQLite file, read-only, with the runtime's
      interpreter, which is the only Python there; the Hive's trail never holds an observation.
    - An observation is read right after the ALARM Patrol that covered it: a quiet Patrol discards
      what it covered, so the other Patrols leave nothing to read.
    - Everything the test writes on the device (the log, the hog's program) is under `APP_DIR`,
      `ELSEWHERE_DIR` or `HOG_PATH`, so a write anywhere else is the Warden's.
    - Every wait is bounded and named, a failure carries the device's own logs, and every
      container and network is removed.

See Also:
    - tests/e2e/test_phase11_exit_watch_colonized.py for the claims made over the record.
    - tests/e2e/test_phase11_exit_watch.py for the same watch on a Level 0 device.
    - e2e/swarm/colony/desktop.py for the story this one is shaped like.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from e2e.scripted_openai import Scenario
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.colony.device import (
    STATE_DIR,
    colonizable,
    device_events,
    device_logs,
    shared_model,
)
from e2e.swarm.colony.models import RUNTIME_PYTHON, hive_tables, local_scenario
from e2e.swarm.colony.waits import promoted_to, until, whole_trail
from e2e.swarm.container import Container, Janitor, docker
from e2e.swarm.enrolled import ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import REPORT_FILE, trail_events, warden_is_idle
from e2e.swarm.operator import cell_rows, swarm
from e2e.swarm.snapshots import (
    DEVICE_TMP,
    WrittenFile,
    changed_paths,
    files_under,
    operator_files,
)
from e2e.watch_helpers import HOG_CORE_FRACTION, HOG_NAME, PatrolModel

from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent
from hivemind.swarm import SwarmNode

APP_DIR = "/srv/app"  # The directory the operator names for the Warden to watch.
ELSEWHERE_DIR = "/srv/elsewhere"  # One on the same device that nobody asks the Warden to read.
APP_LOG = f"{APP_DIR}/app.log"
OUTSIDE_LOG = f"{ELSEWHERE_DIR}/secret.log"
HOG_PATH = f"{DEVICE_TMP}/{HOG_NAME}"  # A copy of the shell, so the process has a name to find.
SCRATCH_ROOT = f"{STATE_DIR}/scratch"  # Where the on-device Warden's leases keep their scratch.
# Lines the log holds: the first before anything is watched, the others written while it is. The
# scripted model never reads them (it decides from the hog alone); they are what the observations
# are looked in.
STARTED_LINE, LOST_LINE, BACK_LINE = (
    "app service started",
    "the database went away",
    "the database came back",
)
_PLANT = (
    f"mkdir -p {APP_DIR} {ELSEWHERE_DIR} && echo '{STARTED_LINE}' > {APP_LOG} && "
    f"echo 'elsewhere service started' > {OUTSIDE_LOG} && cp /bin/sh {HOG_PATH}"
)
# The ladder wants some free memory before it moves a Warden onto a device, and a container reports
# the host's: this scenario is about watching, not the host.
LADDER = "[swarm.ladder]\nmin_free_memory_bytes = 0\n"
GRACE_S = 900.0  # How long the Queen holds a lost device before she judges it: past the scenario.
QUIET_PATROLS = 3  # Uneventful Patrols the idle device has before anything is planted.
QUIET_AGAIN = 2  # And after the second hog is gone.
# Bounds, in real seconds. The Warden on a device beats every fifteen seconds, and each of its
# Patrols is on a beat, so each bound is several beats; the whole run is about six minutes.
UP_S, COLONIZE_S, WATCHING_S = 90.0, 300.0, 120.0
QUIET_S, ALARM_S, RENEW_S, MODE_S = 180.0, 120.0, 240.0, 90.0
# Every observation the Warden kept, read-only, reduced to what the claims are about: when it was
# kept, the log lines and file changes in it, and the names of its processes. `payload` is the
# observation's own JSON (BeeBreadEntry.payload), inside the row's body.
_OBSERVATIONS_READER = (
    "import json, sqlite3, sys\n"
    "db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)\n"
    "query = \"select created_at, body from memory_bee_bread where kind = 'WATCH_OBSERVATION' \"\n"
    "query += 'order by created_at'\n"
    "kept = []\n"
    "for at, body in db.execute(query):\n"
    "    seen = json.loads(json.loads(body)['payload'])\n"
    "    kept.append({\n"
    "        'at': at,\n"
    "        'lines': [[x['path'], x['line']] for x in seen['log_lines']],\n"
    "        'changes': [[x['change'], x['path']] for x in seen['file_changes']],\n"
    "        'processes': sorted({x['name'] for x in seen['processes']}),\n"
    "    })\n"
    "db.close()\n"
    "print(json.dumps(kept))\n"
)

__all__ = [
    "APP_DIR",
    "APP_LOG",
    "BACK_LINE",
    "ELSEWHERE_DIR",
    "HOG_PATH",
    "LOST_LINE",
    "OUTSIDE_LOG",
    "QUIET_AGAIN",
    "QUIET_PATROLS",
    "SCRATCH_ROOT",
    "STARTED_LINE",
    "Alarmed",
    "Patrolled",
    "Rest",
    "Seen",
    "WatchRun",
    "device_observations",
    "device_patrols",
    "run_watch_scenario",
]


class Patrolled(NamedTuple):
    """One `warden.patrol` of the device Warden's own trail: when, and how it ended."""

    at: datetime
    outcome: str  # NOTHING, ALARM, NECTAR, WAX or SKIPPED.


class Seen(NamedTuple):
    """One observation as the Warden kept it, boiled down to what the claims are about."""

    at: datetime
    lines: frozenset[tuple[str, str]]  # Each (log path, line) it holds.
    changes: frozenset[tuple[str, str]]  # Each (change, path) of a file it holds.
    processes: frozenset[str]  # The names of the processes it lists.


class Alarmed(NamedTuple):
    """An ALARM Patrol: what it was, what it covered and how many Alarms the Queen holds then."""

    patrol: Patrolled
    covered: tuple[Seen, ...]  # The observations the device still kept right after it.
    anomalies: int  # `PATROL_ANOMALY` Alarms on the Hive's trail by then, this one included.


class Rest(NamedTuple):
    """The device at rest, the Warden watching and nothing of the Hive's leased on it."""

    files: dict[str, WrittenFile | str]  # The operator's directories, less the test's own.
    changed: frozenset[str]  # `docker diff`: everything the container's layer has had written.
    scratch: tuple[str, ...]  # What the Warden's leases hold in scratch: nothing at rest.


@dataclass(frozen=True, slots=True)
class WatchRun:
    """What one run of the scenario left behind: the record the criteria are asserted over.

    Attributes:
        node: The device, as the Hive's registry knows it.
        row: The device's row of `hive cells list`, once its Warden watched.
        before: The device at rest once its Warden watched and before anything was planted.
        quiet: The device Warden's first Patrols, before anything was planted.
        unnamed: The first hog's Patrol, while the operator had named no directory.
        named: The second hog's Patrol, once the operator named `APP_DIR` and the Warden restarted.
        later: The Patrols after the second hog was gone.
        after: The device at rest again at the end.
        starts: How many times the device Warden has started: once colonized, once renewed.
        calls: The `llm.call` events in the device Warden's own trail: one per Patrol it ran.
        leases: `cell.leased` and `cell.released` events in the device Warden's own trail.
        trail: The Hive's whole trail at the end, oldest first.
    """

    node: SwarmNode
    row: Mapping[str, Any]
    before: Rest
    quiet: tuple[Patrolled, ...]
    unnamed: Alarmed
    named: Alarmed
    later: tuple[Patrolled, ...]
    after: Rest
    starts: int
    calls: int
    leases: tuple[int, int]
    trail: tuple[PheromoneEvent, ...]


def device_patrols(container: Container) -> tuple[Patrolled, ...]:
    """Every `warden.patrol` in the on-device Warden's own trail, oldest first."""
    rows = device_events(container, "warden.patrol")
    return tuple(
        Patrolled(datetime.fromisoformat(str(row["at"])), str(_payload(row)["outcome"]))
        for row in rows
    )


def device_observations(container: Container) -> tuple[Seen, ...]:
    """Every observation the on-device Warden still keeps, oldest first.

    Args:
        container: The device.

    Returns:
        What the Warden's Bee Bread holds right now; empty before its first sample, and after a
        quiet Patrol, which discards what it covered.
    """
    path = f"{STATE_DIR}/warden.sqlite"
    shown = container.exec([RUNTIME_PYTHON, "-c", _OBSERVATIONS_READER, path], check=False)
    if shown.exit_code != 0:
        return ()  # No store yet: the Warden has not started.
    return tuple(
        Seen(
            datetime.fromisoformat(row["at"]),
            frozenset((path, line) for path, line in row["lines"]),
            frozenset((change, path) for change, path in row["changes"]),
            frozenset(row["processes"]),
        )
        for row in json.loads(shown.stdout)
    )


def _payload(event: Mapping[str, object]) -> Mapping[str, object]:
    """The payload of an event body read from the device's own trail file."""
    payload = event["payload"]
    assert isinstance(payload, dict), "an event's payload is a JSON object"
    return payload


def run_watch_scenario(image: str, directory: Path) -> WatchRun:
    """Run the whole story on a fresh container and Hive, and clean up after it.

    Args:
        image: The device image (`e2e.swarm.image.device_image_or_skip`).
        directory: The scenario's own directory; its files are written here.

    Returns:
        The record of the run.
    """
    janitor = Janitor()
    try:
        network = janitor.network()
        # The Warden on the device asks its `warden` slot's model, which is this server on the Hive
        # Stand's side of the cable: an ALARM only for a Patrol shown the hog by name.
        with shared_model(network, Scenario(alarm_on=HOG_NAME)) as shared_url:
            options = colonizable(
                directory, network, shared_url, local_scenario(1, 1, REPORT_FILE), _watch_table()
            )
            terms = BridgeTerms({"unreachable_grace_s": GRACE_S}, tables=LADDER + hive_tables())
            # Built here, before any loop runs: opening the Hive's stores starts a loop of its own.
            hive = bridge_hive(directory, network, PatrolModel().responder, terms)
            return asyncio.run(_scenario(janitor, image, _Story(hive, directory, options)))
    finally:
        janitor.close()


def _watch_table() -> str:
    """The device manifest's `[supervision.watch]`: a sample and a Patrol at every beat.

    The Warden on a device wakes every fifteen seconds, so a sample every fifth of a second and a
    Patrol every two mean one of each per beat. Nothing is purged by age during the run.
    """
    share = min(1.0, HOG_CORE_FRACTION / (os.cpu_count() or 1))  # A share of all cores together.
    return (
        "[supervision.watch]\ninterval_s = 0.2\nretention_s = 86400.0\npatrol_interval_s = 2.0\n"
        f"cpu_share_alarm = {share}\ndisk_fill_alarm = 1.0\n"
    )


@dataclass
class _Story:
    """The Hive and the device's terms of one run, with a method for each step of the story."""

    hive: Hive
    directory: Path
    options: DeviceOptions
    device: ContainerDevice | None = None  # Set once the container has enrolled.

    @property
    def node(self) -> SwarmNode:
        """The device's node."""
        assert self.device is not None
        return self.device.node

    @property
    def container(self) -> Container:
        """The device's container."""
        assert self.device is not None
        return self.device.container

    async def tell(self) -> WatchRun:
        """Tell the story in order and return its record."""
        idle = lambda: warden_is_idle(self.hive, self.node)  # noqa: E731 - named for the wait
        await until("the Hive Stand's Warden for the device to start and idle", idle, UP_S)
        await asyncio.to_thread(self.container.exec, ["sh", "-c", _PLANT])
        await self._colonize()
        before = await self._at_rest()
        quiet = await self._patrols(QUIET_PATROLS, "the idle device to be Patrolled quietly")
        unnamed = await self._alarmed(1, LOST_LINE)
        await self._name(APP_DIR)
        named = await self._alarmed(2, BACK_LINE)
        held = len(await asyncio.to_thread(device_patrols, self.container))
        later = (await self._patrols(held + QUIET_AGAIN, "quiet again"))[held:]
        row = await self._operator_row()
        after = await self._at_rest()
        return WatchRun(
            node=self.node,
            row=row,
            before=before,
            quiet=quiet,
            unnamed=unnamed,
            named=named,
            later=later,
            after=after,
            starts=len(await self._events("warden.started")),
            calls=len(await self._events("llm.call")),
            leases=(
                len(await self._events("cell.leased")),
                len(await self._events("cell.released")),
            ),
            trail=tuple(await whole_trail(self.hive)),
        )

    async def _events(self, kind: str) -> list[dict[str, object]]:
        """The events of `kind` in the device Warden's own trail, as it recorded them."""
        return await asyncio.to_thread(device_events, self.container, kind)

    async def _colonize(self) -> None:
        """Say `hive swarm promote NODE`, and wait for the Warden to be on the device."""
        result = await swarm(self.directory / "hive.toml", "promote", self.node.device_id)
        assert result.exit_code == 0, result.stderr
        moved = lambda: promoted_to(self.hive, "COLONIZED")  # noqa: E731 - named for the wait
        await until("the Warden to move onto the device", moved, COLONIZE_S)

    async def _at_rest(self) -> Rest:
        """Wait until the device's Warden watches with no lease, then measure the device."""
        container = self.container

        async def watching() -> bool:
            leased = len(await self._events("cell.leased"))
            released = len(await self._events("cell.released"))
            idle = not await asyncio.to_thread(files_under, container, SCRATCH_ROOT)
            return bool(await self._events("warden.watch")) and leased == released and idle

        await until("the device's Warden to watch it with no lease", watching, WATCHING_S)
        return await asyncio.to_thread(_measure, container)

    async def _patrols(self, count: int, what: str) -> tuple[Patrolled, ...]:
        """Wait until the device Warden has Patrolled `count` times in all; return every Patrol."""
        container = self.container

        async def enough() -> bool:
            return len(await asyncio.to_thread(device_patrols, container)) >= count

        await until(what, enough, QUIET_S)
        return await asyncio.to_thread(device_patrols, container)

    async def _alarmed(self, anomalies: int, line: str) -> Alarmed:
        """Plant a hog and write `line` to the log; return the ALARM Patrol and what it covered."""
        container = self.container
        before = len(await asyncio.to_thread(device_patrols, container))
        await asyncio.to_thread(container.exec, ["sh", "-c", f"echo '{line}' >> {APP_LOG}"])
        # Detached: the exec returns at once and the program keeps spinning in the container.
        spin = ["exec", "--detach", container.name, HOG_PATH, "-c", "while :; do :; done"]
        await asyncio.to_thread(docker, *spin)

        async def alarmed() -> bool:
            patrols = (await asyncio.to_thread(device_patrols, container))[before:]
            return any(patrol.outcome == "ALARM" for patrol in patrols)

        await until("the next Patrol to raise the alarm", alarmed, ALARM_S)
        # At once: the next Patrol is a beat away, and a quiet one discards what this one covered.
        covered = await asyncio.to_thread(device_observations, container)
        patrol = next(p for p in device_patrols(container)[before:] if p.outcome == "ALARM")
        await asyncio.to_thread(container.exec, ["pkill", "-x", HOG_NAME])
        raised = lambda: self._anomalies(anomalies)  # noqa: E731 - named for the wait
        await until("the Queen to hold the Patrol's Alarm", raised, ALARM_S)
        return Alarmed(patrol, covered, len(await self._anomaly_events()))

    async def _anomaly_events(self) -> list[PheromoneEvent]:
        """Every `PATROL_ANOMALY` Alarm on the Hive's trail."""
        raised = await trail_events(self.hive, "alarm.raised")
        return [event for event in raised if event.payload["kind"] == "PATROL_ANOMALY"]

    async def _anomalies(self, count: int) -> bool:
        """Whether the Hive's trail holds `count` `PATROL_ANOMALY` Alarms."""
        return len(await self._anomaly_events()) >= count

    async def _name(self, directory: str) -> None:
        """Say `hive swarm watch NODE DIRECTORY`, and wait for the restarted Warden to Patrol."""
        label = str(self.node.label)
        result = await swarm(self.directory / "hive.toml", "watch", label, directory)
        assert result.exit_code == 0, result.stderr
        patrols = len(await asyncio.to_thread(device_patrols, self.container))

        async def restarted() -> bool:
            return len(await self._events("warden.started")) >= 2

        await until("the Queen to restart the device's Warden", restarted, RENEW_S)
        await self._patrols(patrols + 1, "the restarted Warden to Patrol")

    async def _operator_row(self) -> Mapping[str, Any]:
        """The device's row of `hive cells list`, once its Warden's mode has reached the Hive."""
        manifest = self.directory / "hive.toml"
        row: dict[str, Any] = {}

        async def watching() -> bool:
            rows = [r for r in await cell_rows(manifest) if r["source"] == "swarm"]
            row.update(rows[0])
            return bool(row["mode"] == "WATCH")

        await until("the operator to see the Warden in WATCH", watching, MODE_S)
        return row


async def _scenario(janitor: Janitor, image: str, story: _Story) -> WatchRun:
    """Start the Hive, enrol the device and tell the story; a failure carries the device's logs."""
    async with run_hive(story.hive):
        story.device = await enrol_device(BridgeHive(story.hive), janitor, image, story.options)
        try:
            return await story.tell()
        except Exception as error:
            error.add_note(await asyncio.to_thread(device_logs, story.container))
            raise


def _measure(container: Container) -> Rest:
    """Measure the device: the operator's files, the container's layer, the Warden's scratch."""
    files = {
        path: entry
        for path, entry in operator_files(container).items()
        if path != APP_DIR and not path.startswith(f"{APP_DIR}/")
    }
    return Rest(
        files=files,
        changed=frozenset(changed_paths(container)),
        scratch=tuple(files_under(container, SCRATCH_ROOT)),
    )
