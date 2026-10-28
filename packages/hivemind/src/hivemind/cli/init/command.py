"""Provide `hive init`: write a starter Hive Manifest, then set the operator's password.

Roadmap 14.2 (and 14.1's Getting Started). `hive init --provider anthropic|ollama|openai-compatible
[DIRECTORY]` writes `DIRECTORY/hive.toml` from `hivemind.manifest.starter`: fresh Hive ids, every
model slot bound, the Hive Stand enabled, so the next `hive run` needs no Docker. It never replaces
an existing `hive.toml` unless `--force` is given, because that file holds the Hive's identity.
Then, unless `--no-password`, it sets the one operator's password the way `hive entrance operator
password` does the first time (ADR-0041 names `hive init` as that function's other caller): a
hidden prompt typed twice, or one line of stdin with `--password-stdin`.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.init`. Registered as `hive init` by
    `hivemind.cli.app`. Calls into `hivemind.manifest.starter`, `hivemind.entrance.enrol`
    (bootstrap_operator), `hivemind.cli.entrance.console` (the Entrance tables, the refusal line),
    `hivemind.cli.landing` (the password) and `hivemind.cli.stores` (loading what was written).

Key invariants:
    - An existing `hive.toml` is never overwritten without `--force`, and a refused or invalid
      starter writes nothing at all.
    - A password already set is never changed here; that is `hive entrance operator password`.
    - No password is taken from an argument or the environment, or printed.

See Also:
    - hivemind.manifest.starter for what the file holds.
    - hivemind.cli.entrance.operators for setting or changing the password later.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from hivemind.cli.entrance.console import entrance_tables, refusing
from hivemind.cli.landing import (
    PASSWORD_STDIN,
    CarriedOption,
    carried_command,
    carried_flag,
    carried_text,
    read_new_password,
)
from hivemind.cli.stores import DEFAULT_MANIFEST, load_manifest_or_exit
from hivemind.entrance.enrol import bootstrap_operator
from hivemind.manifest import HiveManifest, ManifestError
from hivemind.manifest.starter import StarterChoice, StarterProvider, render_starter
from waggle.clock import SystemClock

USAGE_EXIT_CODE = 2  # A refused choice or an existing file, as the other commands use for usage.
VERB = "init"  # The refusal line's command name.

# The starter's values and the password switches ride on the context (codingrules 5.1's parameter
# limit), the way the console commands carry `--manifest` and `--password-stdin`.
NAME = CarriedOption(key="hivemind.cli.init.name", decls=("--name",), help="The Hive's label.")
MODEL = CarriedOption(
    key="hivemind.cli.init.model",
    decls=("--model",),
    help="The model id a server path binds every slot to (required for a server).",
)
EMBED_MODEL = CarriedOption(
    key="hivemind.cli.init.embed_model",
    decls=("--embed-model",),
    help="The embedder slot's model (default: --model).",
)
BASE_URL = CarriedOption(
    key="hivemind.cli.init.base_url",
    decls=("--base-url",),
    help="The server's /v1 root (Ollama's own by default; required for openai-compatible).",
)
NO_PASSWORD = CarriedOption(
    key="hivemind.cli.init.no_password",
    decls=("--no-password",),
    help="Skip setting the operator password (hive entrance operator password sets it later).",
    is_flag=True,
)
InitCommand = carried_command(NAME, MODEL, EMBED_MODEL, BASE_URL, NO_PASSWORD, PASSWORD_STDIN)

__all__ = ["InitCommand", "init_command"]


def init_command(
    ctx: typer.Context,
    provider: Annotated[
        StarterProvider, typer.Option("--provider", help="The model provider path to start on.")
    ],
    directory: Annotated[
        Path, typer.Argument(help="Where to write hive.toml (created if missing).")
    ] = Path(),
    force: Annotated[bool, typer.Option("--force", help="Replace an existing hive.toml.")] = False,
) -> None:
    """Write a starter hive.toml with the Hive Stand enabled, then set the operator password."""
    path = directory / DEFAULT_MANIFEST.name
    # The manifest holds the Hive's identity: replacing it is never a side effect of a re-run.
    if path.exists() and not force:
        _exit_usage(f"{path} already exists; pass --force to replace it.")
    choice = StarterChoice(
        provider,
        name=carried_text(ctx, NAME) or "",
        model=carried_text(ctx, MODEL),
        embed_model=carried_text(ctx, EMBED_MODEL),
        base_url=carried_text(ctx, BASE_URL),
    )
    try:
        text = render_starter(choice, SystemClock())
    except ManifestError as exc:
        _exit_usage(str(exc))
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    typer.echo(f"Wrote {path} ({provider} provider, the Hive Stand enabled).")
    if not carried_flag(ctx, NO_PASSWORD):
        manifest = load_manifest_or_exit(path)
        from_stdin = carried_flag(ctx, PASSWORD_STDIN)
        typer.echo(refusing(VERB, lambda: asyncio.run(_set_password(manifest, from_stdin))))
    typer.echo(f"Next: hive doctor --manifest {path}")


async def _set_password(manifest: HiveManifest, from_stdin: bool) -> str:
    """Set the operator password on a Hive that has none yet; leave a set one alone."""
    async with entrance_tables(manifest, SystemClock()) as deps:
        # Latency: one local read. A --force re-run over a Hive that already has an operator
        # keeps its password: changing one needs the current password, which init never asks.
        if await deps.store.get_operator() is not None:
            return (
                "The operator password is already set; hive entrance operator password changes it."
            )
        new = read_new_password(from_stdin)
        console = await bootstrap_operator(deps, new.get_secret_value())
    return f"Operator password set; the Hive Stand console is {console.id}."


def _exit_usage(message: str) -> NoReturn:
    """Print `message` on stderr and exit 2 (nothing was written)."""
    typer.echo(f"hive init refused: {message}", err=True)
    raise typer.Exit(code=USAGE_EXIT_CODE)
