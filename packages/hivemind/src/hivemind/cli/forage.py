"""Provide `hive forage`: read the Forage ledger, and revise one Warden's standing grant.

Three thin typer layers over `hivemind.queen.forage`'s own public API (codingrules section 2's CLI
row: no logic of its own). `status` restores a `hivemind.queen.forage.ForageLedger` from the
durable `SqliteLedgerStore` (`hivemind.cli.stores.open_ledger`) and prints its headroom, the Royal
Reserve, every Cell's latest reported capacity and every Warden's reported local pool (the ledger's
own three in-memory tables, rebuilt by `ForageLedger.restore`). `grants` lists every live grant the
same way. `grant <warden> --sub-bees N [--seats N] [--spend USD]` writes a grown or shrunk revision
through `hivemind.queen.forage.grants.revise` (ADR-0014: "growing or shrinking a grant is a new
GrantIssued revision... never itself a GrantState change") of that Warden's own existing live
grant, and records the same `forage.granted` event shape `hivemind.queen.dispatcher`'s own dispatch
path already writes for a fresh grant, so the trail carries one shape for "a grant now has these
terms" everywhere it happens. Nothing here starts a Queen: a revision written while no Queen runs
takes effect the moment she next starts (her own `ForageLedger.restore` reads it back), or, while
she is already running, the next time the holding Warden's own heartbeat renews its grants -- this
command's own help text says so, since there is no live Warden link in this process to push a
`GrantIssued` down early. `--manifest`/`--db` are read once on this app's own callback and shared
through `ctx.obj` (codingrules section 5.1's parameter cap: `grant` already carries four flags of
its own).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.queen.forage`, `hivemind.forage`,
    `hivemind.pheromone` (ForageEvent) and `hivemind.cli.stores` only.

Key invariants:
    - `grant` never starts a second Queen and never writes anywhere but the ledger's own store plus
      the one `forage.granted` trail event, in that order, matching every other write path in this
      package (codingrules section 8.11's "one write path" read across to the CLI's own commands).
    - `grant` only revises an already-live grant (a Warden's first grant is always issued by a
      running Queen's own dispatcher, never by this command): it reuses that grant's own `cell_id`,
      `token_budget` and `allowed` bindings unchanged, refusing with a clear message when no live
      grant exists yet for the named Warden.

See Also:
    - .claude/roadmap.md step 4.11 for this module's own deliverable, verbatim.
    - .claude/roadmap.md step 4.7 for the ledger and grant-lease shape this module reads and writes.
    - docs/adr/0014 for "growing or shrinking a grant is a new revision, never a GrantState change".
    - hivemind.queen.forage.ledger for ForageLedger, `status`'s and `grants`'s one read.
    - hivemind.queen.forage.grants for revise, `grant`'s one write.
    - hivemind.queen.dispatcher for `record_forage_granted`, the `forage.granted` shape this
      module's own `_record_granted` mirrors for a CLI-issued revision.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    JsonOption,
    ManifestOption,
    load_manifest_or_exit,
    open_ledger,
    open_trail,
)
from hivemind.forage import ForageGrant, SeatReservation
from hivemind.manifest import HiveManifest
from hivemind.pheromone import ForageEvent, PheromoneTrail, TrailQuery
from hivemind.queen.forage import ForageLedger, revise
from hivemind.queen.forage.ledger import SqliteLedgerStore
from waggle.clock import SystemClock
from waggle.ids import WardenId, new_event_id

app = typer.Typer(name="forage", help="Read the Forage ledger; revise a Warden's standing grant.")

__all__ = ["app"]


@dataclass(frozen=True, slots=True)
class _ForageCliContext:
    """The resolved manifest and db path, shared by every `hive forage` subcommand below."""

    manifest: HiveManifest
    db: Path


class _GrantRow(BaseModel):
    """One `hive forage grants` row: a live grant's own terms."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: str = Field(description="The grant's own id.")
    holder: str = Field(description="The Warden that holds it.")
    state: str = Field(description="Its GrantState.")
    max_sub_bees: int = Field(description="Sub-bees the holder may run under this grant.")
    seats: int = Field(description="Total seats reserved across every source this grant names.")
    spend_budget: float = Field(description="Total spend the grant allows.")
    spent: float = Field(description="Spend already consumed.")
    expires_at: str = Field(description="When it returns to the pool unless renewed, ISO 8601.")


class _StatusPayload(BaseModel):
    """Everything `hive forage status` prints, gathered in one place for `--json`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sub_bee_headroom: int = Field(description="Shared sub-bee slots still free.")
    shared_seat_headroom: int = Field(description="Shared seats still free.")
    reserve_seats: int = Field(description="Seats the Royal Reserve holds back.")
    capacities: tuple[tuple[str, int, int], ...] = Field(
        description="Per Cell: (cell_id, cores, max_sub_bees)."
    )
    local_pools: tuple[tuple[str, int, int], ...] = Field(
        description="Per Warden: (warden_id, sub_bees_active, seats_exported)."
    )
    throttled: tuple[tuple[str, str, float], ...] = Field(
        description="Per source still inside a provider's rate-limit window: (source id, or the "
        "provider when the map had no source, the window's end as ISO 8601, the wait in s)."
    )
    note: str = Field(
        description="Where the throttled rows come from: the trail's own llm.throttled events, "
        "since the live figure is only in a running Queen's in-memory Forage map."
    )


@app.callback()
def forage_callback(
    ctx: typer.Context, manifest: ManifestOption = DEFAULT_MANIFEST, db: DbOption = None
) -> None:
    """Load the manifest and resolve the db file every `hive forage` subcommand shares."""
    loaded = load_manifest_or_exit(manifest)
    db_path = db if db is not None else loaded.resolve_path(loaded.hive.db)
    ctx.obj = _ForageCliContext(manifest=loaded, db=db_path)


async def _restored_ledger(store: SqliteLedgerStore, manifest: HiveManifest) -> ForageLedger:
    """Restore a fresh in-memory ForageLedger from `store`, seeded with the manifest's reserve."""
    ledger = ForageLedger(reserve=manifest.forage.reserve, store=store)
    await ledger.restore()
    return ledger


# ──────────────────────────────────────────────────────────────────────────────
# hive forage status
# ──────────────────────────────────────────────────────────────────────────────


@app.command("status")
def status_command(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """Print headroom, the Royal Reserve, every Cell's capacity and every Warden's local pool."""
    cli_ctx: _ForageCliContext = ctx.obj
    store = open_ledger(cli_ctx.db)
    throttled = asyncio.run(_throttled_now(open_trail(cli_ctx.db)))
    payload = asyncio.run(_status_payload(store, cli_ctx.manifest)).model_copy(
        update={"throttled": throttled}
    )
    if as_json:
        typer.echo(payload.model_dump_json(indent=2))
        return
    _print_status(payload)


async def _status_payload(store: SqliteLedgerStore, manifest: HiveManifest) -> _StatusPayload:
    """Build `_StatusPayload` from a restored ledger's headroom plus the store's own raw lists."""
    ledger = await _restored_ledger(store, manifest)
    capacities = tuple(
        (str(cell_id), capacity.host.cores, capacity.max_sub_bees)
        for cell_id, capacity in await store.list_capacities()
    )
    local_pools = tuple(
        (str(report.warden_id), report.sub_bees_active, report.seats_exported)
        for report in await store.list_local_reports()
    )
    headroom = ledger.headroom()
    return _StatusPayload(
        sub_bee_headroom=headroom.sub_bees,
        shared_seat_headroom=headroom.shared_seats,
        reserve_seats=ledger.reserve.seats,
        capacities=capacities,
        local_pools=local_pools,
        throttled=(),
        note="throttled rows are rebuilt from the trail's llm.throttled events.",
    )


async def _throttled_now(trail: PheromoneTrail) -> tuple[tuple[str, str, float], ...]:
    """Return every source whose last `llm.throttled` window has not passed yet.

    The live throttle is a fact on a running Queen's in-memory `ForageMap`, out of this
    process's reach; the Fanner records every one as `llm.throttled` with the source and the
    wait, which is enough to rebuild what is still masked right now.
    """
    events = await trail.query(TrailQuery(family="llm", kind="llm.throttled"))
    now = SystemClock().now()
    latest: dict[str, tuple[str, str, float]] = {}
    for event in events:  # Trail order: a later throttle of the same source replaces an earlier.
        raw_wait = event.payload.get("wait_s")
        # A payload is untyped JSON; anything but a number means no usable window.
        wait_s = float(raw_wait) if isinstance(raw_wait, int | float) else 0.0
        until = event.at + timedelta(seconds=wait_s)
        name = str(event.payload.get("source_id") or event.payload.get("provider") or "?")
        if until > now:
            latest[name] = (name, until.isoformat(), wait_s)
        else:
            latest.pop(name, None)
    return tuple(latest.values())


def _print_status(payload: _StatusPayload) -> None:
    """Print `payload` as a handful of small, labelled tables."""
    typer.echo(
        f"headroom: sub_bees={payload.sub_bee_headroom}  "
        f"shared_seats={payload.shared_seat_headroom}  reserve_seats={payload.reserve_seats}"
    )
    typer.echo("\nCELLS")
    typer.echo(f"{'CELL':<30}  {'CORES':>6}  MAX_SUB_BEES")
    for cell_id, cores, max_sub_bees in payload.capacities:
        typer.echo(f"{cell_id:<30}  {cores:>6}  {max_sub_bees}")
    typer.echo("\nWARDENS (local pool, reported not granted)")
    typer.echo(f"{'WARDEN':<30}  {'SUB_BEES_ACTIVE':>16}  SEATS_EXPORTED")
    for warden_id, active, exported in payload.local_pools:
        typer.echo(f"{warden_id:<30}  {active:>16}  {exported}")
    typer.echo("\nTHROTTLED SOURCES (inside a provider's rate-limit window)")
    typer.echo(f"{'SOURCE':<30}  {'UNTIL':<32}  WAIT_S")
    for name, until, wait_s in payload.throttled:
        typer.echo(f"{name:<30}  {until:<32}  {wait_s:g}")
    typer.echo(f"\nnote: {payload.note}")


# ──────────────────────────────────────────────────────────────────────────────
# hive forage grants
# ──────────────────────────────────────────────────────────────────────────────


@app.command("grants")
def grants_command(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List every live grant: holder, state, max sub-bees, seats, spend budget and spent."""
    cli_ctx: _ForageCliContext = ctx.obj
    store = open_ledger(cli_ctx.db)
    ledger = asyncio.run(_restored_ledger(store, cli_ctx.manifest))
    rows = tuple(_grant_row(grant) for grant in ledger.live_grants())
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    typer.echo(
        f"{'GRANT':<30}  {'HOLDER':<20}  {'STATE':<10}  {'SUB_BEES':>8}  {'SEATS':>5}  "
        f"{'SPEND_BUDGET':>12}  {'SPENT':>8}  EXPIRES_AT"
    )
    for row in rows:
        typer.echo(
            f"{row.grant_id:<30}  {row.holder:<20}  {row.state:<10}  {row.max_sub_bees:>8}  "
            f"{row.seats:>5}  {row.spend_budget:>12.2f}  {row.spent:>8.2f}  {row.expires_at}"
        )


def _grant_row(grant: ForageGrant) -> _GrantRow:
    """Build one `hive forage grants` row from a live ForageGrant."""
    return _GrantRow(
        grant_id=grant.id,
        holder=grant.holder,
        state=grant.state.value,
        max_sub_bees=grant.max_sub_bees,
        seats=sum(seat.seats for seat in grant.seats),
        spend_budget=grant.spend_budget,
        spent=grant.spent,
        expires_at=grant.expires_at.isoformat(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# hive forage grant <warden>
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _GrantEdits:
    """What the operator asked `hive forage grant` to change, bundled per codingrules 5.1."""

    warden: WardenId
    sub_bees: int
    seats: int | None
    spend: float | None


@app.command("grant")
def grant_command(
    ctx: typer.Context,
    warden: Annotated[str, typer.Argument(help="The Warden this grant revision is for.")],
    sub_bees: Annotated[int, typer.Option("--sub-bees", help="Sub-bees this grant now allows.")],
    seats: Annotated[
        int | None, typer.Option("--seats", help="Shared seats this grant now reserves.")
    ] = None,
    spend: Annotated[
        float | None, typer.Option("--spend", help="Spend budget this grant now allows, in USD.")
    ] = None,
) -> None:
    """Write a grown or shrunk grant revision for WARDEN, and record `forage.granted`.

    Only revises an already-live grant (module docstring); takes effect the moment the running
    Queen next starts, or the next time WARDEN's own heartbeat renews its grants.
    """
    cli_ctx: _ForageCliContext = ctx.obj
    store = open_ledger(cli_ctx.db)
    trail = open_trail(cli_ctx.db)
    ledger = asyncio.run(_restored_ledger(store, cli_ctx.manifest))
    edits = _GrantEdits(warden=WardenId(warden), sub_bees=sub_bees, seats=seats, spend=spend)
    revised = _revised_grant(ledger, edits, cli_ctx.manifest.forage.grant_ttl_s)
    asyncio.run(_write_revision(ledger, trail, cli_ctx.manifest, revised))
    typer.echo(
        f"granted: {revised.id}  holder={revised.holder}  max_sub_bees={revised.max_sub_bees}"
    )


def _revised_grant(ledger: ForageLedger, edits: _GrantEdits, grant_ttl_s: float) -> ForageGrant:
    """Grow or shrink `edits.warden`'s existing live grant; refuse when none exists yet."""
    base = next(iter(ledger.grants_for(edits.warden)), None)
    if base is None:
        raise typer.BadParameter(
            f"No live grant exists yet for warden {edits.warden!r}; a Warden's first grant is "
            "always issued by a running Queen's own dispatcher, never by this command."
        )
    return base.model_copy(
        update={
            "revision": base.revision + 1,
            "seats": _seat_reservations(base, edits.seats),
            "max_sub_bees": edits.sub_bees,
            "spend_budget": edits.spend if edits.spend is not None else base.spend_budget,
            "expires_at": SystemClock().now() + timedelta(seconds=grant_ttl_s),
            "reason": "Revised by the operator via `hive forage grant`.",
        }
    )


def _seat_reservations(base: ForageGrant, seats: int | None) -> tuple[SeatReservation, ...]:
    """Return `base`'s own seat reservations, or its first one resized when `--seats` was given.

    Args:
        base: The live grant being revised; `base.seats` is used as-is when `seats` is None.
        seats: The new total to reserve on `base`'s own first source, when given.

    Raises:
        typer.BadParameter: `seats` was given but `base` reserves no source to attribute it to.
    """
    if seats is None:
        return base.seats
    if not base.seats:
        raise typer.BadParameter(
            f"Grant {base.id!r} reserves no seats on any source yet; --seats has no source to "
            "resize."
        )
    first, *rest = base.seats
    return (first.model_copy(update={"seats": seats}), *rest)


async def _write_revision(
    ledger: ForageLedger, trail: PheromoneTrail, manifest: HiveManifest, revised: ForageGrant
) -> None:
    """Commit `revised` through `revise`, then record the one `forage.granted` event for it."""
    await revise(ledger, revised)
    await _record_granted(trail, manifest, revised)


async def _record_granted(
    trail: PheromoneTrail, manifest: HiveManifest, grant: ForageGrant
) -> None:
    """Record `forage.granted` exactly as the dispatcher's own `record_forage_granted` does."""
    clock = SystemClock()
    event = ForageEvent(
        id=new_event_id(clock),
        hive_id=manifest.hive.id,
        node_id=manifest.hive.node_id,
        at=clock.now(),
        actor="human",
        kind="forage.granted",
        subject_id=grant.id,
        payload={
            "warden_id": grant.holder,
            "max_sub_bees": grant.max_sub_bees,
            "revision": grant.revision,
        },
    )
    await trail.record(event)
