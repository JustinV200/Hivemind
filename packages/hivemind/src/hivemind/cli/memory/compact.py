"""Provide `hive memory compact <bee>`: one real compaction over closed-task Bee Bread entries.

Runs `hivemind.memory.compact` on `ModelSlot.RIPENER`, through the registry built from the
manifest (a real model call; the fake provider in tests), over every closed task's Bee Bread
entries placed under one Warden.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Mounted by `hivemind.cli.memory.app` as `compact`.
    Calls into `hivemind.memory`, `hivemind.brood_chamber`, `hivemind.forage` (ModelSlot),
    `hivemind.llm` (BoundModel, DirectCallGate) and `hivemind.cli.stores`/`.context` only.

Key invariants:
    - `<bee>` names a `hivemind.brood_chamber.Task.warden_id`, the one bee-shaped field a Task
      carries this phase (see this dispatch's own report for why).

See Also:
    - .claude/roadmap.md step 4.11 for this command's own deliverable, verbatim.
    - hivemind.memory.compact for compact, this module's one core call.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

import typer

from hivemind.brood_chamber import TERMINAL_STATUSES, BroodChamber, TaskFilter
from hivemind.cli.memory.context import WIDEST, chamber_identity, memory_context, resolved_db
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    build_registry,
    closing_registry,
    load_manifest_or_exit,
    open_chamber,
    open_memory,
)
from hivemind.forage import ModelSlot
from hivemind.llm import BoundModel, DirectCallGate
from hivemind.memory import (
    BeeBread,
    BeeBreadEntryKind,
    CompactionDeps,
    CompactionRequest,
    CompactionResult,
    MemoryContext,
    MemoryStore,
    compact,
)
from hivemind.memory.bee_bread import MAX_REF_IDS
from waggle.clock import SystemClock
from waggle.ids import WardenId

__all__ = ["compact_command"]


def compact_command(
    bee: Annotated[str, typer.Argument(help="A Warden id: compacts its closed tasks' Bee Bread.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Run one real compaction (`ModelSlot.RIPENER`) over BEE's closed-task Bee Bread entries."""
    loaded = load_manifest_or_exit(manifest)
    db_path = resolved_db(loaded, db)
    chamber = open_chamber(db_path, chamber_identity(loaded, "system"))
    store = open_memory(db_path)
    registry = build_registry(loaded, os.environ, SystemClock())
    bound = registry.bound(ModelSlot.RIPENER)
    ctx = memory_context(loaded, store, "system")
    result = asyncio.run(
        closing_registry(registry, _compact_closed_tasks(chamber, store, ctx, bound, WardenId(bee)))
    )
    if result is None:
        typer.echo(f"nothing to compact: no closed task's Bee Bread entries for warden {bee}")
        return
    typer.echo(
        f"compacted: entry={result.entry.id}  tokens_before={result.tokens_before}  "
        f"tokens_after={result.tokens_after}  cost_usd={result.cost_usd}"
    )


async def _compact_closed_tasks(
    chamber: BroodChamber,
    store: MemoryStore,
    ctx: MemoryContext,
    bound: BoundModel,
    warden: WardenId,
) -> CompactionResult | None:
    """Gather `warden`'s closed tasks' Bee Bread entries and run one `compact` call over them."""
    tasks = await chamber.list(TaskFilter())
    closed_ids = [
        task.id for task in tasks if task.status in TERMINAL_STATUSES and task.warden_id == warden
    ]
    bee_bread = BeeBread(store)
    entries = []
    for task_id in closed_ids:
        for entry in await bee_bread.by_task(task_id, WIDEST):
            if entry.kind is not BeeBreadEntryKind.SUMMARY:
                entries.append(entry)
    if not entries:
        return None
    entries = entries[:MAX_REF_IDS]
    pins = await store.list_pins(WIDEST)
    request = CompactionRequest(sources=tuple(entries), pins=pins, clearance=WIDEST, task_id=None)
    deps = CompactionDeps(bound=bound, gate=DirectCallGate(), ctx=ctx)
    return await compact(request, deps)
