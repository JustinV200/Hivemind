"""Classify every Pheromone Trail event kind: the point that authorises it, or why it is no action.

ADR-0031 closes the "a new action ships without a check" gap through the one list every
state-changing action already has to join: the trail vocabulary (codingrules section 12 makes
every such action a trail event). This module is that classification as plain data. Each kind is
either authorised at a named `EnforcementPoint` (the action it records only happens once that
point's check passed) or not an action at all, with the reason why: an outcome of an action
authorised elsewhere, an observation, a lifecycle notice, a refusal, a narrowing (which only ever
takes authority away and so needs none), a proposal or decision record, or memory bookkeeping.
`PENDING_POINTS` names the points whose subsystem is not built yet, with the roadmap step or phase
that wires them, so that subsystem classifies its kinds against a point that already exists.
A test (`tests/unit/guard/policy/test_catalogue.py`) fails when a kind is unclassified or
classified twice, and holds the registry of call sites that must keep naming each built point.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Pure data read by that test and by
    anything that wants to say where an event's action was authorised (the Observation Hive).
    Calls into `hivemind.guard.policy.points` only; the kind strings are the vocabulary of
    `hivemind.pheromone.events.families`, repeated here on purpose so a new kind must be placed.

Key invariants:
    - Every kind appears exactly once across `AUTHORISED_AT` and `NOT_ACTIONS`, and every kind in
      either names a kind the trail vocabulary defines (the test proves both directions).
    - `PENDING_POINTS` and the test's call-site registry partition `EnforcementPoint`: a point is
      either wired at a named call site or pending with the step that wires it.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Every state-changing
      action has a named enforcement point".
    - hivemind.pheromone.events.families for the vocabulary classified here.
    - hivemind.guard.policy.points for EnforcementPoint.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from hivemind.guard.policy.points import EnforcementPoint as P

# The reasons a kind is not an action, shared so the table below stays one line per kind.
_OUTCOME = "an outcome: how an action authorised at its own point ended"
_OBSERVATION = "an observation: something seen, measured or scored, which changes nothing"
_LIFECYCLE = "a lifecycle notice: a state its own action (authorised where it began) moved into"
_REFUSAL = "a refusal: the record of an action that did not happen"
_NARROWING = "a narrowing: it only ever takes authority or capacity away, which needs none"
_DECISION = "a decision record: the action it leads to records its own event at its own point"
_PROPOSAL = "a proposal: nothing changes until the decision it asks for is recorded"
_MEMORY = "memory bookkeeping: a bee's own hot, warm or episode state, never an effect elsewhere"
_ROYAL_JELLY = "Royal Jelly's tool lifecycle (phase 9): promotion is gated by a passing report"
_RETURN = "a return: giving back a borrowed or provisioned Cell is never refused"

__all__ = ["AUTHORISED_AT", "NOT_ACTIONS", "PENDING_POINTS", "classify"]

# Every kind whose event records an action, and the point whose check that action passed first.
AUTHORISED_AT: tuple[tuple[str, P], ...] = (
    ("cell.provisioned", P.COMB_SHIELD_EGRESS),  # A fresh Virtual Cell's tier goes live.
    ("cell.resumed", P.COMB_SHIELD_EGRESS),  # A dormant Cell's tier goes live again for a task.
    ("cell.granted", P.PLACEMENT),  # A Virtual Cell handed to the task placement chose it for.
    ("cell.leased", P.LEASE_CREATION),
    ("cell.touched_outside_scratch", P.SESSION_OUTSIDE_SCRATCH),
    ("cell.left", P.SESSION_OUTSIDE_SCRATCH),  # An outside-scratch write that stays.
    ("cell.leaving_removed", P.ENTRANCE_ROUTE),  # The operator's CLI, an Entrance client.
    ("cell.sting_cut", P.STING_CUT),
    ("task.submitted", P.ENTRANCE_ROUTE),  # A goal from a client (entrance:submit).
    ("task.assigned", P.PLACEMENT),
    ("task.blocked", P.QUESTION_ROUTING),  # A question routed up to the human.
    ("task.answered", P.ENTRANCE_ROUTE),  # The human's answer (entrance:answer).
    ("queen.goal_request_received", P.ENTRANCE_ROUTE),  # A goal from a client (entrance:submit).
    ("queen.goal_request_confirmed", P.ENTRANCE_ROUTE),  # The human confirmed an echoed goal.
    ("queen.human_message_received", P.ENTRANCE_ROUTE),  # A chat message from a client.
    ("forage.requested", P.FORAGE_REQUEST),
    ("forage.granted", P.GRANT_ISSUE),
    ("queen.placed", P.PLACEMENT),
    ("queen.assigned", P.PLACEMENT),
    ("warden.spawned", P.WARDEN_SPAWN),
    ("warden.started", P.LEASE_CREATION),  # Its lease opened.
    ("warden.migrated", P.SUPERSEDURE),
    ("swarm.invited", P.ENTRANCE_ROUTE),  # The operator's own device administration.
    ("swarm.enrolled", P.ENTRANCE_ROUTE),
    ("swarm.revoked", P.ENTRANCE_ROUTE),
    ("swarm.promoted", P.NUC_PROMOTION),
    ("swarm.demoted", P.NUC_PROMOTION),
    ("swarm.command_sent", P.DEVICE_COMMAND),
    ("capping.applied", P.TOOL_INVOCATION),  # A tool's proposed effect, capped then applied.
    ("llm.rebound", P.SLOT_BINDING),
    ("worker.spawned", P.SLOT_BINDING),  # A sub-bee starts on its first, checked binding.
    ("guard.reopened", P.ENTRANCE_ROUTE),  # Loopback reopened the Entrance.
    ("guard.entrance_invited", P.ENTRANCE_ROUTE),
    ("guard.entrance_pending", P.ENTRANCE_ROUTE),  # An invite redeemed through its route.
    ("guard.entrance_approved", P.ENTRANCE_ROUTE),
    ("guard.entrance_denied", P.ENTRANCE_ROUTE),
    ("guard.entrance_unlocked", P.ENTRANCE_ROUTE),
    ("guard.entrance_revoked", P.ENTRANCE_ROUTE),
    ("guard.entrance_login", P.ENTRANCE_ROUTE),  # The login route opened a session.
    ("guard.entrance_confirmed", P.ENTRANCE_ROUTE),  # A held request carried out after step-up.
    ("cell.isolated", P.ISOLATION),
    ("cell.isolation_lifted", P.ENTRANCE_ROUTE),  # Only the human lifts it, with step-up.
    ("warden.intervened", P.QUARANTINE),  # The one intervention that records it (step 10.6c).
    ("memory.taint_cleared", P.TAINT_CLEAR),
)

# Every kind that records no action of its own, and why not.
NOT_ACTIONS: tuple[tuple[str, str], ...] = (
    ("cell.attested", _OBSERVATION),
    ("cell.ready", _OBSERVATION),
    ("cell.released", _RETURN),
    ("cell.overwintered", _LIFECYCLE),
    ("cell.destroyed", _RETURN),
    ("cell.purged", _OUTCOME),
    ("cell.provisioning", _LIFECYCLE),
    ("cell.virtual_released", _LIFECYCLE),
    ("cell.destroying", _LIFECYCLE),
    ("cell.provision_failed", _OUTCOME),
    ("cell.evicted", _RETURN),
    ("cell.orphans_swept", _OBSERVATION),
    ("task.unassigned", _LIFECYCLE),
    ("task.started", _LIFECYCLE),
    ("task.progressed", _OBSERVATION),
    ("task.question_withdrawn", _LIFECYCLE),
    ("task.paused", _LIFECYCLE),
    ("task.resumed", _LIFECYCLE),
    ("task.succeeded", _OUTCOME),
    ("task.failed", _OUTCOME),
    ("task.cancelled", _OUTCOME),
    ("alarm.raised", _OBSERVATION),
    ("alarm.handled", _DECISION),
    ("alarm.escalated", _DECISION),  # One hop up; the human is always the chain's last hop.
    ("alarm.resolved", _OUTCOME),
    ("forage.capacity_reported", _OBSERVATION),
    ("forage.denied", _REFUSAL),
    ("forage.revoked", _NARROWING),
    ("forage.expired", _NARROWING),
    ("forage.hosting_decided", _DECISION),
    ("forage.plan_written", _DECISION),
    ("forage.ceilings_set", _NARROWING),
    ("memory.checkpoint", _MEMORY),
    ("memory.handoff", _MEMORY),
    ("memory.reset", _MEMORY),
    ("memory.compacted", _MEMORY),
    ("memory.wax_proposed", _PROPOSAL),
    ("memory.wax_written", _DECISION),
    ("memory.wax_rejected", _DECISION),
    ("memory.wax_cleared", _DECISION),
    ("memory.wax_expired", _LIFECYCLE),
    ("memory.episode", _MEMORY),
    ("memory.note", _MEMORY),
    ("memory.pinned", _MEMORY),
    ("memory.bee_bread_deposited", _MEMORY),
    ("memory.overflow", _OBSERVATION),
    ("queen.started", _LIFECYCLE),
    ("queen.woke", _OBSERVATION),
    ("queen.clustered", _LIFECYCLE),
    ("queen.resumed", _LIFECYCLE),
    ("queen.stopped", _LIFECYCLE),
    ("queen.decided", _DECISION),
    ("queen.planned", _DECISION),
    ("queen.awake", _OBSERVATION),
    ("queen.leave_remembered", _DECISION),  # Replays a human's own earlier answer.
    ("queen.goal_request_held", _LIFECYCLE),  # Waiting on the human's confirmation.
    ("queen.goal_request_planning", _LIFECYCLE),
    ("queen.goal_request_planned", _OUTCOME),  # queen.planned itself is the decision record.
    ("queen.goal_request_refused", _REFUSAL),
    ("queen.goal_request_finished", _OUTCOME),  # Every task of its goal is terminal.
    ("queen.replied", _DECISION),  # Words to the human in the chat; nothing else changes.
    ("warden.watch", _LIFECYCLE),
    ("warden.active", _LIFECYCLE),
    ("warden.clustered", _LIFECYCLE),
    ("warden.offline", _LIFECYCLE),
    ("warden.reconnected", _LIFECYCLE),
    ("warden.stopped", _LIFECYCLE),
    ("tool.requested", _PROPOSAL),
    ("tool.scaffolded", _ROYAL_JELLY),
    ("tool.quarantined", _ROYAL_JELLY),
    ("tool.promoted", _ROYAL_JELLY),
    ("tool.rejected", _ROYAL_JELLY),
    ("tool.retired", _ROYAL_JELLY),
    ("capping.proposed", _PROPOSAL),
    ("capping.checked", _OBSERVATION),
    ("capping.capped", _DECISION),
    ("capping.verified", _OUTCOME),
    ("capping.rejected", _REFUSAL),
    ("capping.rolled_back", _OUTCOME),
    ("capping.audited", _OBSERVATION),
    ("capping.leave_decided", _DECISION),
    ("capping.summary", _OBSERVATION),
    ("llm.call", _OBSERVATION),  # Metering of a call on a binding checked at slot_binding.
    ("llm.fallback", _LIFECYCLE),  # The next link of a chain already bound.
    ("llm.spill", _DECISION),  # Routing within Forage the grant already covers.
    ("llm.throttled", _OBSERVATION),
    ("worker.started", _LIFECYCLE),
    ("worker.handing_off", _LIFECYCLE),
    ("worker.paused", _LIFECYCLE),
    ("worker.resumed", _LIFECYCLE),
    ("worker.done", _OUTCOME),
    ("worker.failed", _OUTCOME),
    ("worker.killed", _OUTCOME),
    ("guard.denied", _REFUSAL),
    ("guard.alert", _OBSERVATION),
    ("guard.injection_suspected", _OBSERVATION),
    ("guard.audit_rate_raised", _NARROWING),
    ("guard.reduced", _NARROWING),
    ("guard.entrance_expired", _LIFECYCLE),
    ("guard.entrance_locked", _NARROWING),
    ("guard.entrance_login_failed", _REFUSAL),
    ("guard.entrance_step_up", _OBSERVATION),
    ("guard.entrance_travel_lock", _NARROWING),
    ("guard.entrance_redeem_failed", _REFUSAL),
    ("guard.reduce_ordered", _NARROWING),
    ("guard.entrance_session_ended", _NARROWING),
    ("guard.entrance_held", _PROPOSAL),  # Nothing happens until an interactive device confirms.
    ("guard.entrance_hold_ended", _LIFECYCLE),
    ("memory.tainted", _NARROWING),  # A tainted item only ever leaves prompts.
)

# Points whose subsystem is not built yet, with the roadmap step or phase that wires each one.
PENDING_POINTS: Mapping[P, str] = MappingProxyType(
    {
        P.EXOSKELETON_REAL_DISPLAY: "phase 6 (the Exoskeleton)",
        P.HONEY_ACCESS: "phase 7 (the Honey Store)",
        P.NUC_PROMOTION: "phase 11 (the Swarm and Nucs)",
        P.DEVICE_COMMAND: "phase 11 (the Swarm and Nucs)",
        P.TACTIC_INVOCATION: "phase 6 (the Pheromone Mask tactics)",
        P.ISOLATION: "step 10.6a (Cell isolation)",
        P.STING_CUT: "phase 13 (Sting Cut)",
        P.SUPERSEDURE: "phase 13 (Supersedure)",
        P.ABSCONDING: "phase 13 (Absconding)",
    }
)

# One lookup built once from the two tables above (pure, codingrules 5.5): kind -> classification.
_BY_KIND: Mapping[str, P | str] = MappingProxyType({**dict(NOT_ACTIONS), **dict(AUTHORISED_AT)})


def classify(kind: str) -> P | str:
    """Return where `kind`'s action is authorised, or why it records no action.

    Args:
        kind: A trail event kind, `<family>.<name>`.

    Returns:
        The EnforcementPoint that authorises the action `kind` records, or a reason sentence for a
        kind that records none.

    Raises:
        KeyError: `kind` is not classified; the catalogue test fails for every such kind first.
    """
    return _BY_KIND[kind]
