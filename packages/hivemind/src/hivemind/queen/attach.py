"""Define attach_warden and detach_warden: admit one Warden to a Queen, and remove it again.

`attach_warden` is `hivemind.queen.queen.Queen.attach_warden`'s own body (roadmap step 10.3): the
`warden_spawn` enforcement point (ADR-0031). The Queen never creates Wardens or Cells herself
(CLAUDE.md), but every Warden she supervises joins her tree only here, so this is where she
checks that her own set holds `warden:spawn` through the Guard's `Enforcer` (a refusal is a
`guard.denied` on the trail and a `WardenSpawnRefusedError` to the caller, and nothing is
attached), starts the link's own reader task (`hivemind.queen.inbox.links.LinkReaders`, so
everything the Warden sends is heard from now on, drained whole by her next tick), and records
the declared `warden.spawned` event, and (roadmap step 10.6a) sends the Warden its Cell's taint
order again when an isolation stands on it (`hivemind.queen.isolation.resend_taint_order`: an order
lost to a closed link reaches the Warden when its link comes back). `detach_warden` is its mirror,
roadmap step 5.6's own new edge -- `hivemind.queen.cell_gate.CellListener` calls it once a Virtual
Cell's accepted connection ends, so a dead link is never drained again on the next tick. Both are
free functions taking `queen: Queen` rather than method bodies on `Queen` itself:
`hivemind.queen.queen`'s own file sits near codingrules 5.1's 300-line file cap, and neither body
(a Guard check and a trail row; reaping the Warden's reader task) fits there without pushing it
over; `queen.py`'s own `_stop_queen`/`_send_intervene` already live as module-level delegates for
the same class-size reason, these are one file further out for the same file-size reason.
`CellListener` (and any test) calls `hivemind.queen.attach.detach_warden(queen, warden_id)`
directly; `Queen.attach_warden` is a one-line delegator to `attach_warden`.

Attaching is also the first moment the Queen hears a Night Veil Cell's own word about its tier
(the `CellReady` it announced, carried on the link's Cell), often before the lifecycle has seen the
Cell at all, so `attach_warden` opens the Cell's ephemeral segment and files the Warden under it
first (codingrules section 12): every record naming either, `warden.spawned` among them, then
reaches the trail as the skeleton only.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen.Queen.attach_warden` (attach) and `hivemind.queen.cell_gate.
    CellListener` on every accepted connection's own close (detach). Calls into
    `hivemind.cell` (CombShieldLevel), `hivemind.guard` (EnforcementPoint), `hivemind.pheromone`
    (WardenEvent, segments_of: the Night Veil segments behind her trail),
    `hivemind.queen.authority`, `.errors`, `.inbox.links` (through the Queen's own `_links`),
    `.isolation` (resend_taint_order) and `.ticks.liveness` only; reaches into `Queen`'s own
    private attributes directly, the same cross-file access `hivemind.wardens.ticks.control`
    already takes on `Warden`'s private state for the identical reason (module docstring there).

Key invariants:
    - `attach_warden` attaches nothing when the Queen's set refuses `warden:spawn`: the refusal is
      on the trail before `WardenSpawnRefusedError` leaves, and `warden.spawned` is recorded only
      for a Warden actually attached.
    - Never raises for a `warden_id` that is not currently attached: a caller may detach
      defensively (e.g. a connection that never finished the readiness handshake).
    - A reader task exists exactly while its Warden is attached: `attach_warden` starts it and
      `detach_warden` always cancels and reaps it, never leaving it cancelled-but-unawaited
      (codingrules section 11).

See Also:
    - hivemind.queen.queen for Queen.attach_warden, the edge this mirrors.
    - hivemind.queen.cell_gate for CellListener, this function's one caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.cell import CombShieldLevel
from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint
from hivemind.pheromone import WardenEvent, segments_of
from hivemind.queen.authority import queen_held, request_for
from hivemind.queen.errors import WardenSpawnRefusedError
from hivemind.queen.isolation import resend_taint_order
from hivemind.queen.ticks.liveness import WardenLiveness
from waggle.ids import WardenId, new_event_id

if TYPE_CHECKING:
    from hivemind.queen.deps import WardenLink
    from hivemind.queen.queen import Queen

# The one capability admitting a Warden to the Queen's tree needs (ADR-0031's warden:spawn flag).
_WARDEN_SPAWN = Capability(family=CapabilityFamily.WARDEN_SPAWN)

__all__ = ["attach_warden", "detach_warden"]


async def attach_warden(queen: Queen, link: WardenLink) -> None:
    """Admit one already-built Warden link to `queen`, once her set holds `warden:spawn`.

    Args:
        queen: The Queen to attach `link` to.
        link: The Warden's own address, Cell and Waggle link.

    Raises:
        WardenSpawnRefusedError: The Queen's set does not hold `warden:spawn` (or the deny list
            covers it); `guard.denied` is already recorded and nothing was attached.
    """
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
    # Bookkeeping first, then the trail row: the event records a Warden that is really attached.
    queen._wardens[link.warden_id] = link
    queen._liveness[link.warden_id] = WardenLiveness(
        last_heartbeat_at=None, missed_heartbeats=0, is_offline=False
    )
    # Heard from now on, whether or not her tick is busy: one reader task for this link.
    await queen._links.add(link.warden_id, link.transport)
    await _record_spawned(queen, link)
    # An isolation standing on its Cell: the taint order may have been lost with the old link.
    await resend_taint_order(deps, link)


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


async def _record_spawned(queen: Queen, link: WardenLink) -> None:
    """Record the declared `warden.spawned` event for a Warden just admitted to the Queen's tree.

    Names the Cell and, when the link proved one, the node the Warden signs as: the Queen's own
    record of which node speaks for which Cell, the one the Guard Bee attributes events by.
    """
    deps = queen._deps
    payload: dict[str, JsonValue] = {"cell_id": link.cell.id}
    if link.node_id is not None:
        payload["node_id"] = link.node_id
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="warden.spawned",
        subject_id=link.warden_id,
        payload=payload,
    )
    await deps.trail.record(event)
