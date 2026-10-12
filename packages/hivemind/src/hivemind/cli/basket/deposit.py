"""Provide `hive basket deposit PATH`: put one file into the Basket as a signed, described blob.

The operator's own deposit, made as the Hive Stand (the machine the operator is on, a borrowed Real
Cell). `deposit PATH --title T --summary S --kind K --media-type MT [--platform linux|windows|macos
...] [--clearance C0|C1|C2]` reads the file and hands it to the Hive's one deposit door
(`RoyalJellyAccess.deposits`, ADR-0048), built the way the running Hive builds it: the door applies
`[basket] max_blob_bytes`, raises the label to the Real Cell floor (C2) whatever `--clearance`
declares, reads text-like content with the untrusted-content scanner, signs the manifest with the
Hive key, and writes the blob's one Honey record through the Honey port. The origin is the Hive
Stand's Cell, `from_borrowed_cell` is true, the tier is the manifest's Hive Stand tier, and there is
no origin bee and no taint. The door's events are recorded on the trail as the actor `human`, and
the blob's hash is printed (a repeat deposit of the same bytes is deduplicated and says so).

Typer turns every parameter into one option and codingrules 5.1 caps a function at five, so the
three metadata options past that cap are declared on the command itself, as `hive run` declares
its own (`DepositCommand`); each one's callback leaves its value on the context, where
`deposit_command` reads it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `basket` command group. Mounted by
    `hivemind.cli.basket` as `deposit`. Calls into `hivemind.basket`, `hivemind.cell`,
    `hivemind.cli.stores`, `hivemind.pheromone` and this group's own `context` only.

Key invariants:
    - The only way bytes enter the Basket here is `BasketDeposits.deposit`; nothing writes a row.
    - A refusal (too large, flagged by the scanner, a label rule) prints the Basket's code and
      exits 1; its `basket.deposit_refused` event is still recorded.
    - Events carry hashes, sizes and labels; never the file's bytes, title or summary.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the deposit order of checks.
    - hivemind.cli.run for RunCommand, the pattern `DepositCommand` follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from typer.core import TyperCommand, TyperOption

from hivemind.basket import BasketError, DepositOutcome, DepositRequest, MediaKind
from hivemind.cell import HoneyClearance, OsFamily, hive_stand_cell_id
from hivemind.cell.local import HiveStandConfig
from hivemind.cli.basket.context import OpenedLab, cli_context, open_lab
from hivemind.cli.stores import closing_registry
from hivemind.cli.tools.context import EXIT_BAD_INPUT, HUMAN_ACTOR, input_or_exit, run_or_exit
from hivemind.manifest import HiveManifest
from hivemind.pheromone import PheromoneEvent

__all__ = ["DepositCommand", "deposit_command"]

# Where `DepositCommand` leaves its three options' values on the command's context (`ctx.meta`).
_MEDIA_TYPE_META = "hivemind.cli.basket.deposit.media_type"
_PLATFORMS_META = "hivemind.cli.basket.deposit.platforms"
_CLEARANCE_META = "hivemind.cli.basket.deposit.clearance"


@dataclass(frozen=True, slots=True)
class _Description:
    """What the operator says about the file: the words and the tags of the Honey record."""

    title: str  # The Honey record's title.
    summary: str  # Its one-paragraph summary.
    kind: MediaKind  # What kind of thing the bytes are.
    media_type: str  # Their MIME type.
    platforms: tuple[OsFamily, ...]  # The operating systems it runs on; empty is any.
    clearance: HoneyClearance | None  # The operator's own label; the Real Cell floor may raise it.


def _keep_media_type(ctx: typer.Context, _param: object, value: str) -> str:
    """Option callback: leave `--media-type` on the context for `deposit_command`."""
    ctx.meta[_MEDIA_TYPE_META] = value
    return value


def _keep_platforms(ctx: typer.Context, _param: object, value: tuple[str, ...]) -> None:
    """Option callback: check each `--platform` names an OS family, in any case, and leave them."""
    try:
        ctx.meta[_PLATFORMS_META] = tuple(OsFamily[name.upper()] for name in value)
    except KeyError as exc:
        raise typer.BadParameter(
            f"{exc.args[0]!r} is not an OS (linux, windows or macos)."
        ) from exc


def _keep_clearance(ctx: typer.Context, _param: object, value: str | None) -> None:
    """Option callback: check `--clearance` names a label, in any case, and leave it."""
    if value is None:
        return  # Not given: the Real Cell floor decides the label alone.
    try:
        ctx.meta[_CLEARANCE_META] = HoneyClearance[value.upper()]
    except KeyError as exc:
        raise typer.BadParameter(f"{value!r} is not a clearance (C0, C1 or C2).") from exc


class DepositCommand(TyperCommand):
    """`hive basket deposit`'s command: the options `deposit_command` declares, plus three more.

    Codingrules 5.1 caps a function at five parameters, and typer turns each parameter into one
    option, so `--media-type`, `--platform` and `--clearance` are declared here, on the command
    itself; each one's callback leaves its value on the context, where `deposit_command` reads it.
    """

    def __init__(self, name: str | None, **settings: object) -> None:
        """Build the command as typer does, then add the three context-carried options."""
        # typer passes every other setting by keyword, exactly as TyperCommand itself takes them.
        super().__init__(name, **cast("dict[str, Any]", settings))
        self.params.append(
            TyperOption(
                param_decls=["--media-type"],
                required=True,
                expose_value=False,
                callback=_keep_media_type,
                help="The file's MIME type, e.g. text/plain or application/zip.",
            )
        )
        self.params.append(
            TyperOption(
                param_decls=["--platform"],
                multiple=True,
                expose_value=False,
                callback=_keep_platforms,
                help="An OS the blob runs on (linux, windows or macos); repeat for several.",
            )
        )
        self.params.append(
            TyperOption(
                param_decls=["--clearance"],
                default=None,
                expose_value=False,
                callback=_keep_clearance,
                help="Your own label (C0, C1 or C2); a Real Cell deposit is at least C2.",
            )
        )


def deposit_command(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="The file to deposit.")],
    title: Annotated[str, typer.Option("--title", help="The Honey record's title.")],
    summary: Annotated[str, typer.Option("--summary", help="One paragraph saying what it is for.")],
    kind: Annotated[MediaKind, typer.Option("--kind", case_sensitive=False, help="What it is.")],
) -> None:
    """Deposit one file into the Basket as the Hive Stand; signed, labelled, described in Honey."""
    cli_ctx = cli_context(ctx)
    # `--media-type`, `--platform` and `--clearance` arrive on the context (`DepositCommand`).
    description = _Description(
        title=title,
        summary=summary,
        kind=kind,
        media_type=str(ctx.meta[_MEDIA_TYPE_META]),
        platforms=ctx.meta.get(_PLATFORMS_META, ()),
        clearance=ctx.meta.get(_CLEARANCE_META),
    )
    request = input_or_exit(lambda: _request(cli_ctx.manifest, _read(path), description))
    lab = open_lab(cli_ctx)
    outcome = run_or_exit(closing_registry(lab.registry, _deposit(lab, request)))
    blob = outcome.blob
    state = "deposited" if outcome.is_new else "already in the Basket (deduplicated)"
    typer.echo(f"{state}: {blob.sha256}")
    typer.echo(f"  {blob.size_bytes} bytes, {blob.media_kind.value}, label {blob.clearance.value}")


def _read(path: Path) -> bytes:
    """Read the file to deposit, or exit 2 saying why it cannot be read."""
    try:
        return path.read_bytes()
    except OSError as exc:
        typer.echo(f"cannot read {path}: {exc}", err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc


def _request(manifest: HiveManifest, content: bytes, said: _Description) -> DepositRequest:
    """Build the deposit as the Hive Stand: a borrowed Real Cell, no bee, nothing tainted."""
    manifest_dir = manifest.source_path.parent if manifest.source_path is not None else Path.cwd()
    tier = HiveStandConfig.from_section(manifest.hive_stand, manifest_dir).comb_shield
    return DepositRequest(
        content=content,
        title=said.title,
        summary=said.summary,
        media_kind=said.kind,
        media_type=said.media_type,
        platform_tags=said.platforms,
        declared=said.clearance,
        origin_cell_id=hive_stand_cell_id(manifest.hive.node_id),
        origin_task_id=None,
        origin_bee=None,
        from_borrowed_cell=True,
        tier=tier,
        tainted=False,
    )


async def _deposit(lab: OpenedLab, request: DepositRequest) -> DepositOutcome:
    """Deposit through the Hive's door and record its events, a refusal's included."""
    try:
        outcome = await lab.access.deposits.deposit(request)
    except BasketError as refused:
        # `basket.deposit_refused` is the audit of a refusal; it is recorded before the error
        # propagates to the exit code.
        await _record(lab, refused.events)
        raise
    await _record(lab, outcome.events)
    return outcome


async def _record(lab: OpenedLab, events: tuple[PheromoneEvent, ...]) -> None:
    """Record the door's events on the trail, as the human who made the deposit."""
    for event in events:
        # The door stamps its own identity (`system`); this deposit is the operator's act.
        await lab.trail.record(event.model_copy(update={"actor": HUMAN_ACTOR}))
