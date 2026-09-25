"""Define EnforcementPoint: every place in the Hive where an action is authorised before it runs.

ADR-0039 gives every state-changing action a named enforcement point: the one place its
capability is checked, through `hivemind.guard.enforcer.Enforcer`, so "who may do this, and why
not" always has an answer and a trail row. This module only declares the points; roadmap step
10.3 wires each call site to its point and adds the test that classifies every trail event kind as
authorised at a point or not an action. Points whose subsystem is not built yet are declared now,
with the phase that builds them in their comment, so that subsystem wires a point that already
exists rather than inventing one. The values are the names a manifest's `[guard.escalation]` table
uses to pick an escalation action per point.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Carried on every
    `hivemind.guard.policy.models.PolicyRequest`, recorded on every `guard.denied` trail event, and
    keyed on by `hivemind.guard.policy.table.GuardPolicy.escalation`. Calls into nothing.

Key invariants:
    - Values are lowercase snake_case and unique; a value is stable once shipped, because stored
      trail events and operators' manifests name it.
    - Adding a state-changing action means adding (or reusing) a point here in the same change.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Every state-changing
      action has a named enforcement point".
    - .claude/roadmap.md step 10.3 for the wiring and the enumeration test.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["EnforcementPoint"]


class EnforcementPoint(Enum):
    """A named place where one kind of action is authorised against a principal's set."""

    PLACEMENT = "placement"  # queen.placement: a task's set must admit the Cell it lands on.
    LEASE_CREATION = "lease_creation"  # A Warden opening a lease on a Real Cell.
    GRANT_ISSUE = "grant_issue"  # The Queen issuing or topping up a Forage grant.
    FORAGE_REQUEST = "forage_request"  # A Warden asking for more Forage (forage:request).
    WARDEN_SPAWN = "warden_spawn"  # Starting a Warden on a Cell (warden:spawn).
    TOOL_INVOCATION = "tool_invocation"  # A Worker calling one tool (tool:<name>).
    SESSION_OUTSIDE_SCRATCH = "session_outside_scratch"  # A session call outside scratch.
    EXOSKELETON_REAL_DISPLAY = "exoskeleton_real_display"  # Attach on a real display (phase 6).
    HONEY_ACCESS = "honey_access"  # Reading or writing Honey at a clearance (phase 7).
    SLOT_BINDING = "slot_binding"  # Binding or rebinding a model slot, inside the grant (llm:).
    QUESTION_ROUTING = "question_routing"  # Routing a question up to the human.
    NUC_PROMOTION = "nuc_promotion"  # Promoting a colonized Real Cell to a Nuc (phase 11).
    DEVICE_COMMAND = "device_command"  # A signed command to a Swarm device (phase 11).
    TACTIC_INVOCATION = "tactic_invocation"  # Invoking a Pheromone Mask tactic (phase 6).
    COMB_SHIELD_EGRESS = "comb_shield_egress"  # Activating a Comb Shield tier's egress policy.
    ENTRANCE_ROUTE = "entrance_route"  # Any Landing Board route at the Hive Entrance (step 10.5).
    ISOLATION = "isolation"  # The Queen isolating one Cell (step 10.6a).
    QUARANTINE = "quarantine"  # Quarantining one bee and tainting its memory (step 10.6c).
    TAINT_CLEAR = "taint_clear"  # A judge verdict clearing tainted memory (step 10.6d).
    STING_CUT = "sting_cut"  # Cutting one Cell off in an emergency (step 13.4a).
    SUPERSEDURE = "supersedure"  # Moving the Hive Stand to another machine (codingrules 8.16).
    ABSCONDING = "absconding"  # Tearing the whole Hive down, break-glass (codingrules 15).
