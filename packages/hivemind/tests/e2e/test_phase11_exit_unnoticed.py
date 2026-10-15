"""Phase 11 exit criterion 2, the part a cable's patience hides: a result sent unnoticed.

"Cut the network between the two machines mid-task: ... the trail segment and results arrive after
reconnection, and nothing is duplicated." The Nuc scenario proves it for a result the Warden queued
once it knew its link was gone. This one proves it for the result a Nuc sends in the seconds after
the cable is pulled, while its connection still looks alive: a websocket whose far end vanished is
declared dead only after a ping interval and a timeout (twenty to forty seconds), and a frame sent
in that time returns as sent and is lost, with no failure for the outbox to queue it on.

The device is a container enrolled on a whole Hive (the real Queen, gate, Wardens and trail),
climbed to a Nuc as the Nuc scenario climbs it. `e2e.swarm.colony.unnoticed` tells the story once:
a first task is put to it and ends, its result heard in the ordinary way; a second is put, and as
its Drone's one command of ten seconds begins the cable is pulled with `docker network
disconnect`; the command ends, the Warden sends the result into the connection nobody knows is
dead, and when the keepalive gives up the Warden notices and the cable is put back. Each test below
is one sentence of the criterion, asserted over the record that run left, so one run of about a
minute and a half proves all of them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one. The waits are the Hive's own beats and the websocket keepalive, none of
    them a sleep that hopes; the one short settle at the end is for a duplicate that would show.

Key invariants:
    - The claims are about order and count over the Hive's whole trail, the frames the Queen's door
      heard and the refusals her replay window made, never about a particular number of seconds
      beyond the two that make the scenario what it is: the result was sent after the pull and
      before the loss was noticed.
    - The cut is the cable, not a message, and the cable is put back by the same command.
    - Every container and network is removed, whatever the run did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", its second bullet.
    - docs/adr/0053 (the amendment on what was sent while the loss went unnoticed) and 0055.
    - tests/e2e/test_phase11_exit_nuc.py for the cut the Warden notices before its task ends.
"""

from __future__ import annotations

import pytest
from e2e.swarm.colony.models import LOCAL_PROVIDER
from e2e.swarm.colony.orders import Frame
from e2e.swarm.colony.trail import (
    about,
    bees_of,
    ends_of,
    grant_of,
    llm_calls,
    named,
    peak_bees,
    recorded_twice,
    repeated,
)
from e2e.swarm.colony.unnoticed import UnnoticedRun, run_unnoticed_scenario
from e2e.swarm.image import device_image_or_skip

from waggle.transport.limits import RefusalKind
from waggle.transport.recent import UNNOTICED_S

# A whole Hive with a device container: e2e, and integration because it needs Docker (codingrules
# 14.2), so integration.yml runs it and ci.yml's ten-minute e2e job leaves it out.
pytestmark = [pytest.mark.e2e, pytest.mark.integration]

_UNMADE = frozenset({"task.failed", "task.cancelled", "task.unassigned"})  # A task that was lost.
_GUARD = ("guard.replay_refused", "guard.frame_ceiling", "guard.segment_refused")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> UnnoticedRun:
    """Tell the whole story once; every test below asserts over what it left behind."""
    return run_unnoticed_scenario(device_image_or_skip(), tmp_path_factory.mktemp("unnoticed"))


def _the_result_of(run: UnnoticedRun, task: str) -> Frame:
    """The one `task.result` frame the Queen's door heard for `task`; the test fails otherwise."""
    heard = run.tap.of("task.result", task)
    assert len(heard) == 1, f"the Queen's door heard {len(heard)} results for {task}, not one"
    return heard[0]


def test_the_cable_is_pulled_under_a_task_that_is_still_running_on_the_device(
    run: UnnoticedRun,
) -> None:
    started = bees_of(run.trail, run.second)
    grant = grant_of(run.trail, run.second)
    calls = llm_calls(run.trail, grant_id=str(grant), provider=LOCAL_PROVIDER, slot="WORKER")
    ended = ends_of(run.trail, run.second)

    # The second task's bee had started and thought on the device's own model; its command, which
    # takes ten seconds, was the thing the cable was pulled under, and it ended after the pull.
    assert len(started) == 1 and started[0].at < run.cut_at
    assert [c for c in calls if c.at < run.cut_at]
    assert len(ended) == 1 and ended[0].kind == "worker.done" and ended[0].at > run.cut_at
    # The first task was over, and its result heard, before the cable was touched.
    assert _the_result_of(run, run.first).heard_at < run.cut_at


def test_the_result_is_sent_after_the_pull_and_before_the_loss_is_noticed(
    run: UnnoticedRun,
) -> None:
    result = _the_result_of(run, run.second)
    [end] = ends_of(run.trail, run.second)

    # The bee ended on the device with the cable out, and the Warden sent its result then: after the
    # pull, and before it had recorded that its link was gone. It sent it on the live link, which
    # is the only way to be in the stretch the keepalive hides, so the outbox never held it.
    assert run.cut_at < end.at < run.noticed_at
    assert run.cut_at < result.sent_at < run.noticed_at
    assert ("task.result", run.second) not in run.queued


def test_the_result_is_not_heard_until_the_cable_is_back_and_then_it_is_heard_once(
    run: UnnoticedRun,
) -> None:
    result = _the_result_of(run, run.second)

    # Into a dead connection it went, and nothing came out the other end: the Queen's door heard
    # it after the cable returned and not before. Once, however many times the Warden sent it.
    assert result.heard_at >= run.back_at
    assert result.envelope_id and len(run.tap.of("task.result", run.second)) == 1


def test_the_task_completes_once_after_the_return_and_is_not_run_again(run: UnnoticedRun) -> None:
    mine = about(run.trail, run.second)
    done = named(mine, "task.succeeded")

    # Held by the Queen while the device was out of reach, and finished by the result when it came:
    # not failed, not cancelled, not placed anywhere else, and the one bee that ran it ran it once.
    assert len(done) == 1 and done[0].at >= run.back_at
    assert not _UNMADE & {e.kind for e in mine}
    assert len(bees_of(run.trail, run.second)) == 1
    assert peak_bees(run.trail, run.second) == 1


def test_what_the_warden_sent_again_but_the_queen_held_was_dropped_by_her_window_unseen(
    run: UnnoticedRun,
) -> None:
    held = _the_result_of(run, run.first)
    second = _the_result_of(run, run.second)
    dropped = {d.envelope_id for d in run.dropped}

    # The first task's result was sent less than a keepalive's patience before the loss, so the
    # Warden sent it again with the rest of its last stretch; the Queen had it, so her window
    # dropped the copy, and inside the reconnection grace, so there is nothing on the trail.
    assert held.heard_at < run.cut_at
    assert (run.noticed_at - held.sent_at).total_seconds() < UNNOTICED_S
    assert held.envelope_id in dropped
    # The result she did not hold was no duplicate: it was handled, and only what she held fell.
    assert second.envelope_id not in dropped
    assert not named(run.trail, *_GUARD)
    # Nothing was a duplicate until the cable came back, and all of it was this device's: the
    # Warden's result and, when its packet had sent a release in that stretch too, the packet's.
    assert all(d.at >= run.back_at and d.kind is RefusalKind.REPLAY for d in run.dropped)
    assert {d.node_id for d in run.dropped} == {run.node.node_id}


def test_nothing_else_is_duplicated(run: UnnoticedRun) -> None:
    tasks = (run.first, run.second)
    once = ("task.succeeded", "task.failed", "worker.spawned", "worker.started")
    heard = [frame.envelope_id for frame in run.tap.frames]

    # One result said per task, one end per task, no bee started twice, no event recorded twice
    # whichever way it came, and no frame handled twice: every id the Queen's door heard is its own.
    assert [len(run.tap.of("task.result", task)) for task in tasks] == [1, 1]
    assert [len(named(about(run.trail, t), "task.succeeded")) for t in tasks] == [1, 1]
    assert repeated(run.trail, once) == []
    assert recorded_twice(run.trail) == []
    assert [peak_bees(run.trail, task) for task in tasks] == [1, 1]
    assert len(heard) == len(set(heard))
