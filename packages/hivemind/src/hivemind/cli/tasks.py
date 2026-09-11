"""Provide `hive tasks`: submit a task graph and read tasks back from the Brood Chamber.

Three commands, each a thin typer layer over `hivemind.brood_chamber.BroodChamber`
(`hivemind.cli.stores.open_chamber`): `submit` reads a `TaskGraphDraft`
(`hivemind.brood_chamber.task`, the JSON graph file this command's `FILE` argument names) and calls
`BroodChamber.submit`; `list` and `show` read tasks back. No command decides anything the chamber
or the store does not already decide; this file only reads options, calls one chamber method, and
formats the result (codingrules section 2's CLI row).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.brood_chamber` and
    `hivemind.cli.stores` only.

Key invariants:
    - `submit` prints exactly one line per task, `key<TAB>id<TAB>status`, in the same order as the
      graph file's own tasks, so a script (`scripts/brood_demo.py`) can parse the minted ids
      without guessing which line is which task.
    - A malformed graph file (bad JSON, a bad id, a cycle, a duplicate key) exits 2 with pydantic's
      own message; an unknown task id to `show` exits 1 with one line on stderr.

See Also:
    - hivemind.brood_chamber.task for TaskGraphDraft, the file `submit` reads.
    - hivemind.cli.stores for open_chamber, resolve_db and the shared option annotations.
    - scripts/brood_demo.py for the phase 2 exit-criteria driver built on this command group.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from hivemind.brood_chamber import (
    ChamberIdentity,
    TaskFilter,
    TaskGraphDraft,
    TaskNotFoundError,
    TaskStatus,
)
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    open_chamber,
    resolve_db,
)
from waggle.clock import SystemClock
from waggle.ids import HiveId, IdKind, NodeId, TaskId, new_hive_id, new_node_id
from waggle.messages.base import check_id

app = typer.Typer(name="tasks", help="Submit and inspect tasks in the Brood Chamber.")

__all__ = ["app"]


def _validate_hive_id(value: str) -> str:
    """Typer callback: validate --hive-id through waggle's own id check."""
    try:
        return check_id(value, IdKind.HIVE)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _validate_node_id(value: str) -> str:
    """Typer callback: validate --node-id through waggle's own id check."""
    try:
        return check_id(value, IdKind.NODE)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


HiveIdOption = Annotated[
    str,
    typer.Option(
        "--hive-id", help="The Hive this submission belongs to.", callback=_validate_hive_id
    ),
]
NodeIdOption = Annotated[
    str,
    typer.Option(
        "--node-id",
        help="This node's id, stamped on every event this submission writes.",
        callback=_validate_node_id,
    ),
]


def _read_identity() -> ChamberIdentity:
    """Build a throwaway identity for a read-only command; never used to write an event."""
    clock = SystemClock()
    return ChamberIdentity(hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system")


@app.command("submit")
def submit_command(
    file: Annotated[Path, typer.Argument(help="A JSON TaskGraphDraft file.")],
    hive_id: HiveIdOption,
    node_id: NodeIdOption,
    actor: Annotated[str, typer.Option("--actor", help="Who is submitting.")] = "human",
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Submit a task graph JSON file to the Brood Chamber; print each minted id."""
    try:
        # SAFETY: top of a CLI command (codingrules section 10): a bad file, bad JSON, a bad id,
        # a duplicate key or a dependency cycle all become one clean stderr line here instead of
        # a traceback, whatever kind of exception the filesystem or pydantic chooses to raise.
        graph = TaskGraphDraft.model_validate_json(file.read_text(encoding="utf-8"))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    identity = ChamberIdentity(hive_id=HiveId(hive_id), node_id=NodeId(node_id), actor=actor)
    # The only store command with no `--db`: it already carries the four parameters codingrules
    # 5.1 allows plus the file itself, and unlike the read commands it writes a task graph into a
    # Hive -- which is the thing a manifest defines, so naming a loose database file to write into
    # is not a gap worth spending the last parameter on.
    chamber = open_chamber(resolve_db(manifest, None), identity)
    tasks = asyncio.run(chamber.submit(graph))

    # One line per task, in graph order, so a driver can parse the minted ids without guessing
    # which line belongs to which draft.
    for draft, task in zip(graph.tasks, tasks, strict=True):
        typer.echo(f"{draft.key}\t{task.id}\t{task.status.value}")


@app.command("list")
def list_command(
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    status: Annotated[
        TaskStatus | None, typer.Option("--status", help="Only tasks in this status.")
    ] = None,
    goal: Annotated[str | None, typer.Option("--goal", help="Only tasks under this goal id.")] = (
        None
    ),
) -> None:
    """List tasks as a fixed-width table: id, status, attempt, title."""
    chamber = open_chamber(resolve_db(manifest, db), _read_identity())
    query = TaskFilter(status=status, goal_id=TaskId(goal) if goal is not None else None)
    tasks = asyncio.run(chamber.list(query))

    typer.echo(f"{'ID':<30}  {'STATUS':<10}  {'ATTEMPT':>7}  TITLE")
    for task in tasks:
        typer.echo(f"{task.id:<30}  {task.status.value:<10}  {task.attempt:>7}  {task.spec.title}")


@app.command("show")
def show_command(
    task_id: Annotated[str, typer.Argument(help="The task id to show.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Print one task as indented JSON."""
    chamber = open_chamber(resolve_db(manifest, db), _read_identity())
    try:
        task = asyncio.run(chamber.get(TaskId(task_id)))
    except TaskNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(task.model_dump_json(indent=2))
