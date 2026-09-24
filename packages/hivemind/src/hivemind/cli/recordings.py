"""Provide `hive recordings`: list, show and export the flight recorder's recordings.

The flight recorder (roadmap step 6.6, ADR-0032) keeps evidence of every GUI action taken while an
Exoskeleton (a Cell's optional display, input, audio and browser) was attached, in two tables of
the Hive's database. Three commands, each a thin typer layer over the durable store
(`hivemind.cli.stores.open_recordings`) and the recorder's playback: `list` prints recording
headers newest first, optionally one Cell's; `show` prints one recording's playback summary
(every action's steps, URLs, postconditions, terminal state and rollback, each frame as its size
and short digest); `export` writes the playback page (`<id>.html`, frames inline, no script) and
its JSON summary (`<id>.json`) into a directory. What a recording holds, how a page is drawn and
what a summary says all live in `hivemind.exoskeleton.recorder` (codingrules section 2's CLI row).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.exoskeleton.errors`,
    `hivemind.exoskeleton.recorder` and `hivemind.cli.stores` only.

Key invariants:
    - No frame's bytes ever reach stdout or stderr: `show` prints digests, and only the file
      `export` writes carries pixels (codingrules section 12: never log screenshots).
    - An unknown recording id exits 1 with one line on stderr, never a traceback.
    - `--json` prints the models' own JSON: `RecordingInfo` rows for `list`, a `RecordingSummary`
      for `show`, the same shape `export` writes.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - hivemind.exoskeleton.recorder.playback for render_html and summarize.
    - hivemind.cli.stores for open_recordings, resolve_db and the shared option annotations.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    JsonOption,
    ManifestOption,
    open_recordings,
    resolve_db,
)
from hivemind.exoskeleton.errors import RecordingNotFoundError
from hivemind.exoskeleton.recorder import (
    DEFAULT_LIST_LIMIT,
    DIGEST_CHARS,
    ActionSummary,
    RecordedAction,
    RecordingInfo,
    RecordingStore,
    RecordingSummary,
    SideSummary,
    render_html,
    summarize,
)

app = typer.Typer(name="recordings", help="List, show and export flight recordings.")

__all__ = ["app"]

# What `show` says for each value of a postcondition's has_held; a failure is shouted.
_HELD_TEXT: dict[bool | None, str] = {True: "held", False: "FAILED", None: "not checked"}

RecordingIdArgument = Annotated[
    str, typer.Argument(help="A recording id, as `hive recordings list` prints it.")
]


@app.command("list")
def list_command(
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    cell: Annotated[str | None, typer.Option("--cell", help="Only this Cell's recordings.")] = None,
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="The most recordings to print.")
    ] = DEFAULT_LIST_LIMIT,
    as_json: JsonOption = False,
) -> None:
    """List recordings newest first: id, Cell, task, clearance and when each started."""
    store = open_recordings(resolve_db(manifest, db))
    infos = asyncio.run(store.recordings(cell_id=cell, limit=limit))
    if as_json:
        typer.echo(json.dumps([info.model_dump(mode="json") for info in infos], indent=2))
        return
    typer.echo(f"{'RECORDING':<30}  {'CELL':<31}  {'TASK':<31}  {'CLEARANCE':<9}  STARTED")
    for info in infos:
        typer.echo(
            f"{info.recording_id:<30}  {info.cell_id:<31}  {info.task_id or '-':<31}  "
            f"{info.clearance:<9}  {info.started_at.isoformat()}"
        )


@app.command("show")
def show_command(
    recording_id: RecordingIdArgument,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    as_json: JsonOption = False,
) -> None:
    """Print one recording's actions: steps, URLs, postconditions, state; frames as digests."""
    summary = summarize(*_load(manifest, db, recording_id))
    if as_json:
        typer.echo(summary.model_dump_json(indent=2))
        return
    _print_summary(summary)


@app.command("export")
def export_command(
    recording_id: RecordingIdArgument,
    out: Annotated[
        Path, typer.Option("--out", help="Directory for <id>.html and <id>.json; made if missing.")
    ],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
) -> None:
    """Write one recording's playback page and its JSON summary into OUT."""
    info, actions = _load(manifest, db, recording_id)
    out.mkdir(parents=True, exist_ok=True)
    # Named after the stored id, which waggle's new_event_id minted: a plain token that is a safe
    # file name on every host, whatever the operator typed to find it.
    page = out / f"{info.recording_id}.html"
    summary = out / f"{info.recording_id}.json"
    page.write_text(render_html(info, actions), encoding="utf-8")
    summary.write_text(summarize(info, actions).model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"exported {len(actions)} actions to {page} and {summary}")


def _load(
    manifest: Path, db: Path | None, recording_id: str
) -> tuple[RecordingInfo, tuple[RecordedAction, ...]]:
    """Read one recording's header and actions, or exit 1 naming the id nothing holds."""
    store = open_recordings(resolve_db(manifest, db))
    try:
        return asyncio.run(_read(store, recording_id))
    except RecordingNotFoundError as exc:
        # The error's own message names the id; one line, no traceback (codingrules section 10).
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


async def _read(
    store: RecordingStore, recording_id: str
) -> tuple[RecordingInfo, tuple[RecordedAction, ...]]:
    """Return the recording's header and its actions, in order."""
    return await store.info(recording_id), await store.actions(recording_id)


def _print_summary(summary: RecordingSummary) -> None:
    """Print the header as aligned label/value lines, then every action."""
    info = summary.recording
    typer.echo(f"recording  {info.recording_id}")
    typer.echo(f"cell       {info.cell_id}")
    typer.echo(f"task       {info.task_id or '-'}")
    typer.echo(f"clearance  {info.clearance}")
    typer.echo(f"started    {info.started_at.isoformat()}")
    typer.echo(f"actions    {len(summary.actions)}")
    for action in summary.actions:
        _print_action(action)


def _print_action(action: ActionSummary) -> None:
    """Print one action: its outcome line, then steps, both sides and every postcondition."""
    typer.echo("")
    typer.echo(
        f"#{action.number}  {action.state}  tier={action.tier}  "
        f"rollback={action.rollback or '-'}  proposal={action.proposal_id}"
    )
    for step in action.steps:
        typer.echo(f"    step    {step}")
    typer.echo(f"    before  {_side_line(action.before)}")
    after = _side_line(action.after) if action.after is not None else "never applied"
    typer.echo(f"    after   {after}")
    for check in action.postconditions:
        typer.echo(
            f"    check   {check.kind} {check.subject}: {_HELD_TEXT[check.has_held]} "
            f"(expected {check.expected or '-'}; observed {check.observed or '-'})"
        )


def _side_line(side: SideSummary) -> str:
    """Describe one side in a line: the frame by size and short digest, then the URL."""
    frame = side.frame
    shown = (
        "no frame"
        if frame is None
        else f"{frame.width}x{frame.height} sha256:{frame.sha256[:DIGEST_CHARS]}"
    )
    return f"{shown}  url={side.url or '-'}"
