"""Provide `hive honey ripen --now`, `reembed` and `relabel`: the operator's maintenance commands.

`ripen --now` runs one House Bee ripening pass (`Ripener.run_pass`: pending Nectar, raw findings,
into Honey, then rows still lacking a vector for the current embedder) against the Hive's own
file, built exactly as the running Hive builds it; a missing RIPENER or EMBEDDER binding is
printed with its reason and the pass still runs (heuristic summaries, no vectors), never a
traceback. Without `--now` it only says how much is waiting, since ripening normally runs in the
House Bee's own loop beside the Queen. `reembed` repeats `Ripener.embed_pending` until nothing is
pending for the current embedding model or a pass makes no progress, then prints vectors per
model (ADR-0032: a changed embedder re-embeds progressively; this runs the backlog now).
`relabel PATH LABEL --reason TEXT` raises or lowers one Honey row's label as the human, through
`HoneyRelabeller` (a lowering passes `check_lowering` with a HUMAN approver first). Every write
here is idempotent by key or recorded by the store's own transaction, so running any of them
beside a live Queen is safe (ADR-0031).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.honey` as `ripen`,
    `reembed` and `relabel`. Calls into `hivemind.honey_store`, `hivemind.cli.stores` and this
    group's own `context` and `render` only.

Key invariants:
    - `reembed` stops on the first pass that embeds nothing, and never runs more than
      MAX_REEMBED_PASSES passes, so a failing embedder can never spin it forever.
    - `reembed` with no usable embedder exits 1 with the reason; it never touches the store.

See Also:
    - hivemind.honey_store.ripening for Ripener.run_pass and embed_pending.
    - hivemind.honey_store.browse.relabel for HoneyRelabeller.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for re-embedding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.honey.context import (
    EXIT_REFUSED,
    cli_context,
    human_identity,
    open_access,
    reader_for,
    run_or_exit,
)
from hivemind.cli.honey.render import print_slot
from hivemind.cli.stores import open_honey_store
from hivemind.honey_store import HoneyStore, NectarState, PassOutcome, Ripener
from hivemind.honey_store.browse import HoneyRelabeller, RelabelDirection, RelabelRequest
from waggle.clock import SystemClock

# A runaway guard, not an expected count: at the default 256 rows a pass this re-embeds millions
# of rows, far past any one Hive's store; each pass that embeds nothing ends the loop anyway.
MAX_REEMBED_PASSES = 10_000
_NO_RIPENER = "summaries are heuristic (the text's own opening)"
_NO_EMBEDDER = "rows are stored without vectors and search is full text only"

__all__ = ["MAX_REEMBED_PASSES", "reembed_command", "relabel_command", "ripen_command"]


def ripen_command(
    ctx: typer.Context,
    now: Annotated[bool, typer.Option("--now", help="Run one ripening pass here, now.")] = False,
) -> None:
    """Run one House Bee ripening pass now (--now); without it, say how much is waiting."""
    cli_ctx = cli_context(ctx)
    # Ripening normally runs in the House Bee's own loop; without --now nothing is written.
    if not now:
        _print_waiting(cli_ctx.db, cli_ctx.manifest.honey.ripening.interval_s)
        return
    opened = open_access(cli_ctx)
    # One pass: every summary and embedding call carries the Ripener's own timeouts, and a model
    # that fails leaves a heuristic summary or an unembedded row, never a failed pass.
    outcome = run_or_exit(opened.access.ripener.run_pass())
    _print_pass(outcome)
    print_slot("ripener", opened.bindings.ripener, _NO_RIPENER)
    print_slot("embedder", opened.bindings.embedder, _NO_EMBEDDER)


def reembed_command(ctx: typer.Context) -> None:
    """Embed every row still lacking a vector for the current embedder, then count per model."""
    opened = open_access(cli_context(ctx))
    model = opened.bindings.embedder.model
    # No embedder, no vectors: the one case this command cannot do anything about.
    if model is None:
        print_slot("embedder", opened.bindings.embedder, "nothing can be re-embedded")
        raise typer.Exit(code=EXIT_REFUSED)
    passes, embedded = run_or_exit(_reembed(opened.access.ripener))
    still_pending = run_or_exit(_has_pending(opened.access.store, model))
    # Coverage per model (ADR-0032): the old model's vectors stay beside the new model's.
    stats = run_or_exit(opened.access.store.stats())
    typer.echo(f"re-embedded {embedded} rows for {model} (embedding passes: {passes})")
    for vector_model, count in sorted(stats.vectors_by_model.items()):
        typer.echo(f"  {vector_model}: {count} vectors")
    # Rows remain after the last pass: the embedder failed or refused them, or the pass bound hit.
    if still_pending:
        typer.echo(f"some rows still lack a {model} vector; see the log for why", err=True)
        raise typer.Exit(code=EXIT_REFUSED)


def relabel_command(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="One Honey row's path, e.g. /hive/<honey id>.")],
    label: Annotated[
        HoneyClearance, typer.Argument(metavar="C0|C1|C2", help="The label it should carry.")
    ],
    reason: Annotated[str, typer.Option("--reason", help="Why; recorded on the trail.")],
) -> None:
    """Raise or lower one Honey row's label as the human, recorded as label_raised/_lowered."""
    cli_ctx = cli_context(ctx)
    # The human's own act: recorded as the human, and only on a row the operator's reader sees.
    relabeller = HoneyRelabeller(
        open_honey_store(cli_ctx.db), human_identity(cli_ctx.manifest), SystemClock()
    )
    request = RelabelRequest(path=path, target=label, reason=reason)
    outcome = run_or_exit(relabeller.relabel(request, reader_for(cli_ctx)))
    # Nothing was written: say so rather than print a change that did not happen.
    if outcome.direction is RelabelDirection.UNCHANGED:
        typer.echo(f"unchanged: {outcome.honey.path} is already {label.value}")
        return
    typer.echo(
        f"{outcome.direction.value.lower()}: {outcome.honey.path} "
        f"{outcome.before.value} -> {outcome.honey.clearance.value}"
    )


def _print_waiting(db: Path, interval_s: float) -> None:
    """Say how much Nectar is waiting to ripen, and when the House Bee would ripen it."""
    # Counts only; local SQLite, a handful of GROUP BYs.
    stats = run_or_exit(open_honey_store(db).stats())
    waiting = stats.nectar_by_state.get(NectarState.RECEIVED, 0)
    typer.echo(
        f"{waiting} Nectar waiting to ripen; a running Hive's House Bee ripens every "
        f"{interval_s:g}s. Pass --now to run one pass here."
    )


def _print_pass(outcome: PassOutcome) -> None:
    """Print one ripening pass's counts."""
    ripen = outcome.ripen
    typer.echo(
        f"ripened {ripen.ripened} Nectar into {ripen.rows} Honey rows "
        f"(failed={ripen.failed}  discarded={ripen.discarded}  deduplicated={ripen.deduped}  "
        f"embedded={ripen.embedded}); re-embedded {outcome.reembedded} older rows"
    )


async def _reembed(ripener: Ripener) -> tuple[int, int]:
    """Run embedding passes until one embeds nothing; return (productive passes, rows embedded)."""
    passes = embedded = 0
    # Each pass embeds up to [honey.ripening] max_embed_per_pass rows for the current model; a
    # pass that embeds none means nothing is pending, or the embedder is failing: stop either way.
    while passes < MAX_REEMBED_PASSES:
        # External await: one or more embedding calls, each bounded by the Ripener's own timeout.
        count = await ripener.embed_pending()
        if count == 0:
            break
        passes += 1
        embedded += count
    return passes, embedded


async def _has_pending(store: HoneyStore, model: str) -> bool:
    """Return whether any live row still lacks a vector for `model`."""
    # Local SQLite on the store's own thread: one indexed probe.
    return bool(await store.pending_vectors(model, 1))
