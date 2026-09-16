"""Define handle_forage_request: the Queen's own tick handler for a Warden's ForageRequest.

Roadmap step 4.7's own dispatch map: within headroom, autopilot grants at once, with no awake
episode; contested (cannot be met from headroom alone, but could be by shrinking another live
grant), the request needs judgement, run at `Effort.HIGH` (codingrules section 8.14, "contested
Forage" at the Queen's own highest effort); otherwise, denied, with a reason either way. This
module is the wire-and-trail half of that: it records `forage.requested` on receipt, calls
`hivemind.queen.forage.requests.handle_sub_bee_request` for the decision (and, on a grant, the
ledger update), then sends the wire reply -- a fresh `GrantIssued` plus a `ForageReply(GRANTED)`
on a grant, or a `ForageReply(DENIED)` otherwise -- and records `forage.granted`/`forage.denied`.
The contested case is recorded as `forage.denied` too, with `contested=True` and the effort class
in its payload: no `forage.*` kind for "needs judgement" exists in
`hivemind.pheromone.ForageEvent.KINDS`, and that file is outside this dispatch's own list to add
one to (flagged in this dispatch's own report, alongside the larger gap: `hivemind.queen.awake`
holds no action to shrink another live grant, so this module does not run an awake episode for
the contested case at all -- roadmap step 4.7 asks only that it "go to NEEDS_JUDGEMENT with an
effort class", which the trail payload alone already satisfies, not that queen.awake resolve it).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s tick for every received
    `waggle.messages.forage.ForageRequest`, ahead of `hivemind.queen.autopilot.table.decide` (whose
    own fallback for an unrecognised payload type is `NEEDS_JUDGEMENT` outright -- exactly what
    "within headroom, no awake episode" must not become). Calls into `hivemind.forage.slots`
    (Effort), `hivemind.queen.autopilot` (ForageAutopilotOutcome), `hivemind.queen.deps`
    (QueenDeps, WardenLink), `hivemind.queen.forage.requests` (ForageRequestOutcome,
    handle_sub_bee_request), `hivemind.queen.trail` (record_forage_event) and waggle only.

Key invariants:
    - Every branch sends exactly one wire reply and records exactly one `forage.*` trail event;
      neither a grant nor a denial is ever left silent.
    - A GRANT always sends the fresh `GrantIssued` before the `ForageReply` that names its
      revision, mirroring `hivemind.queen.dispatcher`'s own "grant before assignment" ordering.

See Also:
    - .claude/roadmap.md step 4.7 for the dispatch map this module implements.
    - .claude/codingrules.md section 8.14 for "the Queen tunes her own effort... contested Forage
      at high".
    - hivemind.queen.forage.requests for the decision and ledger-update logic this module wires.
    - hivemind.queen.dispatcher for the sibling "grant, then message" ordering this module mirrors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from hivemind.forage.slots import Effort
from hivemind.queen.autopilot import ForageAutopilotOutcome
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage.requests import ForageRequestOutcome, handle_sub_bee_request
from hivemind.queen.trail import record_forage_event
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import wrap
from waggle.ids import MessageId, WardenId
from waggle.messages.forage import ForageOutcome, ForageReply
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.forage.values import ForageDelta

__all__ = ["handle_forage_request", "handle_forage_request_for_item"]

_EMPTY_DELTA = ForageDelta(
    seats=0, source_id=None, spend=0.0, tokens=0, sub_bees=0, slot=None, minimum_grade=None
)


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
        await handle_forage_request(deps, link, wire_request, MessageId(item.id))


async def handle_forage_request(
    deps: QueenDeps, link: WardenLink, wire_request: WireForageRequest, request_id: MessageId
) -> None:
    """Answer one Warden's ForageRequest: grant within headroom, deny (or defer) otherwise.

    Args:
        deps: The Queen's collaborators.
        link: The requesting Warden's own link; the reply and any fresh GrantIssued go here.
        wire_request: The Warden's own request.
        request_id: The request's own envelope id: `ForageReply` is a reply
            (`waggle.envelope.MessageShape.REPLY`) and must carry the correlation_id of the
            request it answers.
    """
    await record_forage_event(
        deps, "forage.requested", wire_request.grant_id, request_kind=wire_request.kind.value
    )
    outcome = await handle_sub_bee_request(deps.ledger, deps, wire_request)
    if outcome.autopilot_outcome is ForageAutopilotOutcome.GRANT and outcome.grant is not None:
        await _send_grant(deps, link, wire_request, outcome, request_id)
        return
    await _send_denial(deps, link, wire_request, outcome, request_id)


async def _send_grant(
    deps: QueenDeps,
    link: WardenLink,
    wire_request: WireForageRequest,
    outcome: ForageRequestOutcome,
    request_id: MessageId,
) -> None:
    """Send the fresh GrantIssued, then the matching ForageReply, and record forage.granted."""
    grant = outcome.grant
    if grant is None:
        # Unreachable: handle_forage_request only calls this branch once it has already checked
        # outcome.grant is not None. A plain raise, not assert (never stripped under -O).
        raise RuntimeError("_send_grant called with no grant on the outcome.")
    sources = {binding.source_id: deps.map.get(binding.source_id) for binding in grant.allowed}
    await link.transport.send(wrap(grant.to_wire(sources), link.hop, clock=deps.clock))
    granted_delta = _EMPTY_DELTA.model_copy(update={"sub_bees": wire_request.wanted.sub_bees})
    reply = ForageReply(
        grant_id=grant.id,
        outcome=ForageOutcome.GRANTED,
        granted=granted_delta,
        revision=grant.revision,
        expires_at=grant.expires_at,
        reason=outcome.reason,
    )
    await link.transport.send(wrap(reply, link.hop, clock=deps.clock, correlation_id=request_id))
    await record_forage_event(
        deps, "forage.granted", grant.id, holder=grant.holder, max_sub_bees=grant.max_sub_bees
    )


async def _send_denial(
    deps: QueenDeps,
    link: WardenLink,
    wire_request: WireForageRequest,
    outcome: ForageRequestOutcome,
    request_id: MessageId,
) -> None:
    """Send a ForageReply(DENIED), and record forage.denied (contested or not)."""
    reply = ForageReply(
        grant_id=wire_request.grant_id,
        outcome=ForageOutcome.DENIED,
        granted=_EMPTY_DELTA,
        revision=None,
        expires_at=None,
        reason=outcome.reason,
    )
    await link.transport.send(wrap(reply, link.hop, clock=deps.clock, correlation_id=request_id))
    contested = outcome.autopilot_outcome is ForageAutopilotOutcome.NEEDS_JUDGEMENT
    await record_forage_event(
        deps,
        "forage.denied",
        wire_request.grant_id,
        request_kind=wire_request.kind.value,
        contested=contested,
        # Effort.HIGH.value only on the contested branch: codingrules 8.14's own "contested
        # Forage at high [effort]" -- a plain denial never reaches queen.awake, so it has none.
        effort=Effort.HIGH.value if contested else None,
    )
