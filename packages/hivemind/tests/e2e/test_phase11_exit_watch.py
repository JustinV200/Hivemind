"""Phase 11 exit criterion 7 on a second machine: an idle device is watched, and nothing is written.

"Leave an enrolled device idle overnight: its Warden sits in `WATCH`, the device shows no writes
from HiveMind, the Patrol runs on schedule, and a planted anomaly (a new process eating CPU)
surfaces as an Alarm at the next Patrol while an uneventful hour records 'nothing notable'." The
night is compressed to seconds: `[supervision.watch]` asks for a sample every fifth of a second and
a Patrol every second. The device is a container enrolled on a real Hive; its Warden, on the Hive
Stand, asks the packet what the machine looks like (`swarm.observe`, no lease and no command),
keeps what it hears as `C2` Bee Bread and reviews it with a scripted model that decides only from
the facts its prompt carries (`e2e.watch_helpers.PatrolModel`).

What it proves: the device's Warden sits in WATCH and holds no lease; Patrols run on the schedule
and each quiet one records "nothing notable"; a CPU hog started in the container with `docker
exec` is seen by name in the next Patrol's facts and becomes a `PATROL_ANOMALY` Alarm the Queen
carries to the human, and once the hog is gone the Patrols are quiet again; and the whole time the
device shows no write from HiveMind: the directories the operator owns are exactly as they were
(names, modes, sizes and modification times), the container's writable layer holds only the
packet's own data directory and the test's own planted program, and not one command was sent.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one.

Key invariants:
    - The planted program is the test's, put on the device before the baseline is taken, so it is
      not mistaken for a write by the Hive.
    - Every container and network is removed, whatever the test did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", the overnight bullet; step 11.10.
    - tests/e2e/test_watch_and_patrol.py for the same Patrol on the Hive Stand and an in-process
      packet.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the design.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from pathlib import Path

import pytest
from e2e.kernel_helpers import wait_until
from e2e.swarm.bridge import BridgeHive, BridgeTerms, bridge_hive
from e2e.swarm.container import Janitor, Network, docker
from e2e.swarm.enrolled import DATA_DIR, ContainerDevice, DeviceOptions, enrol_device
from e2e.swarm.hive import trail_events, warden_is_idle
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.operator import cell_rows
from e2e.swarm.snapshots import DEVICE_TMP, changes_outside, operator_files
from e2e.watch_helpers import HOG_NAME, PatrolModel, patrolled, watch_table

from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import PheromoneEvent

pytestmark = pytest.mark.e2e

_WAIT_S = 90.0  # Each wait; a Patrol is a second away, a device enrols in seconds.
_PATROL_S = 1.0  # `watch_table`'s Patrol interval.
_QUIET_PATROLS = 3  # How many uneventful Patrols the device has before the anomaly is planted.
# A copy of the shell, so the process carries a name to be found by.
_HOG_PATH = f"{DEVICE_TMP}/{HOG_NAME}"
_MIN_ENTRIES = 100  # Fewer files than this under the operator's directories means the look failed.


@pytest.fixture(scope="module")
def image() -> str:
    """The device image for this tree; the module skips without a docker daemon."""
    return device_image_or_skip()


def test_an_idle_device_is_watched_without_a_write_and_a_planted_hog_becomes_an_alarm(
    image: str, tmp_path: Path
) -> None:
    janitor = Janitor()
    try:
        network = janitor.network()
        model = PatrolModel()
        terms = BridgeTerms(watch_table=watch_table())
        hive = bridge_hive(tmp_path, network, model.responder, terms)

        asyncio.run(_scenario(hive, janitor, image, (network, tmp_path / "hive.toml"), model))
    finally:
        janitor.close()


async def _scenario(
    hive: Hive, janitor: Janitor, image: str, where: tuple[Network, Path], model: PatrolModel
) -> None:
    """Enrol a container, let its Warden watch it, plant a hog, remove it, and compare the disk."""
    network, manifest = where
    async with run_hive(hive):
        device = await enrol_device(
            BridgeHive(hive), janitor, image, DeviceOptions(network=network)
        )
        container, node = device.container, device.node
        await wait_until(lambda: warden_is_idle(hive, node), timeout_s=_WAIT_S)
        await asyncio.to_thread(container.exec, ["cp", "/bin/sh", _HOG_PATH])
        baseline = await asyncio.to_thread(operator_files, container)
        # A snapshot that found nothing would equal itself whatever happened; a Debian image's
        # /etc and /usr/local alone hold hundreds of entries.
        assert len(baseline) > _MIN_ENTRIES, "the snapshot of the device's own files is empty"

        await _quiet_patrols_on_schedule(hive, device)
        await _the_operator_sees_the_warden_watch(manifest, device)
        await _a_planted_hog_becomes_an_alarm(hive, device, model)
        await _quiet_again_once_the_hog_is_gone(hive, device)

        assert await asyncio.to_thread(operator_files, container) == baseline
        unexplained = await asyncio.to_thread(changes_outside, container, DATA_DIR, _HOG_PATH)
        assert unexplained == []
        assert await trail_events(hive, "swarm.command_sent") == []
        assert warden_is_idle(hive, node)


async def _patrols(hive: Hive, device: ContainerDevice) -> list[PheromoneEvent]:
    """Every `warden.patrol` of the device's Warden, oldest first."""
    events = await trail_events(hive, "warden.patrol")
    return [event for event in events if event.subject_id == device.node.warden_id]


async def _anomalies(hive: Hive) -> list[PheromoneEvent]:
    """Every `PATROL_ANOMALY` Alarm on the trail."""
    raised = await trail_events(hive, "alarm.raised")
    return [event for event in raised if event.payload["kind"] == "PATROL_ANOMALY"]


async def _quiet_patrols_on_schedule(hive: Hive, device: ContainerDevice) -> None:
    """The device's Warden Patrols on the interval, and every Patrol of an idle device is quiet."""

    async def enough() -> bool:
        return len(await _patrols(hive, device)) >= _QUIET_PATROLS

    await wait_until(enough, timeout_s=_WAIT_S)
    patrols = await _patrols(hive, device)
    assert [event.payload["outcome"] for event in patrols] == ["NOTHING"] * len(patrols)
    stamps = [event.at for event in patrols]
    gaps = [(later - earlier).total_seconds() for earlier, later in pairwise(stamps)]
    # On schedule: one Patrol per interval, neither bunched up nor skipped.
    assert all(_PATROL_S / 2 <= gap <= 5 * _PATROL_S for gap in gaps), gaps
    assert not await trail_events(hive, "alarm.raised"), "an idle device raised an Alarm"


async def _the_operator_sees_the_warden_watch(manifest: Path, device: ContainerDevice) -> None:
    """`hive cells list` shows the device's Warden in WATCH, which is its mode while it is idle."""
    [row] = [r for r in await cell_rows(manifest) if r["source"] == "swarm"]
    assert (row["id"], row["mode"], row["warden_runs"]) == (
        device.node.cell_id,
        "WATCH",
        "hive_stand",
    )


async def _a_planted_hog_becomes_an_alarm(
    hive: Hive, device: ContainerDevice, model: PatrolModel
) -> None:
    """A CPU hog started with `docker exec` is an ALARM Patrol and a `PATROL_ANOMALY` Alarm."""
    before = len(await _patrols(hive, device))
    # Detached: the exec returns at once and the program keeps spinning in the container.
    await asyncio.to_thread(
        docker, "exec", "--detach", device.container.name, _HOG_PATH, "-c", "while :; do :; done"
    )

    await wait_until(lambda: patrolled(hive, "ALARM", device.node.warden_id), timeout_s=_WAIT_S)

    async def raised() -> bool:
        return bool(await _anomalies(hive))

    # The Alarm is the Patrol's outcome, written a moment after the Patrol's own row.
    await wait_until(raised, timeout_s=_WAIT_S)
    assert [e.payload["outcome"] for e in (await _patrols(hive, device))[before:]][-1] == "ALARM"
    assert any(HOG_NAME in facts for facts in model.shown), "no Patrol was shown the hog by name"
    # The hog's name is the machine's own fact: it is in the Patrols' prompts and nowhere on the
    # trail, which records only that a Patrol ended in an Alarm.
    assert HOG_NAME not in str([dict(e.payload) for e in await trail_events(hive)])


async def _quiet_again_once_the_hog_is_gone(hive: Hive, device: ContainerDevice) -> None:
    """With the hog killed, the Patrols after the next one are uneventful again."""
    await asyncio.to_thread(device.container.exec, ["pkill", "-x", HOG_NAME])
    after = len(await _patrols(hive, device))

    async def quiet() -> bool:
        patrols = await _patrols(hive, device)
        return len(patrols) >= after + 3 and patrols[-1].payload["outcome"] == "NOTHING"

    await wait_until(quiet, timeout_s=_WAIT_S)
    assert (await _patrols(hive, device))[-1].payload["outcome"] == "NOTHING"
