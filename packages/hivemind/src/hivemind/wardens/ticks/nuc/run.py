"""Carry out one swarm.nuc_promote: start the named model server and offer its models, or stop it.

A device becomes a Nuc when its Warden (the always-on supervisor of its Cell) runs a model server
on it, so promotion is the Warden's own act on its own machine, ordered and bounded by the Queen
(the Hive's central orchestrator). ``run_order`` does it, as a job the Warden owns because a server
takes seconds to turn healthy and no tick waits on that. A PROMOTE starts the server the order
names from the device's own manifest (the wire carries only its key; the argv and the port are the
operator's, never the Queen's), offering the models the order lists within the Queen's ceilings,
and replies PROMOTED with what is on offer; it fails closed when she has set no ceilings, when the
server is not one this device declares, when the order is for another Cell or when the machine
cannot be leased. A DEMOTE stops every server the Warden runs, so the device is an ordinary
colonized Cell again and left as it was found. Both are idempotent: a PROMOTE for a server already
up offers any listed model not yet on offer, and a DEMOTE with nothing running just says so. Every
failure is a FAILED reply with a stable word; nothing here raises into the job.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.nuc sub-package. Started by ``handle`` as a job of the Warden. Calls into
    ``hivemind.wardens.local_pool.hosting`` (the host that starts and stops servers),
    ``hivemind.wardens.ticks.lease`` (leasing the Cell again), ``reply`` and waggle only.

Key invariants:
    - A model server is started only through ``ModelServerHost``, so it is within the ceilings and
      stopped with the lease.
    - The wait for the server is the Queen's ``deadline_s`` at most (and the manifest's own
      ``startup_timeout_s`` if shorter): nothing here waits without a bound.
    - The reply's level is the rung the device is on afterwards, read from what runs, so a failed
      PROMOTE of a second server never claims the first is gone.
    - An order for another Cell, or a PROMOTE naming a server the device did not declare, starts
      nothing.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder.
    - hivemind.wardens.local_pool.hosting.host for ``start`` and ``load``.
    - hivemind.queen.swarm_gate.colonize.nuc for the flow that sends the order and awaits the reply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import CellError
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.wardens.local_pool.hosting import HostedCell, ModelServerHost
from hivemind.wardens.ticks.lease import open_lease
from hivemind.wardens.ticks.nuc.reply import demoted, failed, promoted, send_reply
from waggle.ids import MessageId
from waggle.messages.forage import SourceRemovalCause
from waggle.messages.swarm import NucAction, NucPromote, NucPromoted, RuntimeLevel

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

log = get_logger(__name__)

__all__ = ["live_host", "run_order"]


async def run_order(warden: Warden, request_id: MessageId, order: NucPromote) -> None:
    """Carry out ``order`` and answer it; every failure becomes a FAILED reply.

    Args:
        warden: The owning Warden (read and written directly; see the ticks package docstring).
        request_id: The envelope id of the order, which the reply is correlated to.
        order: The Queen's order.
    """
    try:
        if order.action is NucAction.PROMOTE:
            reply = await _promote(warden, order)
        else:
            reply = await _demote(warden, order)
    except (CellError, HiveMindError) as error:
        # The type only: a device error can carry text the device wrote.
        log.warning(
            "warden.nuc_order_failed", action=order.action.value, error=type(error).__name__
        )
        reply = failed(order, _level_of(warden), type(error).__name__)
    await send_reply(warden, request_id, reply)


async def live_host(warden: Warden) -> ModelServerHost | None:
    """Return a model host over a lease this Warden holds now, leasing the Cell again if need be.

    A device's Warden gives its lease back when it is idle, and a host built over that lease holds
    a session that is closed; with nothing running there is nothing to carry over, so a fresh
    host is built over the new lease.

    Args:
        warden: The owning Warden.

    Returns:
        The host, or None when the Cell cannot be leased (the lease was refused or the device
        went away).
    """
    if warden._lease is None:
        # An idle device Warden released its lease; the server needs the machine back.
        await open_lease(warden)
    cell, session = warden._cell, warden._session
    if warden._lease is None or cell is None or session is None:
        return None
    host = warden._models
    if host is None or not host.running:
        place = HostedCell(cell=cell, session=session, ceilings_of=lambda: warden._ceilings)
        host = ModelServerHost(warden._deps, warden._warden_id, place)
        warden._models = host
    return host


async def _promote(warden: Warden, order: NucPromote) -> NucPromoted:
    """Start the server the order names and offer its models, or say why not."""
    server_id = order.server_id
    level = _level_of(warden)
    if _other_cell(warden, order):
        return failed(order, level, "wrong_cell")
    if server_id is None or server_id not in warden._deps.servers:
        return failed(order, level, "server_unknown")
    if warden._ceilings is None:
        # No ceilings, no allowlist: the Queen sets them before she orders a promotion.
        return failed(order, level, "no_ceilings")
    host = await live_host(warden)
    if host is None:
        return failed(order, level, "cell_unavailable")
    already_up = server_id in host.running
    # External: a local process start and its health wait, bounded by the order's own deadline.
    if not await host.start(server_id, only=order.initial_models, deadline_s=order.deadline_s):
        return failed(order, _level_of(warden), "server_not_started")
    if already_up:
        # A repeated order: the start did nothing, so offer what the listed models still lack.
        for source_id in order.initial_models:
            await host.load(server_id, source_id)
    return promoted(order, tuple(s.source_id for s in host.running[server_id].sources))


async def _demote(warden: Warden, order: NucPromote) -> NucPromoted:
    """Stop every server this Warden runs; the device is a colonized Cell again."""
    if _other_cell(warden, order):
        return failed(order, _level_of(warden), "wrong_cell")
    if warden._models is not None:
        # The sources leave the map and the Queen is told, then the processes stop.
        await warden._models.stop_all(SourceRemovalCause.SERVER_STOPPED)
    return demoted(order)


def _other_cell(warden: Warden, order: NucPromote) -> bool:
    """Whether the order names a Cell other than the one this Warden supervises."""
    cell = warden._cell
    # Not yet leased (an idle release before the first task): the lease about to be opened is
    # of the Cell the device offers, and `open_lease` takes no other.
    return cell is not None and order.cell_id != cell.id


def _level_of(warden: Warden) -> RuntimeLevel:
    """The rung this Warden's device is on: a Nuc exactly while a model server runs on it."""
    running = warden._models is not None and bool(warden._models.running)
    return RuntimeLevel.NUC if running else RuntimeLevel.COLONIZED
