"""Tests for hivemind.supervision.intervention: the six levers and their wire conversion.

Fits into the Hive:
    Mirrors src/hivemind/supervision/intervention.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.intervention for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from hivemind.forage import ModelSlot
from hivemind.supervision.errors import SupervisionError
from hivemind.supervision.intervention import (
    Cancel,
    Checkpoint,
    Compact,
    Handoff,
    Intervention,
    Rebind,
    Takeover,
    from_wire,
    to_wire,
)
from waggle.messages.supervision import Intervene, InterventionAction

_INTERVENTION_ADAPTER: TypeAdapter[object] = TypeAdapter(Intervention)


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
