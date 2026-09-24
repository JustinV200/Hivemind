"""Tests for Waggle 1.7's quarantine lever: SECURITY, QUARANTINE and Intervene.suspect_episode_id.

Roadmap steps 10.6 and 10.6c (ADR-0035) bump the protocol one minor: `AlarmKind` gains `SECURITY`,
`InterventionAction` gains `QUARANTINE`, and `Intervene` gains `suspect_episode_id`, the episode
from which the quarantined bee's memory is suspect. These tests pin the new field's round trip and
its refusals (required exactly for QUARANTINE, which must also name its bee), that a 1.6 frame
without the field still decodes, and that a receiver meeting an intervention action it does not
know refuses the whole frame as an invalid payload rather than reading it as anything else.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors waggle.messages.supervision.oversight and
    .alarms for the members this minor added; split from test_oversight.py by feature (14.2).

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.supervision.oversight for Intervene and InterventionAction.
    - docs/waggle/spec.md sections 4 (the 1.7 entry) and 8.3 (Intervene).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import PROTOCOL_MINOR, PROTOCOL_VERSION, Envelope
from waggle.errors import InvalidPayloadError
from waggle.ids import IdKind, new_id
from waggle.messages.labels import AlarmSeverity, HoneyClearance
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    Intervene,
    InterventionAction,
)

MakeEnvelope = Callable[..., Envelope]

CLOCK = FakeClock()
TASK_ID = new_id(IdKind.TASK, CLOCK)
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
EPISODE_ID = new_id(IdKind.EVENT, CLOCK)
ALARM_ID = new_id(IdKind.ALARM, CLOCK)


def _quarantine(**changes: object) -> Intervene:
    """A valid Intervene(QUARANTINE) naming the bee and its task, with some fields replaced."""
    fields: dict[str, object] = {
        "action": "QUARANTINE",
        "subject": WORKER_ID,
        "task_id": TASK_ID,
        "slot": None,
        "alarm_id": ALARM_ID,
        "reason": "Guard report: injection suspected in the bee's tool results.",
        "suspect_episode_id": EPISODE_ID,
    }
    return Intervene.model_validate({**fields, **changes})


def test_the_protocol_is_at_minor_seven() -> None:
    assert (PROTOCOL_VERSION, PROTOCOL_MINOR) == ("1.7", 7)


def test_a_quarantine_round_trips_with_its_suspect_episode() -> None:
    intervene = _quarantine()

    restored = Intervene.model_validate(intervene.model_dump(mode="json"))

    assert restored == intervene
    assert restored.action is InterventionAction.QUARANTINE
    assert restored.suspect_episode_id == EPISODE_ID


@pytest.mark.parametrize("named", [{"subject": None}, {"task_id": None}])
def test_a_quarantine_may_name_its_bee_by_subject_or_by_task(named: dict[str, object]) -> None:
    assert _quarantine(**named).action is InterventionAction.QUARANTINE


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"suspect_episode_id": None}, "required exactly when action is QUARANTINE"),
        ({"subject": None, "task_id": None}, "names its bee by subject or by task_id"),
        ({"suspect_episode_id": TASK_ID}, "event_"),
        ({"slot": "WORKER"}, "required exactly when action is REBIND"),
    ],
)
def test_a_quarantine_is_refused_without_its_bee_or_its_episode(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        _quarantine(**changes)


@pytest.mark.parametrize("action", ["CANCEL", "CHECKPOINT", "HANDOFF", "RELEASE_LEASE"])
def test_a_suspect_episode_on_any_other_lever_is_refused(action: str) -> None:
    with pytest.raises(ValidationError, match="required exactly when action is QUARANTINE"):
        _quarantine(action=action)


def test_the_suspect_episode_defaults_to_none_so_a_1_6_payload_still_validates() -> None:
    older = {
        "action": "CANCEL",
        "subject": WORKER_ID,
        "task_id": TASK_ID,
        "slot": None,
        "alarm_id": None,
        "reason": "The goal was withdrawn.",
    }

    assert Intervene.model_validate(older).suspect_episode_id is None


def test_a_security_alarm_round_trips() -> None:
    alarm = AlarmRaised(
        alarm_id=ALARM_ID,
        kind=AlarmKind.SECURITY,
        severity=AlarmSeverity.CRITICAL,
        origin=WARDEN_ID,
        attempts=0,
        raised_at=CLOCK.now(),
        context=AlarmContext(
            task_id=TASK_ID, cell_id=None, worker_id=WORKER_ID, event_id=EPISODE_ID, handoff=None
        ),
        detail="Worker quarantined; its memory from the suspect episode on is tainted.",
        clearance=HoneyClearance.C1,
        reason="A quarantine always reaches the Queen.",
    )

    assert AlarmRaised.model_validate(alarm.model_dump(mode="json")) == alarm


def test_a_quarantine_crosses_the_codec_unchanged(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope(_quarantine())

    decoded = plain_codec.decode(plain_codec.encode(envelope))

    assert decoded == envelope
    assert decoded.version == "1.7"


@pytest.mark.parametrize("unknown", ["ISOLATE", "LEVITATE"])
def test_an_intervention_action_the_receiver_does_not_know_is_refused_never_read_as_another(
    plain_codec: Codec, make_envelope: MakeEnvelope, unknown: str
) -> None:
    # A newer peer's lever (or garbage) must fail the frame; a receiver that read it as a CANCEL,
    # or as anything else, would carry out an order nobody gave.
    wire: dict[str, object] = json.loads(plain_codec.encode(make_envelope(_quarantine())))
    payload = wire["payload"]
    assert isinstance(payload, dict)
    payload["action"] = unknown
    frame = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    with pytest.raises(InvalidPayloadError) as caught:
        plain_codec.decode(frame)

    assert caught.value.code == "waggle.codec.invalid_payload"
    assert "action" in str(caught.value)
