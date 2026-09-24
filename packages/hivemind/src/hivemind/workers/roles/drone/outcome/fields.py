"""Derive a Handoff's guidance fields from one attempt's own recorded tool calls.

Defect 1 this dispatch fixes: `hivemind.workers.roles.drone.outcome.build.build_handoff_outcome`
used to build `do_not_redo`, `tried_and_failed`, `constraints`, `open_threads` and `pinned_facts`
as empty tuples always, regardless of what the attempt actually did. Every function here answers
one of those fields from data the attempt already has -- `hivemind.workers.roles.drone.outcome.
records.ToolCallRecord`s, the one call refused for a checkpoint, this attempt's own pins, and the
assignment's own acceptance criteria -- never from a second read of a conversation (codingrules
section 8.8).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone.outcome`. Called by
    `hivemind.workers.roles.drone.outcome.build.build_handoff_outcome`, this module's one caller.
    Calls into `hivemind.cell`, `hivemind.llm`, `hivemind.memory`,
    `hivemind.workers.context`, `hivemind.workers.roles.drone.outcome.records` and waggle only.

Key invariants:
    - Every derived list is capped to `MAX_DERIVED_LIST_ITEMS`, well under Handoff's own
      `MAX_LIST_ITEMS` (64, `hivemind.memory.handoff`): a bounded tool loop (`DRONE_MAX_ROUNDS`,
      12) never comes close, but the cap holds regardless of a future round-cap change.
    - `next_step_lines` only ever marks a FILE_EXISTS/FILE_ABSENT criterion done, by re-reading
      the Cell right now; a COMMAND_EXITS_ZERO or TEST_PASSES criterion always stays open, since
      re-running a command here would itself be a second, unproposed side effect.
    - `pinned_fact_lines` copies `Pin.text` verbatim, never summarised (codingrules section 8.9).

See Also:
    - .claude/codingrules.md section 8.9 for the Handoff shape these functions populate.
    - hivemind.memory.handoff for Handoff's own per-field caps, mirrored here.
    - hivemind.workers.roles.drone.outcome.records for ToolCallRecord, classify_error's own result,
      and target_for.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from hivemind.cell import HoneyClearance, PathNotAllowedError
from hivemind.llm import ToolCall
from hivemind.memory import Decision
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.drone.outcome.records import (
    ALLOWLIST_FAILED_MARK,
    CAPABILITY_DENIAL_PREFIXES,
    SIZE_CAP_FAILED_MARK,
    ToolCallRecord,
    is_lasting,
    target_for,
)
from waggle.messages import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign

MAX_LIST_ITEM_CHARS = 500  # Mirrors hivemind.memory.handoff.MAX_LIST_ITEM_CHARS.
MAX_DERIVED_LIST_ITEMS = 20  # See module docstring's own "Key invariants".
MAX_DECISION_TEXT_CHARS = 500  # Mirrors MAX_DECISION_WHAT_CHARS / MAX_DECISION_WHY_CHARS.
MAX_DECISIONS_KEPT = 20  # Well under Handoff's own MAX_DECISIONS (64).

# What a resuming bee reads for each side-effecting tool's own do_not_redo line.
_REDO_VERBS: dict[str, str] = {
    "write_file": "writing to",
    "run_command": "running",
    "http_request": "requesting",
    "keep": "keeping",
}
# The fallback next_steps line for the rare case every named acceptance criterion already looks
# satisfied by the time this attempt checkpoints -- an edge case, not the common path: the whole
# point of a Handoff is that something is still left to do.
_NEXT_STEPS_ALL_DONE = (
    "Resume the objective from this Handoff; every named acceptance criterion already appears "
    "satisfied -- verify before reporting done.",
)

__all__ = [
    "MAX_DECISIONS_KEPT",
    "MAX_DECISION_TEXT_CHARS",
    "MAX_DERIVED_LIST_ITEMS",
    "MAX_LIST_ITEM_CHARS",
    "constraint_lines",
    "decisions_from_records",
    "do_not_redo_lines",
    "next_step_lines",
    "open_thread_lines",
    "pinned_fact_lines",
    "summarise_progress",
    "tried_and_failed_lines",
]


def summarise_progress(records: Sequence[ToolCallRecord]) -> str:
    """Render every tool call made so far as one line of progress, oldest first."""
    if not records:
        return "No tool calls completed before handing off."
    return "; ".join(
        f"called {record.call.name}" + (" (failed)" if record.is_error else "")
        for record in records
    )


def decisions_from_records(records: Sequence[ToolCallRecord]) -> tuple[Decision, ...]:
    """Turn the most recent tool calls and their results into Handoff Decisions."""
    kept = records[-MAX_DECISIONS_KEPT:]
    return tuple(
        Decision(
            what=f"Called {record.call.name}"[:MAX_DECISION_TEXT_CHARS],
            why=(
                record.result_text[:MAX_DECISION_TEXT_CHARS]
                if record.result_text
                else "no result text"
            ),
        )
        for record in kept
    )


def do_not_redo_lines(records: Sequence[ToolCallRecord]) -> tuple[str, ...]:
    """Name every side-effecting call that actually landed, so a resuming bee never repeats it."""
    lines: list[str] = []
    for record in records:
        if record.is_error or not is_lasting(record.call):
            continue
        # An irreversible GUI action has no verb of its own: it is named for what it was.
        verb = _REDO_VERBS.get(record.call.name, f"the irreversible {record.call.name} on")
        lines.append(
            f"Do not redo {verb} {target_for(record.call)}; it already succeeded before this "
            "checkpoint."
        )
    return _capped(lines)


def tried_and_failed_lines(records: Sequence[ToolCallRecord]) -> tuple[str, ...]:
    """Name every call that came back an error, with its result's own first line."""
    lines: list[str] = []
    for record in records:
        if not record.is_error:
            continue
        first_line = record.result_text.splitlines()[0] if record.result_text else "no result text"
        line = f"{record.call.name} {target_for(record.call)} failed: {first_line}"
        lines.append(line[:MAX_LIST_ITEM_CHARS])
    return _capped(lines)


def constraint_lines(records: Sequence[ToolCallRecord]) -> tuple[str, ...]:
    """Name every limit a failed call's result surfaced: an allowlist, size cap or capability."""
    lines: list[str] = []
    for record in records:
        if not record.is_error:
            continue
        constraint = _constraint_for(record)
        if constraint is not None:
            lines.append(constraint)
    return _capped(lines)


def open_thread_lines(pending_call: ToolCall | None) -> tuple[str, ...]:
    """Name the call this attempt was about to make when it checkpointed, if any."""
    if pending_call is None:
        return ()
    line = (
        f"In progress: was about to call {pending_call.name} ({target_for(pending_call)}) when "
        "this attempt checkpointed."
    )
    return (line[:MAX_LIST_ITEM_CHARS],)


async def pinned_fact_lines(ctx: WorkerContext, clearance: HoneyClearance) -> tuple[str, ...]:
    """Return this attempt's own pins, verbatim (codingrules 8.9: pins are copied, not summarised).

    Args:
        ctx: This attempt's WorkerContext; supplies the memory store pins are read from.
        clearance: This attempt's own clearance ceiling; only pins within it are ever visible.

    Returns:
        Up to `MAX_DERIVED_LIST_ITEMS` pins' own text, oldest first, verbatim.
    """
    pins = await ctx.memory.list_pins(clearance)
    return tuple(pin.text for pin in pins[:MAX_DERIVED_LIST_ITEMS])


async def next_step_lines(ctx: WorkerContext, assignment: TaskAssign) -> tuple[str, ...]:
    """Name every acceptance criterion not yet verifiably satisfied.

    Args:
        ctx: This attempt's WorkerContext; supplies the session a FILE_EXISTS/FILE_ABSENT
            criterion is checked against.
        assignment: The task this attempt was working; `acceptance` is never empty on the wire.

    Returns:
        One line per still-open criterion, capped to `MAX_DERIVED_LIST_ITEMS`; the fallback
        `_NEXT_STEPS_ALL_DONE` line when every criterion this attempt could check already holds.
    """
    steps: list[str] = []
    for criterion in assignment.acceptance:
        if await _criterion_satisfied(ctx, criterion):
            continue
        steps.append(_describe_remaining(criterion)[:MAX_LIST_ITEM_CHARS])
    if not steps:
        return _NEXT_STEPS_ALL_DONE
    return tuple(steps[:MAX_DERIVED_LIST_ITEMS])


def _capped(lines: list[str]) -> tuple[str, ...]:
    """Keep the most recent `MAX_DERIVED_LIST_ITEMS` entries (module docstring)."""
    return tuple(lines[-MAX_DERIVED_LIST_ITEMS:])


def _constraint_for(record: ToolCallRecord) -> str | None:
    """Return one constraint line for `record`, or None when its failure named no limit at all."""
    content = record.result_text
    target = target_for(record.call)
    if ALLOWLIST_FAILED_MARK in content:
        return f"{record.call.name} {target}: blocked by the path/command allowlist."
    if SIZE_CAP_FAILED_MARK in content:
        return f"{record.call.name} {target}: blocked by the diff size cap."
    if content.startswith(CAPABILITY_DENIAL_PREFIXES):
        return f"{record.call.name} {target}: {content.splitlines()[0]}"
    return None


async def _criterion_satisfied(ctx: WorkerContext, criterion: Postcondition) -> bool:
    """Return whether `criterion` can be confirmed satisfied by re-reading the Cell right now."""
    if criterion.kind is PostconditionKind.FILE_EXISTS:
        return await _file_exists(ctx, criterion.subject)
    if criterion.kind is PostconditionKind.FILE_ABSENT:
        return not await _file_exists(ctx, criterion.subject)
    # A command/test criterion can only be confirmed by running it, which this checkpoint path
    # never does (module docstring): stays open until a resuming attempt actually runs it.
    return False


async def _file_exists(ctx: WorkerContext, path: str) -> bool:
    """Return whether `path` currently exists under this attempt's session."""
    try:
        await ctx.session.get_file(Path(path))
    except (FileNotFoundError, PathNotAllowedError):
        return False
    return True


def _describe_remaining(criterion: Postcondition) -> str:
    """Say one still-open Postcondition in the words a resuming bee acts on, never the raw enum."""
    argv = " ".join(criterion.argv)
    if criterion.kind is PostconditionKind.FILE_EXISTS:
        return f"Create a file at {criterion.subject}."
    if criterion.kind is PostconditionKind.FILE_ABSENT:
        return f"Remove the file at {criterion.subject}."
    if criterion.kind is PostconditionKind.COMMAND_EXITS_ZERO:
        return f"Make the command `{argv}` exit with code 0."
    if criterion.kind is PostconditionKind.TEST_PASSES:
        return f"Make the test run `{argv}` pass."
    expected = f" -> {criterion.expected}" if criterion.expected else ""
    return f"Satisfy {criterion.kind.value}: {criterion.subject}{expected}"
