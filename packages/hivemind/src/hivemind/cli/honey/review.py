"""Provide `hive honey review`: list label lowering proposals, decide one as the human, judge now.

Anything gathered on a Real Cell (a borrowed device, the Hive Stand, the machine the Queen runs on,
included) is labelled C2 in the Honey Store (the Hive's knowledge base) by the provenance floor,
so a default C1 goal never reads it. ADR-0034 lets that label come down only through a proposal:
the House Bee (the maintenance role) files one when the Ripener read a deposit's text as less
sensitive, and an independent clearance judge on the JUDGE model slot, or the human, decides it.
This module is the operator's side of that. `review [--limit N] [--json]` lists proposals,
waiting ones first (PROPOSED, then REJECTED, which the human may still lower, then LOWERED), each
with its labels, attempts, the Ripener's reason, the judge's reasons, the decision and the note
saying why it waits for the human. `review approve ID --reason TEXT` lowers as the human (from
PROPOSED or REJECTED) and `review deny ID --reason TEXT` rejects (from PROPOSED), both through
`LabelLowering.decide` under the human's identity, so the trail names the actor `human` and the
approver HUMAN. `review --judge` runs the judge's review step over waiting proposals now, the way
`ripen --now` runs the House Bee's whole pass, and prints what it decided; with no judge bound,
or `[honey.lowering] enabled = false`, it says so and exits 1.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.honey` as the `review`
    group (its callback lists or judges; `approve` and `deny` are its subcommands). Calls into
    `hivemind.honey_store` (the lowering service and the store's lowering reads),
    `hivemind.cli.stores` and this group's own `context` and `render` only.

Key invariants:
    - Listing and the human's decisions need no model binding: they open the store alone; only
      `--judge` builds the bindings, and closes the registry in the same event loop.
    - The human's reason is kept on the proposal and never put on the trail (the service's own
      rule); a decision the store refuses exits 1 with its code, an unusable reason exits 2.
    - A proposal's title is the deposit's own words, so it is shown only when its Nectar's label
      is within the group's `--clearance`; ids, labels and reasons are always shown.
    - A flag that would be silently ignored (`--judge` or `--json` beside `approve`/`deny`, or
      both together) is refused as usage, exit 2.

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the decision.
    - hivemind.honey_store.lowering for LabelLowering, what every command here drives.
    - hivemind.cli.honey.maintain for `ripen --now`, which files and reviews in its pass too.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.honey.context import (
    EXIT_BAD_INPUT,
    EXIT_REFUSED,
    HoneyCliContext,
    cli_context,
    human_identity,
    open_access,
    run_or_exit,
)
from hivemind.cli.honey.render import (
    ReviewEntry,
    print_decision,
    print_review,
    print_review_entry,
    print_slot,
    review_counts,
    review_json,
)
from hivemind.cli.stores import JsonOption, closing_registry, open_honey_store
from hivemind.honey_store import (
    HoneyPart,
    HoneyStore,
    LabelLowering,
    LoweringDeps,
    LoweringId,
    LoweringProposal,
    LoweringState,
    ReviewOutcome,
)
from waggle.clock import SystemClock

DEFAULT_REVIEW_LIMIT = 50  # A screenful; there is at most one proposal per eligible Nectar.
MAX_REVIEW_LIMIT = 1_000  # Each listed proposal costs two indexed reads; bounded all the same.
# Waiting first (the judge's queue and the human's), then REJECTED (the human may still lower one),
# then LOWERED (terminal, kept for the record).
_LISTING_ORDER = (LoweringState.PROPOSED, LoweringState.REJECTED, LoweringState.LOWERED)
_NO_JUDGE = "every lowering proposal waits for the human (hive honey review approve|deny)"

__all__ = [
    "DEFAULT_REVIEW_LIMIT",
    "MAX_REVIEW_LIMIT",
    "approve_command",
    "deny_command",
    "review_callback",
]

JudgeOption = Annotated[
    bool, typer.Option("--judge", help="Ask the clearance judge about waiting proposals now.")
]
LimitOption = Annotated[
    int,
    typer.Option("--limit", min=1, max=MAX_REVIEW_LIMIT, help="The most proposals to list."),
]
ProposalArgument = Annotated[
    str, typer.Argument(help="The proposal's id (lowering_...), as `hive honey review` lists it.")
]
ReasonOption = Annotated[
    str, typer.Option("--reason", help="Why; kept on the proposal, never put on the trail.")
]


def review_callback(
    ctx: typer.Context,
    judge: JudgeOption = False,
    limit: LimitOption = DEFAULT_REVIEW_LIMIT,
    as_json: JsonOption = False,
) -> None:
    """List label lowering proposals, waiting ones first; --judge asks the judge about them now."""
    _check_flags(ctx, judge, as_json)
    # `approve` and `deny` do their own work; this body lists or judges only when neither is named.
    if ctx.invoked_subcommand is not None:
        return
    cli_ctx = cli_context(ctx)
    if judge:
        _judge_now(cli_ctx)
        return
    # Reading proposals needs no model: the store alone. Local SQLite, a few indexed reads each.
    store = open_honey_store(cli_ctx.db)
    entries, is_truncated = run_or_exit(_listing(store, limit, cli_ctx.clearance))
    # --json prints every proposal's stored fields with its title and path, for a script to read.
    if as_json:
        typer.echo(review_json(entries, is_truncated))
        return
    print_review(entries, is_truncated)


def approve_command(
    ctx: typer.Context, proposal_id: ProposalArgument, reason: ReasonOption
) -> None:
    """Lower one proposal's Nectar as the human: from PROPOSED, or from REJECTED (the last word)."""
    decided = _decide(cli_context(ctx), proposal_id, approve=True, reason=reason)
    # The store re-checked eligibility and found the target gone: nothing was lowered.
    if decided.state is not LoweringState.LOWERED:
        raise typer.Exit(code=EXIT_REFUSED)


def deny_command(ctx: typer.Context, proposal_id: ProposalArgument, reason: ReasonOption) -> None:
    """Reject one PROPOSED proposal as the human; its Nectar keeps its label."""
    _decide(cli_context(ctx), proposal_id, approve=False, reason=reason)


def _check_flags(ctx: typer.Context, judge: bool, as_json: bool) -> None:
    """Refuse, as usage, a flag the command would otherwise silently ignore."""
    # --judge and --json shape the listing; beside approve or deny they would do nothing at all.
    if ctx.invoked_subcommand is not None and (judge or as_json):
        _usage("--judge and --json apply to the listing, not to approve or deny")
    # --judge prints what the judge decided as text; the JSON document is the listing's.
    if judge and as_json:
        _usage("--json prints the listing; --judge prints what the judge decided, as text")


def _usage(message: str) -> NoReturn:
    """Print a usage refusal to stderr and exit EXIT_BAD_INPUT, as typer's own usage errors do."""
    typer.echo(message, err=True)
    raise typer.Exit(code=EXIT_BAD_INPUT)


def _decide(
    cli_ctx: HoneyCliContext, proposal_id: str, *, approve: bool, reason: str
) -> LoweringProposal:
    """Record the human's decision on one proposal and print it; exit through run_or_exit."""
    # The human decides with no judge and no model: the store alone, under the human's identity,
    # so every event names the actor `human` and the approver HUMAN.
    deps = LoweringDeps(
        open_honey_store(cli_ctx.db),
        human_identity(cli_ctx.manifest),
        SystemClock(),
        cli_ctx.manifest.honey.lowering,
    )
    # Local SQLite: one transaction for the decision, any label change and its event.
    work = LabelLowering(deps).decide(LoweringId(proposal_id), approve, reason)
    decided = run_or_exit(work)
    print_decision(decided)
    return decided


def _judge_now(cli_ctx: HoneyCliContext) -> None:
    """Run the judge's review step over waiting proposals now and print what it decided."""
    opened = open_access(cli_ctx)
    access = opened.access
    # No judge bound, or lowering switched off: nothing here can decide. Say why and exit 1, as
    # `reembed` does with no embedder.
    if access.judge is None:
        print_slot("judge", opened.bindings.judge, _NO_JUDGE)
        raise typer.Exit(code=EXIT_REFUSED)
    # The judge's decisions are the Hive's ("system"), exactly as the House Bee's pass records
    # them; only the approver, JUDGE, says who decided.
    deps = LoweringDeps(access.store, access.identity, SystemClock(), access.lowering, access.judge)
    work = _judge_queue(
        LabelLowering(deps), access.store, access.lowering.max_reviews_per_pass, cli_ctx.clearance
    )
    outcome, entries = run_or_exit(closing_registry(opened.registry, work))
    typer.echo(f"judge review: {review_counts(outcome)}")
    if not entries:
        typer.echo("no proposal was waiting for the judge")
    for entry in entries:
        print_review_entry(entry)
    print_slot("judge", opened.bindings.judge, _NO_JUDGE)


async def _judge_queue(
    lowering: LabelLowering, store: HoneyStore, limit: int, ceiling: HoneyClearance
) -> tuple[ReviewOutcome, tuple[ReviewEntry, ...]]:
    """Run one review step; return its counts and how each proposal it was asked about stands."""
    # The same queue and bound `review_pending` reads, so what is printed is what was judged.
    queued = await store.pending_lowerings(limit)
    # External awaits inside: one JUDGE call per waiting proposal, each under the judge's own
    # timeout; an outage propagates to run_or_exit as a typed error, never a traceback.
    outcome = await lowering.review_pending()
    decided = [await store.get_lowering(proposal.id) for proposal in queued]
    return outcome, tuple([await _entry(store, proposal, ceiling) for proposal in decided])


async def _listing(
    store: HoneyStore, limit: int, ceiling: HoneyClearance
) -> tuple[tuple[ReviewEntry, ...], bool]:
    """Read up to `limit` proposals, waiting ones first; return them and whether more exist."""
    proposals: list[LoweringProposal] = []
    # One state at a time, in listing order, reading one past `limit` in all so the listing
    # knows whether it is the whole list; oldest first within a state (the store's own order).
    for state in _LISTING_ORDER:
        room = limit + 1 - len(proposals)
        if room <= 0:
            break
        proposals.extend(await store.list_lowerings(state, room))
    entries = tuple([await _entry(store, proposal, ceiling) for proposal in proposals[:limit]])
    return entries, len(proposals) > limit


async def _entry(
    store: HoneyStore, proposal: LoweringProposal, ceiling: HoneyClearance
) -> ReviewEntry:
    """Look up what one proposal concerns: its Nectar's title and its SUMMARY row's path."""
    # Local SQLite, two indexed reads on the store's own thread.
    nectar = await store.get_nectar(proposal.nectar_id)
    rows = await store.honey_for_nectar(proposal.nectar_id)
    summary = next(
        (row for row in rows if row.part is HoneyPart.SUMMARY and row.retired_at is None), None
    )
    # A title is the deposit's own words: shown only within the reader's ceiling, as `ls` does.
    title = nectar.title if nectar.clearance.rank <= ceiling.rank else None
    return ReviewEntry(
        proposal=proposal, title=title, path=summary.path if summary is not None else None
    )
