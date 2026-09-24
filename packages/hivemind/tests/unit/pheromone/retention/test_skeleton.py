"""Tests for hivemind.pheromone.retention.skeleton: what survives a Night Veil Cell, cut to what.

One test per payload the skeleton cuts (codingrules section 12's list, kind by kind), one that
every other kind a Night Veil Cell's life records never crosses at all, and the per-tier Capping
counts one `capping.summary` carries.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/retention/skeleton.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention.skeleton for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from hivemind.pheromone import LlmEvent, LlmUsage, PheromoneEvent, TaskEvent, event_class_for
from hivemind.pheromone.retention import SKELETON_KINDS, TierCount, skeleton_event, tier_counts
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_hive_id, new_node_id

_CLOCK = FakeClock()
_CELL = new_cell_id(_CLOCK)
_TASK = "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_WARDEN = "warden_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_REQUEST = "goalreq_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _event(kind: str, payload: dict[str, JsonValue], subject_id: str = _CELL) -> PheromoneEvent:
    """One valid event of `kind`'s own family, about `subject_id`."""
    return event_class_for(kind)(
        id=new_event_id(_CLOCK),
        hive_id=new_hive_id(_CLOCK),
        node_id=new_node_id(_CLOCK),
        at=_CLOCK.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload=payload,
    )


def _cut(kind: str, payload: dict[str, JsonValue], subject_id: str = _CELL) -> JsonValue:
    """The payload `kind`'s skeleton copy keeps."""
    cut = skeleton_event(_event(kind, payload, subject_id))
    assert cut is not None
    return cut.payload


def test_cell_provisioned_keeps_the_backend_image_and_tier() -> None:
    payload: dict[str, JsonValue] = {
        "backend": "docker",
        "image": "night-veil-ubuntu",
        "comb_shield": "NIGHT_VEIL",
        "reason": "anything a later writer adds",
    }

    assert _cut("cell.provisioned", payload) == {
        "backend": "docker",
        "image": "night-veil-ubuntu",
        "comb_shield": "NIGHT_VEIL",
    }


def test_cell_attested_keeps_pass_or_fail_per_check_and_never_a_checks_detail() -> None:
    payload: dict[str, JsonValue] = {
        "tor_healthy": {"status": "FAIL", "detail": "exit relay saw 203.0.113.9"},
        "kill_switch_active": {"status": "PASS", "detail": "nft ruleset loaded"},
        "passed": False,
        "red": ["tor_healthy"],
    }

    assert _cut("cell.attested", payload) == {
        "tor_healthy": {"status": "FAIL"},
        "kill_switch_active": {"status": "PASS"},
        "passed": False,
        "red": ["tor_healthy"],
    }


def test_queen_placed_keeps_only_the_human_request_it_stands_on() -> None:
    payload: dict[str, JsonValue] = {
        "reason": "NIGHT_VEIL always provisions a fresh Virtual Cell.",
        "outcome": "ProvisionVirtual",
        "image": "night-veil-ubuntu",
        "backend": "docker",
        "goal_request_id": _REQUEST,
    }

    assert _cut("queen.placed", payload, _TASK) == {"goal_request_id": _REQUEST}


def test_forage_granted_keeps_the_task_and_the_grant_size_but_not_the_warden() -> None:
    payload: dict[str, JsonValue] = {"task_id": _TASK, "warden_id": _WARDEN, "max_sub_bees": 2}

    assert _cut("forage.granted", payload, "grant_01HZZZZZZZZZZZZZZZZZZZZZZZ") == {
        "task_id": _TASK,
        "max_sub_bees": 2,
    }


def test_forage_plan_written_keeps_its_revision_and_slot_count() -> None:
    payload: dict[str, JsonValue] = {"revision": 0, "slots": 9, "sources": ["local_worker"]}

    assert _cut("forage.plan_written", payload) == {"revision": 0, "slots": 9}


def test_cell_sting_cut_keeps_nothing_beyond_the_cell() -> None:
    assert _cut("cell.sting_cut", {"reason": "operator cut it"}) == {}


def test_capping_summary_keeps_its_tier_and_counts() -> None:
    payload = TierCount("SCRATCH_WRITE", approved=3, rejected=1, rolled_back=0).payload()

    assert _cut("capping.summary", {**payload, "proposals": ["msg_1"]}) == payload


def test_cell_destroyed_keeps_the_undertakers_cleanup_counts_only() -> None:
    payload: dict[str, JsonValue] = {
        "grants_revoked": 1,
        "wax_retired": 0,
        "leavings_removed": 0,
        "reason": "task finished",
    }

    assert _cut("cell.destroyed", payload) == {
        "grants_revoked": 1,
        "wax_retired": 0,
        "leavings_removed": 0,
    }


@pytest.mark.parametrize("kind", sorted(TaskEvent.KINDS))
def test_every_task_event_carries_nothing_beyond_the_task_id(kind: str) -> None:
    payload: dict[str, JsonValue] = {"title": "Write a haiku", "cell_id": _CELL, "reason": "x"}

    assert _cut(kind, payload, _TASK) == {}


@pytest.mark.parametrize(
    "kind",
    [
        "cell.provisioning",
        "cell.ready",
        "cell.granted",
        "cell.virtual_released",
        "cell.destroying",
        "cell.leased",
        "cell.released",
        "cell.purged",
        "warden.spawned",
        "warden.stopped",
        "worker.spawned",
        "worker.done",
        "queen.assigned",
        "queen.planned",
        "queen.decided",
        "forage.ceilings_set",
        "forage.revoked",
        "capping.proposed",
        "capping.capped",
        "capping.rolled_back",
        "memory.episode",
        "guard.denied",
        "llm.rebound",
    ],
)
def test_a_kind_outside_the_skeleton_never_crosses(kind: str) -> None:
    assert skeleton_event(_event(kind, {"cell_id": _CELL})) is None


def test_an_llm_call_never_crosses() -> None:
    usage = LlmUsage(input_tokens=1, output_tokens=1, cached_tokens=0, cost_usd=0.0)
    call = LlmEvent(
        id=new_event_id(_CLOCK),
        hive_id=new_hive_id(_CLOCK),
        node_id=new_node_id(_CLOCK),
        at=_CLOCK.now(),
        actor="system",
        kind="llm.call",
        subject_id=new_event_id(_CLOCK),
        payload={"goal_id": _TASK},
        slot="WORKER",
        provider="local",
        usage=usage,
    )

    assert skeleton_event(call) is None


def test_a_skeleton_copy_keeps_everything_but_the_cut_payload() -> None:
    original = _event("queen.placed", {"goal_request_id": _REQUEST, "reason": "x"}, _TASK)

    cut = skeleton_event(original)

    assert cut is not None and type(cut) is type(original)
    assert cut.model_dump(exclude={"payload"}) == original.model_dump(exclude={"payload"})


def test_the_skeleton_names_exactly_codingrules_twelves_list() -> None:
    lifecycle = {
        "cell.provisioned",
        "cell.attested",
        "queen.placed",
        "forage.granted",
        "forage.plan_written",
        "cell.sting_cut",
        "capping.summary",
        "cell.destroyed",
    }

    assert lifecycle | TaskEvent.KINDS == SKELETON_KINDS
    # Every one is already trail vocabulary: the skeleton adds no kind of its own.
    assert all(kind in event_class_for(kind).KINDS for kind in SKELETON_KINDS)


def test_tier_counts_join_every_outcome_to_its_proposals_tier() -> None:
    events = [
        _event("capping.proposed", {"tier": "SCRATCH_WRITE"}, "msg_01HAAAAAAAAAAAAAAAAAAAAAAA"),
        _event("capping.capped", {"tier": "SCRATCH_WRITE"}, "msg_01HAAAAAAAAAAAAAAAAAAAAAAA"),
        _event("capping.rolled_back", {"method": "SNAPSHOT"}, "msg_01HAAAAAAAAAAAAAAAAAAAAAAA"),
        _event("capping.proposed", {"tier": "SPEND"}, "msg_01HBBBBBBBBBBBBBBBBBBBBBBB"),
        _event("capping.rejected", {"tier": "SPEND"}, "msg_01HBBBBBBBBBBBBBBBBBBBBBBB"),
        _event("capping.proposed", {"tier": "SPEND"}, "msg_01HCCCCCCCCCCCCCCCCCCCCCCC"),
        _event("worker.done", {}, "worker_01HZZZZZZZZZZZZZZZZZZZZZZZ"),
    ]

    assert tier_counts(events) == (
        TierCount("SCRATCH_WRITE", approved=1, rejected=0, rolled_back=1),
        TierCount("SPEND", approved=0, rejected=1, rolled_back=0),
    )


def test_tier_counts_skip_a_proposal_whose_tier_never_shipped() -> None:
    orphan = _event("capping.rolled_back", {"method": "SNAPSHOT"}, "msg_01HAAAAAAAAAAAAAAAAAAAAAAA")

    assert tier_counts([orphan]) == ()
