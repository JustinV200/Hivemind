"""Phase 11 exit criterion 2 on a second machine: a Warden moves onto it, it is a Nuc, it is cut.

"Move its Warden onto it (Level 1) without the running task failing; then promote it to a Nuc; cut
the network between the two machines mid-task: the task completes on the device using only its local
pool, a deliberately high-bar subtask that needs a shared source waits in the outbox rather than
failing, the trail segment and results arrive after reconnection, and nothing is duplicated. The
Nuc's Warden loads an allowlisted model within its ceiling with no request on the trail, and is
refused when it tries one outside the allowlist."

The device is a container enrolled on a whole Hive (the real Queen, gate, Wardens and trail) that
listens on the docker network's host address, with the Hive runtime in a wheelhouse the packet
installs from, and a model server the device's manifest declares and its Warden starts when the
Queen says. `e2e.swarm.colony.run` tells the story once, in order, on that one device: a long task
runs at Level 0; the operator's `hive swarm promote` moves the Warden onto the device and the task
goes on from its Handoff; a second `promote` makes it a Nuc; a goal of two tasks is put to it, one
whose Drone thinks on the device's own model and one whose bar only a shared source clears; the
cable is pulled with `docker network disconnect` and put back; the Warden is then ordered to load a
model the Hive allows and one it does not. Each test below is one sentence of the criterion,
asserted over the record that run left, so one run of about six minutes proves all of them.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Needs a docker daemon and skips
    cleanly without one. The waits are the Hive's own beats and the websocket keepalive (a link
    whose far end vanished is declared dead after about forty-five seconds), none of them sleeps.

Key invariants:
    - The criteria are claims about order and count over the Hive's whole trail, the Queen's door
      and the device's outbox, never about a particular number of seconds.
    - The two promotions are the operator's own command; the cut is the cable, not a message.
    - Every container and network is removed, whatever the run did.

See Also:
    - .claude/roadmap.md phase 11 "Exit criteria", its second bullet.
    - docs/adr/0052 and 0053 for the ladder and for what a cut-off Warden keeps doing.
    - tests/e2e/swarm/colony/ for the run, the manifests and the reads used here.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from e2e.swarm.colony.models import (
    ALLOWED,
    EXTRA,
    LOCAL_PROVIDER,
    ROGUE,
    SERVER_ID,
    SHARED_PROVIDER,
    SMALL,
)
from e2e.swarm.colony.run import NucRun, run_nuc_scenario
from e2e.swarm.colony.trail import (
    about,
    bees_of,
    between,
    ends_of,
    grant_of,
    llm_calls,
    named,
    peak_bees,
    recorded_twice,
    repeated,
)
from e2e.swarm.image import device_image_or_skip

from hivemind.manifest import load_manifest
from hivemind.manifest.device import load_device_manifest
from hivemind.pheromone import PheromoneEvent
from waggle.messages.swarm import NucAction, NucOutcome, RuntimeLevel

# A whole Hive with a device container: e2e, and integration because it needs Docker (codingrules
# 14.2), so integration.yml runs it and ci.yml's ten-minute e2e job leaves it out.
pytestmark = [pytest.mark.e2e, pytest.mark.integration]

_UNMADE = frozenset({"task.failed", "task.cancelled", "task.unassigned"})  # A task that was lost.
_RESUME_S = 60.0  # How soon after the link is back a held task starts again (it takes about ten).


@pytest.fixture(scope="module")
def nuc(tmp_path_factory: pytest.TempPathFactory) -> NucRun:
    """Tell the whole story once; every test below asserts over what it left behind."""
    return run_nuc_scenario(device_image_or_skip(), tmp_path_factory.mktemp("nuc"))


def _on_device(run: NucRun) -> list[PheromoneEvent]:
    """The events the device's own Warden recorded (they reached the Hive's trail by segment)."""
    return [e for e in run.trail if e.node_id == run.node.node_id]


def _first(events: Iterable[PheromoneEvent], kind: str, **payload: object) -> PheromoneEvent:
    """The first event of `kind` with `payload`; the test fails if the trail holds none."""
    found = [
        e for e in named(events, kind) if all(e.payload.get(k) == v for k, v in payload.items())
    ]
    assert found, f"the trail holds no {kind} {payload}"
    return found[0]


def test_a_long_task_is_running_on_the_device_at_level_0(nuc: NucRun) -> None:
    moved = _first(nuc.trail, "warden.migrated")
    first_bee = bees_of(nuc.trail, nuc.long)[0]
    before = [e for e in nuc.trail if e.at < moved.at]

    # The device had no Warden: its first bee was supervised from the Hive Stand, and had been at
    # work (model calls, so commands run) when the operator moved the Warden. The task was not over.
    assert first_bee.node_id == moved.payload["from_node"] != nuc.node.node_id
    assert first_bee.at < moved.at
    assert len(llm_calls(before, goal_id=nuc.long)) >= 2
    assert not named(about(before, nuc.long), "task.succeeded")


def test_the_warden_moves_onto_the_device_and_the_running_task_goes_on_from_its_handoff(
    nuc: NucRun,
) -> None:
    mine = about(nuc.trail, nuc.long)
    kinds = [e.kind for e in mine]
    paused, resumed, done = (
        kinds.index(k) for k in ("task.paused", "task.resumed", "task.succeeded")
    )

    assert not _UNMADE & set(kinds)  # The task did not fail, was not cancelled, was not re-placed.
    assert paused < resumed < done
    assert "memory.checkpoint" in kinds[:resumed]  # Its bee wrote a Handoff before it stopped.
    started = [(i, e) for i, e in enumerate(mine) if e.kind == "worker.started"]
    assert all(e.node_id != nuc.node.node_id for i, e in started if i < paused)
    # The bee that took it up was the device's own Warden's, and it was the only one: it ran on.
    assert [e.node_id for i, e in started if i > resumed] == [nuc.node.node_id]
    assert (
        _first(nuc.trail, "swarm.promoted", to_level="COLONIZED").payload["from_level"] == "GATEWAY"
    )


def test_the_warden_moves_first_and_keeps_its_id(nuc: NucRun) -> None:
    warden = _first(nuc.trail, "swarm.enrolled").payload["warden_id"]
    moved = _first(nuc.trail, "warden.migrated")
    sources = [
        e for e in named(nuc.trail, "forage.source_added") if e.payload["reporter"] == "warden"
    ]

    # The Warden that served the device from the Hive Stand is the one that serves its models.
    assert moved.subject_id == warden and moved.payload["to_node"] == nuc.node.node_id
    assert sources and {e.payload["warden_id"] for e in sources} == {warden}
    assert {e.node_id for e in sources} == {nuc.node.node_id}  # None was started by the Hive Stand.


def test_the_promotion_runs_the_frames_in_order(nuc: NucRun) -> None:
    ask = nuc.tap.orders[0]
    colonized = _first(nuc.trail, "swarm.promoted", to_level="COLONIZED")
    added = [
        f
        for f in nuc.tap.of("forage.source_added")
        if ask.asked_at <= f.heard_at <= ask.answered_at
    ]
    said = nuc.tap.of("swarm.nuc_promoted")[0]
    promoted = _first(nuc.trail, "swarm.promoted", to_level="NUC")
    plan = _first(nuc.trail, "forage.plan_written", revision=1)

    # The Warden declared the server in its capacity report; the Queen set the ceilings; she then
    # ordered it; the Warden said a new source per model and only then that it had promoted.
    assert nuc.tap.of("forage.capacity_report")[0].heard_at < ask.asked_at
    assert [
        e for e in named(nuc.trail, "forage.ceilings_set") if colonized.at < e.at <= ask.asked_at
    ]
    order = ask.order
    assert (order.action, order.server_id, order.initial_models) == (
        NucAction.PROMOTE,
        SERVER_ID,
        ALLOWED,
    )
    assert order.deadline_s > 0
    assert len(added) == len(ALLOWED) and max(f.heard_at for f in added) <= said.heard_at
    assert (ask.reply.outcome, ask.reply.level) == (NucOutcome.PROMOTED, RuntimeLevel.NUC)
    assert ask.reply.loaded_models == ALLOWED
    # The trail has the rung, then the plan that hosts the new sources.
    assert said.heard_at <= promoted.at < plan.at
    # No request on the trail for any of it: the Warden loaded what the Queen allowed, unasked.
    assert not named(between(nuc.trail, ask.asked_at, plan.at), "forage.requested")


def test_the_hive_lists_the_server_without_starting_it_and_the_device_maps_what_it_loads(
    nuc: NucRun,
) -> None:
    hive = load_manifest(nuc.directory / "hive.toml", {})
    device = load_device_manifest(nuc.directory / "warden.toml")

    server = hive.llm.servers[SERVER_ID]
    assert server.load_on_start is False  # The Hive Stand starts none; the device's Warden does.
    assert tuple(server.sources) == ALLOWED and ROGUE not in hive.forage.map
    assert {*ALLOWED, ROGUE} <= set(device.forage.map)  # Rows for everything its server could load.
    loaded = {e.payload["source_id"] for e in named(_on_device(nuc), "forage.source_added")}
    assert loaded >= set(ALLOWED)  # And it did load them, so each was a row it could resolve.


def test_a_local_drone_takes_no_grant_slot_and_the_bees_stay_within_the_ceiling(
    nuc: NucRun,
) -> None:
    queens = {nuc.long, nuc.ordinary, nuc.exacting}
    device = _on_device(nuc)
    local = {e.payload["task_id"] for e in named(device, "worker.spawned")} - queens
    ceilings = [e for e in named(nuc.trail, "forage.ceilings_set") if e.at < nuc.cut_at]

    # The cut-off Warden sent a Drone of its own to find out why its link went (ADR-0056). It is
    # no task of the Queen's: she never placed or granted it, so it holds no slot of any grant.
    assert local
    for task in local:
        assert not {"task.assigned", "forage.granted", "task.submitted"} & {
            e.kind for e in about(nuc.trail, str(task))
        }
    limit = ceilings[-1].payload["new_max_sub_bees"]
    assert isinstance(limit, int) and peak_bees(device) <= limit


def test_the_task_completes_on_the_device_while_cut_off_using_only_its_local_pool(
    nuc: NucRun,
) -> None:
    ends = ends_of(nuc.trail, nuc.ordinary)
    grant = grant_of(nuc.trail, nuc.ordinary)
    calls = [
        c for c in llm_calls(nuc.trail, grant_id=str(grant)) if nuc.cut_at <= c.at < nuc.back_at
    ]
    [frame] = nuc.tap.of("task.result", nuc.ordinary)

    # Finished on the device with the cable out: its last bee ended in the outage, without failing,
    # on the model the device serves; the result sat in the outbox and reached the Queen after.
    assert ends and {e.kind for e in ends} == {"worker.done"}
    assert nuc.cut_at < max(e.at for e in ends) < nuc.back_at
    assert len(calls) >= 3 and {c.provider for c in calls} == {LOCAL_PROVIDER}
    assert ("task.result", nuc.ordinary) in nuc.queued
    assert frame.heard_at >= nuc.back_at


def test_a_subtask_that_needs_a_shared_source_waits_in_the_outbox_rather_than_failing(
    nuc: NucRun,
) -> None:
    grant = grant_of(nuc.trail, nuc.exacting)
    offline = next(e for e in named(_on_device(nuc), "warden.offline") if e.at >= nuc.cut_at)
    bees = bees_of(nuc.trail, nuc.exacting)
    [request] = nuc.tap.of("forage.request", nuc.exacting)

    # Queued while cut off and not sent: a request for what only a shared source gives.
    assert ("forage.request", nuc.exacting) in nuc.queued
    assert ("task.result", nuc.exacting) not in nuc.queued
    assert request.heard_at >= nuc.back_at
    # It did not fail: no bee started while the link was out, the one it had stopped at a Handoff,
    # and one bee took it up when the link was back, which then finished it on the shared model.
    assert not _UNMADE & {e.kind for e in about(nuc.trail, nuc.exacting)}
    assert [b for b in bees if offline.at <= b.at < nuc.back_at] == []
    assert any(
        e.kind == "worker.done" and offline.at <= e.at < nuc.back_at
        for e in ends_of(nuc.trail, nuc.exacting)
    )
    resumed = [b for b in bees if b.at >= nuc.back_at]
    assert len(resumed) == 1 and (resumed[0].at - nuc.back_at).total_seconds() < _RESUME_S
    after = [
        c
        for c in llm_calls(nuc.trail, grant_id=str(grant), provider=SHARED_PROVIDER)
        if c.at >= nuc.back_at
    ]
    assert after
    # The Queen answered the request on the way back, and the task ended once.
    asked = [e for e in named(nuc.trail, "forage.requested") if e.subject_id == grant]
    assert len(asked) == 1 and asked[0].at >= nuc.back_at
    assert [
        e
        for e in named(nuc.trail, "forage.denied", "forage.granted")
        if e.subject_id == grant and e.at >= asked[0].at
    ]


def test_after_the_cable_returns_the_segment_is_merged_and_the_results_arrive(nuc: NucRun) -> None:
    window = [e for e in _on_device(nuc) if nuc.cut_at <= e.at < nuc.back_at]
    back = [e for e in named(nuc.trail, "warden.reconnected") if e.at >= nuc.back_at]
    segments = [f for f in nuc.tap.of("swarm.trail_segment_sync") if f.heard_at >= nuc.back_at]
    queens = [e for e in back if e.node_id != nuc.node.node_id]

    # While the cable was out none of the device's own events was on the trail; now what it did
    # meanwhile is, merged from a segment the Queen verified (she refuses an unsigned one, loudly).
    assert nuc.heard_while_cut == 0
    assert "warden.offline" in {e.kind for e in window} and len(window) > 10
    assert segments and not named(nuc.trail, "guard.segment_refused")
    # All of it: the device's own file lists the events it recorded while the cable was out, from
    # the pull to the return, including the forty-odd seconds before it knew; none is missing.
    on_trail = {event.id for event in nuc.trail}
    assert nuc.recorded_while_cut and nuc.recorded_while_cut <= on_trail
    # The Warden says it is back, and so does the Queen: ceilings, plan and grants re-sent.
    assert {e.node_id for e in back} >= {nuc.node.node_id} and len(queens) == 1
    assert queens[0].payload["ceilings"] is True and queens[0].payload["plan"] is True
    for task in (nuc.ordinary, nuc.exacting):
        ended = named(about(nuc.trail, task), "task.succeeded")
        assert len(ended) == 1 and ended[0].at >= nuc.back_at


def test_nothing_is_duplicated(nuc: NucRun) -> None:
    tasks = (nuc.long, nuc.ordinary, nuc.exacting)
    once = ("task.succeeded", "task.failed", "worker.spawned", "worker.started")

    # One result said per task, however often the link broke; one end per task; no bee twice.
    assert [len(nuc.tap.of("task.result", task)) for task in tasks] == [1, 1, 1]
    assert [len(named(about(nuc.trail, t), "task.succeeded")) for t in tasks] == [1, 1, 1]
    assert repeated(nuc.trail, once) == []
    # No task was run twice: never two bees on one task at once, and the one that was held was
    # taken up by one bee. No event of the trail was recorded twice, whichever way it arrived.
    assert [peak_bees(nuc.trail, task) for task in tasks] == [1, 1, 1]
    assert len(nuc.tap.of("forage.request", nuc.exacting)) == 1
    assert recorded_twice(nuc.trail) == []


def test_the_nucs_warden_loads_an_allowlisted_model_with_no_request(nuc: NucRun) -> None:
    [load] = [a for a in nuc.tap.orders if a.order.initial_models == (EXTRA,)]
    refusal = next(a for a in nuc.tap.orders if a.order.initial_models == (ROGUE,))
    window = between(nuc.trail, load.asked_at, refusal.asked_at)
    added = [e for e in named(window, "forage.source_added") if e.payload["reporter"] == "warden"]

    # Asked for one more model of the running server, the Warden offered it: a new source, and
    # nothing else on the trail, no request to the Queen and no answer from her.
    assert load.reply.outcome is NucOutcome.PROMOTED and EXTRA in load.reply.loaded_models
    assert [e.payload["source_id"] for e in added] == [EXTRA]
    assert not named(window, "forage.requested", "forage.granted", "forage.denied")


def test_the_nucs_warden_is_refused_a_model_outside_the_allowlist(nuc: NucRun) -> None:
    [refusal] = [a for a in nuc.tap.orders if a.order.initial_models == (ROGUE,)]
    denied = [e for e in named(_on_device(nuc), "guard.denied") if e.at >= refusal.asked_at]
    sources = {e.payload["source_id"] for e in named(nuc.trail, "forage.source_added")}

    # The device lists the model and its server could run it; the Queen's ceilings do not allow it.
    # The Warden said no at the point where a model is bound, and the trail says who and why.
    assert ROGUE not in refusal.reply.loaded_models and ROGUE not in sources
    [event] = denied
    assert event.payload["point"] == "slot_binding"
    assert event.payload["rule"] == "guard.scope.loadable_source"
    assert event.payload["principal_kind"] == "warden"
    assert SMALL in refusal.reply.loaded_models  # What it already offered is still on offer.
