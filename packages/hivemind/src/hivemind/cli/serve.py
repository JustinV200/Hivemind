"""Provide `hive serve`: run the Queen, her Warden and the Hive Entrance until interrupted.

`hive serve --manifest hive.toml` is the thin typer layer over `hivemind.cli.compose.entrance`: it
loads the manifest, builds the Hive with a `waggle.clock.SystemClock` (codingrules section 11:
`SystemClock` only at the command edge), and runs `serve_hive` until SIGINT or SIGTERM, which it
owns (the listeners install no signal handler of their own). It prints where the loopback listener
answers (and the remote one, when exposed), and on the way out closes every socket and listener
before stopping the Queen. A mode this host cannot honour exits 2 with the rule it broke; a
loopback listener that cannot bind exits 1. Like `hive run`, it is a bare command registered with
`app.command("serve")`, not a group.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.cli.compose.entrance`,
    `hivemind.cli.stores`, `hivemind.entrance.expose` (its refusal) and waggle only.

Key invariants:
    - No exception escapes as a traceback: the command body's own broad catch (codingrules
      section 10's third allowed site) turns any other failure into one stderr line and exit 1.
    - The exit code is 0 after an interrupt, 2 for a refused exposure, 1 for anything else.

See Also:
    - hivemind.cli.compose.entrance for what is built and run.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the process shape.
"""

from __future__ import annotations

import asyncio
import os
import signal

import typer

from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.cli.stores import DEFAULT_MANIFEST, ManifestOption, load_manifest_or_exit
from hivemind.entrance.expose import ExposureRefusedError
from hivemind.entrance.runtime import HiveEntrance
from waggle.clock import SystemClock

__all__ = ["serve_command"]

_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)  # What ends `hive serve` cleanly.


def serve_command(manifest: ManifestOption = DEFAULT_MANIFEST) -> None:
    """Run the Hive from MANIFEST with its Hive Entrance until interrupted."""
    loaded = load_manifest_or_exit(manifest)
    # build_served_hive must run outside any event loop, like build_hive (hivemind.cli.run).
    served = build_served_hive(loaded, environ=os.environ, clock=SystemClock())
    try:
        asyncio.run(_serve(served))
    except ExposureRefusedError as exc:
        typer.echo(f"hive serve refused to expose the Entrance: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        # SAFETY: the command body's own broad catch (codingrules section 10's third allowed
        # site): a bind failure or any other error ends in one clean stderr line and exit 1.
        typer.echo(f"hive serve failed: {_describe(exc)}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=0)


async def _serve(served: ServedHive) -> None:
    """Serve until SIGINT or SIGTERM; leaving the block stops the Entrance, then the Queen."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for number in _STOP_SIGNALS:
        try:
            loop.add_signal_handler(number, stop.set)
        except NotImplementedError:
            # Windows' loop has no signal handlers: Ctrl+C arrives as KeyboardInterrupt instead.
            break
    async with serve_hive(served) as entrance:
        _announce(entrance)
        await stop.wait()
    typer.echo("hive serve stopped.")


def _announce(entrance: HiveEntrance) -> None:
    """Print where the Entrance answers."""
    listeners = entrance.listeners
    typer.echo(f"Hive Entrance on http://localhost:{listeners.loopback_port} (loopback)")
    # The remote socket is bound before start returns, while uvicorn may still be finishing its
    # own startup: a bound port is the listener, whereas `remote_listening` read here once told an
    # operator an Entrance that was only starting had been reduced (a real `hive serve` run).
    if listeners.remote_port is not None:
        typer.echo(f"Remote listener on port {listeners.remote_port}")
    elif listeners.exposed:
        typer.echo("Remote listener not started: the Entrance is reduced to loopback.")


def _describe(exc: BaseException) -> str:
    """Flatten ``exc`` (a task group's error group included) into one line an operator can use."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_describe(inner) for inner in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"
