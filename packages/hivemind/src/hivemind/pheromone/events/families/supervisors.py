"""Define the event families of the supervisors and their gates: Queen, Warden, Capping, Guard.

Four of the Pheromone Trail's thirteen event families record supervision: the Queen's own kernel
and her human end (`queen`), a Warden's lifecycle (`warden`), the Capping gate's proposal state
machine (`capping`) and every Guard decision or Hive Entrance security edge (`guard`). Each is a
`PheromoneEvent` subclass (`hivemind.pheromone.events.base`) fixing `FAMILY` and `KINDS`.

Vocabulary (family -> kind -> when it is recorded):
    queen: started (the Queen process came up); placed (a Placement decision was made for a task);
        woke (an awake episode ran); clustered (Clustering paused affected bees); resumed (bees
        resumed from Clustering); stopped (the Queen process is shutting down); decided (the Queen
        acted on an awake QueenDecision, or autopilot itself decided ESCALATE_TO_HUMAN, roadmap
        step 3.20); planned (a goal was decomposed and its task graph persisted, roadmap step
        3.20); assigned (a ready task was placed, granted and assigned to a Warden, roadmap step
        3.20); awake (one stateless awake episode ran for the Queen, roadmap step 3.20 -- distinct
        from the existing `woke`, reserved for other Queen lifecycle wake-ups); leave_remembered
        (the Queen answered a leave Question herself from "keep for this whole goal" memory,
        roadmap step 5.0d; carries the goal id, Cell id and the original wire question id the
        remembered answer derives from). Roadmap step 10.5 (ADR-0032, the human inbox) adds one
        kind per edge of a goal request's state machine (`hivemind.queen.intake.state`), each
        written in the same transaction as the row: goal_request_received (a goal request was
        committed, RECEIVED, before the Entrance acknowledged it; carries the request id, origin,
        source, device and requested tier); goal_request_held (RECEIVED -> AWAITING_CONFIRMATION:
        a spoken goal echoed back, or a request held for a human's step-up);
        goal_request_confirmed (AWAITING_CONFIRMATION -> RECEIVED: the human confirmed it);
        goal_request_planning (RECEIVED -> PLANNING: the Queen began planning it);
        goal_request_planned (PLANNING -> PLANNED, with the goal id); goal_request_refused (->
        REFUSED, with a reason code, never the reason's own words); goal_request_finished (every
        task of the goal it planned reached a terminal status, recorded once so its device is
        told once). And two for the chat: human_message_received (a human's chat message entered
        the Queen's inbox; carries the chat entry id and the device); replied (the Queen answered
        in the chat, an awake REPLY decision; carries the chat entry id). None of the step 10.5
        kinds ever carries the human's or the Queen's words, and all are the Queen's own records,
        on her trail, never inside a Night Veil Cell's segment, so a teardown leaves them.
    warden: spawned (a Warden started supervising a Cell); started (its Cell lease opened and it
        moved STARTING -> ACTIVE, roadmap step 3.19); watch (a Real Cell's Warden with no active
        sub-bees, or a refused lease, moved to WATCH); active (a spawn moved it WATCH -> ACTIVE);
        clustered (every sub-bee was paused by Clustering, roadmap step 4.9); offline (its
        connection to the Queen was lost); reconnected (its connection came back); migrated (it
        moved to another host, a Supersedure or promotion step); stopped (it is shutting down);
        intervened (it quarantined one of its bees: checkpointed, cancelled, killed, its slice of
        the grant revoked and its memory from the suspect episode on tainted; carries the task, the
        bee, the action and that episode id, roadmap step 10.6c, ADR-0035).
    capping: proposed (a Proposal entered CHECKING); checked (one tier check ran, pass or fail);
        capped (every required check passed, CAPPED); applied (the proposal's side effect ran);
        verified (postconditions held after applying); rejected (a check failed, before applying);
        rolled_back (postconditions failed after applying, and the effect was undone); audited (a
        sampled, already-terminal proposal was reviewed after the fact by the judge -- roadmap
        step 4.10's AuditSampler, for a tier the table marks as not judge-gated in real time;
        findings become Nectar and an AUDIT_FAILED Alarm on a REJECT verdict); leave_decided (the
        leave policy decided ALLOW/ASK/DENY for one outside-scratch path, roadmap step 5.0c;
        carries the path, its PathClass and whether it was persisted -- never the human-readable
        reason); summary (a per-tier rollup of approved/rejected/rolled_back counts, the only
        capping.* record kept through a Night Veil teardown, codingrules section 12).
    guard: denied (an enforcement point refused an action; carries the principal's kind, id and
        role, the point, the capability, the rule, the reason and the escalation, never content,
        ADR-0031). Reserved, declared now so the later phase 10 steps that record them never race
        on this file: alert (a Guard Bee report, roadmap step 10.6); injection_suspected (the
        untrusted-content scanner flagged outside text, 10.6b); audit_rate_raised (a Guard Bee
        rule raised a Capping tier's sampled-audit rate, 10.6); reduced (the Entrance Reducer
        dropped the Entrance to loopback only, 10.5e); reopened (loopback reopened it after a
        reduction, 10.5e); entrance_invited, entrance_pending, entrance_approved, entrance_denied,
        entrance_expired, entrance_locked, entrance_unlocked, entrance_revoked (one per edge of
        the enrolled-device state machine, 10.5d-e: an invite minted, a request waiting, approved,
        denied, expired unredeemed or unapproved, locked out, unlocked on loopback, revoked);
        entrance_login_failed (a login failed its device proof or password, 10.5e);
        entrance_step_up (a step-up re-ran both factors, 10.5e); entrance_travel_lock (a known
        device appeared from a new network with travel_lock on, 10.5e); entrance_redeem_failed (an
        invite redemption was refused: an unknown, expired or used code, or a bad key proof; the
        address and the reason, never the code, 10.5d; the Guard Bee's invite-abuse signal);
        reduce_ordered (a Guard Bee rule ordered the Entrance Reducer, which the Entrance carries
        out by following the trail, ADR-0035); entrance_login (a login passed both factors and
        opened a session: the device and the listener, never the token); entrance_session_ended
        (a session ended: logout, expiry, idling out, or its device leaving APPROVED; carries the
        reason); entrance_held (a non-interactive device's request that needs step-up was held as
        a pending confirmation: its id, the device and the action, never the request's content);
        entrance_confirmed (an interactive device confirmed a held request after step-up);
        entrance_hold_ended (a held request expired or was cancelled, with which). Roadmap step
        10.6 adds two node-integrity refusals at the Cell gate, each about the Cell whose own
        authenticated link carried it and naming a reason, never the frame: envelope_refused (a
        frame on an attached Virtual Cell's link failed its signature, and the link was closed);
        segment_refused (a trail segment it shipped was not merged: another node's or Warden's,
        an unknown format, or bytes that do not match what it declared). The design
        documents'
        `guard.entrance.*` is spelled `guard.entrance_*` here because a kind has exactly one dot
        (KIND_PATTERN, the shape waggle shares), just as the Cell Wax kinds are `memory.wax_*`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.events.
    families`. Constructed by the Queen, every Warden, the Capping gate, the Guard and the Hive
    Entrance; decoded by `hivemind.pheromone.events.families.codec`. Calls into
    `hivemind.pheromone.events.base` only.

Key invariants:
    - Every class's KINDS contains only strings whose family segment equals its own FAMILY.
    - Every kind here is classified by `hivemind.guard.policy.catalogue` (ADR-0031): the point
      that authorises its action, or why it records none; a new kind joins that table too.

See Also:
    - .claude/codingrules.md section 8.12 for the Capping state machine capping.* mirrors.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the queen.goal_*
      and chat kinds.
    - hivemind.pheromone.events.families.codec for EVENT_FAMILIES and the JSON codec.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.pheromone.events.base import PheromoneEvent

__all__ = ["CappingEvent", "GuardEvent", "QueenEvent", "WardenEvent"]


class QueenEvent(PheromoneEvent):
    """A Queen lifecycle, mode or human-end event; see the module docstring's `queen` entry."""

    FAMILY: ClassVar[str] = "queen"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "queen.started",
            "queen.placed",
            "queen.woke",
            "queen.clustered",
            "queen.resumed",
            "queen.stopped",
            # roadmap step 3.20 (the Queen kernel): the decision, plan, assignment and awake-
            # episode events her tick loop, planner and dispatcher record.
            "queen.decided",
            "queen.planned",
            "queen.assigned",
            "queen.awake",
            "queen.leave_remembered",
            # roadmap step 10.5 (ADR-0032): one per goal request edge, then the chat's two.
            "queen.goal_request_received",
            "queen.goal_request_held",
            "queen.goal_request_confirmed",
            "queen.goal_request_planning",
            "queen.goal_request_planned",
            "queen.goal_request_refused",
            "queen.goal_request_finished",
            "queen.human_message_received",
            "queen.replied",
        }
    )


class WardenEvent(PheromoneEvent):
    """A Warden lifecycle event; see the module docstring's `warden` entry."""

    FAMILY: ClassVar[str] = "warden"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "warden.spawned",
            "warden.started",
            "warden.watch",
            "warden.active",
            "warden.clustered",  # Roadmap step 4.9: every sub-bee paused by Clustering.
            "warden.offline",
            "warden.reconnected",
            "warden.migrated",
            "warden.stopped",
            "warden.intervened",  # Roadmap step 10.6c: a bee quarantined (ADR-0035).
        }
    )


class CappingEvent(PheromoneEvent):
    """A Capping gate proposal-state-machine step; see the module docstring's `capping` entry."""

    FAMILY: ClassVar[str] = "capping"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "capping.proposed",
            "capping.checked",
            "capping.capped",
            "capping.applied",
            "capping.verified",
            "capping.rejected",
            "capping.rolled_back",
            "capping.audited",
            "capping.leave_decided",
            "capping.summary",
        }
    )


class GuardEvent(PheromoneEvent):
    """A Guard decision or an Entrance security edge; see the module docstring's `guard` entry."""

    FAMILY: ClassVar[str] = "guard"
    KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "guard.denied",
            "guard.alert",
            "guard.injection_suspected",
            "guard.audit_rate_raised",
            "guard.reduced",
            "guard.reopened",
            "guard.entrance_invited",
            "guard.entrance_pending",
            "guard.entrance_approved",
            "guard.entrance_denied",
            "guard.entrance_expired",
            "guard.entrance_locked",
            "guard.entrance_unlocked",
            "guard.entrance_revoked",
            "guard.entrance_login_failed",
            "guard.entrance_step_up",
            "guard.entrance_travel_lock",
            "guard.entrance_redeem_failed",
            "guard.reduce_ordered",
            "guard.entrance_login",  # Roadmap step 10.5e: a session opened by both factors.
            "guard.entrance_session_ended",
            "guard.entrance_held",  # A pending confirmation waiting on a human's step-up.
            "guard.entrance_confirmed",
            "guard.entrance_hold_ended",
            "guard.envelope_refused",  # Roadmap step 10.6: a node-integrity refusal at the gate.
            "guard.segment_refused",
        }
    )
