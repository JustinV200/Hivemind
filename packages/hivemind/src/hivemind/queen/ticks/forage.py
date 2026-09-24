"""Define handle_forage_request: the Queen's own tick handler for a Warden's ForageRequest.

Roadmap step 4.7's own dispatch map: within headroom, autopilot grants at once, with no awake
episode; contested (cannot be met from headroom alone, but could be by shrinking another live
grant), the request needs judgement, run at `Effort.HIGH` (codingrules section 8.14, "contested
Forage" at the Queen's own highest effort); otherwise, denied, with a reason either way. This
module is the wire-and-trail half of that: it records `forage.requested` on receipt, calls
`hivemind.queen.forage.requests.handle_forage_request_for_kind` for the decision (and, on a grant,
the ledger update), then sends the wire reply -- a fresh `GrantIssued` plus a `ForageReply(GRANTED)`
on a grant, or a `ForageReply(DENIED)` otherwise -- and records `forage.granted`/`forage.denied`.

Roadmap step 10.3 (ADR-0031) puts the `forage_request` enforcement point in front of all of that:
the requesting Warden must hold `forage:request` in its set (which the Queen computes from its
Cell's access level, `hivemind.queen.authority.warden_held`) and must be the holder of the grant it
asks to grow -- before this, any attached Warden could top up any grant by naming its id. Either
refusal goes through the Queen's `Enforcer` (`check` for the capability, `refuse` for the holder,
so both are `guard.denied` rows with a reason) and answers with the ordinary `ForageReply(DENIED)`
and `forage.denied`; nothing is judged, granted or shrunk.

Roadmap step 4.7's own leftover closes the contested gap a prior dispatch's report flagged:
`_resolve_contested` runs one awake episode (`hivemind.queen.awake.decide_awake`, at
`Effort.HIGH`) whose prompt names the request's own contested amount and every other live grant in
the same dimension (`EpisodeExtras.system_hint`, rather than a whole new hot-state category for a
handful of lines specific to one decision). The model returns `GRANT_BY_SHRINKING` (naming a grant
to shrink and by how much) or `DENY_REQUEST`; a shrink applies `hivemind.queen.forage.grants.
revise` to the named grant first (which the shrunk holder's own Warden may in turn report
`AlarmKind.GRANT_EXCEEDED` for, on its next assignment check, if the shrink put it over what it
already runs -- `hivemind.wardens.ticks.assign`'s own existing rule, unaffected by this module),
then `hivemind.queen.forage.requests.grant_wanted` grows the requester's own grant from the
headroom the shrink freed.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s tick for every received
    `waggle.messages.forage.ForageRequest`, ahead of `hivemind.queen.autopilot.table.decide` (whose
    own fallback for an unrecognised payload type is `NEEDS_JUDGEMENT` outright -- exactly what
    "within headroom, no awake episode" must not become). Calls into `hivemind.cell`
    (HoneyClearance), `hivemind.forage.slots` (Effort), `hivemind.guard` (the forage_request
    point), `hivemind.memory` (TriggerEvent), `hivemind.queen.authority` (warden_held),
    `hivemind.queen.autopilot` (ForageAutopilotOutcome, QueenAction), `hivemind.queen.awake`
    (EpisodeExtras, QueenSources, decide_awake), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.forage.grants` (revise), `hivemind.queen.forage.requests`
    (ForageRequestOutcome, grant_wanted, handle_forage_request_for_kind), `hivemind.queen.
    human_inbox` (HumanInbox), `hivemind.queen.trail` (record_forage_event) and waggle only.

Key invariants:
    - Every branch sends exactly one wire reply and records exactly one `forage.*` trail event;
      neither a grant nor a denial is ever left silent.
    - A request from a Warden without `forage:request`, or for a grant it does not hold, never
      reaches the ledger: it is refused before `handle_forage_request_for_kind` runs.
    - A GRANT always sends the fresh `GrantIssued` before the `ForageReply` that names its
      revision, mirroring `hivemind.queen.dispatcher`'s own "grant before assignment" ordering.
    - `ForageReply.granted` always matches `wire_request.kind`'s own dimension (sub_bees, seats or
      spend), never the SUB_BEES-only shape this module shipped with (roadmap step 4.7's leftover).
    - `_resolve_contested` never shrinks the requester's own grant, and never grants the requester
      without first committing the shrink: `grant_wanted` only runs once `grants.revise` on the
      shrink target has already returned.

See Also:
    - .claude/roadmap.md step 4.7 for the dispatch map this module implements, and its leftover
      for the GRANT_BY_SHRINKING/DENY_REQUEST resolution this dispatch adds.
    - .claude/codingrules.md section 8.14 for "the Queen tunes her own effort... contested Forage
      at high".
    - hivemind.queen.forage.requests for the decision and ledger-update logic this module wires.
    - hivemind.queen.dispatcher for the sibling "grant, then message" ordering this module mirrors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from hivemind.cell import HoneyClearance
from hivemind.forage import ForageGrant
from hivemind.forage.slots import Effort
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    EnforcementPoint,
    PolicyRequest,
    warden_principal,
)
from hivemind.memory import TriggerEvent
from hivemind.queen.authority import warden_held
from hivemind.queen.autopilot import ForageAutopilotOutcome, QueenAction
from hivemind.queen.awake import EpisodeExtras, QueenSources, decide_awake
from hivemind.queen.awake.decision import QueenDecision
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.forage.requests import (
    ForageRequestOutcome,
    grant_wanted,
    handle_forage_request_for_kind,
)
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.trail import record_forage_event
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import wrap
from waggle.ids import GrantId, MessageId, WardenId
from waggle.messages.forage import ForageDelta, ForageOutcome, ForageReply
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind

__all__ = ["handle_forage_request", "handle_forage_request_for_item"]

_EMPTY_DELTA = ForageDelta(
    seats=0, source_id=None, spend=0.0, tokens=0, sub_bees=0, slot=None, minimum_grade=None
)
_FORAGE_REQUEST = Capability(family=CapabilityFamily.FORAGE_REQUEST)  # Asking for more Forage.


@dataclass(frozen=True, slots=True)
class _Reply:
    """One request's own link, request and correlation id, grouped for codingrules 5.1's limit."""

    link: WardenLink
    wire_request: WireForageRequest
    request_id: MessageId


async def handle_forage_request_for_item(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> None:
    """Unwrap one ordered InboxItem carrying a ForageRequest, and answer it.

    `hivemind.queen.queen.Queen`'s own tick calls this directly for a received ForageRequest, so
    every bit of unwrapping -- the sending Warden's id from `item.principal`, its own link (a
    request from a Warden this Queen no longer attaches has nowhere to reply, and is dropped),
    the request's own envelope id `handle_forage_request`'s reply must correlate to -- lives here
    rather than at that call site.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered InboxItem; `item.payload` must be a `WireForageRequest` (the one
            caller, `hivemind.queen.queen.Queen`, already checked this with `isinstance` before
            calling here; `cast` below trusts that rather than re-checking it a second time).
    """
    link = wardens.get(WardenId(item.principal))
    if link is not None:
        wire_request = cast(WireForageRequest, item.payload)
        await handle_forage_request(deps, wardens, link, wire_request, MessageId(item.id))


async def handle_forage_request(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    link: WardenLink,
    wire_request: WireForageRequest,
    request_id: MessageId,
) -> None:
    """Answer one Warden's ForageRequest: grant within headroom, deny (or resolve) otherwise.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id; needed only for the contested path,
            to reach the shrunk grant's own holder with a fresh `GrantIssued`.
        link: The requesting Warden's own link; the reply and any fresh GrantIssued go here.
        wire_request: The Warden's own request.
        request_id: The request's own envelope id: `ForageReply` is a reply
            (`waggle.envelope.MessageShape.REPLY`) and must carry the correlation_id of the
            request it answers.
    """
    await record_forage_event(
        deps, "forage.requested", wire_request.grant_id, request_kind=wire_request.kind.value
    )
    reply = _Reply(link=link, wire_request=wire_request, request_id=request_id)
    refusal = await _authorize_request(deps, link, wire_request)
    if refusal is not None:
        await _send_denial(deps, reply, refusal, contested=False)
        return
    outcome = await handle_forage_request_for_kind(deps.ledger, deps, wire_request)
    if outcome.autopilot_outcome is ForageAutopilotOutcome.GRANT and outcome.grant is not None:
        await _send_grant(deps, reply, outcome.grant, outcome.reason)
        return
    if outcome.autopilot_outcome is ForageAutopilotOutcome.NEEDS_JUDGEMENT:
        await _resolve_contested(deps, wardens, reply, outcome)
        return
    await _send_denial(deps, reply, outcome.reason, contested=False)


async def _authorize_request(
    deps: QueenDeps, link: WardenLink, wire_request: WireForageRequest
) -> str | None:
    """Pass the `forage_request` point: `forage:request` held, and the grant the requester's own.

    Returns:
        None when the request may proceed; otherwise the refusal's reason sentence, which is
        already on the trail as `guard.denied`.
    """
    request = PolicyRequest(
        principal=warden_principal(link.warden_id),
        point=EnforcementPoint.FORAGE_REQUEST,
        needed=_FORAGE_REQUEST,
        held=warden_held(deps, link.cell),
    )
    decision = await deps.enforcer.check(request)
    if not decision.allowed:
        return decision.reason
    existing = deps.ledger.grant(wire_request.grant_id)
    # An unknown grant falls through: handle_forage_request_for_kind denies it with its own reason.
    if existing is None or existing.holder == link.warden_id:
        return None
    why = f"grant {existing.id} is held by {existing.holder}, not by this Warden"
    return (await deps.enforcer.refuse(request, "grant_holder", why)).reason


async def _resolve_contested(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    reply: _Reply,
    outcome: ForageRequestOutcome,
) -> None:
    """Judge a contested ForageRequest with an awake episode; grant by shrinking, or deny."""
    wire_request = reply.wire_request
    live = [g for g in deps.ledger.live_grants() if g.id != wire_request.grant_id]
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    event = TriggerEvent(
        kind="forage.contested",
        summary=f"ForageRequest {wire_request.grant_id} ({wire_request.kind.value}) is contested: "
        f"{outcome.reason}",
        clearance=HoneyClearance.C2,
    )
    extras = EpisodeExtras(system_hint=_grants_hint(wire_request, live))
    decision = await decide_awake(deps, event, sources, Effort.HIGH, extras)
    granted = await _apply_shrink_decision(deps, wardens, wire_request, decision)
    if granted is not None:
        await _send_grant(deps, reply, granted, decision.reason)
        return
    reason = decision.reason if decision.reason else outcome.reason
    await _send_denial(deps, reply, reason, contested=True)


async def _apply_shrink_decision(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    wire_request: WireForageRequest,
    decision: QueenDecision,
) -> ForageGrant | None:
    """Shrink the named grant and grow the requester's, or None to deny (module docstring)."""
    if (
        decision.action is not QueenAction.GRANT_BY_SHRINKING
        or decision.shrink_grant_id is None
        or decision.shrink_amount is None
    ):
        return None
    target = deps.ledger.grant(GrantId(decision.shrink_grant_id))
    requester = deps.ledger.grant(wire_request.grant_id)
    if target is None or requester is None or target.id == wire_request.grant_id:
        return None  # Nothing sane to shrink, or the model named the requester's own grant.
    await _shrink_and_notify(deps, wardens, target, decision.shrink_amount, wire_request.kind)
    return await grant_wanted(deps.ledger, requester, wire_request)


async def _shrink_and_notify(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    target: ForageGrant,
    amount: float,
    kind: WireForageRequestKind,
) -> None:
    """Shrink `target` by `amount` in `kind`'s own dimension, commit it, and notify its holder."""
    revised = _shrunk(target, amount, kind)
    await forage_grants.revise(deps.ledger, revised)
    await record_forage_event(
        deps, "forage.granted", revised.id, holder=revised.holder, shrunk_by=amount
    )
    holder_link = wardens.get(revised.holder)
    if holder_link is None:
        return  # Unreachable: the shrink is still committed (mirrors this module's own rule).
    sources = {b.source_id: deps.map.get(b.source_id) for b in revised.allowed}
    await holder_link.transport.send(
        wrap(revised.to_wire(sources), holder_link.hop, clock=deps.clock)
    )


def _shrunk(target: ForageGrant, amount: float, kind: WireForageRequestKind) -> ForageGrant:
    """Return `target` with `amount` subtracted from `kind`'s own dimension, floored at zero."""
    if kind is WireForageRequestKind.SUB_BEES:
        new_value = max(0, target.max_sub_bees - int(amount))
        return target.model_copy(
            update={"max_sub_bees": new_value, "revision": target.revision + 1}
        )
    if kind is WireForageRequestKind.SPEND:
        new_spend = max(0.0, target.spend_budget - amount)
        return target.model_copy(
            update={"spend_budget": new_spend, "revision": target.revision + 1}
        )
    # SHARED_SEATS: shrink every reservation proportionally is out of scope for a first cut;
    # shrinking max_sub_bees by the same amount still frees real headroom for a seats request,
    # since a Warden with fewer sub-bees needs fewer seats too.
    new_value = max(0, target.max_sub_bees - int(amount))
    return target.model_copy(update={"max_sub_bees": new_value, "revision": target.revision + 1})


def _grants_hint(wire_request: WireForageRequest, live: list[ForageGrant]) -> str:
    """Render the live grants in `wire_request`'s own dimension, for the awake episode's prompt."""
    lines = [
        f"Contested {wire_request.kind.value} request on grant {wire_request.grant_id}, wants "
        f"{_wanted_amount(wire_request)}. Other live grants in this dimension:"
    ]
    for grant in live:
        lines.append(
            f"- grant {grant.id} held by {grant.holder}: max_sub_bees={grant.max_sub_bees}, "
            f"spend_budget={grant.spend_budget}"
        )
    return "\n".join(lines)


def _wanted_amount(wire_request: WireForageRequest) -> float:
    """Return the one number `wire_request.wanted` actually asks for, by its own kind."""
    if wire_request.kind is WireForageRequestKind.SUB_BEES:
        return float(wire_request.wanted.sub_bees)
    if wire_request.kind is WireForageRequestKind.SHARED_SEATS:
        return float(wire_request.wanted.seats)
    if wire_request.kind is WireForageRequestKind.SPEND:
        return wire_request.wanted.spend
    return 0.0


async def _send_grant(deps: QueenDeps, reply: _Reply, grant: ForageGrant, reason: str) -> None:
    """Send the fresh GrantIssued, then the matching ForageReply, and record forage.granted."""
    link = reply.link
    sources = {binding.source_id: deps.map.get(binding.source_id) for binding in grant.allowed}
    await link.transport.send(wrap(grant.to_wire(sources), link.hop, clock=deps.clock))
    wire_reply = ForageReply(
        grant_id=grant.id,
        outcome=ForageOutcome.GRANTED,
        granted=_granted_delta_for(reply.wire_request),
        revision=grant.revision,
        expires_at=grant.expires_at,
        reason=reason,
    )
    await link.transport.send(
        wrap(wire_reply, link.hop, clock=deps.clock, correlation_id=reply.request_id)
    )
    await record_forage_event(
        deps, "forage.granted", grant.id, holder=grant.holder, max_sub_bees=grant.max_sub_bees
    )


async def _send_denial(deps: QueenDeps, reply: _Reply, reason: str, *, contested: bool) -> None:
    """Send a ForageReply(DENIED), and record forage.denied (contested or not)."""
    link = reply.link
    wire_request = reply.wire_request
    wire_reply = ForageReply(
        grant_id=wire_request.grant_id,
        outcome=ForageOutcome.DENIED,
        granted=_EMPTY_DELTA,
        revision=None,
        expires_at=None,
        reason=reason,
    )
    await link.transport.send(
        wrap(wire_reply, link.hop, clock=deps.clock, correlation_id=reply.request_id)
    )
    await record_forage_event(
        deps,
        "forage.denied",
        wire_request.grant_id,
        request_kind=wire_request.kind.value,
        contested=contested,
        # Effort.HIGH.value only on the contested branch: codingrules 8.14's own "contested
        # Forage at high [effort]" -- a plain denial never reaches queen.awake, so it has none.
        effort=Effort.HIGH.value if contested else None,
        # This dispatch's own fix: the wire ForageReply already carries `reason`; the trail event
        # needs it too, or "denied with a reason on the trail" (roadmap 4.8's own exit criterion)
        # has nothing to show.
        reason=reason,
    )


def _granted_delta_for(wire_request: WireForageRequest) -> ForageDelta:
    """Build the ForageReply.granted delta actually granted, matching wire_request's own kind."""
    if wire_request.kind is WireForageRequestKind.SUB_BEES:
        return _EMPTY_DELTA.model_copy(update={"sub_bees": wire_request.wanted.sub_bees})
    if wire_request.kind is WireForageRequestKind.SHARED_SEATS:
        return _EMPTY_DELTA.model_copy(
            update={"seats": wire_request.wanted.seats, "source_id": wire_request.wanted.source_id}
        )
    if wire_request.kind is WireForageRequestKind.SPEND:
        return _EMPTY_DELTA.model_copy(update={"spend": wire_request.wanted.spend})
    return _EMPTY_DELTA
