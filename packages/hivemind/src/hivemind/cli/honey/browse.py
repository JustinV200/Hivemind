"""Provide `hive honey ls`, `cat` and `propose`: walk the Honey Store by path, and add a note.

Thin layers over `hivemind.honey_store.browse.HoneyBrowser` (roadmap 7.10), which presents the
Honey Store (the Hive's cold-tier knowledge base) as a read-only folder tree: `/hive`,
`/cells/<cell>` (with the Cell's live Cell Wax, the Queen's standing cautions, under `wax`),
`/bees/<bee>`, `/tasks/<task>` and `/bee-bread` (recent warm memory). `ls [PATH] [--limit N]
[--offset N] [--json]` lists a folder, `cat PATH [--json]` reads one document, both as the
operator's reader (every scope, up to the group's `--clearance`). `propose PATH TITLE TEXT` adds a
note from a folder: from a Cell's folder it becomes a PROPOSED Cell Wax note filed through
`hivemind.memory.propose_wax` (the Queen's next tick judges it); from anywhere else the browser
queues it for the House Bee (the maintenance Worker) to take in as C2 Nectar. These commands need
no model binding, so the browser is built over the store and the memory store alone.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.honey` as `ls`, `cat`
    and `propose`. Calls into `hivemind.honey_store.browse`, `hivemind.memory` (the wax proposal
    path), `hivemind.cli.stores`, `hivemind.cli.memory.context` and this group's own `context`,
    `sources` and `render` only.

Key invariants:
    - `ls` and `cat` write nothing; `propose` writes one PROPOSED wax row or one queued note, each
      with its own trail event, and never a Honey row.
    - A proposal never becomes WRITTEN wax here: only the Queen writes Cell Wax.

See Also:
    - hivemind.honey_store.browse for HoneyBrowser, the folder tree and its filtering.
    - hivemind.memory.cell_wax.writes.propose_wax for the wax proposal path.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli.honey.context import (
    HUMAN_ACTOR,
    HoneyCliContext,
    cli_context,
    human_identity,
    reader_for,
    run_or_exit,
)
from hivemind.cli.honey.render import document_json, print_document, print_listing
from hivemind.cli.honey.sources import MemoryBeeBreadSource, MemoryWaxSource
from hivemind.cli.memory.context import memory_context
from hivemind.cli.stores import JsonOption, open_honey_store, open_memory
from hivemind.honey_store.browse import (
    DEFAULT_PAGE_ROWS,
    MAX_PAGE_ROWS,
    ROOT_PATH,
    BrowserDeps,
    BrowseSources,
    CellWaxProposal,
    HoneyBrowser,
    Page,
)
from hivemind.memory import CellWax, MemoryStore, WaxProposalInput, WaxSeverity, propose_wax
from waggle.clock import SystemClock
from waggle.messages.cell.wax import WaxOrigin

__all__ = ["cat_command", "ls_command", "propose_command"]

# A folder or document path, e.g. /hive, /cells/<cell id>/wax, /tasks/<task id>/<honey id>.
PathArgument = Annotated[str, typer.Argument(help="A Honey path, e.g. /hive or /cells/<id>/wax.")]
LimitOption = Annotated[
    int, typer.Option("--limit", min=1, max=MAX_PAGE_ROWS, help="Entries on this page.")
]
OffsetOption = Annotated[int, typer.Option("--offset", min=0, help="Entries to skip first.")]


def ls_command(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="The folder to list; / by default.")] = ROOT_PATH,
    limit: LimitOption = DEFAULT_PAGE_ROWS,
    offset: OffsetOption = 0,
    as_json: JsonOption = False,
) -> None:
    """List one Honey folder: what it holds, each item with its clearance and provenance."""
    cli_ctx = cli_context(ctx)
    browser = _browser(cli_ctx, open_memory(cli_ctx.db))
    # Local SQLite reads only, filtered for the operator's reader before they are paged.
    listing = run_or_exit(browser.ls(path, reader_for(cli_ctx), Page(limit=limit, offset=offset)))
    # --json prints the listing unchanged, for a script to read.
    if as_json:
        typer.echo(listing.model_dump_json(indent=2))
        return
    print_listing(listing)


def cat_command(ctx: typer.Context, path: PathArgument, as_json: JsonOption = False) -> None:
    """Read one Honey document: a Honey row, a live Cell Wax note or a Bee Bread entry."""
    cli_ctx = cli_context(ctx)
    browser = _browser(cli_ctx, open_memory(cli_ctx.db))
    # One local read; anything the reader may not see exits 1, exactly like a missing path.
    document = run_or_exit(browser.cat(path, reader_for(cli_ctx)))
    # --json prints the document with its path and kind, for a script to read.
    if as_json:
        typer.echo(document_json(path, document))
        return
    print_document(path, document)


def propose_command(
    ctx: typer.Context,
    path: PathArgument,
    title: Annotated[str, typer.Argument(help="A one-line label for the note.")],
    text: Annotated[str, typer.Argument(help="The note itself.")],
) -> None:
    """Propose a note from a folder: Cell Wax from a Cell's folder, else queued as C2 Nectar."""
    cli_ctx = cli_context(ctx)
    memory = open_memory(cli_ctx.db)
    # The browser decides which kind of proposal the folder makes; only a queued note is written.
    proposal = run_or_exit(_browser(cli_ctx, memory).propose_note(path, title, text))
    # A Cell's folder: the browser only described the wax; filing it is the memory tier's job.
    if isinstance(proposal, CellWaxProposal):
        wax = run_or_exit(_file_wax(cli_ctx, memory, proposal))
        typer.echo(
            f"proposed Cell Wax: {wax.id} on {wax.cell_id} "
            "(PROPOSED; a running Queen's next tick judges it)"
        )
        return
    typer.echo(
        f"queued note: {proposal.proposal_id} in {proposal.scope} "
        f"(the House Bee takes it in as {proposal.clearance.value} Nectar)"
    )


def _browser(cli_ctx: HoneyCliContext, memory: MemoryStore) -> HoneyBrowser:
    """Build a list-and-read browser over the Hive's store and memory: no retriever, no model."""
    # Wax and Bee Bread live in memory; the browser reads them through its two source Protocols.
    sources = BrowseSources(wax=MemoryWaxSource(memory), bee_bread=MemoryBeeBreadSource(memory))
    deps = BrowserDeps(
        store=open_honey_store(cli_ctx.db),
        sources=sources,
        identity=human_identity(cli_ctx.manifest),
        clock=SystemClock(),
    )
    return HoneyBrowser(deps)


async def _file_wax(
    cli_ctx: HoneyCliContext, memory: MemoryStore, proposal: CellWaxProposal
) -> CellWax:
    """File a Cell Wax proposal through the memory tier's own path, as the human."""
    inputs = WaxProposalInput(
        cell_id=proposal.cell_id,
        severity=WaxSeverity.NOTE,
        text=proposal.text,
        reason=proposal.reason,
        clearance=proposal.clearance,
        origin=WaxOrigin.HUMAN,
        proposer=None,
    )
    # One transaction for the PROPOSED row and its memory.wax_proposed event; milliseconds.
    return await propose_wax(
        inputs,
        cli_ctx.manifest.memory.wax_text_cap_chars,
        memory_context(cli_ctx.manifest, memory, HUMAN_ACTOR),
    )
