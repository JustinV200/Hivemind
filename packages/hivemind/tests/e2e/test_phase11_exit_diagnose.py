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
from e2e.swarm.diagnose import WRONG_ADDRESS, findings_of, for_device, run_case
from e2e.swarm.diagnose import reversible as rev
from e2e.swarm.diagnose.reversible import Mended
from e2e.swarm.image import device_image_or_skip

from hivemind.pheromone import PheromoneEvent

pytestmark = pytest.mark.e2e

_UNMADE = frozenset({"task.failed", "task.cancelled", "task.unassigned"})  # A task that was lost.
# What the Queen records about a device that was away and came back, none of it a decision about
# its work: that a look from the Hive Stand's end was asked for, and that the hold on it ended.
_BOOKKEEPING = frozenset({("device_diagnosis", "task_created"), ("hold_released", "reconnected")})
_LOOK = "Check reachability of device"  # What the Hive Stand's own look opens its objective with.


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
