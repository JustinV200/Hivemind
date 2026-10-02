"""Provide `hive honey ripen --now`, `reembed` and `relabel`: the operator's maintenance commands.

`ripen --now` runs the House Bee's whole pass (`HouseBeeRipening.run_pass`: drain every operator
note queued through `propose` into HUMAN Nectar; `Ripener.run_pass` -- pending Nectar, raw
findings, into Honey, then rows still lacking a vector for the current embedder; then ADR-0038's
label lowering -- file a proposal for each newly eligible Nectar and ask the clearance judge about
waiting ones) against the Hive's own file, built exactly as the running Hive builds it,
attributed to the Hive Stand's own stable Cell id (`hivemind.cell.hive_stand_cell_id`, phase 7
handoff item 4: derived from the manifest's `[hive] node_id`, since the CLI has no running Hive
Stand lease of its own to attribute notes to). A missing RIPENER, EMBEDDER or JUDGE binding is
printed with its reason and the pass still runs (heuristic summaries, no vectors, proposals left
for the human), never a traceback. One pass drains at most the House Bee's own per-pass batch of
notes, so `--now` repeats the pass while each drains a full batch and notes remain, up to
MAX_RIPEN_PASSES, then prints the summed counts and any notes still queued, with why (phase 7
handoff item 7). Without `--now` it says how much Nectar and how many notes are waiting, since
both normally drain in the House Bee's own loop beside the Queen. Either form prints one JSON
object instead with `--json`. `reembed`
repeats `Ripener.embed_pending` until nothing is pending for the current embedding model or a
pass makes no progress, then prints vectors per model (ADR-0036: a changed embedder re-embeds
progressively; this runs the backlog now); with `--prune` it then drops every other model's
vectors, but only when every live row has one for the bound model (ADR-0037: on the operator's
word, never automatically), and otherwise refuses, says how many rows still lack one, deletes
nothing and exits 1. `relabel PATH LABEL --reason TEXT` raises or lowers one Honey row's label as
the human, through `HoneyRelabeller` (a lowering passes `check_lowering` with a HUMAN approver
first). Every write here is idempotent by key or recorded by the store's own transaction, so
running any of them beside a live Queen is safe (ADR-0035).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.honey` as `ripen`,
    `reembed` and `relabel`. Calls into `hivemind.cell` (hive_stand_cell_id),
    `hivemind.workers.roles.house_bee` (HouseBeeRipening), `hivemind.honey_store`,
    `hivemind.cli.stores` and this group's own `context` and `render` only.

Key invariants:
    - `reembed` stops on the first pass that embeds nothing, and never runs more than
      MAX_REEMBED_PASSES passes, so a failing embedder can never spin it forever.
    - `reembed` with no usable embedder exits 1 with the reason; it never touches the store, so
      `--prune` never runs without a bound model to keep.
    - `--prune` runs only after the re-embedding backlog was drained, and the store's own
      transaction re-checks coverage before it deletes anything; a prune is recorded as the
      human's act (`honey.vectors_pruned`, actor `human`).
    - `ripen --now` never leaves a note queued and silently undrained: every proposal
      `HouseBeeRipening.run_pass` sees is either drained into Nectar or logged and passed over
      (its own module docstring), never dropped.
    - `ripen` never reports a capped count as exact: a queued-note count that reached
      `_WAITING_NOTES_LIMIT` is printed as "at least" that many, and `--now` says when it stopped
      at MAX_RIPEN_PASSES with notes left, and that running it again drains more.

See Also:
    - hivemind.workers.roles.house_bee.loop for HouseBeeRipening, the pass `ripen --now` runs.
    - hivemind.honey_store.ripening for Ripener.run_pass, embed_pending and prune_vectors, the
      parts this module also calls directly for `reembed`.
    - hivemind.honey_store.browse.relabel for HoneyRelabeller.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for re-embedding.
    - docs/adr/0037-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for pruning.
    - .claude/phase-7-handoff.md section 8 items 4 and 7 for why `ripen --now` needed a stable
      Cell id before it could drain notes at all.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cell import HoneyClearance, hive_stand_cell_id
from hivemind.cli.honey.context import (
    EXIT_REFUSED,
    HoneyCliContext,
    cli_context,
    human_identity,
    open_access,
    reader_for,
    run_or_exit,
)
from hivemind.cli.honey.render import (
    RipenTotals,
    WaitingCounts,
    print_prune,
    print_ripen,
    print_slot,
    print_waiting,
    ripen_json,
)
from hivemind.cli.stores import JsonOption, closing_registry, open_honey_store
from hivemind.honey_store import (
    HoneyAccess,
    HoneyStats,
    HoneyStore,
    NectarState,
    ReviewOutcome,
    Ripener,
    RipenerDeps,
    RipenOutcome,
    prune_vectors,
)
from hivemind.honey_store.browse import HoneyRelabeller, RelabelDirection, RelabelRequest
from hivemind.workers.roles.house_bee import MAX_PROPOSALS_PER_PASS, HouseBeeRipening
from waggle.clock import SystemClock

# A runaway guard, not an expected count: at the default 256 rows a pass this re-embeds millions
# of rows, far past any one Hive's store; each pass that embeds nothing ends the loop anyway.
MAX_REEMBED_PASSES = 10_000
# `ripen --now` repeats the House Bee's pass while each one drains a full batch of operator notes;
# this bounds one invocation to about a thousand notes (32 passes of 32), and it says so when it
# stops here with notes still queued (phase 7 handoff item 7).
MAX_RIPEN_PASSES = 32
# The most notes one pass drains: the House Bee's own per-pass cap, read rather than restated.
_DRAIN_BATCH = MAX_PROPOSALS_PER_PASS
# An indexed, oldest-first scan (idx_honey_proposals_pending): cheap even at this generous cap, so
# counting queued notes costs nothing close to a real ripening pass. A count that reaches it is
# printed as "at least" this many, never as exactly this many.
_WAITING_NOTES_LIMIT = 1_000
_NO_RIPENER = "summaries are heuristic (the text's own opening)"
_NO_EMBEDDER = "rows are stored without vectors and search is full text only"
_NO_JUDGE = "lowering proposals wait for the human (hive honey review)"

__all__ = [
    "MAX_REEMBED_PASSES",
    "MAX_RIPEN_PASSES",
    "reembed_command",
    "relabel_command",
    "ripen_command",
]

# `reembed --prune`: the operator's own word that the old models' vectors may go (ADR-0037).
PruneOption = Annotated[
    bool,
    typer.Option(
        "--prune",
        help="Then drop every other embedding model's vectors, only if every live row has one "
        "for the bound embedder.",
    ),
]


def ripen_command(
    ctx: typer.Context,
    now: Annotated[bool, typer.Option("--now", help="Run House Bee passes here, now.")] = False,
    as_json: JsonOption = False,
) -> None:
    """Run the House Bee's pass now (--now) until notes drain; else say how much is waiting."""
    cli_ctx = cli_context(ctx)
    # Ripening (and draining notes) normally runs in the House Bee's own loop; without --now
    # nothing is written.
    if not now:
        _print_waiting(cli_ctx, as_json)
        return
    opened = open_access(cli_ctx)
    # The Hive Stand's own stable Cell id (phase 7 handoff item 4): every drained note is
    # attributed to it, exactly as the running Hive's own House Bee attributes them, even though
    # this invocation leases nothing itself.
    stand_cell_id = hive_stand_cell_id(cli_ctx.manifest.hive.node_id)
    ripening = HouseBeeRipening(opened.access, stand_cell_id, SystemClock())
    # Each pass: drain queued notes, then every summary and embedding call, each carrying the
    # Ripener's own timeouts (a model that fails leaves a heuristic summary or an unembedded row,
    # never a failed pass), then file lowering proposals and one JUDGE call per waiting one; a
    # judge outage fails the run as a typed error, after ripening and filing are committed.
    work = _ripen_now(ripening, opened.access.store)
    totals = run_or_exit(closing_registry(opened.registry, work))
    # --json prints the totals alone, so a script reads one JSON value from stdout.
    if as_json:
        typer.echo(ripen_json(totals))
        return
    print_ripen(totals)
    print_slot("ripener", opened.bindings.ripener, _NO_RIPENER)
    print_slot("embedder", opened.bindings.embedder, _NO_EMBEDDER)
    print_slot("judge", opened.bindings.judge, _NO_JUDGE)


def reembed_command(ctx: typer.Context, prune: PruneOption = False) -> None:
    """Embed every row lacking a vector for the current embedder, count per model; --prune after."""
    cli_ctx = cli_context(ctx)
    opened = open_access(cli_ctx)
    model = opened.bindings.embedder.model
    # No embedder, no vectors: the one case this command cannot do anything about (and with no
    # bound model to keep, nothing may be pruned either).
    if model is None:
        print_slot("embedder", opened.bindings.embedder, "nothing can be re-embedded")
        raise typer.Exit(code=EXIT_REFUSED)
    passes, embedded = run_or_exit(
        closing_registry(opened.registry, _reembed(opened.access.ripener))
    )
    still_pending = run_or_exit(_has_pending(opened.access.store, model))
    typer.echo(f"re-embedded {embedded} rows for {model} (embedding passes: {passes})")
    # Only now, with the backlog drained, may the other models' vectors go (ADR-0037).
    refused = prune and _prune(cli_ctx, opened.access, model)
    # Coverage per model (ADR-0036): the old model's vectors stay beside the new model's unless
    # the operator pruned them just now.
    stats = run_or_exit(opened.access.store.stats())
    for vector_model, count in sorted(stats.vectors_by_model.items()):
        typer.echo(f"  {vector_model}: {count} vectors")
    # Rows remain after the last pass: the embedder failed or refused them, or the pass bound hit.
    if still_pending:
        typer.echo(f"some rows still lack a {model} vector; see the log for why", err=True)
    # Either way the command did not finish what was asked: exit as a refusal.
    if still_pending or refused:
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


def _print_waiting(cli_ctx: HoneyCliContext, as_json: bool) -> None:
    """Say how much Nectar and how many operator notes are waiting, and when the House Bee acts."""
    # open_honey_store runs its own asyncio.run for setup, so it must be called here, outside the
    # event loop run_or_exit opens next -- never from inside _waiting_counts itself.
    store = open_honey_store(cli_ctx.db)
    stats, (notes, capped) = run_or_exit(_waiting_counts(store))
    counts = WaitingCounts(
        nectar=stats.nectar_by_state.get(NectarState.RECEIVED, 0),
        notes=notes,
        notes_capped=capped,
        interval_s=cli_ctx.manifest.honey.ripening.interval_s,
    )
    # --json prints the counts alone, so a script reads one JSON value from stdout.
    if as_json:
        typer.echo(ripen_json(counts))
        return
    print_waiting(counts)


async def _waiting_counts(store: HoneyStore) -> tuple[HoneyStats, tuple[int, bool]]:
    """Read `store`'s Nectar counts and how many operator notes are still queued.

    Two cheap reads on the one already-open store: `stats()` is a handful of GROUP BYs, and
    `_queued_notes` is one indexed, capped scan.
    """
    return await store.stats(), await _queued_notes(store)


async def _queued_notes(store: HoneyStore) -> tuple[int, bool]:
    """Count queued operator notes up to `_WAITING_NOTES_LIMIT`; say whether it hit that cap."""
    # Local SQLite, an indexed oldest-first scan. Reading one past the cap tells a backlog of
    # exactly the cap apart from a larger one, so the count is never silently short.
    queued = len(await store.pending_proposals(_WAITING_NOTES_LIMIT + 1))
    return min(queued, _WAITING_NOTES_LIMIT), queued > _WAITING_NOTES_LIMIT


async def _ripen_now(ripening: HouseBeeRipening, store: HoneyStore) -> RipenTotals:
    """Run House Bee passes until queued notes drain, at most MAX_RIPEN_PASSES; sum them.

    A pass drains at most `_DRAIN_BATCH` notes, so another runs only while the last one drained
    a full batch and notes remain. A short batch means the rest were refused and passed over (the
    House Bee logs each), and a repeat would only re-refuse them and re-ask the judge for nothing.
    """
    passes = drained = reembedded = filed = 0
    ripen, review = RipenOutcome(), ReviewOutcome()
    while True:
        # External awaits inside: the pass's own bounded model calls (see ripen_command).
        result = await ripening.run_pass()
        passes, drained, filed = passes + 1, drained + result.drained, filed + result.filed
        ripen, review = ripen + result.outcome.ripen, review + result.review
        reembedded += result.outcome.reembedded
        left, capped = await _queued_notes(store)
        full_batch = result.drained >= _DRAIN_BATCH
        # Drained, nothing more this command can drain, or the bound: stop and report what is left.
        if left == 0 or not full_batch or passes >= MAX_RIPEN_PASSES:
            break
    return RipenTotals(
        passes=passes,
        drained=drained,
        ripen=ripen,
        reembedded=reembedded,
        filed=filed,
        review=review,
        notes_left=left,
        notes_left_capped=capped,
        hit_pass_cap=left > 0 and full_batch,
    )


def _prune(cli_ctx: HoneyCliContext, access: HoneyAccess, kept_model: str) -> bool:
    """Drop every model's vectors but `kept_model`'s and print what went; return if it refused."""
    # The operator's own word (ADR-0037), so the prune is recorded as the human's act. Pruning
    # needs no model: the store, an identity and a clock.
    deps = RipenerDeps(
        access.store, human_identity(cli_ctx.manifest), SystemClock(), access.ripening
    )
    # Local SQLite: one transaction re-checks coverage, deletes and records the event, or refuses.
    outcome = run_or_exit(prune_vectors(deps, kept_model))
    print_prune(outcome)
    return outcome.refused


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
