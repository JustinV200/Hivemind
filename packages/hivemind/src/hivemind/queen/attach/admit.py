"""Define attach_warden and detach_warden: admit one Warden to a Queen, and remove it again.

`attach_warden` is `hivemind.queen.queen.Queen.attach_warden`'s own body (roadmap step 10.3): the
`warden_spawn` enforcement point (ADR-0039). The Queen never creates Wardens or Cells herself
(CLAUDE.md), but every Warden she supervises joins her tree only here, so this is where she
checks that her own set holds `warden:spawn` through the Guard's `Enforcer` (a refusal is a
`guard.denied` on the trail and a `WardenSpawnRefusedError` to the caller, and nothing is
attached), starts the link's own reader task (`hivemind.queen.inbox.links.LinkReaders`, so
everything the Warden sends is heard from now on, drained whole by her next tick), records the
declared `warden.spawned` event, and (roadmap step 10.6a) sends the Warden its Cell's taint order
again when an isolation stands on it (`hivemind.queen.isolation.resend_taint_order`: an order lost
to a closed link reaches the Warden when its link comes back). `detach_warden` is its mirror,
roadmap step 5.6's own new edge -- the Cell gate calls it once a Virtual Cell's accepted connection
ends, so a dead link is never drained again on the next tick. Both are free functions taking
`queen: Queen` rather than method bodies on `Queen` itself: `hivemind.queen.queen`'s own file sits
near codingrules 5.1's 300-line file cap, and neither body fits there without pushing it over;
`queen.py`'s own `_stop_queen`/`_send_intervene` already live as module-level delegates for the
same class-size reason, these are one file further out for the same file-size reason.
`Queen.attach_warden` is a one-line delegator to `attach_warden`.

**Reconnection** (roadmap 11.9, ADR-0053). A remote Warden that loses its link dials again with
the id it has always had, and the Queen must recognise it rather than meet a stranger. Attaching a
`warden_id` she already knows (attached with a dead link, which the new link replaces; or attached
earlier in this process's life and detached since) is a reconnection: the new link replaces the old
one, `warden.reconnected` is recorded instead of `warden.spawned`, the dispatcher's hold on the
Warden's Cell is lifted so it resumes what it was holding (`DispatchBook.held`), and, as for every
attach whose Warden the Forage ledger already holds decisions for, the current ceilings, hosting
plan and live grants are sent again (`hivemind.queen.forage.resend`), closing the gap that
ceilings were only ever sent once per Queen life: a Warden that restarted, or that a restarted Queen
meets for the first time, would otherwise run on whatever it last remembered.

Attaching is also the first moment the Queen hears a Night Veil Cell's own word about its tier
(the `CellReady` it announced, carried on the link's Cell), often before the lifecycle has seen the
Cell at all, so `attach_warden` opens the Cell's ephemeral segment and files the Warden under it
first (codingrules section 12): every record naming either, `warden.spawned` among them, then
reaches the trail as the skeleton only.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.attach`.
    Called by `hivemind.queen.queen.Queen.attach_warden` (attach), by `hivemind.queen.attach.serve`
    and the swarm gate's Warden table (detach). Calls into `hivemind.cell` (CombShieldLevel),
    `hivemind.guard` (EnforcementPoint), `hivemind.pheromone` (WardenEvent, segments_of: the Night
    Veil segments behind her trail), `hivemind.queen.authority`, `.errors`, `.forage.resend`,
    `.inbox.links` (through the Queen's own `_links`), `.isolation` (resend_taint_order) and
    `.ticks.liveness` only; reaches into `Queen`'s own private attributes directly, the same
    cross-file access `hivemind.wardens.ticks.control` already takes on `Warden`'s private state
    for the identical reason (module docstring there).

Key invariants:
    - `attach_warden` attaches nothing when the Queen's set refuses `warden:spawn`: the refusal is
      on the trail before `WardenSpawnRefusedError` leaves, and `warden.spawned` or
      `warden.reconnected` is recorded only for a Warden actually attached.
    - `warden_state` reads only what a Warden said itself: nothing is inferred for a silent one.
    - A Warden is attached under one link at a time: a reconnection replaces the old link, and the
      old link's reader is reaped (`LinkReaders.add` removes it first).
    - Never raises for a `warden_id` that is not currently attached: a caller may detach
      defensively (e.g. a connection that never finished the readiness handshake).
    - A reader task exists exactly while its Warden is attached: `attach_warden` starts it and
      `detach_warden` always cancels and reaps it, never leaving it cancelled-but-unawaited
      (codingrules section 11).
    - `detach_if_current` detaches a Warden only while the link it is given is still the Warden's
      own, so a connection that ends after its replacement attached takes nothing away.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the reconnection rule.
    - hivemind.queen.queen for Queen.attach_warden, the edge this mirrors.
    - hivemind.queen.attach.serve for the helper that attaches a remote Warden's connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.cell import CombShieldLevel
from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint
from hivemind.pheromone import WardenEvent, segments_of
from hivemind.queen.authority import queen_held, request_for
from hivemind.queen.errors import WardenSpawnRefusedError
from hivemind.queen.forage import Resent, resend_state
from hivemind.queen.isolation import resend_taint_order
from hivemind.queen.ticks.liveness import WardenLiveness
from hivemind.queen.ticks.tools import announce_current_tools, forget_warden
from waggle.ids import WardenId, new_event_id
from waggle.messages.supervision import WardenState

if TYPE_CHECKING:
    from hivemind.queen.deps import WardenLink
    from hivemind.queen.queen import Queen

# The one capability admitting a Warden to the Queen's tree needs (ADR-0039's warden:spawn flag).
_WARDEN_SPAWN = Capability(family=CapabilityFamily.WARDEN_SPAWN)

__all__ = [
    "attach_warden",
    "current_link",
    "detach_if_current",
    "detach_warden",
    "warden_state",
]


async def attach_warden(queen: Queen, link: WardenLink) -> bool:
    """Admit one already-built Warden link to `queen`, once her set holds `warden:spawn`.

    A Warden the Queen already knows is reconnected rather than spawned (module docstring).

    Args:
        queen: The Queen to attach `link` to.
        link: The Warden's own address, Cell and Waggle link.

    Returns:
        True when the Warden was a reconnection (she had it attached, or had it earlier in this
        life), False when it is new to her. A gate reads it to tell the transport that a
        duplicate frame is the expected replay of a reconnection.

    Raises:
        WardenSpawnRefusedError: The Queen's set does not hold `warden:spawn` (or the deny list
            covers it); `guard.denied` is already recorded and nothing was attached.
    """
    deps = queen._deps
    await _admit(queen, link)
    # Known before the bookkeeping below changes what "known" reads: a Warden this Queen has had
    # attached at any time in this life (still attached with a dead link, or detached since).
    returning = link.warden_id in queen._seen
    queen._seen.add(link.warden_id)
    # Bookkeeping first, then the trail row: the event records a Warden that is really attached.
    queen._wardens[link.warden_id] = link
    queen._liveness[link.warden_id] = WardenLiveness(
        last_heartbeat_at=None, missed_heartbeats=0, is_offline=False
    )
    # Heard from now on, whether or not her tick is busy: one reader task for this link (a
    # reconnection's replaces the old one, which `add` reaps first).
    await queen._links.add(link.warden_id, link.transport)
    # What the ledger holds for this Warden goes to it again, whichever way it arrived: a returning
    # Warden, a restarted one, or one a restarted Queen meets for the first time.
    resent = await resend_state(deps, link)
    if returning:
        deps.dispatch.held.release(link.cell.id)  # The dispatcher resumes what it was holding.
        await _record_reconnected(queen, link, resent)
    else:
        await _record_spawned(queen, link)
    # An isolation standing on its Cell: the taint order may have been lost with the old link.
    await resend_taint_order(deps, link)
    # Phase 9: a Cell attached after a promotion gets every current hive-scope tool it can run,
    # as a job: shipping waits on this very Warden's acks, which her tick has not read yet.
    await announce_current_tools(deps, link)
    return returning


async def _admit(queen: Queen, link: WardenLink) -> None:
    """Veil a Night Veil Cell's Warden, then let the Guard decide whether it may join her tree."""
    deps = queen._deps
    # Codingrules 12: a Night Veil Cell's Warden is veiled before a single record names it (its
    # `warden.spawned` included), even when it dials in before the lifecycle has seen its Cell.
    night_veil = segments_of(deps.trail)
    if night_veil is not None and link.cell.comb_shield is CombShieldLevel.NIGHT_VEIL:
        night_veil.open(link.cell.id)
        night_veil.file(link.warden_id, link.cell.id)
    request = request_for(deps, EnforcementPoint.WARDEN_SPAWN, _WARDEN_SPAWN, queen_held(deps))
    # The Cell the Warden supervises is where the action lands: its tier and access level are the
    # context a floor (roadmap step 10.3a) will read.
    context = request.context.model_copy(
        update={"comb_shield": link.cell.comb_shield, "access_level": link.cell.access_level}
    )
    decision = await deps.enforcer.check(request.model_copy(update={"context": context}))
    if not decision.allowed:
        raise WardenSpawnRefusedError(link.warden_id, decision.reason)


async def detach_warden(queen: Queen, warden_id: WardenId) -> None:
    """Remove `warden_id`'s own bookkeeping from `queen` and reap its link's reader task.

    Does not close the transport itself (the caller already did, or is about to) and does not
    touch the Brood Chamber or any Cell record; a later phase's Undertaker sweep reconciles those.

    Args:
        queen: The Queen to detach `warden_id` from.
        warden_id: The Warden to detach.
    """
    for table in (queen._wardens, queen._liveness, queen._last_heartbeat):
        table.pop(warden_id, None)
    # Whatever the link still had queued goes with it: nothing drains a detached Warden again.
    await queen._links.remove(warden_id)
    # Phase 9: a shipment or a Comb order waiting on this Warden fails now, not after its timeout.
    forget_warden(queen._deps, warden_id)


def current_link(queen: Queen, warden_id: WardenId) -> WardenLink | None:
    """Return the link `warden_id` is attached under right now, or None when it has none.

    Args:
        queen: The Queen the Warden may be attached to.
        warden_id: The Warden.
    """
    return queen._wardens.get(warden_id)


def warden_state(queen: Queen, warden_id: WardenId) -> WardenState | None:
    """Return the state `warden_id` last reported in a Heartbeat, or None before any.

    Args:
        queen: The Queen the Warden is attached to.
        warden_id: The Warden.

    Returns:
        Its own word for where it is (`WATCH` while it holds no work and observes), as of its newest
        Heartbeat the Queen has handled; None when it is not attached or has sent none yet.
    """
    heartbeat = queen._last_heartbeat.get(warden_id)
    return heartbeat.warden_state if heartbeat is not None else None


async def detach_if_current(queen: Queen, link: WardenLink) -> bool:
    """Detach `link`'s Warden, unless a newer link has replaced `link` as its own.

    A connection that ends after the Warden reconnected on another one must take nothing away: the
    Warden is attached under the new link, and detaching it would orphan a live Warden.

    Args:
        queen: The Queen the Warden is attached to.
        link: The link whose connection just ended.

    Returns:
        True when `link` was current and the Warden is now detached; False when it was not.
    """
    if queen._wardens.get(link.warden_id) is not link:
        return False
    await detach_warden(queen, link.warden_id)
    return True


async def _record_spawned(queen: Queen, link: WardenLink) -> None:
    """Record the declared `warden.spawned` event for a Warden just admitted to the Queen's tree.

    Names the Cell and, when the link proved one, the node the Warden signs as: the Queen's own
    record of which node speaks for which Cell, the one the Guard Bee attributes events by.
    """
    await _record(queen, link, "warden.spawned", {})


async def _record_reconnected(queen: Queen, link: WardenLink, resent: Resent) -> None:
    """Record `warden.reconnected`: the Warden is back, with what the Queen sent it again.

    Carries the same Cell and node as `warden.spawned` (so the Guard Bee learns a node that took
    over a Warden, a device's after a migration, binds to the Cell) and the counts of what was
    re-sent, never any of it.
    """
    extra: dict[str, JsonValue] = {
        "ceilings": resent.ceilings,
        "plan": resent.plan,
        "grants": resent.grants,
    }
    await _record(queen, link, "warden.reconnected", extra)


async def _record(queen: Queen, link: WardenLink, kind: str, extra: dict[str, JsonValue]) -> None:
    """Record one `warden.*` event about `link`'s Warden on the Queen's own trail."""
    deps = queen._deps
    payload: dict[str, JsonValue] = {"cell_id": link.cell.id, **extra}
    if link.node_id is not None:
        payload["node_id"] = link.node_id
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=link.warden_id,
        payload=payload,
    )
    await deps.trail.record(event)
