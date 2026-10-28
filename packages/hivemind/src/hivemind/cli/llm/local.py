"""Define `hive llm models|pull|keep-alive`: manage the models on a local model server.

Three commands for the operator of a local model server (a program on this machine that runs
open-weight models): `models` lists what a provider's server has, `pull` downloads a model, and
`keep-alive` holds one in memory for a while. Only Ollama can pull or hold; whether a provider is
Ollama is decided by probing its native API (`OllamaManager.detect`, ADR-0044), never by its name,
so `models` falls back to the generic `/models` listing the chat adapter already has for vLLM,
llama.cpp and LM Studio, and `pull` and `keep-alive` say plainly that the server cannot do it.
The one seam to a server is a `ManagerOpener` carried in the typer context (`LocalCliDeps`), so
tests hand the commands a `FakeOllamaManager` and production gets `OllamaManager.open`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm`. Registered on the
    `hive llm` group by `hivemind.cli.llm.commands`. Calls into `hivemind.llm`,
    `hivemind.llm.providers.ollama` and `hivemind.cli.stores` only.

Key invariants:
    - A provider that `[llm] offline = true` refuses is refused before any probe leaves the
      machine: the registry builds the provider first, and only then is the server contacted.
    - A call failure is a typed `LLMError`: one line on stderr and exit 1; a provider that is
      unknown, has no `base_url` or is not an OpenAI-compatible server is a usage error: exit 2.
    - No model id, host or port is written here; all come from the manifest or the command line.

See Also:
    - hivemind.llm.providers.ollama for the manager these commands drive.
    - hivemind.cli.llm.commands for the group these register on.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn, Protocol, runtime_checkable

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.llm.rows import OPENAI_COMPAT_KIND
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    ManifestOption,
    build_registry,
    closing_registry,
    load_manifest_or_exit,
)
from hivemind.llm import LLMError, ProviderRegistry
from hivemind.llm.providers.ollama import (
    SUCCESS_STATUS,
    ManagerOpener,
    ModelServerManager,
    OllamaManager,
    OllamaModel,
    PullProgress,
)
from waggle.clock import SystemClock

PROGRESS_STEP = 0.1  # Print a layer's percentage each time it advances by this fraction.
BYTES_PER_GB = 1_000_000_000  # Ollama sizes are bytes; the table shows decimal gigabytes.
USAGE_EXIT_CODE = 2  # A bad provider name or an unsupported server, as the other commands use.
FAILURE_EXIT_CODE = 1  # A server that was reached but failed, as `hive llm test` uses.

ProviderOption = Annotated[
    str, typer.Option("--provider", help="The [llm.providers.<name>] key of the server.")
]

__all__ = ["LocalCliDeps", "ModelListing", "deps_from_context", "register_local_commands"]


@dataclass(frozen=True, slots=True)
class LocalCliDeps:
    """The seam to a model server: how a command opens a manager for a provider's `base_url`."""

    open_manager: ManagerOpener = OllamaManager.open


@runtime_checkable
class ModelListing(Protocol):
    """A provider that can list the model ids its server serves (the generic `/models` probe)."""

    async def list_models(self) -> tuple[str, ...]:
        """Return the served model ids."""
        ...


class ModelRow(BaseModel):
    """One `hive llm models` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="The model's id on the server.")
    size_bytes: int | None = Field(description="Bytes on disk; None when the server does not say.")
    parameters: str = Field(description="Parameter count as the server words it, or ''.")
    quantization: str = Field(description="Weight quantisation, or ''.")


class _CommandError(Exception):
    """A command cannot proceed; carries the message to print and the exit code to use."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Target:
    """The provider a command acts on: its name, base URL and request timeout."""

    name: str
    base_url: str
    timeout_s: float


def register_local_commands(app: typer.Typer) -> None:
    """Attach `models`, `pull` and `keep-alive` to the `hive llm` group."""
    app.command("models")(models_command)
    app.command("pull")(pull_command)
    app.command("keep-alive")(keep_alive_command)


def models_command(
    ctx: typer.Context,
    provider: ProviderOption,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    as_json: JsonOption = False,
) -> None:
    """List the models a provider's server has: Ollama's installed models, else its /models ids."""
    registry, target = _resolve(manifest, provider)
    rows = _run(registry, _list_rows(registry, target, deps_from_context(ctx)))
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_model_table(rows)


def pull_command(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="The model tag to download.")],
    provider: ProviderOption,
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Download MODEL onto an Ollama server, printing progress; other servers cannot pull."""
    registry, target = _resolve(manifest, provider)
    _run(registry, _pull(registry, target, deps_from_context(ctx), model))


def keep_alive_command(
    ctx: typer.Context,
    model: Annotated[str, typer.Argument(help="An installed model tag.")],
    provider: ProviderOption,
    seconds: Annotated[
        int,
        typer.Option(
            "--seconds", min=-1, help="Seconds to hold it in memory; 0 unloads, -1 holds forever."
        ),
    ],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Load MODEL on an Ollama server and keep it in memory for --seconds."""
    registry, target = _resolve(manifest, provider)
    _run(registry, _keep_alive(registry, target, deps_from_context(ctx), model, seconds))
    typer.echo(f"{model}: keep-alive set to {seconds}s")


def deps_from_context(ctx: typer.Context) -> LocalCliDeps:
    """Return the deps a caller placed on the typer context (tests do), else the production ones."""
    return ctx.obj if isinstance(ctx.obj, LocalCliDeps) else LocalCliDeps()


def _resolve(path: Path, provider: str) -> tuple[ProviderRegistry, _Target]:
    """Load the manifest (exit 2 if bad), build its registry and resolve `--provider`."""
    manifest = load_manifest_or_exit(path)
    spec = manifest.llm.providers.get(provider)
    if spec is None:
        _exit_with(f"unknown provider {provider!r}; see `hive llm providers`.", USAGE_EXIT_CODE)
    # A server is reached over its base_url; a hosted or in-process provider has none to manage.
    if spec.kind != OPENAI_COMPAT_KIND or not spec.base_url:
        _exit_with(f"provider {provider!r} is not a model server with a base_url.", USAGE_EXIT_CODE)
    registry = build_registry(manifest, os.environ, SystemClock())
    return registry, _Target(name=provider, base_url=spec.base_url, timeout_s=spec.timeout_s)


def _exit_with(message: str, code: int) -> NoReturn:
    """Print `message` on stderr and exit with `code`."""
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _run[ResultT](registry: ProviderRegistry, work: Awaitable[ResultT]) -> ResultT:
    """Run `work` to completion, closing the registry; map a failure to a message and an exit."""
    try:
        return asyncio.run(closing_registry(registry, work))
    except _CommandError as exc:
        _exit_with(str(exc), exc.code)
    except LLMError as exc:
        # Every failure at the provider boundary is a typed LLMError whose message names the
        # provider and the reason (codingrules 8.6); a traceback would add nothing.
        _exit_with(str(exc), FAILURE_EXIT_CODE)


async def _open_manager(
    registry: ProviderRegistry, target: _Target, deps: LocalCliDeps
) -> ModelServerManager | None:
    """Open the provider's manager if its server is manageable, else None."""
    # Building the provider first is the offline guard: a refused (non-loopback) provider raises
    # here, before the native probe could reach the network (codingrules 8.6).
    registry.provider(target.name)
    # External await: a local probe answers in milliseconds; failure to connect is just None.
    return await deps.open_manager(target.name, target.base_url, target.timeout_s)


async def _manager_or_refuse(
    registry: ProviderRegistry, target: _Target, deps: LocalCliDeps, action: str
) -> ModelServerManager:
    """Open the manager, or refuse: only an Ollama server can `action`."""
    manager = await _open_manager(registry, target, deps)
    if manager is None:
        raise _CommandError(
            f"provider {target.name!r} is not an Ollama server; only Ollama can {action}.",
            USAGE_EXIT_CODE,
        )
    return manager


async def _list_rows(
    registry: ProviderRegistry, target: _Target, deps: LocalCliDeps
) -> tuple[ModelRow, ...]:
    """List through the Ollama manager when detected, else through the generic /models probe."""
    manager = await _open_manager(registry, target, deps)
    if manager is not None:
        try:
            return tuple(_ollama_row(model) for model in await manager.list_models())
        finally:
            await manager.aclose()
    provider = registry.provider(target.name)
    if not isinstance(provider, ModelListing):
        raise _CommandError(f"provider {target.name!r} cannot list models.", USAGE_EXIT_CODE)
    ids = await provider.list_models()
    return tuple(ModelRow(name=i, size_bytes=None, parameters="", quantization="") for i in ids)


async def _pull(
    registry: ProviderRegistry, target: _Target, deps: LocalCliDeps, model: str
) -> None:
    """Pull `model`, printing progress, and fail unless the server ends with `success`."""
    manager = await _manager_or_refuse(registry, target, deps, "pull models")
    printer = _ProgressPrinter()
    last: PullProgress | None = None
    try:
        # External await: a download, streaming one progress line at a time until `success`.
        async for progress in manager.pull(model):
            line = printer.render(progress)
            if line is not None:
                typer.echo(line)
            last = progress
    finally:
        await manager.aclose()
    if last is None or last.status != SUCCESS_STATUS:
        raise _CommandError(f"pull of {model!r} ended without success.", FAILURE_EXIT_CODE)


async def _keep_alive(
    registry: ProviderRegistry, target: _Target, deps: LocalCliDeps, model: str, seconds: int
) -> None:
    """Ask the Ollama server to hold `model` in memory for `seconds`."""
    manager = await _manager_or_refuse(registry, target, deps, "hold models in memory")
    try:
        await manager.keep_alive(model, seconds)
    finally:
        await manager.aclose()


class _ProgressPrinter:
    """Turn a pull's many progress lines into a readable few: one per status, one per 10%."""

    def __init__(self) -> None:
        self._status = ""
        self._bucket = -1

    def render(self, progress: PullProgress) -> str | None:
        """Return the line to print for `progress`, or None when it adds nothing new."""
        fraction = progress.fraction
        # A status-only line (no byte counts) is printed once per change.
        bucket = -1 if fraction is None else int(fraction / PROGRESS_STEP)
        if progress.status == self._status and bucket == self._bucket:
            return None
        self._status, self._bucket = progress.status, bucket
        return progress.status if fraction is None else f"{progress.status}  {fraction:.0%}"


def _ollama_row(model: OllamaModel) -> ModelRow:
    """Map one Ollama model to a row."""
    return ModelRow(
        name=model.name,
        size_bytes=model.size,
        parameters=model.details.parameter_size,
        quantization=model.details.quantization_level,
    )


def _print_model_table(rows: tuple[ModelRow, ...]) -> None:
    """Print one fixed-width row per model."""
    typer.echo(f"{'NAME':<36}  {'SIZE':>9}  {'PARAMS':<8}  QUANT")
    for row in rows:
        size = "-" if row.size_bytes is None else f"{row.size_bytes / BYTES_PER_GB:.1f} GB"
        typer.echo(f"{row.name:<36}  {size:>9}  {row.parameters:<8}  {row.quantization}")
