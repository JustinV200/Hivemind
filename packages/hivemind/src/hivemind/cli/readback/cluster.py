"""Provide `hive cluster [--provider]`, `hive cluster status` and `hive wake [provider]`.

docs/adr/0024-clustering-protocol.md's second decision: `hive cluster`/`hive wake` run in a second
process from the running Queen (`hive run`), so an operator order cannot reach her through a direct
call -- it is written as a durable `hivemind.queen.cluster.ClusterOrder` row
(`hivemind.cli.stores.open_cluster_orders`) the running Queen's own `run_cluster_tick` polls every
tick and acts on as an inbox item. `cluster [--provider]` and `wake [provider]` each append one
order and print its id; naming no provider means "every currently-bound one" for `cluster` and
"every currently clustered one" for `wake` (`ClusterOrder.provider`'s own docstring; `cluster_
callback`'s own docstring explains why `cluster` takes `--provider` as an option while `wake`
keeps it as a bare positional). `cluster status`
prints every still-pending order plus, reconstructed from the trail the same way
`hivemind.cli.readback.inbox` reconstructs escalated Alarms, every `queen.clustered`/`queen.
resumed` event a running Queen has already acted on -- there is no `OrderStore` method to list a
*handled* order by itself (only `pending()`), and the trail already carries the same fact durably,
recorded by the same tick that marks the order handled (docs/adr/0024's own "every pause and resume
is on the trail per bee").

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.queen.cluster` (ClusterOrder,
    OrderKind), `hivemind.pheromone` (TrailQuery) and `hivemind.cli.stores` only.

Key invariants:
    - Neither command ever starts a Queen: both only append a durable row and print its id
      (docs/adr/0024's "operator orders are durable rows the running Queen polls, not a second
      Queen"), matching this package's own `inbox.py` sibling's write shape.
    - `status`'s "handled" section is a read of trail history, not of `OrderStore` itself, so an
      order whose own row the Queen's tick has already marked handled still shows here even though
      `pending()` no longer returns it.

See Also:
    - docs/adr/0024-clustering-protocol.md for the whole protocol this module's two writes trigger.
    - .claude/roadmap.md step 4.11 for this module's own deliverable, verbatim.
    - hivemind.queen.cluster.orders for ClusterOrder, OrderKind and OrderStore, this module's one
      write path.
    - hivemind.queen.cluster.tick for run_cluster_tick, the running Queen's own reader and the
      writer of the `queen.clustered`/`queen.resumed` events `status` reads back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    load_manifest_or_exit,
    open_cluster_orders,
    open_trail,
)
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from hivemind.queen.cluster import ClusterOrder, OrderKind, OrderStore, new_order_id
from waggle.clock import SystemClock

app = typer.Typer(
    name="cluster",
    help="Pause a provider (durable order; the running Queen polls and acts on it).",
    invoke_without_command=True,
)

__all__ = ["app", "wake_command"]

# A generous trail-read window for `status`'s own reconstruction, matching every other readback
# command's own MAX_QUERY_LIMIT-scale bound.
_TRAIL_LIMIT = 5_000


@app.callback(invoke_without_command=True)
def cluster_callback(
    ctx: typer.Context,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider", help="A [llm.providers] name; unset means every currently-bound one."
        ),
    ] = None,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Append a CLUSTER order for `--provider` (or every bound provider), and print its id.

    `--provider` is an option here, not a bare positional (the roadmap's own `hive cluster
    [provider]` wording): a Click `Group` always resolves a leftover token against its own
    optional positional `Argument` before ever trying it as a subcommand name, so a bare
    `hive cluster status` would silently be parsed as "cluster the provider literally named
    status" instead of dispatching to the `status` subcommand below (verified empirically while
    drafting this module) -- an option removes the ambiguity entirely, since it can never be
    confused with a subcommand name. `hive wake [provider]` keeps the positional form: it is a
    plain command with no subcommands of its own, so no such ambiguity exists there.
    """
    if ctx.invoked_subcommand is not None:
        return  # `hive cluster status` was named; let that subcommand run instead.
    typer.echo(_write_order(manifest, db, OrderKind.CLUSTER, provider))


def wake_command(
    provider: Annotated[
        str | None,
        typer.Argument(help="A [llm.providers] name; unset means every currently clustered one."),
    ] = None,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Append a WAKE order for PROVIDER (or every clustered provider), and print its id.

    Registered on the root `hive` app as a bare command (`hivemind.cli.app`), matching `hive run`'s
    own shape: `hive wake`, never `hive cluster wake`.
    """
    typer.echo(_write_order(manifest, db, OrderKind.WAKE, provider))


def _write_order(manifest: Path, db: Path | None, kind: OrderKind, provider: str | None) -> str:
    """Load the manifest, open the order store, append one order, and return its id.

    Synchronous, like every other CLI command body in this package: `open_cluster_orders` runs
    its own internal `asyncio.run` (`hivemind.cli.stores`'s own docstring), so it -- and the one
    `await store.put_order(...)` this needs -- must both happen here, never inside a coroutine
    this function's own caller already wrapped in `asyncio.run`, or the second `asyncio.run` call
    would raise "cannot be called from a running event loop" (verified empirically while drafting
    this module; the same nested-asyncio.run trap `hivemind.cli.readback.inbox`'s own module
    docstring documents).
    """
    loaded = load_manifest_or_exit(manifest)
    db_path = db if db is not None else loaded.resolve_path(loaded.hive.db)
    store = open_cluster_orders(db_path)
    order = ClusterOrder(
        id=new_order_id(SystemClock()),
        kind=kind,
        provider=provider,
        requested_at=SystemClock().now(),
    )
    asyncio.run(store.put_order(order))
    return order.id


@app.command("status")
def status_command(manifest: ManifestOption = DEFAULT_MANIFEST, db: DbOption = None) -> None:
    """Print every still-pending order, then every clustered/resumed event already acted on."""
    loaded = load_manifest_or_exit(manifest)
    db_path = db if db is not None else loaded.resolve_path(loaded.hive.db)
    store = open_cluster_orders(db_path)
    trail = open_trail(db_path)
    pending, handled = asyncio.run(_status(store, trail))
    typer.echo("PENDING")
    typer.echo(f"{'ID':<30}  {'KIND':<8}  {'PROVIDER':<16}  REQUESTED_AT")
    for order in pending:
        provider = order.provider or "(all)"
        typer.echo(f"{order.id:<30}  {order.kind.value:<8}  {provider:<16}  {order.requested_at}")
    typer.echo("\nHANDLED (from the trail)")
    typer.echo(f"{'KIND':<16}  {'PROVIDER':<16}  AT")
    for event in handled:
        provider = str(event.payload.get("provider", "(unknown)"))
        typer.echo(f"{event.kind:<16}  {provider:<16}  {event.at}")


async def _status(
    store: OrderStore, trail: PheromoneTrail
) -> tuple[tuple[ClusterOrder, ...], tuple[PheromoneEvent, ...]]:
    """Read pending orders and reconstruct handled ones from `queen.clustered`/`queen.resumed`."""
    pending = await store.pending()
    events = await trail.query(TrailQuery(family="queen", limit=_TRAIL_LIMIT))
    handled = tuple(event for event in events if event.kind in {"queen.clustered", "queen.resumed"})
    return pending, handled
