"""Phase 11 exit criterion 10 on a second machine: a Drone looks into every lost link, on both ends.

"Cut a Nuc's link with a planted, reversible problem (e.g. a wrong resolver entry) within reach of
its access level: its diagnostic Drone finds and fixes it inside the `reconnect_budget`, and the
task completes with no Queen or human involvement. Cut it with an unfixable problem (egress
genuinely gone): the Drone gives up inside budget and falls through to 11.9's existing Clustering
path unchanged. Symmetrically, unplug a colonized device: `W0` on the Hive Stand spawns its own
diagnostic Drone and deposits a Nectar finding before the grace period's existing 'then decide'
fires." Each sentence is a case, and each case is a container enrolled on its own Hive and its own
docker network, climbed by the operator's own `hive swarm promote`, with a task's Drone at work.
`e2e.swarm.diagnose` tells each story once, in order, and returns the record it left (the Hive's
whole trail, the Nectar the Hive stored, the tasks the Queen holds, the device's own file where the
cable made the Hive deaf); each test below is one claim of the criterion asserted over that record,
so a failure names the claim that broke. The Drones are the real role running real commands in a
real container (or on the Hive Stand); only their choice of commands is scripted, because the
containers have no model.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one. The waits are the Hive's own beats, the redial schedule's backoff and the
    websocket keepalive (a cable pulled is found out after about forty-five seconds); none sleeps.

Key invariants:
    - The claims are about order, count and kind over the Hive's whole trail, never about a
      particular number of seconds except the three the criterion states: inside the budget,
      Clustering at the offline limit, and before the grace ends.
    - After the fault the stories issue no operator command and write nothing to the device: the
      only writers are the Drones the Hive's own Wardens send.
    - Every container and network is removed, whatever the run did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", its last bullet, and steps 11.9 and 11.9a.
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for the Drone and its budget.
    - tests/e2e/swarm/diagnose/ for the rig, and test_hive_stand_diagnosis.py for the Hive Stand's
      Drone in process.
"""

from __future__ import annotations

import pytest
from e2e.swarm.colony.trail import about, bees_of, named
from e2e.swarm.diagnose import WRONG_ADDRESS, findings_of, for_device, outcome_of, run_case
from e2e.swarm.diagnose import reversible as rev
from e2e.swarm.diagnose import unfixable as gone_case
from e2e.swarm.diagnose import unplugged as stand
from e2e.swarm.diagnose.reversible import Mended
from e2e.swarm.diagnose.scripts import CHECKED
from e2e.swarm.diagnose.unfixable import Gone
from e2e.swarm.diagnose.unplugged import Unplugged
from e2e.swarm.image import device_image_or_skip

from hivemind.cell import RequestOrigin
from hivemind.pheromone import PheromoneEvent
from waggle.messages.swarm import RuntimeLevel

pytestmark = pytest.mark.e2e

_UNMADE = frozenset({"task.failed", "task.cancelled", "task.unassigned"})  # A task that was lost.
# What the Queen records about a device that was away and came back, none of it a decision about
# its work: that a look from the Hive Stand's end was asked for, and that the hold on it ended.
_BOOKKEEPING = frozenset({("device_diagnosis", "task_created"), ("hold_released", "reconnected")})
_LOOK = "Check reachability of device"  # What the Hive Stand's own look opens its objective with.
# How late, in seconds, a deadline the Warden keeps may be met: the scheduling of its tick on a busy
# host, and the few writes it makes before it stamps the time. A deadline it only checked on its
# heartbeat (15 s) or on a frame would miss this.
_SLACK_S = 2.0


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Mended:
    """Tell the reversible case once; every test below asserts over what it left behind."""
    directory = tmp_path_factory.mktemp("reversible")
    return run_case(device_image_or_skip(), directory, rev.reversible_setup(), rev.mended)


def _after_cut(run: Mended) -> list[PheromoneEvent]:
    """Everything the Hive's trail recorded from the moment the links were reset."""
    return [event for event in run.trail if event.at >= run.fault.cut_at]


def test_the_planted_fault_cut_the_link_and_only_the_name_was_wrong(run: Mended) -> None:
    offline = named(for_device(_after_cut(run), run.node), "warden.offline")

    # The name resolved to the Hive Stand before, to an address nothing answers on after the plant,
    # and the Warden found its link gone: the fault was real, and the name was all of it.
    assert run.fault.before == run.gateway
    assert run.fault.broken == WRONG_ADDRESS
    assert len(offline) == 1


def test_the_nucs_drone_finds_and_fixes_it_inside_the_reconnect_budget(run: Mended) -> None:
    after = for_device(_after_cut(run), run.node)
    [offline] = named(after, "warden.offline")
    [verdict] = named(after, "warden.diagnosed")
    lines = [line.split() for line in run.hosts.splitlines() if "hive.test" in line]

    # One Drone, and it was enough: the link came back and a cause was named, so FIXED.
    assert verdict.payload["outcome"] == "FIXED" and verdict.payload["reason"] == "link_back"
    assert verdict.payload["attempts"] == 1
    # Inside the budget, by the Warden's own count and by the name coming right on the device.
    assert float(str(verdict.payload["spent_s"])) < rev.BUDGET_S
    assert (run.mended_at - offline.at).total_seconds() < rev.BUDGET_S
    # What the Drone wrote is the one line the name needs, and no other line mentions it.
    assert lines == [[run.gateway, "hive.test"]]


def test_the_drone_is_the_wardens_own_and_the_queen_never_knew_it(run: Mended) -> None:
    after = for_device(_after_cut(run), run.node)
    spawned = named(after, "worker.spawned")
    [drone] = {str(event.payload["task_id"]) for event in spawned}

    # The Warden minted its task and its grant itself, from its own pool: the Queen never
    # submitted, assigned or granted it, and it is not the task whose Drone was at work.
    assert drone != run.task
    mine = {event.kind for event in about(run.trail, drone)}
    assert not {"task.submitted", "task.assigned", "forage.granted"} & mine


def test_the_task_completes_with_no_queen_or_human_involvement(run: Mended) -> None:
    after = _after_cut(run)
    mine = about(run.trail, run.task)
    decided = [
        (str(e.payload["reason"]), str(e.payload["detail"]))
        for e in named(after, "queen.decided")
        if e.subject_id == run.node.device_id
    ]
    others = [task for task in run.tasks if str(task.id) != run.task]

    # It ended once, under its own id, on the bee it had: never failed, cancelled or re-placed.
    assert len(named(mine, "task.succeeded")) == 1 and not _UNMADE & {e.kind for e in mine}
    assert len(bees_of(run.trail, run.task)) == 1
    guard = [e for e in after if e.kind.startswith("guard.")]

    # The Queen decided nothing about the device (her grace never ran out); no human acted.
    assert set(decided) <= _BOOKKEEPING
    assert [e for e in after if e.actor == "human"] == []
    # The Guard was asked for no exception and refused the Nuc and its Drones nothing. All it
    # recorded is the Queen fitting the grant of the Hive Stand's look to the ceiling that look
    # was given (each slot the ceiling leaves out is a denial at `grant_issue`).
    assert [e for e in guard if e.node_id == run.node.node_id] == []
    assert {(e.kind, e.payload["point"]) for e in guard} <= {("guard.denied", "grant_issue")}
    # And no task was made to repair it: any other task the Queen holds is the Hive Stand's own
    # look from its end, pinned there and told to change nothing.
    assert all(
        task.spec.objective.startswith(_LOOK)
        and "cell:hive_stand" in (task.spec.capabilities or ())
        for task in others
    )


def test_the_findings_reach_the_hive_as_nectar_after_the_link_is_back(run: Mended) -> None:
    back = [
        e for e in named(_after_cut(run), "warden.reconnected") if e.node_id == run.node.node_id
    ]
    [finding] = findings_of(run.arrivals, run.node)

    # The Warden's report of why the link went is the Hive's to read once it can be sent: stored
    # after the reconnection, with the cause the Drone named and the verdict the Warden judged.
    assert back and finding.event.at >= back[0].at
    assert "FIXED" in run.findings and "CAUSE:" in run.findings
    assert run.gateway in run.findings or "hive.test" in run.findings


# ──────────────────────────────────────────────────────────────────────────────
# Unfixable: the network is gone
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def lost(tmp_path_factory: pytest.TempPathFactory) -> Gone:
    """Tell the unfixable case once; every test below asserts over what it left behind."""
    directory = tmp_path_factory.mktemp("unfixable")
    return run_case(device_image_or_skip(), directory, gone_case.unfixable_setup(), gone_case.gone)


def _device_window(lost: Gone) -> list[PheromoneEvent]:
    """What the device's Warden recorded between the cable being pulled and being put back."""
    own = for_device(lost.trail, lost.node)
    return [e for e in own if lost.cut_at <= e.at < lost.back_at]


def test_the_drone_gives_up_inside_the_budget_when_the_network_is_gone(lost: Gone) -> None:
    window = _device_window(lost)
    [offline] = named(window, "warden.offline")
    [verdict] = named(window, "warden.diagnosed")
    [drone] = named(window, "worker.spawned")
    [ended] = [e for e in named(window, "worker.done") if e.subject_id == drone.subject_id]

    # One Drone looked, named what it saw and could do nothing about it: DIAGNOSED, not FIXED.
    assert verdict.payload["outcome"] == "DIAGNOSED" and verdict.payload["reason"] == "cause_known"
    assert verdict.payload["attempts"] == 1
    # The Drone gave up well inside the budget, and the Warden's whole search ended with it: its
    # own count of what it spent, and the trail's, are the budget and not a heartbeat more.
    assert (ended.at - offline.at).total_seconds() < gone_case.BUDGET_S
    assert float(str(verdict.payload["spent_s"])) < gone_case.BUDGET_S + _SLACK_S
    assert (verdict.at - offline.at).total_seconds() < gone_case.BUDGET_S + _SLACK_S


def test_the_warden_then_follows_the_existing_path_and_clusters_at_its_limit(lost: Gone) -> None:
    window = _device_window(lost)
    states = [e.kind for e in window if e.kind.startswith("warden.")]
    [offline] = named(window, "warden.offline")
    [clustered] = named(window, "warden.clustered")
    over = (clustered.at - offline.at).total_seconds()

    # The same two states in the same order as with no Drone at all, the verdict between them.
    assert states == ["warden.offline", "warden.diagnosed", "warden.clustered"]
    # Full Clustering came when `max_offline_s` ran out: the search did not shorten the wait and
    # did not stretch it. (`offline.at` is a moment after the clock of the outage began.)
    assert abs(over - gone_case.OFFLINE_S) < _SLACK_S
    # It paused the one bee of the task and none other: the Drone was not a task of the Queen's.
    assert clustered.payload["paused"] == 1


def test_the_findings_wait_in_the_outbox_and_arrive_once_the_cable_is_back(lost: Gone) -> None:
    [finding] = findings_of(lost.arrivals, lost.node)

    # Queued while the link was down, not sent in a gap ...
    assert ("honey.nectar_deposit", None) in lost.queued
    # ... and stored only after the Warden had reconnected, with the verdict and what the Drone saw.
    assert finding.event.at >= lost.back_at
    assert "DIAGNOSED" in lost.findings and "CAUSE:" in lost.findings


# ──────────────────────────────────────────────────────────────────────────────
# The Hive Stand's end: a colonized device is unplugged
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def unplugged(tmp_path_factory: pytest.TempPathFactory) -> Unplugged:
    """Tell the Hive Stand's case once; every test below asserts over what it left behind."""
    directory = tmp_path_factory.mktemp("unplugged")
    return run_case(device_image_or_skip(), directory, stand.unplugged_setup(), stand.unplugged)


def _since_unplugged(run: Unplugged) -> list[PheromoneEvent]:
    """Everything the Hive's trail recorded from the moment the cable was pulled.

    The enrolment has a hold of its own (a device whose packet has not dialled yet is held a
    moment, with no task), so a claim about *the* hold reads only what came after the cut.
    """
    return [event for event in run.trail if event.at >= run.cut_at]


def _look_of(run: Unplugged) -> str:
    """The id of the task the Queen made to look into the device from the Hive Stand's end."""
    [look] = [t for t in run.tasks if t.spec.objective.startswith(f"{_LOOK} {run.node.cell_id}")]
    return str(look.id)


def test_the_hold_makes_the_hive_stands_look_for_a_device_that_holds_a_task(
    unplugged: Unplugged,
) -> None:
    run = unplugged
    [look] = [t for t in run.tasks if str(t.id) == _look_of(run)]
    after = _since_unplugged(run)
    [held] = [e for e in named(after, "swarm.node_held") if e.subject_id == run.node.device_id]
    made = [
        e
        for e in named(after, "queen.decided")
        if e.subject_id == run.node.device_id and e.payload.get("detail") == "task_created"
    ]

    # The device was colonized and holding a task; the hold made one task, the Queen's own, pinned
    # to the Hive Stand and run by W0, a Warden other than the device's, on a Cell other than its.
    assert held.payload["level"] == RuntimeLevel.COLONIZED.value and held.payload["tasks"] == 1
    assert [e.payload["task_id"] for e in made] == [str(look.id)]
    assert look.spec.origin is RequestOrigin.QUEEN and "cell:hive_stand" in (
        look.spec.capabilities or ()
    )
    assert look.warden_id != run.node.warden_id and look.cell_id != run.node.cell_id


def test_w0_runs_its_own_drone_and_the_look_succeeds(unplugged: Unplugged) -> None:
    run = unplugged
    mine = about(run.trail, _look_of(run))
    [look] = [t for t in run.tasks if str(t.id) == _look_of(run)]
    bees = [e for e in named(mine, "worker.spawned") if e.node_id != run.node.node_id]

    # A Drone of the Hive Stand's own Warden did the looking, and said what it saw.
    assert len(bees) == 1 and bees[0].payload["role"] == "DRONE"
    assert len(named(mine, "task.succeeded")) == 1
    assert look.outcome is not None and CHECKED in look.outcome.summary


def test_the_finding_is_deposited_as_nectar_before_the_hold_decides(unplugged: Unplugged) -> None:
    run = unplugged
    look, after = _look_of(run), _since_unplugged(run)
    [held] = [e for e in named(after, "swarm.node_held") if e.subject_id == run.node.device_id]
    [deposit] = outcome_of(run.arrivals, look)
    [decided] = [
        e
        for e in named(after, "queen.decided")
        if e.subject_id == run.node.device_id and e.payload.get("reason") == "unreachable_decision"
    ]
    succeeded = named(about(run.trail, look), "task.succeeded")[0]

    # The order the criterion states, on the trail: the look is done, its finding is stored, and
    # only then does the grace end and the Queen decide (a colonized device's tasks are re-placed).
    assert succeeded.at <= deposit.event.at < decided.at
    assert (decided.at - held.at).total_seconds() >= stand.GRACE_S
    assert decided.payload["detail"] == "replace"
