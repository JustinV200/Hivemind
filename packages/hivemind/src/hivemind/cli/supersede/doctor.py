"""Provide `hive doctor --supersedure NODE`: the Supersedure candidate test, every check listed.

Roadmap 13.2a (codingrules 8.16: "`hive doctor --supersedure <node>` checks all of it before
anything starts"). It acts as the Hive Stand's console over the running `hive serve`'s loopback
listener and reads `GET /v1/hive/supersede/{node}/preflight`, which runs the same test `hive
supersede` runs first. Each check prints as PASSED, FAILED or UNVERIFIED with one line of why; the
command exits 0 only when nothing failed. The rest of `hive doctor` (the environment and manifest
checks) is roadmap 14.2's and is not built yet, so the command asks for `--supersedure`.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.supersede`. Registered as `hive doctor` by
    `hivemind.cli.app`. Calls into `hivemind.cli.entrance.console`, `hivemind.cli.landing`,
    `hivemind.cli.stores` (the JSON flag) and the route's view only.

Key invariants:
    - Read-only: nothing on the Hive changes.

See Also:
    - hivemind.queen.supersedure.preflight for what each check means.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli.entrance.console import Stand, run_console
from hivemind.cli.landing import SignedIn
from hivemind.cli.stores import JsonOption
from hivemind.entrance.routes.isolation import PreflightView

GROUP = "doctor"

SupersedureOption = Annotated[
    str | None,
    typer.Option("--supersedure", help="Run the Supersedure candidate test for this node."),
]

__all__ = ["doctor_command"]


def doctor_command(
    ctx: typer.Context, supersedure: SupersedureOption = None, as_json: JsonOption = False
) -> None:
    """Check the Hive; today, whether NODE can become its Hive Stand (`--supersedure NODE`)."""
    if supersedure is None:
        typer.echo("hive doctor: only --supersedure NODE is built yet (roadmap 14.2).", err=True)
        raise typer.Exit(code=2)
    node = supersedure

    async def check(board: SignedIn, _stand: Stand) -> PreflightView:
        """Read the candidate test on loopback."""
        path = f"/v1/hive/supersede/{node}/preflight"
        return await board.call("GET", path, None, PreflightView)

    view = run_console(ctx, "--supersedure", check, group=GROUP)
    if as_json:
        typer.echo(view.model_dump_json(indent=2))
    else:
        for item in view.checks:
            typer.echo(f"  {item.outcome:<10} {item.name:<16} {item.detail}")
        typer.echo(f"{node}: {'may' if view.passed else 'may not'} become the Hive Stand.")
    if not view.passed:
        raise typer.Exit(code=1)
