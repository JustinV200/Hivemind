"""Check one action at an enforcement point and record a refusal: the Guard's one effectful adapter.

Every enforcement point (ADR-0031; wired by roadmap step 10.3) asks one `Enforcer` whether a
principal may take its action. The decision itself is `hivemind.guard.policy.evaluate`, a pure
function; this class is the thin effectful edge around it (codingrules 8.3): on a denial it
records a `guard.denied` event on the Pheromone Trail before returning, so "why was this
refused" is always a trail row carrying the principal, the point, the capability, the rule, the
reason and the escalation, never any content the action carried. It never raises on a denial:
the caller reads `decision.allowed` and decides what refusing means at its own point (return an
error to a tool call, skip a placement candidate), and `decision.escalation` says whether to
raise an Alarm or ask the human as well.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by a composition root with the
    `GuardPolicy`, the node's `PheromoneTrail`, its `Clock` and the identity it records as; called
    by every enforcement point. Calls into `hivemind.guard.policy` (evaluate) and
    `hivemind.pheromone` (GuardEvent, PheromoneTrail).

Key invariants:
    - A denial is on the trail before `check` returns it (codingrules section 12); an allow
      writes nothing, since the action's own event records what then happened.
    - Payload strings stay within the trail's bound (`MAX_PAYLOAD_STRING_CHARS`): a capability or
      reason longer than that (a very long path) is shortened, never refused.
    - A trail failure propagates out of `check`, so a refusal that could not be recorded is never
      silently returned as if it had been.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md, "Every
      state-changing action has a named enforcement point".
    - hivemind.guard.policy.evaluate for the decision this adapter wraps.
    - hivemind.pheromone.events.families for the `guard` kind vocabulary.
"""

from __future__ import annotations

from hivemind.cell import CellIdentity
from hivemind.guard.policy import (
    OPERATOR_ID,
    GuardPolicy,
    PolicyDecision,
    PolicyRequest,
    PrincipalRef,
    evaluate,
)
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS, GuardEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import new_event_id

DENIED_KIND = "guard.denied"  # The trail kind every refusal at an enforcement point records.
_ELLIPSIS = "…"  # Marks a payload string shortened to fit the trail's per-string bound.

__all__ = ["DENIED_KIND", "Enforcer"]


class Enforcer:
    """Decide actions against one GuardPolicy and put every refusal on the Pheromone Trail.

    Holds no mutable state of its own: each `check` is one `evaluate` call plus, on a denial, one
    trail write, so one Enforcer may serve every enforcement point of a process concurrently.
    """

    def __init__(
        self, policy: GuardPolicy, trail: PheromoneTrail, clock: Clock, identity: CellIdentity
    ) -> None:
        """Build an Enforcer.

        Args:
            policy: The Guard policy every request is decided against.
            trail: Where a `guard.denied` event is recorded; this node's own trail segment.
            clock: Mints each event's id and timestamp.
            identity: The Hive, node and actor this Enforcer records as (the process enforcing,
                usually "system"); the refused principal is the event's subject, not its actor.
        """
        self._policy = policy
        self._trail = trail
        self._clock = clock
        self._identity = identity

    @property
    def policy(self) -> GuardPolicy:
        """The Guard policy this Enforcer decides against."""
        return self._policy

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        """Decide `request` and, if it is refused, record `guard.denied` before returning.

        Args:
            request: Who is acting, at which point, needing what, holding what, and where.

        Returns:
            The PolicyDecision `evaluate` returned; never raised as an error when refused.

        Raises:
            Whatever the trail's `record` raises: a refusal is never returned unrecorded.
        """
        decision = evaluate(request, self._policy)
        if not decision.allowed:
            # A local trail write (milliseconds, no network), awaited before the refusal is
            # returned so the record always exists first; a failure propagates to the caller.
            await self._trail.record(self._denied_event(request, decision))
        return decision

    def _denied_event(self, request: PolicyRequest, decision: PolicyDecision) -> GuardEvent:
        """Build the `guard.denied` event for one refusal: identifiers and the rule, no content."""
        principal = request.principal
        return GuardEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=self._clock.now(),
            actor=self._identity.actor,
            kind=DENIED_KIND,
            subject_id=self._subject(principal),
            payload={
                "principal_kind": principal.kind.value,
                "principal_id": principal.id,
                "role": principal.role,
                "point": request.point.value,
                "capability": _bounded(str(request.needed)),
                "rule": decision.rule,
                "reason": _bounded(decision.reason),
                "escalation": decision.escalation.value,
            },
        )

    def _subject(self, principal: PrincipalRef) -> str:
        """Return the id a refusal is recorded about: the principal's own, or the Hive's.

        Every principal but the operator has a minted id the trail can index a refusal under; the
        operator's is the literal "human", which is an actor, not a subject, so a refusal of the
        operator is recorded about the Hive the operator runs.
        """
        return self._identity.hive_id if principal.id == OPERATOR_ID else principal.id


def _bounded(text: str) -> str:
    """Shorten `text` to the trail's per-string bound, marking the cut; unchanged if it fits."""
    if len(text) <= MAX_PAYLOAD_STRING_CHARS:
        return text
    return text[: MAX_PAYLOAD_STRING_CHARS - len(_ELLIPSIS)] + _ELLIPSIS
