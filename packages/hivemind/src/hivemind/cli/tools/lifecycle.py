"""Provide `hive tools retire` and `hive tools rollback`: withdraw a tool version, or restore one.

Both act through the Comb Registry with the Queen's authority (`QueenPrincipal`): the Queen is the
principal that may retire or roll back at either scope, and the operator's CLI is a door to her
authority in exactly the way `hive honey review approve` acts as `human` -- the registry sees the
Queen, the trail sees the actor `human`. `retire NAME --version V --reason TEXT [--cell ID]` marks
one version RETIRED (the row stays as history and never becomes current again; retiring the
current version leaves the tool with none until a promotion or a rollback). `rollback NAME --to V
--reason TEXT [--cell ID]` makes an older PROMOTED version current again; its own passing report is
its evidence, so nothing is quarantined again. Each prints the registry's `RegistryChange` and
records the matching events: `tool.retired`, or `tool.promoted` (naming the version it displaced)
and `tool.rolled_back`, as ChangeKind says a rollback must.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `tools` command group. Mounted by
    `hivemind.cli.tools` as `retire` and `rollback`. Calls into `hivemind.cli.stores` (the
    registry and the trail), `hivemind.pheromone`, `hivemind.royal_jelly` and this group's own
    `context` and `render` only.

Key invariants:
    - The registry write happens first; the trail event is recorded only for a change that
      happened, so a refusal (exit 1, with the refusal's stable code) records nothing.
    - Events carry ids, the version, a short hash and the human's reason; never the package.
    - The registry is the only way a version changes standing; nothing here edits a row.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for retire and rollback.
    - hivemind.royal_jelly.registry for CombRegistry.retire and .rollback and their refusals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import typer
from pydantic import JsonValue

from hivemind.cli.stores import open_comb_registry, open_trail
from hivemind.cli.tools.context import (
    HUMAN_ACTOR,
    CellOption,
    cli_context,
    run_or_exit,
    tool_key,
)
from hivemind.cli.tools.render import print_change
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS, PheromoneTrail, ToolEvent
from hivemind.royal_jelly import CombRegistry, QueenPrincipal, RegistryChange, ToolKey
from waggle.clock import Clock, SystemClock
from waggle.ids import new_event_id

__all__ = ["retire_command", "rollback_command"]

_HASH_PREFIX_CHARS = 16  # A hash on the trail is recognisable by its first sixteen hex digits.
NameArgument = Annotated[str, typer.Argument(help="The tool's name.")]
ReasonOption = Annotated[str, typer.Option("--reason", help="Why; recorded on the trail.")]


@dataclass(frozen=True, slots=True)
class _Ledger:
    """What a registry change is written to, and the identity it is recorded under."""

    registry: CombRegistry  # The Comb Registry the change is made in.
    trail: PheromoneTrail  # Where the change's events land.
    manifest: HiveManifest  # Names the Hive and node the events belong to.
    clock: Clock  # Stamps the events.


def retire_command(
    ctx: typer.Context,
    name: NameArgument,
    version: Annotated[str, typer.Option("--version", help="The version to retire.")],
    reason: ReasonOption,
    cell: CellOption = None,
) -> None:
    """Retire one version of a tool, as the Queen's authority; recorded on the trail as human."""
    ledger = _ledger(ctx)
    change = run_or_exit(_retire(ledger, tool_key(name, cell), version, reason))
    print_change(change)


def rollback_command(
    ctx: typer.Context,
    name: NameArgument,
    to: Annotated[str, typer.Option("--to", help="The older version to make current again.")],
    reason: ReasonOption,
    cell: CellOption = None,
) -> None:
    """Make an older version current again, as the Queen's authority; recorded as human."""
    ledger = _ledger(ctx)
    change = run_or_exit(_rollback(ledger, tool_key(name, cell), to, reason))
    print_change(change)


def _ledger(ctx: typer.Context) -> _Ledger:
    """Open the registry and the trail the command writes to."""
    cli_ctx = cli_context(ctx)
    return _Ledger(
        registry=open_comb_registry(cli_ctx.db),
        trail=open_trail(cli_ctx.db),
        manifest=cli_ctx.manifest,
        clock=SystemClock(),
    )


async def _retire(ledger: _Ledger, key: ToolKey, version: str, reason: str) -> RegistryChange:
    """Retire a version with the Queen's authority, then record `tool.retired` as the human."""
    # The registry write comes first and raises on any refusal, so a refused retirement leaves
    # nothing on the trail. The operator's CLI acts with the Queen's authority: the registry
    # requires her principal for HIVE scope and accepts it for CELL scope.
    change = await ledger.registry.retire(key, version, QueenPrincipal(), reason)
    await ledger.trail.record(_event(ledger, "tool.retired", change))
    return change


async def _rollback(ledger: _Ledger, key: ToolKey, to: str, reason: str) -> RegistryChange:
    """Roll a tool back, then record `tool.promoted` and `tool.rolled_back` as the human."""
    change = await ledger.registry.rollback(key, to, QueenPrincipal(), reason)
    # ChangeKind.ROLLED_BACK asks for both: the restored version is current again (a promotion
    # naming the one it displaced, ADR-0046) and the rollback itself is the reason it is.
    await ledger.trail.record(_event(ledger, "tool.promoted", change))
    await ledger.trail.record(_event(ledger, "tool.rolled_back", change))
    return change


def _event(ledger: _Ledger, kind: str, change: RegistryChange) -> ToolEvent:
    """Build one tool event for a registry change, under the actor `human`."""
    row = change.row
    payload: dict[str, JsonValue] = {
        "name": row.name,
        "version": row.version,
        "scope": row.scope.value,
        "cell_id": row.cell_id,
        "package_sha256": row.package_sha256[:_HASH_PREFIX_CHARS],
        "supersedes_version": change.supersedes_version,
        "authority": change.by,
        "reason": change.reason[: MAX_PAYLOAD_STRING_CHARS - 1],
    }
    return ToolEvent(
        id=new_event_id(ledger.clock),
        hive_id=ledger.manifest.hive.id,
        node_id=ledger.manifest.hive.node_id,
        at=ledger.clock.now(),
        actor=HUMAN_ACTOR,
        kind=kind,
        subject_id=row.tool_id,
        payload=payload,
    )
