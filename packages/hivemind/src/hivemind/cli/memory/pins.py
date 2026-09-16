"""Provide `hive memory pins add|list|remove`: facts that never decay out of hot state.

Three thin typer layers over `hivemind.memory.add_pin`/`MemoryStore.list_pins`/`.remove_pin`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.memory.app` as `pins`.
    Calls into `hivemind.memory` and `hivemind.cli.stores`/`.context` only.

Key invariants:
    - None beyond `hivemind.memory.pins`'s own (add_pin commits the row and its trail event
      together; see that module's docstring).

See Also:
    - .claude/roadmap.md step 4.11 for this command's own deliverable, verbatim.
    - hivemind.memory.pins for Pin, PinSource and add_pin, this module's whole write path.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.memory.context import memory_context, resolved_db
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    load_manifest_or_exit,
    open_memory,
)
from hivemind.memory import Pin, PinSource, add_pin
from waggle.ids import EventId, new_event_id

app = typer.Typer(name="pins", help="Add, list or remove a Pin: a fact that never decays.")

__all__ = ["app"]


@app.command("add")
def add_command(
    text: Annotated[str, typer.Argument(help="The fact to pin.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    clearance: Annotated[
        HoneyClearance, typer.Option("--clearance", help="This pin's own data-sensitivity label.")
    ] = HoneyClearance.C1,
) -> None:
    """Add a Pin: a fact hot state packs first and (almost) never drops."""
    loaded = load_manifest_or_exit(manifest)
    ctx = memory_context(loaded, open_memory(resolved_db(loaded, db)), "human")
    pin = Pin(
        id=new_event_id(ctx.clock),
        text=text,
        clearance=clearance,
        source=PinSource.RUNTIME,
        created_at=ctx.clock.now(),
    )
    asyncio.run(add_pin(pin, ctx))
    typer.echo(pin.id)


@app.command("list")
def list_command(
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    clearance: Annotated[
        HoneyClearance, typer.Option("--clearance", help="Only pins within this allowance.")
    ] = HoneyClearance.C2,
) -> None:
    """List every stored Pin within `--clearance`."""
    loaded = load_manifest_or_exit(manifest)
    store = open_memory(resolved_db(loaded, db))
    pins = asyncio.run(store.list_pins(clearance))
    typer.echo(f"{'ID':<30}  {'CLEARANCE':<10}  {'SOURCE':<10}  TEXT")
    for pin in pins:
        typer.echo(f"{pin.id:<30}  {pin.clearance.value:<10}  {pin.source.value:<10}  {pin.text}")


@app.command("remove")
def remove_command(
    pin_id: Annotated[str, typer.Argument(help="A pin's own id, from `pins list`.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Remove a Pin by id; idempotent (matches `MemoryStore.remove_pin`'s own contract)."""
    loaded = load_manifest_or_exit(manifest)
    asyncio.run(open_memory(resolved_db(loaded, db)).remove_pin(EventId(pin_id)))
    typer.echo(f"removed: {pin_id}")
