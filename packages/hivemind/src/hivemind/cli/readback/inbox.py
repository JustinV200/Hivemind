"""Provide `hive inbox`: list pending questions and Alarms at the human, and answer a question.

`hive inbox --manifest hive.toml [--json]` (the group's own default action, invoked with no
subcommand) reads pending questions straight from the Brood Chamber (`chamber.pending_questions`)
and reconstructs Alarms still escalated to the human from the trail's own `alarm.escalated` events
with no later `alarm.resolved` for the same `alarm_id` -- there is no live `hivemind.queen.
human_inbox.HumanInbox` to read in a separate CLI process (v0 has no live link into a running
`hive run`'s Queen; the Landing Board, roadmap phase 10, is where one arrives). `hive inbox answer
<question_id> "text" [--option N] --manifest hive.toml` records the answer through `chamber.answer`
with `AnswerSource.HUMAN`, exactly `hive inbox answer`'s roadmap job, and also leaves a
`hivemind.memory.Note` (`hivemind.queen.answer_note_author`) a running `hive run`'s own
`hivemind.queen.sync_answers_from_chamber` polls for and forwards on its next poll -- see that
function's own module docstring for why a Note, not the chamber's own `Answer`, carries the text
across the process boundary.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.brood_chamber`, `hivemind.cell`
    (HoneyClearance), `hivemind.cli.stores`, `hivemind.memory` (Note), `hivemind.pheromone`,
    `hivemind.queen` (`answer_note_author`) and waggle only.

Key invariants:
    - `open_trail`/`open_chamber`/`open_memory` (each running its own `asyncio.run` internally,
      `hivemind.cli.stores`'s own docstring) are always called from this module's synchronous
      command bodies, never from inside this module's own `asyncio.run(...)` calls: nesting one
      inside the other raises `RuntimeError: asyncio.run() cannot be called from a running event
      loop` (verified against this repository's own `hive capping queue`, which does nest them,
      while drafting this module -- flagged in this dispatch's own report as a pre-existing bug
      outside this dispatch's owned files).
    - `answer` never writes an `Answer` a Warden could resume from without also writing the Note
      `sync_answers_from_chamber` needs: both writes happen in the same command body, so a crash
      between them is the only way one exists without the other.

See Also:
    - .claude/roadmap.md step 3.21 for this command's own roadmap bullet.
    - hivemind.queen.questions for `sync_answers_from_chamber` and `answer_note_author`, the other
      half of the cross-process handoff this module's `answer` command starts.
    - hivemind.brood_chamber.chamber.questions for `ask`/`answer`, the state machine `answer`
      drives.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import (
    Answer,
    AnswerSource,
    BroodChamber,
    ChamberIdentity,
    Question,
    Task,
)
from hivemind.cell import HoneyClearance
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    ManifestOption,
    load_manifest_or_exit,
    open_chamber,
    open_memory,
    open_trail,
)
from hivemind.manifest import HiveManifest
from hivemind.memory import MemoryContext, MemoryIdentity, Note, add_note
from hivemind.pheromone import PheromoneTrail, TrailQuery
from hivemind.queen import answer_note_author
from waggle.clock import SystemClock
from waggle.ids import MessageId, new_event_id

app = typer.Typer(
    name="inbox",
    help="List pending questions and Alarms at the human; answer a question.",
    invoke_without_command=True,
)

__all__ = ["app"]

# A human's answer is always C2 by convention (codingrules 6.1: any human-supplied detail is C2)
# unless the question itself was asked at a lower ceiling, in which case the answer never exceeds
# it (a Warden never receives an answer more sensitive than what it asked for).
_HUMAN_ANSWER_CLEARANCE = HoneyClearance.C2


class _QuestionRow(BaseModel):
    """One pending question: what `hive inbox` lists, and what `answer` resolves by id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="The Brood Chamber's own Question id; answer by this.")
    task_id: str = Field(description="The task waiting on this answer.")
    text: str = Field(description="The question itself.")
    options: tuple[str, ...] = Field(description="Closed choices offered, if any.")
    asked_at: str = Field(description="When it was asked, ISO 8601.")


class _AlarmRow(BaseModel):
    """One Alarm still escalated to the human, reconstructed from the trail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alarm_id: str = Field(description="The Alarm's own id.")
    task_id: str = Field(description="What this event is about (the trail's own subject_id).")
    escalated_at: str = Field(description="When it was escalated, ISO 8601.")


@app.callback(invoke_without_command=True)
def list_command(
    ctx: typer.Context, manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False
) -> None:
    """List every pending question and every Alarm still escalated to the human."""
    if ctx.invoked_subcommand is not None:
        return  # `hive inbox answer ...` was named; let that subcommand run instead.
    loaded = load_manifest_or_exit(manifest)
    db = loaded.resolve_path(loaded.hive.db)
    # Both run their own asyncio.run internally (module docstring): called here, synchronously,
    # before this command's own asyncio.run(_read_inbox(...)) below ever starts.
    chamber = open_chamber(db, _identity(loaded))
    trail = open_trail(db)
    questions, alarms = asyncio.run(_read_inbox(chamber, trail))
    if as_json:
        payload = {
            "questions": [row.model_dump(mode="json") for row in questions],
            "alarms": [row.model_dump(mode="json") for row in alarms],
        }
        typer.echo(json.dumps(payload, indent=2))
        return
    _print_tables(questions, alarms)


@app.command("answer")
def answer_command(
    question_id: Annotated[str, typer.Argument(help="A pending question's own id.")],
    text: Annotated[str, typer.Argument(help="The answer text.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    option: Annotated[
        int | None,
        typer.Option("--option", help="Pick one of the question's own offered options by index."),
    ] = None,
) -> None:
    """Record TEXT as the human's answer to QUESTION_ID, and leave it for a running `hive run`."""
    loaded = load_manifest_or_exit(manifest)
    db = loaded.resolve_path(loaded.hive.db)
    chamber = open_chamber(db, _identity(loaded))
    memory_ctx = MemoryContext(
        store=open_memory(db), identity=_memory_identity(loaded), clock=SystemClock()
    )
    task = asyncio.run(_answer(chamber, memory_ctx, MessageId(question_id), text, option))
    typer.echo(f"recorded: task {task.id} is now {task.status.value}")


async def _read_inbox(
    chamber: BroodChamber, trail: PheromoneTrail
) -> tuple[tuple[_QuestionRow, ...], tuple[_AlarmRow, ...]]:
    """Read pending questions from the chamber and reconstruct escalated Alarms from the trail."""
    questions = await chamber.pending_questions()
    question_rows = tuple(_question_row(question) for question in questions)
    alarm_rows = await _escalated_alarms(trail)
    return question_rows, alarm_rows


async def _escalated_alarms(trail: PheromoneTrail) -> tuple[_AlarmRow, ...]:
    """Return every `alarm.escalated` event with no later `alarm.resolved` for the same alarm.

    No code in this Hive records either kind yet (`hivemind.queen.ticks.alarms` records
    `queen.decided` for its own `ESCALATE_TO_HUMAN` instead, per that module's own docstring), so
    this always returns empty today; it is written against the trail shape codingrules section 12
    calls for so a later dispatch that starts recording these kinds needs no change here.
    """
    events = await trail.query(TrailQuery(family="alarm"))
    escalated: dict[str, datetime] = {}
    for event in events:
        if event.kind == "alarm.escalated":
            escalated[event.subject_id] = event.at
        elif event.kind == "alarm.resolved":
            escalated.pop(event.subject_id, None)
    return tuple(
        _AlarmRow(alarm_id=alarm_id, task_id=alarm_id, escalated_at=at.isoformat())
        for alarm_id, at in escalated.items()
    )


async def _answer(
    chamber: BroodChamber, ctx: MemoryContext, question_id: MessageId, text: str, option: int | None
) -> Task:
    """Record `text` as a HUMAN answer, then leave the Note `sync_answers_from_chamber` reads."""
    chosen_option = await _resolve_option(chamber, question_id, option)
    answer = Answer(
        text=text,
        chosen_option=chosen_option,
        source=AnswerSource.HUMAN,
        clearance=_HUMAN_ANSWER_CLEARANCE,
        answered_at=ctx.clock.now(),
    )
    task = await chamber.answer(question_id, answer)
    # add_note builds and records the accompanying memory.note MemoryEvent itself, atomically
    # with the Note (hivemind.memory.notes's own module docstring); this is the one write this
    # command makes that a running hive run's sync_answers_from_chamber later reads back.
    note = Note(
        id=new_event_id(ctx.clock),
        author=answer_note_author(question_id),
        text=text,
        clearance=_HUMAN_ANSWER_CLEARANCE,
        written_at=ctx.clock.now(),
    )
    await add_note(note, ctx)
    return task


async def _resolve_option(
    chamber: BroodChamber, question_id: MessageId, option: int | None
) -> int | None:
    """Validate `option` against the question's own options while it is still ASKED.

    `Answer.chosen_option` (`hivemind.brood_chamber.questions.Answer`) is the INDEX into the
    Question's own `options`, not the option text -- `Question`'s own validator re-checks the same
    bound once `chamber.answer` runs, so this is belt-and-braces, not the only guard; it exists to
    turn an out-of-range `--option` into a `typer.BadParameter` instead of a pydantic
    `ValidationError` raised deep inside `chamber.answer`. Only ASKED questions are readable at all
    (`hivemind.brood_chamber`'s own `pending_questions`); this runs before `chamber.answer` moves
    it to ANSWERED, the one moment its `options` are ever reachable again outside the process that
    first asked it.
    """
    if option is None:
        return None
    pending = await chamber.pending_questions()
    question = next((q for q in pending if q.id == question_id), None)
    if question is None or option < 0 or option >= len(question.options):
        raise typer.BadParameter(f"--option {option} does not name one of this question's options.")
    return option


def _identity(manifest: HiveManifest) -> ChamberIdentity:
    """Build the ChamberIdentity every chamber read/write in this module is stamped with."""
    return ChamberIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="human")


def _memory_identity(manifest: HiveManifest) -> MemoryIdentity:
    """Build the MemoryIdentity `answer`'s own Note write is stamped with."""
    return MemoryIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="human")


def _question_row(question: Question) -> _QuestionRow:
    """Build one `hive inbox` question row from a Brood Chamber Question."""
    return _QuestionRow(
        id=question.id,
        task_id=question.task_id,
        text=question.text,
        options=question.options,
        asked_at=question.asked_at.isoformat(),
    )


def _print_tables(questions: tuple[_QuestionRow, ...], alarms: tuple[_AlarmRow, ...]) -> None:
    """Print two small tables: pending questions, then Alarms escalated to the human."""
    typer.echo("QUESTIONS")
    typer.echo(f"{'ID':<30}  {'TASK':<30}  {'TEXT':<40}  OPTIONS")
    for row in questions:
        typer.echo(f"{row.id:<30}  {row.task_id:<30}  {row.text:<40}  {', '.join(row.options)}")
    typer.echo("")
    typer.echo("ALARMS")
    typer.echo(f"{'ALARM ID':<30}  ESCALATED AT")
    for alarm_row in alarms:
        typer.echo(f"{alarm_row.alarm_id:<30}  {alarm_row.escalated_at}")
