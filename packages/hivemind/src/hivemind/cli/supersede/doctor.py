"""Provide `hive doctor --supersedure NODE`: the Supersedure candidate test, every check listed.

Roadmap 13.2a (codingrules 8.16: "`hive doctor --supersedure <node>` checks all of it before
anything starts"). It acts as the Hive Stand's console over the running `hive serve`'s loopback
listener and reads `GET /v1/hive/supersede/{node}/preflight`, which runs the same test `hive
supersede` runs first. Each check prints as PASSED, FAILED or UNVERIFIED with one line of why; the
command exits 0 only when nothing failed. `hive doctor` itself (roadmap 14.2,
`hivemind.cli.doctor`) runs the environment and manifest checks and hands `--supersedure NODE`
here.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.supersede`. Called by
    `hivemind.cli.doctor.command` for `hive doctor --supersedure`. Calls into
    `hivemind.cli.entrance.console`, `hivemind.cli.landing` and the route's view only.

Key invariants:
    - Read-only: nothing on the Hive changes.

See Also:
    - hivemind.queen.supersedure.preflight for what each check means.
"""

from __future__ import annotations

import typer

from hivemind.cli.entrance.console import Stand, run_console
from hivemind.cli.landing import SignedIn
from hivemind.entrance.routes.isolation import PreflightView

GROUP = "doctor"

__all__ = ["supersedure_doctor"]


def supersedure_doctor(ctx: typer.Context, node: str, as_json: bool) -> None:
    """Print whether NODE can become the Hive Stand; exit 1 when any check failed.

    Args:
        ctx: The command's context, carrying the console's `--manifest` and `--password-stdin`.
        node: The candidate Swarm node.
        as_json: Print the route's view as JSON instead of one line per check.
    """

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
