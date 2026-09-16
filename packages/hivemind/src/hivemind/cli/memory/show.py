"""Provide `hive memory show <bee>`: print a bee's hot state as `hivemind.memory.assemble` packs it.

Reconstructs a `hivemind.memory.hot_state.HotStateSources` view over the stores a CLI process can
reach (the Brood Chamber for tasks and questions, the memory store for pins/notes/Cell Wax, the
trail for Alarms and the latest Handoff) and calls the real `assemble`, so what prints is exactly
what an awake episode's own packing would produce for that principal -- not a hand-rolled summary.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.memory.app`. Calls into
    `hivemind.memory`, `hivemind.brood_chamber`, `hivemind.pheromone`, `hivemind.forage`
    (ModelSlot) and `hivemind.cli.stores`/`.context` only.

Key invariants:
    - `active_tasks`/`pending_questions` are Hive-wide, not filtered to one bee: the Brood Chamber
      carries no bee-to-task link this phase (`hivemind.cli.readback.wardens`'s own flagged
      limitation, same root cause), so "open items visible to that principal" is read as "every
      open item this clearance may see" rather than a per-bee filter this store cannot do.

See Also:
    - .claude/roadmap.md step 4.11 for this command's own deliverable, verbatim.
    - hivemind.memory.hot_state for HotStateSources and assemble, this module's one core call.
    - hivemind.cli.memory.context for the shared identity/db helpers this module builds on.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from hivemind.brood_chamber import TERMINAL_STATUSES, BroodChamber, TaskFilter
from hivemind.cell import HoneyClearance
from hivemind.cli.memory.context import WIDEST, chamber_identity, resolved_db
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    load_manifest_or_exit,
    open_chamber,
    open_memory,
    open_trail,
)
from hivemind.forage import ModelSlot
from hivemind.memory import (
    AlarmSummary,
    AssembleRequest,
    CellWaxSummary,
    DecisionSummary,
    EstimateCounter,
    Handoff,
    HotStateSources,
    MemoryStore,
    Note,
    Pin,
    Principal,
    Prompt,
    QuestionSummary,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
    WaxState,
    assemble,
)
from hivemind.pheromone import PheromoneEvent, PheromoneTrail, TrailQuery
from waggle.ids import AlarmId, CellId, EventId, TaskId

__all__ = ["show_command"]

# A CLI display command is not a real awake episode, so it has no bound model's context window to
# size a budget from; generous fixed constants keep `show` from ever dropping hot state a real
# episode's own (much larger, provider-sized) budget would have kept.
_MAX_INPUT_TOKENS = 100_000
_OUTPUT_RESERVE = 1_000
_ROLE = "bee"
# A generous but bounded trail-read window; matches hivemind.cli.capping's own MAX_QUERY_LIMIT use.
_TRAIL_LIMIT = 5_000


class _CliHotStateSources:
    """A `HotStateSources` built over the stores a detached CLI process can reach.

    Not a live Queen's or Warden's own view (neither exists in this process): `active_tasks` and
    `pending_questions` are Hive-wide (module docstring's own "Key invariants"), `open_alarms` is
    reconstructed from the trail the same way `hivemind.cli.readback.inbox` reconstructs escalated
    ones, `wax` reads only Cells named in `cells`, and `handoff` is the latest `memory.checkpoint`
    this `bee` itself wrote.
    """

    def __init__(
        self, chamber: BroodChamber, store: MemoryStore, trail: PheromoneTrail, bee: str
    ) -> None:
        """Build the view `show` assembles from."""
        self._chamber = chamber
        self._store = store
        self._trail = trail
        self._bee = bee

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        """Every non-terminal task, Hive-wide (module docstring)."""
        tasks = await self._chamber.list(TaskFilter())
        return tuple(
            TaskSummary(
                id=task.id,
                title=task.spec.title,
                status=task.status.value,
                objective=task.spec.objective[:500],
                updated_at=task.updated_at,
                clearance=task.spec.clearance,
            )
            for task in tasks
            if task.status not in TERMINAL_STATUSES
        )

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        """Every Alarm with an `alarm.raised` and no later `alarm.resolved`, Hive-wide."""
        events = await self._trail.query(TrailQuery(family="alarm", limit=_TRAIL_LIMIT))
        open_by_id: dict[str, PheromoneEvent] = {}
        for event in events:
            if event.kind == "alarm.raised":
                open_by_id[event.subject_id] = event
            elif event.kind == "alarm.resolved":
                open_by_id.pop(event.subject_id, None)
        return tuple(_alarm_summary(alarm_id, event) for alarm_id, event in open_by_id.items())

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        """Every pending question, Hive-wide."""
        questions = await self._chamber.pending_questions()
        return tuple(
            QuestionSummary(
                id=question.id,
                task_id=question.task_id,
                text=question.text[:500],
                options=question.options,
                clearance=question.clearance,
                asked_at=question.asked_at,
            )
            for question in questions
        )

    async def wax(self, cells: frozenset[CellId]) -> tuple[CellWaxSummary, ...]:
        """WRITTEN Cell Wax for `cells`, matching `HotStateSources.wax`'s own empty-set contract."""
        summaries: list[CellWaxSummary] = []
        for cell_id in cells:
            written = await self._store.list_wax(cell_id, frozenset({WaxState.WRITTEN}), WIDEST)
            summaries.extend(
                CellWaxSummary(
                    id=note.id,
                    cell_id=note.cell_id,
                    severity=note.severity.value,
                    text=note.text[:500],
                    clearance=note.clearance,
                    written_at=note.decided_at or note.proposed_at,
                )
                for note in written
            )
        return tuple(summaries)

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        """No episode records exist to reconstruct off-process for `show` (module docstring)."""
        del limit  # Unused: see docstring; always empty.
        return ()

    async def pins(self) -> tuple[Pin, ...]:
        """Every pin, within the widest allowance; `assemble` re-filters by the real principal."""
        return await self._store.list_pins(WIDEST)

    async def notes(self) -> tuple[Note, ...]:
        """Every note, within the widest allowance."""
        return await self._store.list_notes(None, WIDEST, 500)

    async def handoff(self) -> Handoff | None:
        """The latest `memory.checkpoint` this bee itself recorded, or None if it never has."""
        events = await self._trail.query(
            TrailQuery(family="memory", kind="memory.checkpoint", limit=_TRAIL_LIMIT)
        )
        mine = [event for event in events if event.actor == self._bee]
        if not mine:
            return None
        latest = max(mine, key=lambda event: event.at)
        handoff, _clearance = await self._store.get_handoff(EventId(latest.id))
        return handoff


def _alarm_summary(alarm_id: str, event: PheromoneEvent) -> AlarmSummary:
    """Build one AlarmSummary from a reconstructed `alarm.raised` event's own payload."""
    task_id = event.payload.get("task_id")
    attempts = event.payload.get("attempts")
    return AlarmSummary(
        id=AlarmId(alarm_id),
        kind=str(event.payload.get("kind", "")),
        severity=str(event.payload.get("severity", "")),
        detail=str(event.payload.get("detail", ""))[:500],
        attempts=int(str(attempts)) if attempts else 0,
        task_id=TaskId(str(task_id)) if task_id else None,
        # The trail never carries an Alarm's clearance (codingrules 12: ids/enums/counts only);
        # C1 is the safe operational default until a real clearance-bearing store exists for Alarms.
        clearance=HoneyClearance.C1,
        raised_at=event.at,
    )


def show_command(
    bee: Annotated[str, typer.Argument(help="The bee id (or role) whose hot state to pack.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    clearance: Annotated[
        HoneyClearance, typer.Option("--clearance", help="The reader's own clearance ceiling.")
    ] = HoneyClearance.C1,
) -> None:
    """Print BEE's hot state as `assemble` would pack it, plus its latest Handoff if any."""
    loaded = load_manifest_or_exit(manifest)
    db_path = resolved_db(loaded, db)
    chamber = open_chamber(db_path, chamber_identity(loaded, bee))
    store = open_memory(db_path)
    trail = open_trail(db_path)
    prompt = asyncio.run(_assemble_for(chamber, store, trail, bee, clearance))
    typer.echo(f"principal: {bee}  clearance: {clearance.value}  tokens: {prompt.token_count}")
    for label, text in prompt.sections.items():
        typer.echo(f"\n=== {label.value} ===")
        typer.echo(text)
    typer.echo(f"\nincluded: {len(prompt.included)}  dropped: {len(prompt.dropped)}")


async def _assemble_for(
    chamber: BroodChamber,
    store: MemoryStore,
    trail: PheromoneTrail,
    bee: str,
    clearance: HoneyClearance,
) -> Prompt:
    """Pack `bee`'s hot state through the real `assemble`, over a CLI-built HotStateSources."""
    sources: HotStateSources = _CliHotStateSources(chamber, store, trail, bee)
    request = AssembleRequest(
        principal=Principal(id=bee, slot=ModelSlot.WORKER, clearance=clearance, role=_ROLE),
        event=TriggerEvent(
            kind="cli.memory_show", summary=f"hive memory show {bee}", clearance=HoneyClearance.C0
        ),
        budget=TokenBudget(max_input_tokens=_MAX_INPUT_TOKENS, output_reserve=_OUTPUT_RESERVE),
    )
    return await assemble(request, sources, EstimateCounter())
