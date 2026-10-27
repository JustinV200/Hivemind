"""Provide `hive abscond`: tear the whole Hive down, human-only and break-glass (roadmap 13.4).

Codingrules 15: Absconding "must always work even if the Brood Chamber is corrupt", releases every
Real Cell lease and destroys every Virtual Cell, and is human-only and break-glass: password
re-auth, an explicit typed confirmation phrase and a final scope review. No route, autopilot rule
or tool calls it; it is this command, typed at the Hive Stand. In order:

1. The operator password re-opens the console key (`consent.reauthenticate`); `--password-stdin`
   reads it from the first line of stdin, else a hidden prompt asks.
2. The `absconding` enforcement point passes as the operator (`consent.authorize_abscond`).
3. The scope review (`scope.gather_scope`, `review.review_lines`) shows what goes and numbers
   every Leaving; the human types the numbers of those to remove (blank keeps them all).
4. A final yes to the review, then the typed phrase `abscond my hive`.
5. `execute.carry_out` tears it down and records `guard.absconded`; the receipt is printed. A
   step that fails partway (Docker down, a locked Leaving) exits 1 with one line; its
   `guard.absconded` holds what was done, and running the command again finishes the rest.

Anything refused or declined before step 5 exits 1 having changed nothing (a refused point
leaves its `guard.denied` row). Each prompt reads the next stdin line under `--password-stdin`
too, in the order above.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.abscond`. Registered as `hive
    abscond` by `hivemind.cli.app`. Calls into `hivemind.cli.compose` (deps, guard, night_veil,
    virtual_cells), `hivemind.cli.landing` (read_password), `hivemind.cli.stores`, the package's
    own modules, and waggle only.

Key invariants:
    - Nothing is destroyed, released, evicted or removed before steps 1-4 all passed.

See Also:
    - docs/runbooks/absconding.md for the operator's walk-through.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, NoReturn

import typer

from hivemind.cell.leavings import Leaving
from hivemind.cli.abscond.consent import (
    PHRASE,
    Evidence,
    authorize_abscond,
    phrase_matches,
    reauthenticate,
)
from hivemind.cli.abscond.execute import carry_out
from hivemind.cli.abscond.review import parse_ticks, receipt_lines, review_lines
from hivemind.cli.abscond.scope import AbscondScope, ScopeSources, gather_scope
from hivemind.cli.compose.deps import build_enforcer, build_ledger, open_default_stores
from hivemind.cli.compose.night_veil import attach_side_channels
from hivemind.cli.compose.virtual_cells import build_virtual_cells
from hivemind.cli.landing import describe, read_password
from hivemind.cli.readback.virtual_abscond import AbscondDeps
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    ManifestOption,
    load_manifest_or_exit,
    open_basket_store,
    open_cluster_orders,
    open_leavings,
    open_trail,
)
from hivemind.common.errors import HiveMindError
from hivemind.manifest import HiveManifest
from waggle.clock import SystemClock

ReasonOption = Annotated[
    str, typer.Option("--reason", help="Why, as a short phrase for the trail.", show_default=False)
]
PasswordStdinOption = Annotated[
    bool,
    typer.Option(
        "--password-stdin", help="Read the operator password, then each answer, from stdin."
    ),
]
# What a refusal, or a step failing partway, raises; anything else is a bug and stays a traceback.
_TYPED_ERRORS = (HiveMindError, PermissionError, ValueError, OSError)
MAX_REASON_CHARS = 300  # A sentence naming ids, like every trail reason in the Hive.

__all__ = ["abscond_command"]


def abscond_command(
    reason: ReasonOption,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    password_stdin: PasswordStdinOption = False,
) -> None:
    """Tear every Cell, lease and grant down (human-only: password, review and typed phrase)."""
    loaded = load_manifest_or_exit(manifest)
    if not reason.strip():
        _refuse("--reason must name why, for the trail.")
    password = _refusing(lambda: read_password(password_stdin))
    # Re-auth before a single store is opened: nothing of the Hive is touched for a stranger.
    _refusing(lambda: asyncio.run(reauthenticate(loaded, password)))
    sources, deps = _open(loaded)
    scope = _refusing(lambda: asyncio.run(_gate(sources)))
    for line in review_lines(scope):
        typer.echo(line)
    ticked: tuple[Leaving, ...] = ()
    if scope.leavings:
        typed = typer.prompt("Leavings to remove (numbers; blank keeps them)", default="")
        ticked = _refusing(lambda: parse_ticks(typed, scope.leavings))
    if not typer.confirm("Abscond now, tearing down everything above?", default=False):
        _refuse("declined at the scope review; nothing was done.")
    if not phrase_matches(typer.prompt(f"Type '{PHRASE}' to confirm")):
        _refuse(f"the phrase was not {PHRASE!r}; nothing was done.")
    evidence = Evidence(reason=reason[:MAX_REASON_CHARS], leavings_ticked=len(ticked))
    try:
        receipt = asyncio.run(carry_out(deps, sources.basket, scope, ticked, evidence))
    except _TYPED_ERRORS as exc:
        _refuse(f"stopped partway ({_why(exc)}); guard.absconded records what was done.")
    for line in receipt_lines(receipt):
        typer.echo(line)


async def _gate(sources: ScopeSources) -> AbscondScope:
    """Steps 2-3: the point, then the scope; raise on a refusal."""
    enforcer = build_enforcer(sources.manifest, sources.trail, SystemClock())
    if not await authorize_abscond(enforcer):
        raise PermissionError("the absconding point refused the operator (see guard.denied).")
    return await gather_scope(sources)


def _open(manifest: HiveManifest) -> tuple[ScopeSources, AbscondDeps]:
    """Open every store the review and the teardown read, as the running Hive opens them."""
    db = manifest.resolve_path(manifest.hive.db)
    clock = SystemClock()
    trail = open_trail(db)
    ledger = build_ledger(manifest, manifest.forage.reserve)
    virtual_cells = build_virtual_cells(manifest, trail, clock)
    if virtual_cells is not None:
        # A Night Veil Cell ended here is purged from every store, whichever process ends it.
        stores = open_default_stores(manifest)
        attach_side_channels(virtual_cells.night_veil, virtual_cells.registry, stores, ledger)
    root = manifest.basket.root
    basket_root = manifest.resolve_path(root) if root is not None else db.parent / "basket"
    basket = open_basket_store(db, basket_root, manifest.basket.max_blob_bytes)
    leavings = open_leavings(db)
    sources = ScopeSources(manifest, trail, ledger, virtual_cells, basket, leavings)
    orders = open_cluster_orders(db)
    deps = AbscondDeps(manifest, trail, ledger, orders, clock, virtual_cells, leavings)
    return sources, deps


def _refusing[T](run: Callable[[], T]) -> T:
    """Run `run`; turn a typed refusal into one stderr line and exit 1."""
    try:
        return run()
    except _TYPED_ERRORS as exc:
        _refuse(_why(exc))


def _why(exc: Exception) -> str:
    """One line naming a typed refusal."""
    return describe(exc) if isinstance(exc, HiveMindError) else str(exc)


def _refuse(why: str) -> NoReturn:
    """Print why `hive abscond` did nothing, and exit 1."""
    typer.echo(f"hive abscond refused: {why}", err=True)
    raise typer.Exit(code=1)
