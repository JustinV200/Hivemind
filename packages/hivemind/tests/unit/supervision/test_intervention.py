"""Tests for hivemind.supervision.intervention: the seven levers and their wire conversion.

Fits into the Hive:
    Mirrors src/hivemind/supervision/intervention.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.intervention for the module under test.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from hivemind.forage import ModelSlot
from hivemind.supervision.errors import SupervisionError, UnknownInterventionError
from hivemind.supervision.intervention import (
    Cancel,
    Checkpoint,
    Compact,
    Handoff,
    Intervention,
    Quarantine,
    Rebind,
    Takeover,
    from_wire,
    to_intervene,
    to_wire,
)
from waggle.clock import FakeClock
from waggle.ids import (
    AlarmId,
    EventId,
    TaskId,
    WorkerId,
    new_alarm_id,
    new_event_id,
    new_task_id,
    new_worker_id,
)
from waggle.messages.supervision import Intervene, InterventionAction

_INTERVENTION_ADAPTER: TypeAdapter[object] = TypeAdapter(Intervention)
_CLOCK = FakeClock()
_BEE: WorkerId = new_worker_id(_CLOCK)
_TASK: TaskId = new_task_id(_CLOCK)
_EPISODE: EventId = new_event_id(_CLOCK)
_ALARM: AlarmId = new_alarm_id(_CLOCK)


def _quarantine() -> Quarantine:
    return Quarantine(
        reason="guard report: injection suspected",
        bee=_BEE,
        task_id=_TASK,
        suspect_episode_id=_EPISODE,
    )


@pytest.mark.parametrize(
    ("variant", "action"),
    [
        (Compact(reason="context is getting long"), InterventionAction.COMPACT),
        (Checkpoint(reason="approaching the handoff threshold"), InterventionAction.CHECKPOINT),
        (Handoff(reason="resetting for a fresh bee"), InterventionAction.HANDOFF),
        (Takeover(reason="the bee is stuck"), InterventionAction.TAKEOVER),
        (Cancel(reason="the goal was withdrawn"), InterventionAction.CANCEL),
    ],
)
def test_to_wire_maps_each_lever_to_its_action_with_no_slot(
    variant: Compact | Checkpoint | Handoff | Takeover | Cancel, action: InterventionAction
) -> None:
    wire_action, slot = to_wire(variant)

    assert wire_action is action
    assert slot is None


def test_to_wire_rebind_carries_the_slot() -> None:
    rebind = Rebind(reason="the model is struggling", slot=ModelSlot.WORKER)

    wire_action, slot = to_wire(rebind)

    assert wire_action is InterventionAction.REBIND
    assert slot == ModelSlot.WORKER.to_wire()


@pytest.mark.parametrize(
    ("action", "expected_type"),
    [
        (InterventionAction.COMPACT, Compact),
        (InterventionAction.CHECKPOINT, Checkpoint),
        (InterventionAction.HANDOFF, Handoff),
        (InterventionAction.TAKEOVER, Takeover),
        (InterventionAction.CANCEL, Cancel),
    ],
)
def test_from_wire_maps_each_action_to_its_lever(
    action: InterventionAction, expected_type: type
) -> None:
    wire = Intervene(
        action=action, subject=None, task_id=None, slot=None, alarm_id=None, reason="why"
    )

    intervention = from_wire(wire)

    assert isinstance(intervention, expected_type)
    assert intervention.reason == "why"


def test_from_wire_rebind_carries_the_slot() -> None:
    wire = Intervene(
        action=InterventionAction.REBIND,
        subject=None,
        task_id=None,
        slot="WORKER",
        alarm_id=None,
        reason="rebinding to a stronger model",
    )

    intervention = from_wire(wire)

    assert isinstance(intervention, Rebind)
    assert intervention.slot is ModelSlot.WORKER


def test_to_wire_and_from_wire_round_trip_every_lever() -> None:
    levers: tuple[Compact | Checkpoint | Handoff | Rebind | Takeover | Cancel, ...] = (
        Compact(reason="r"),
        Checkpoint(reason="r"),
        Handoff(reason="r"),
        Rebind(reason="r", slot=ModelSlot.QUEEN),
        Takeover(reason="r"),
        Cancel(reason="r"),
    )
    for lever in levers:
        action, slot = to_wire(lever)
        wire = Intervene(
            action=action, subject=None, task_id=None, slot=slot, alarm_id=None, reason="r"
        )

        restored = from_wire(wire)

        assert restored == lever


def test_intervention_variants_are_frozen() -> None:
    compact = Compact(reason="r")

    with pytest.raises(ValidationError, match="frozen"):
        compact.reason = "changed"  # type: ignore[misc]  # The assignment is the test.


def test_intervention_variants_reject_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Compact.model_validate({"kind": "compact", "reason": "r", "extra": "nope"})


def test_intervention_union_discriminates_by_kind() -> None:
    built = _INTERVENTION_ADAPTER.validate_python({"kind": "takeover", "reason": "r"})

    assert isinstance(built, Takeover)


def test_intervention_union_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        _INTERVENTION_ADAPTER.validate_python({"kind": "levitate", "reason": "r"})


def test_from_wire_raises_when_a_rebind_has_no_slot() -> None:
    # Intervene's own validator forbids constructing this directly; build it via model_construct
    # to exercise from_wire's own defensive check against a peer that skipped validation.
    wire = Intervene.model_construct(
        action=InterventionAction.REBIND,
        subject=None,
        task_id=None,
        slot=None,
        alarm_id=None,
        reason="r",
    )

    with pytest.raises(SupervisionError, match="REBIND"):
        from_wire(wire)


# ──────────────────────────────────────────────────────────────────────────────
# Quarantine (roadmap step 10.6c) and the refusal of an unknown lever (ADR-0043)
# ──────────────────────────────────────────────────────────────────────────────


def test_every_lever_of_the_union_has_a_wire_action() -> None:
    # Walks the union itself, so a lever added without a wire action fails here, not in a tick.
    variants = get_args(get_args(Intervention)[0])
    samples = {
        Rebind: Rebind(reason="r", slot=ModelSlot.WORKER),
        Quarantine: _quarantine(),
    }

    for variant in variants:
        lever = samples.get(variant) or variant(reason="r")
        action, _slot = to_wire(lever)  # type: ignore[arg-type]  # Each is a lever of the union.
        assert isinstance(action, InterventionAction)


def test_a_quarantine_travels_as_the_whole_intervene() -> None:
    wire = to_intervene(_quarantine(), alarm_id=_ALARM)

    assert wire.action is InterventionAction.QUARANTINE
    assert (wire.subject, wire.task_id, wire.suspect_episode_id) == (_BEE, _TASK, _EPISODE)
    assert wire.alarm_id == _ALARM
    assert wire.slot is None


def test_a_quarantine_round_trips_through_the_wire() -> None:
    lever = _quarantine()

    assert from_wire(to_intervene(lever)) == lever


def test_to_intervene_carries_the_task_and_slot_of_the_other_levers() -> None:
    wire = to_intervene(Rebind(reason="r", slot=ModelSlot.WORKER), task_id=_TASK)

    assert (wire.action, wire.slot, wire.task_id, wire.subject) == (
        InterventionAction.REBIND,
        ModelSlot.WORKER.to_wire(),
        _TASK,
        None,
    )


def test_a_quarantine_names_its_bee_or_its_task() -> None:
    assert Quarantine(reason="r", task_id=_TASK, suspect_episode_id=_EPISODE).bee is None
    with pytest.raises(ValidationError, match="named neither"):
        Quarantine(reason="r", suspect_episode_id=_EPISODE)


def test_a_quarantine_refuses_an_episode_that_is_not_an_event_id() -> None:
    with pytest.raises(ValidationError, match="event_"):
        Quarantine(reason="r", bee=_BEE, suspect_episode_id=_TASK)


def test_from_wire_refuses_an_action_the_union_has_no_lever_for_never_a_cancel() -> None:
    # RELEASE_LEASE is a Warden's own order, carried out before from_wire is ever reached; read
    # here it used to fall through to Cancel, an order nobody gave.
    wire = Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,
        task_id=None,
        slot=None,
        alarm_id=None,
        reason="r",
    )

    with pytest.raises(UnknownInterventionError, match="RELEASE_LEASE") as caught:
        from_wire(wire)

    assert caught.value.code == "hivemind.supervision.unknown_intervention"


def test_from_wire_raises_when_a_quarantine_has_no_suspect_episode() -> None:
    wire = Intervene.model_construct(
        action=InterventionAction.QUARANTINE,
        subject=_BEE,
        task_id=_TASK,
        slot=None,
        alarm_id=None,
        reason="r",
        suspect_episode_id=None,
    )

    with pytest.raises(SupervisionError, match="suspect episode"):
        from_wire(wire)
