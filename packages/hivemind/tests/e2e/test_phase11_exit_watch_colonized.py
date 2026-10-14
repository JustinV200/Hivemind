"""Phase 11 step 11.10 on a colonized device: the Warden that lives on it watches, writing nothing.

"Leave an enrolled device idle overnight: its Warden sits in `WATCH`, the device shows no writes
from HiveMind, the Patrol runs on schedule, and a planted anomaly (a new process eating CPU)
surfaces as an Alarm at the next Patrol while an uneventful hour records 'nothing notable'." Step
11.10 applies to every Real Cell, and `tests/e2e/test_phase11_exit_watch.py` proves it for a Level 0
device, whose Warden is on the Hive Stand and asks the packet. This is the same night for a device
whose Warden has moved onto it (Level 1, the rung a Nuc stands on): the Warden is local to the
machine, looks at it through a session that holds no lease, keeps what it sees in its own Bee Bread
on the device, and reviews it with its own model.

The device is a container enrolled on a whole Hive and colonized by the operator's own `hive swarm
promote`. `e2e.swarm.colony.watching` tells the story once, the night compressed to the Warden's
beat of fifteen seconds (a sample and a Patrol at every one): the idle device is Patrolled and every
Patrol is quiet; a planted CPU hog makes the next Patrol an ALARM while the operator has named no
directory, and what that Patrol covered holds no file; the operator names one with `hive swarm
watch`, the Queen finds that the Warden runs under the old list and restarts it under the new one,
and a second hog's Patrol covers the log lines of that directory and of none other; with the hog
gone the Patrols are quiet again. Each test below is one sentence of the criterion, asserted over
the record that run left, so one run of about six minutes proves all of them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one. The waits are the Hive's and the Warden's own beats, none of them a sleep
    that hopes.

Key invariants:
    - The claims are about what ran where and what was read, never about a number of seconds, but
      for the Patrols' spacing, which is a few beats at most.
    - The machine's words (the hog's name, a log line) are looked for on the device and are never
      on the Hive's trail.
    - Every container and network is removed, whatever the run did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", the overnight bullet; steps 11.10 and 11.6a.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the design and its amendment.
    - tests/e2e/swarm/colony/watching.py for the run and the reads used here.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from e2e.phase6_exit_helpers import payloads_text
from e2e.swarm.colony.models import LOCAL_SCENARIO, SCRIPTED_SERVER, WARDEN_MANIFEST
from e2e.swarm.colony.trail import named
from e2e.swarm.colony.watching import (
    APP_DIR,
    APP_LOG,
    BACK_LINE,
    ELSEWHERE_DIR,
    HOG_PATH,
    LOST_LINE,
    OUTSIDE_LOG,
    QUIET_AGAIN,
    QUIET_PATROLS,
    STARTED_LINE,
    Alarmed,
    WatchRun,
    run_watch_scenario,
)
from e2e.swarm.enrolled import DATA_DIR
from e2e.swarm.image import device_image_or_skip
from e2e.swarm.snapshots import unexplained
from e2e.watch_helpers import HOG_NAME

from hivemind.cli.in_cell.config import DEFAULT_HEARTBEAT_INTERVAL_S
from hivemind.pheromone import PheromoneEvent

pytestmark = pytest.mark.e2e

_BEAT_S = DEFAULT_HEARTBEAT_INTERVAL_S  # The beat a Warden on a device wakes and watches on.
_MIN_ENTRIES = 100  # Fewer files than this under the operator's directories means the look failed.
# What the container's layer holds besides the packet's directory, none of it the Warden's: what the
# test planted, the three files its harness copied in before the packet started, and the user unit
# `pollen install` wrote for the packet's own service.
_NOT_THE_WARDENS = (
    APP_DIR,
    ELSEWHERE_DIR,
    HOG_PATH,
    WARDEN_MANIFEST,
    LOCAL_SCENARIO,
    SCRIPTED_SERVER,
    "/root/.config",
)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> WatchRun:
    """Tell the whole story once; every test below asserts over what it left behind."""
    return run_watch_scenario(device_image_or_skip(), tmp_path_factory.mktemp("watch"))


def _anomalies(run: WatchRun) -> list[PheromoneEvent]:
    """The `PATROL_ANOMALY` Alarms the Queen holds, oldest first."""
    return [e for e in named(run.trail, "alarm.raised") if e.payload["kind"] == "PATROL_ANOMALY"]


def _read(alarmed: Alarmed) -> set[str]:
    """Every path a log line or a file change in the observations a Patrol covered names."""
    paths = {path for seen in alarmed.covered for path, _ in seen.lines}
    return paths | {path for seen in alarmed.covered for _, path in seen.changes}


def test_the_operator_sees_the_warden_on_the_device_watching_it(run: WatchRun) -> None:
    row = run.row

    # The Warden runs on the device (not on the Hive Stand), on its second rung, and its mode
    # reached the Hive through the trail it ships: the same WATCH the Level 0 device shows.
    assert (row["id"], row["warden_runs"], row["level"], row["mode"]) == (
        run.node.cell_id,
        "on_cell",
        "COLONIZED",
        "WATCH",
    )


def test_the_idle_device_is_patrolled_on_the_wardens_beat_and_every_patrol_is_quiet(
    run: WatchRun,
) -> None:
    assert len(run.quiet) >= QUIET_PATROLS
    assert [patrol.outcome for patrol in run.quiet] == ["NOTHING"] * len(run.quiet)
    gaps = [(later.at - earlier.at).total_seconds() for earlier, later in pairwise(run.quiet)]
    # On schedule: one Patrol a beat, neither bunched up nor skipped. A Warden wakes at its beat,
    # and a Patrol is due at every one because the manifest asks for one every two seconds.
    assert all(_BEAT_S / 2 <= gap <= 3 * _BEAT_S for gap in gaps), gaps
    # No Patrol Alarm before anything was planted: an uneventful stretch records nothing notable.
    [first, *_] = _anomalies(run)
    assert first.at > run.quiet[-1].at


def test_a_planted_hog_becomes_a_patrol_anomaly_alarm_at_the_next_patrol(run: WatchRun) -> None:
    first, second = run.unnamed, run.named

    # The Patrol that was shown the hog by name decided ALARM, the Queen holds exactly one Alarm
    # for it, and once the hog is gone the Patrols after are quiet again.
    assert first.patrol.outcome == "ALARM" and first.anomalies == 1
    assert second.patrol.outcome == "ALARM" and second.anomalies == 2
    assert first.patrol.at > run.quiet[-1].at
    assert any(HOG_NAME in seen.processes for seen in first.covered)
    assert any(HOG_NAME in seen.processes for seen in second.covered)
    assert len(run.later) >= QUIET_AGAIN
    assert [patrol.outcome for patrol in run.later] == ["NOTHING"] * len(run.later)
    assert len(_anomalies(run)) == 2


def test_the_warden_reads_no_file_until_the_operator_names_a_directory(run: WatchRun) -> None:
    covered = run.unnamed.covered

    # What the first hog's Patrol covered is the device's processes and resources, and not one log
    # line or file change: with no directory named the Warden has none to read, the log on the
    # device notwithstanding.
    assert covered, "the Patrol covered no observation"
    assert _read(run.unnamed) == set()
    assert all(not seen.lines and not seen.changes for seen in covered)


def test_a_log_line_in_the_named_directory_reaches_an_observation_and_no_other_does(
    run: WatchRun,
) -> None:
    lines = {line for seen in run.named.covered for line in seen.lines}

    # After the Queen restarted the Warden under the directory, the lines of its log are in what the
    # next Patrol covered, the new one among them; the log in the directory nobody named is not.
    assert {(APP_LOG, STARTED_LINE), (APP_LOG, LOST_LINE), (APP_LOG, BACK_LINE)} <= lines
    assert _read(run.named)
    assert all(path.startswith(f"{APP_DIR}/") for path in _read(run.named)), _read(run.named)
    assert OUTSIDE_LOG not in _read(run.named)
    assert run.starts == 2  # Once when it moved onto the device, once when the directory changed.
    assert run.row["level"] == "COLONIZED"  # Renewed in place: the device did not fall back.
    decided = [e.payload.get("reason") for e in named(run.trail, "queen.decided")]
    assert "renew_failed" not in decided


def test_the_patrol_is_an_episode_of_the_wardens_own_and_nothing_of_it_is_on_the_hives_trail(
    run: WatchRun,
) -> None:
    patrols = len(run.quiet) + 2 + len(run.later)

    # One model call of the Warden's own binding for each Patrol it ran, recorded in its own trail,
    # and the machine's words (a process name, a log line) are nowhere on the Hive's.
    assert run.calls >= patrols
    shown = payloads_text(run.trail)
    for words in (HOG_NAME, STARTED_LINE, LOST_LINE, BACK_LINE, APP_LOG):
        assert words not in shown, words


def test_the_device_is_left_as_found(run: WatchRun) -> None:
    made = sorted(run.after.changed - run.before.changed)
    leased, released = run.leases

    # The Hive sent the packet no command; the Warden never held a lease while it watched (it leased
    # once for each start and gave each back); the lease's scratch is gone; and the operator's
    # directories are as they were.
    assert len(run.before.files) > _MIN_ENTRIES, "the snapshot of the device's own files is empty"
    assert not named(run.trail, "swarm.command_sent")
    assert leased == released == run.starts
    assert run.after.scratch == ()
    assert run.after.files == run.before.files
    # Since it began to watch, the container's layer gained only what the Warden keeps in the
    # packet's directory (its log, say, rotating); and in all its life nothing outside that
    # directory is the Warden's: the rest is the test's, its harness's and the packet's own install.
    assert unexplained(made, DATA_DIR) == []
    assert unexplained(sorted(run.after.changed), DATA_DIR, *_NOT_THE_WARDENS) == []
