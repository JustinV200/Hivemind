"""Provide `hive basket ship`, `evict` and `retire`: queue a shipment, free a copy, withdraw a blob.

The operator's three acts on the Basket's standing, each recorded on the trail as the actor `human`.
`ship SHA CELL_ID --reason TEXT` queues a shipment (`BasketStore.queue_shipment`) and prints its id:
the running Queen's basket tick drains the queue and moves the bytes (wave 3G), so this command
moves nothing itself; a retired or tainted blob is refused here, since neither is ever shipped.
`evict SHA [--cell ID]` forgets that a Cell holds a resident copy (the Hive Stand's own, by
default) and records `basket.evicted`; the blob itself is untouched. `retire SHA --reason TEXT`
withdraws a blob: its row and bytes stay, but readers are refused and it is never shipped or loaded
again; it records `basket.retired`. Retiring is idempotent, and a blob already retired records
nothing new. SHA is the full hash or any unambiguous prefix.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `basket` command group. Mounted by
    `hivemind.cli.basket` as `ship`, `evict` and `retire`. Calls into `hivemind.basket`,
    `hivemind.cell`, `hivemind.cli.stores` (the trail), `hivemind.pheromone`, and this group's own
    `context` and `render` only.

Key invariants:
    - Events carry ids, the hash, sizes and labels; never a reason's text (the store keeps that on
      the blob's row, for the human) and never bytes.
    - A refusal (exit 1, with the Basket's stable code) records nothing.
    - The store is the only way a blob's standing changes; nothing here edits a row.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for residency and shipping.
    - hivemind.basket.store.protocol for BasketStore.queue_shipment, .evict and .retire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import typer

from hivemind.basket import BasketStore, BlobRefusedError, BlobState, Shipment
from hivemind.basket.events import EventStamp, basket_event, blob_facts
from hivemind.cell import CellIdentity, hive_stand_cell_id
from hivemind.cli.basket.context import BasketCliContext, cli_context, open_store, resolve_sha256
from hivemind.cli.basket.render import print_shipment
from hivemind.cli.stores import open_trail
from hivemind.cli.tools.context import HUMAN_ACTOR, cell_id_of, parse_cell_id, run_or_exit
from hivemind.pheromone import PheromoneTrail
from waggle.clock import SystemClock
from waggle.ids import CellId

__all__ = ["evict_command", "retire_command", "ship_command"]

ShaArgument = Annotated[str, typer.Argument(help="A blob's sha256, or the front of it.")]


def _nonblank(value: str) -> str:
    """Option callback: a reason the human gave must say something."""
    # The store keeps whatever it is given; a blank reason would leave the row saying nothing.
    if not value.strip():
        raise typer.BadParameter("a reason must not be blank.")
    return value


ReasonOption = Annotated[
    str,
    typer.Option("--reason", help="Why; kept on the blob's row or queue.", callback=_nonblank),
]


@dataclass(frozen=True, slots=True)
class _Desk:
    """What an act on the Basket needs: the store, the trail, and who is recording."""

    store: BasketStore  # The Basket the act changes.
    trail: PheromoneTrail  # Where the act's event lands.
    stamp: EventStamp  # The Hive, node and actor `human` every event is stamped with.
    hive_stand: CellId  # The Hive Stand's Cell: the default residency to evict.


def ship_command(
    ctx: typer.Context,
    sha256: ShaArgument,
    cell_id: Annotated[str, typer.Argument(help="The Cell that should end up holding the blob.")],
    reason: ReasonOption,
) -> None:
    """Queue a shipment of one blob to one Cell; the running Queen's basket tick moves it."""
    destination = parse_cell_id(cell_id)
    desk = _desk(cli_context(ctx))
    print_shipment(run_or_exit(_ship(desk, sha256, destination, reason)))


def evict_command(
    ctx: typer.Context,
    sha256: ShaArgument,
    cell: Annotated[
        str | None, typer.Option("--cell", help="The Cell to free (default: the Hive Stand).")
    ] = None,
) -> None:
    """Forget that a Cell holds a resident copy of a blob (the Hive Stand's by default)."""
    desk = _desk(cli_context(ctx))
    target = cell_id_of(cell) or desk.hive_stand
    blob_sha, was_resident = run_or_exit(_evict(desk, sha256, target))
    verb = "evicted from" if was_resident else "was not resident in"
    typer.echo(f"blob {blob_sha} {verb} {target}")


def retire_command(ctx: typer.Context, sha256: ShaArgument, reason: ReasonOption) -> None:
    """Withdraw a blob: the row and bytes stay, readers are refused, it is never shipped again."""
    desk = _desk(cli_context(ctx))
    blob_sha, was_new = run_or_exit(_retire(desk, sha256, reason))
    typer.echo(f"blob {blob_sha} {'retired' if was_new else 'was already retired'}")


def _desk(cli_ctx: BasketCliContext) -> _Desk:
    """Open the store and the trail, and the identity the operator's events carry."""
    manifest = cli_ctx.manifest
    identity = CellIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=HUMAN_ACTOR
    )
    return _Desk(
        store=open_store(cli_ctx),
        trail=open_trail(cli_ctx.db),
        stamp=EventStamp(identity, SystemClock()),
        hive_stand=hive_stand_cell_id(manifest.hive.node_id),
    )


async def _ship(desk: _Desk, text: str, destination: CellId, reason: str) -> Shipment:
    """Queue the shipment after checking the blob may be shipped at all."""
    sha256 = await resolve_sha256(desk.store, text)
    blob = await desk.store.get(sha256)
    # Neither a withdrawn blob nor a tainted one is ever shipped (ADR-0048); refuse before the
    # queue, so the Queen's tick never has to.
    if blob.state is BlobState.RETIRED:
        raise BlobRefusedError("retired", f"blob {sha256} is retired and is never shipped.")
    if blob.tainted:
        raise BlobRefusedError("tainted", f"blob {sha256} is tainted and is never shipped.")
    return await desk.store.queue_shipment(sha256, destination, reason)


async def _evict(desk: _Desk, text: str, cell_id: CellId) -> tuple[str, bool]:
    """Free the Cell's resident copy; return the blob's hash and whether a copy was there."""
    sha256 = await resolve_sha256(desk.store, text)
    blob = await desk.store.get(sha256)
    held = any(row.sha256 == sha256 for row in await desk.store.resident(cell_id))
    await desk.store.evict(cell_id, sha256)
    # The event says a copy was freed, so a no-op (nothing was resident) records nothing.
    if held:
        facts = blob_facts(blob)
        await desk.trail.record(basket_event(desk.stamp, "basket.evicted", cell_id, facts))
    return sha256, held


async def _retire(desk: _Desk, text: str, reason: str) -> tuple[str, bool]:
    """Withdraw the blob; return its hash and whether this call was the one that retired it."""
    sha256 = await resolve_sha256(desk.store, text)
    before = await desk.store.get(sha256)
    blob = await desk.store.retire(sha256, reason)
    # Retiring is idempotent, and the trail records the act once: the facts are the blob's, and
    # the reason stays on the row for the human (the store's own rule), never on the trail.
    was_new = before.state is not BlobState.RETIRED
    if was_new:
        await desk.trail.record(
            basket_event(desk.stamp, "basket.retired", blob.origin_cell_id, blob_facts(blob))
        )
    return sha256, was_new
