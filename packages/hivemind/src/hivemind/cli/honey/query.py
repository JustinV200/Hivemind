"""Provide `hive honey query` and `hive honey stats`: search the Honey Store, and count it.

`query <text> [--scope ...] [--max-hits N] [--json]` searches the Hive's Honey (ripened,
labelled knowledge in the Honey Store, its cold memory tier) with the Hive's own retriever -- the
same hybrid full-text and vector search a bee's `HoneyQuery` gets -- as the operator's reader:
every scope, up to the group's `--clearance` (C2 by default). The retriever is built by
`build_honey_access`, so with no usable embedder the search runs on full text alone and says why.
`stats [--json]` prints `HoneyStore.stats()`: Nectar by state, live Honey by part, label and
scope kind, and vectors per embedding model (ADR-0036's coverage per model). Both are thin
layers: the retriever and the store do the work, `hivemind.cli.honey.render` the printing.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.honey` as `query` and
    `stats`. Calls into `hivemind.honey_store` and `hivemind.cli.honey.context`/`.render` only.

Key invariants:
    - A query records its one `honey.queried` event (counts only, never the words), exactly as
      any other reader's does; `stats` records nothing.
    - A `--scope` that is not a valid scope string exits 2 before anything is opened.

See Also:
    - hivemind.honey_store.honey.retrieve for HoneyRetriever, the search this runs.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for coverage per model.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli.honey.context import (
    EXIT_BAD_INPUT,
    cli_context,
    human_identity,
    open_access,
    reader_for,
    run_or_exit,
)
from hivemind.cli.honey.render import print_response, print_slot, print_stats
from hivemind.cli.stores import JsonOption, closing_registry, open_honey_store
from hivemind.honey_store import HoneySearch, InvalidScopeError, folder_for_scope
from waggle.messages.honey.exchange import DEFAULT_MAX_HITS, MAX_MAX_HITS, MIN_MAX_HITS

__all__ = ["query_command", "stats_command"]

# `--scope`, repeatable; none means every scope the operator may read.
ScopeOption = Annotated[
    list[str] | None,
    typer.Option(
        "--scope",
        help="Search only this scope: hive, cell:<id>, bee:<id> or task:<id>. Repeatable.",
    ),
]
# `--max-hits`, bounded exactly as a HoneyQuery's own max_hits is on the wire.
MaxHitsOption = Annotated[
    int,
    typer.Option("--max-hits", min=MIN_MAX_HITS, max=MAX_MAX_HITS, help="The most hits to return."),
]


def query_command(
    ctx: typer.Context,
    text: Annotated[str, typer.Argument(help="What to search for.")],
    scope: ScopeOption = None,
    max_hits: MaxHitsOption = DEFAULT_MAX_HITS,
    as_json: JsonOption = False,
) -> None:
    """Search the Honey Store as the operator, within the group's --clearance."""
    cli_ctx = cli_context(ctx)
    # A bad --scope is refused before anything is opened.
    scopes = _checked_scopes(scope or [])
    # The Hive's own retriever, built the one way the running Hive builds it.
    opened = open_access(cli_ctx)
    access = opened.access
    search = HoneySearch(
        text=text,
        reader=reader_for(cli_ctx),
        requested_scopes=scopes,
        max_hits=max_hits,
        # The operator is not a model with a window: the manifest's own ceiling is the budget.
        max_tokens=access.retrieval.max_budget_tokens,
    )
    # At most one model call (the query's own embedding), bounded by embed_timeout_s; on a
    # timeout or an outage the search runs on full text alone and its reason says so.
    # The operator is asking, so the query's trail event names the human, not the Hive.
    retriever = access.retriever.with_identity(human_identity(cli_ctx.manifest))
    response = run_or_exit(closing_registry(opened.registry, retriever.search(search)))
    # --json prints the wire response unchanged, for a script to read.
    if as_json:
        typer.echo(response.model_dump_json(indent=2))
        return
    print_response(response)
    print_slot("embedder", opened.bindings.embedder, "this search used full text only")


def stats_command(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """Count the Honey Store: Nectar by state, Honey by part, label and scope, vectors by model."""
    cli_ctx = cli_context(ctx)
    # Counting needs no model: the store alone. Local SQLite, a handful of GROUP BYs.
    store = open_honey_store(cli_ctx.db)
    stats = run_or_exit(store.stats())
    # --json prints the counts unchanged, for a script to read.
    if as_json:
        typer.echo(stats.model_dump_json(indent=2))
        return
    print_stats(stats)


def _checked_scopes(raw: list[str]) -> tuple[str, ...]:
    """Return the `--scope` values as scopes, exiting 2 on the first that is not one."""
    # Each value is checked on its own, so the error names the one that is wrong.
    for scope in raw:
        try:
            # folder_for_scope refuses anything but hive, cell:<id>, bee:<id> and task:<id>.
            folder_for_scope(scope)
        except InvalidScopeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=EXIT_BAD_INPUT) from exc
    return tuple(raw)
