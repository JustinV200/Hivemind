"""Tests for hivemind.pheromone.events.families.supervisors: queen, warden, capping and guard.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/events/families/supervisors.py (codingrules section 3:
    tests/unit mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.events.families.supervisors for the module under test.
    - test_codec.py beside this module for the tests every family shares.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.pheromone.events.families import CappingEvent, GuardEvent, QueenEvent, WardenEvent
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id


def test_capping_event_kinds_include_audited() -> None:
    # Roadmap step 4.10: sampled, after-the-fact judge review of an already-terminal proposal
    # records capping.audited, alongside the gate's own real-time-check kinds.
    assert "capping.audited" in CappingEvent.KINDS


def test_queen_event_kinds_include_the_phase_3_20_additions() -> None:
    # roadmap step 3.20 (the Queen kernel): added alongside the tick loop that first needs them.
    assert {
        "queen.decided",
        "queen.planned",
        "queen.assigned",
        "queen.awake",
    } <= QueenEvent.KINDS


def test_queen_event_kinds_hold_one_kind_per_goal_request_edge_and_the_chat_pair() -> None:
    # Roadmap step 10.5 (ADR-0032): every edge of hivemind.queen.intake.state is its own kind,
    # plus a finished goal, a human message arriving and the Queen's reply.
    assert {
        "queen.goal_request_received",
        "queen.goal_request_held",
        "queen.goal_request_confirmed",
        "queen.goal_request_planning",
        "queen.goal_request_planned",
        "queen.goal_request_refused",
        "queen.goal_request_finished",
        "queen.human_message_received",
        "queen.replied",
    } <= QueenEvent.KINDS


def test_a_queen_chat_event_refuses_a_payload_that_would_carry_the_words() -> None:
    # Codingrules section 12: the trail records that a message arrived, never what it said; the
    # base validator's forbidden keys are what keep a careless caller honest.
    clock = FakeClock()

    with pytest.raises(ValidationError):
        QueenEvent(
            id=new_id(IdKind.EVENT, clock),
            hive_id=new_id(IdKind.HIVE, clock),
            node_id=new_id(IdKind.NODE, clock),
            at=clock.now(),
            actor="system",
            kind="queen.human_message_received",
            subject_id=new_id(IdKind.HIVE, clock),
            payload={"text": "hello"},
        )


def test_warden_event_kinds_include_the_phase_3_19_additions() -> None:
    # roadmap step 3.19 (the Warden): started/watch/active added alongside the state machine that
    # first needs them.
    assert {"warden.started", "warden.watch", "warden.active"} <= WardenEvent.KINDS


def test_guard_event_kinds_hold_denied_and_every_reserved_phase_10_kind() -> None:
    # Roadmap step 10.2 records guard.denied; the rest are declared now so later phase 10 steps
    # never race on this file. The documents' guard.entrance.* is spelled guard.entrance_*
    # because a kind has exactly one dot (KIND_PATTERN). Step 10.6 adds the Cell gate's two
    # node-integrity refusals.
    edges = {
        "invited",
        "pending",
        "approved",
        "denied",
        "expired",
        "locked",
        "unlocked",
        "revoked",
        "login_failed",
        "step_up",
        "travel_lock",
        "redeem_failed",
        "login",
        "session_ended",
        "held",
        "confirmed",
        "hold_ended",
    }
    assert {
        "guard.denied",
        "guard.alert",
        "guard.injection_suspected",
        "guard.audit_rate_raised",
        "guard.reduced",
        "guard.reopened",
        "guard.reduce_ordered",
        "guard.envelope_refused",
        "guard.segment_refused",
    } | {f"guard.entrance_{edge}" for edge in edges} == GuardEvent.KINDS


def test_warden_event_kinds_include_the_quarantine_record() -> None:
    # Roadmap step 10.6c (ADR-0035): a quarantine is the one intervention a Warden records itself.
    assert "warden.intervened" in WardenEvent.KINDS
