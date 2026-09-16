"""Provide `hive memory wax <cell> list|propose|clear`: Cell Wax, a Queen-written caution.

`list` reads every note on CELL through `MemoryStore.list_wax`. `propose "<text>" [--severity]
[--expires-in]` writes a PROPOSED note through `hivemind.memory.propose_wax` (never WRITTEN
directly: codingrules 8.9 and roadmap step 4.2a both give only the Queen that write) -- a running
Queen's next tick judges it. `clear <id>` has no operator-facing proposal path of its own in v0
(only the Queen's own `clear_wax` moves WRITTEN -> CLEARED), so it is exposed here as an explicit
operator override that calls `clear_wax` directly and records `memory.wax_cleared` with
`WaxOrigin.HUMAN` in its own reason text -- this command's own `--help` says so. CELL, `--manifest`
and `--db` are captured once by this group's own callback (codingrules section 5.1's parameter
cap: a `propose` command with its own five flags has no room left for three more shared ones), and
read back through `ctx.obj` by every subcommand.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.memory.app` as `wax`.
    Calls into `hivemind.memory` and `hivemind.cli.stores`/`.context` only.

Key invariants:
    - `propose` never writes a WRITTEN row itself; it always calls `propose_wax`, leaving the note
      PROPOSED for a running Queen's next tick to judge (roadmap step 4.11's own wording).

See Also:
    - .claude/roadmap.md step 4.11 for this command's own deliverable, verbatim.
    - hivemind.memory.cell_wax for propose_wax/clear_wax, this module's two write paths.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.memory.context import WIDEST, memory_context, resolved_db
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    load_manifest_or_exit,
    open_memory,
)
from hivemind.manifest import HiveManifest
from hivemind.memory import WaxSeverity, WaxState, clear_wax, propose_wax
from hivemind.memory.cell_wax import WaxProposalInput
from waggle.ids import CellId
from waggle.messages.cell.wax import WaxOrigin

app = typer.Typer(
    name="wax",
    help="List, propose or clear Cell Wax on one Cell (`hive memory wax <cell> ...`).",
)

__all__ = ["app"]


@dataclass(frozen=True, slots=True)
class _WaxCliContext:
    """CELL plus the resolved manifest and db path, shared by every subcommand below."""

    cell: CellId
    manifest: HiveManifest
    db: Path


@app.callback()
def wax_callback(
    ctx: typer.Context,
    cell: Annotated[str, typer.Argument(help="The Cell this wax is about.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Capture CELL, `--manifest` and `--db` for every `hive memory wax CELL <subcommand>` below."""
    loaded = load_manifest_or_exit(manifest)
    ctx.obj = _WaxCliContext(cell=CellId(cell), manifest=loaded, db=resolved_db(loaded, db))


@app.command("list")
def list_command(ctx: typer.Context) -> None:
    """List every Cell Wax note on this CELL, newest first."""
    cli_ctx: _WaxCliContext = ctx.obj
    store = open_memory(cli_ctx.db)
    notes = asyncio.run(store.list_wax(cli_ctx.cell, frozenset(WaxState), WIDEST))
    typer.echo(f"{'ID':<30}  {'STATE':<10}  {'SEVERITY':<10}  TEXT")
    for note in notes:
        typer.echo(f"{note.id:<30}  {note.state.value:<10}  {note.severity.value:<10}  {note.text}")


@app.command("propose")
def propose_command(
    ctx: typer.Context,
    text: Annotated[str, typer.Argument(help="The caution's own text.")],
    severity: Annotated[
        WaxSeverity, typer.Option("--severity", help="NOTE, CAUTION or BLOCK.")
    ] = WaxSeverity.NOTE,
    expires_in: Annotated[
        int | None, typer.Option("--expires-in", help="Seconds until this note expires on its own.")
    ] = None,
    reason: Annotated[str, typer.Option("--reason", help="Why the operator believes it.")] = (
        "Reported by the operator via `hive memory wax propose`."
    ),
) -> None:
    """Propose a PROPOSED Cell Wax note; a running Queen's next tick judges it (never WRITTEN)."""
    cli_ctx: _WaxCliContext = ctx.obj
    memory_ctx = memory_context(cli_ctx.manifest, open_memory(cli_ctx.db), "human")
    expires_at = (
        memory_ctx.clock.now() + timedelta(seconds=expires_in) if expires_in is not None else None
    )
    inputs = WaxProposalInput(
        cell_id=cli_ctx.cell,
        severity=severity,
        text=text,
        reason=reason,
        clearance=HoneyClearance.C1,
        origin=WaxOrigin.HUMAN,
        proposer=None,
        expires_at=expires_at,
    )
    wax = asyncio.run(propose_wax(inputs, cli_ctx.manifest.memory.wax_text_cap_chars, memory_ctx))
    typer.echo(f"proposed: {wax.id} (PROPOSED; a running Queen's next tick judges it)")


@app.command("clear")
def clear_command(
    ctx: typer.Context,
    wax_id: Annotated[str, typer.Argument(help="A Cell Wax note's own id, from `wax list`.")],
) -> None:
    """Operator override: clear a WRITTEN Cell Wax note directly, recording it as a HUMAN clear.

    Roadmap step 4.11: "if the only clear path is the Queen's own `clear_wax`, expose it as an
    operator action that records `memory.wax_cleared` with `WaxOrigin.HUMAN`." There is no
    operator-facing clear *request* path in v0, so this command calls `clear_wax` itself rather
    than leaving a request nobody drains -- an explicit, named exception to "only the Queen writes
    WRITTEN/CLEARED", made visible in this help text.
    """
    cli_ctx: _WaxCliContext = ctx.obj
    store = open_memory(cli_ctx.db)
    memory_ctx = memory_context(cli_ctx.manifest, store, "human")
    wax = asyncio.run(store.get_wax(wax_id))
    reason = "Cleared by the operator via `hive memory wax clear` (WaxOrigin.HUMAN)."
    cleared = asyncio.run(clear_wax(wax, reason, memory_ctx))
    typer.echo(f"cleared: {cleared.id}")
