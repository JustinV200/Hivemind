"""Define `hive llm`'s commands: inspect configured providers and slots, and send a test call.

Three commands, each a thin typer layer over `hivemind.llm.registry.ProviderRegistry`
(`hivemind.cli.stores.build_registry`): `providers` lists every `[llm.providers.<name>]` row with
its declared shape and a live health probe; `slots` lists every `hivemind.forage.slots.ModelSlot`
resolved to its current binding, price and fallback chain; `test` resolves one slot and sends a
single small completion through it, the way `hivemind.llm.ladders.gate.DirectCallGate` would, to
prove a binding actually answers before a real goal depends on it -- except for `EMBEDDER`
(roadmap step 7.1), which has no completion to send: `test` embeds one short text through
`ProviderRegistry.embedder()` instead, the way `hivemind.llm.embedding.gate.DirectEmbedGate`
would, and prints the model, its vector dimension and the latency in place of usage and a reply.
No rule about what a provider or a slot binding *is* lives here; every one of those lives in
`hivemind.llm` and `hivemind.forage`
(codingrules section 2's CLI row: "commands call into subsystem APIs, never contain logic"). This
module is the CLI's own composition root for one loaded `HiveManifest`'s worth of `os.environ`
reads (codingrules section 13: environment variables are read in exactly one place, `manifest.env`,
but the raw mapping is still supplied at the very edge by whichever composition root needs it).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm`. Called by an operator's
    shell through the `hive` console script (`hivemind.cli.app`). Calls into `hivemind.llm`,
    `hivemind.forage`, `hivemind.manifest`, `hivemind.cli.stores` and `hivemind.cli.llm.rows`
    (the provider and slot rows) only.

Key invariants:
    - `providers`/`slots`/`test` never raise a raw `pydantic.ValidationError` or `ManifestError`:
      a bad `--manifest` path or a manifest that fails validation prints one line on stderr and
      exits 2, matching `hivemind.cli.tasks`' and `hivemind.cli.trail`'s own bad-input handling.
    - `providers` never lets `[llm] offline = true` refusing a provider's construction
      (`hivemind.llm.errors.OfflineViolationError`) bubble up as a command failure: that row's
      health column reads `"refused: offline"` instead.
    - `test` exits 1 with the raised `LLMError`'s own message on any call failure; it never prints
      a traceback for one (codingrules section 10: a CLI command is one of the three places a
      broad-looking catch is allowed, here narrowed to the typed `LLMError` tree).

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names" and the health-state
      vocabulary this command surfaces.
    - hivemind.cli.stores for build_registry, the composition function this module calls.
    - hivemind.llm.ladders.gate for DirectCallGate, the seam `test` mirrors (model-stamping) and
      reuses directly.
    - hivemind.llm.fake for FakeLLMProvider, the `kind = "fake"` adapter `test` runs against fully
      offline in this package's own tests.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Annotated

import typer

from hivemind.cli.llm.rows import ProviderRow, SlotRow, provider_rows, slot_row_for
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    ManifestOption,
    build_registry,
    closing_registry,
    load_manifest_or_exit,
)
from hivemind.forage import ModelSlot
from hivemind.llm import (
    DirectCallGate,
    DirectEmbedGate,
    EmbeddingRequest,
    LLMError,
    LLMRequest,
    Message,
    ProviderRegistry,
    Role,
    Usage,
)
from waggle.clock import SystemClock

app = typer.Typer(name="llm", help="Inspect the Hive's model providers and slots, or test one.")

__all__ = ["app"]

# A short, neutral prompt: enough for a provider to prove it answers, cheap enough to run against
# a hosted API without worrying about spend (codingrules section 8.6's model-id rule keeps this
# free of any real model name; it is just English text).
DEFAULT_TEST_PROMPT = "Reply with exactly one word: ready."
# A one-word reply needs a handful of tokens, but a thinking model spends its reasoning out of the
# same budget (32 was exhausted before the answer started on a local reasoning model at HIGH
# effort); 512 leaves room for that while keeping a hosted probe call cheap.
TEST_MAX_OUTPUT_TOKENS = 512


def _validate_slot(value: str) -> str:
    """Typer callback: validate SLOT through ModelSlot.from_manifest_key's own check.

    Defined ahead of the command that uses it as an Argument callback (rather than after, where
    codingrules 5.3 would otherwise place a private helper): a typer parameter default is
    evaluated when its `def` executes, so the name must already exist by then, exactly like
    `hivemind.cli.tasks._validate_hive_id` ahead of `HiveIdOption`.
    """
    try:
        ModelSlot.from_manifest_key(value)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return value


@dataclass(frozen=True, slots=True)
class _TestResult:
    """What `hive llm test` prints: elapsed time, normalised usage, and the reply's first line."""

    latency_s: float
    usage: Usage
    first_line: str


@dataclass(frozen=True, slots=True)
class _EmbedTestResult:
    """What `hive llm test embedder` prints: elapsed time, the model and its dimension."""

    latency_s: float
    model: str
    dimensions: int


@app.command("providers")
def providers_command(
    manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False
) -> None:
    """List every configured provider: kind, base URL, seats, API key presence and health."""
    loaded = load_manifest_or_exit(manifest)
    registry = build_registry(loaded, os.environ, SystemClock())
    rows = asyncio.run(closing_registry(registry, provider_rows(loaded, registry)))
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_provider_table(rows)


@app.command("slots")
def slots_command(manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False) -> None:
    """List every ModelSlot resolved to its current binding, price and fallback chain."""
    loaded = load_manifest_or_exit(manifest)
    registry = build_registry(loaded, os.environ, SystemClock())
    rows = tuple(slot_row_for(registry, slot) for slot in ModelSlot)
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_slot_table(rows)


@app.command("test")
def test_command(
    slot: Annotated[
        str,
        typer.Argument(help="A ModelSlot's manifest key, e.g. 'worker'.", callback=_validate_slot),
    ],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    prompt: Annotated[
        str, typer.Option("--prompt", help="Override the default one-word test prompt.")
    ] = DEFAULT_TEST_PROMPT,
) -> None:
    """Resolve SLOT and send one small completion (or embed) through it; print the result."""
    loaded = load_manifest_or_exit(manifest)
    registry = build_registry(loaded, os.environ, SystemClock())
    model_slot = ModelSlot.from_manifest_key(slot)
    try:
        if model_slot is ModelSlot.EMBEDDER:
            # EMBEDDER has no completion to run; embed one short text instead (roadmap 7.1).
            _print_embed_test_result(
                asyncio.run(closing_registry(registry, _run_embed_test_call(registry, prompt)))
            )
            return
        _print_test_result(
            asyncio.run(closing_registry(registry, _run_test_call(registry, model_slot, prompt)))
        )
    except LLMError as exc:
        # Every call failure the provider boundary can raise is a typed LLMError (codingrules
        # section 8.6); its own message already names the provider and the reason.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _print_test_result(result: _TestResult) -> None:
    """Print a completion test's latency, usage and reply, in `hive llm test`'s fixed format."""
    typer.echo(f"latency: {result.latency_s:.2f}s")
    typer.echo(
        f"usage: input={result.usage.input_tokens} output={result.usage.output_tokens} "
        f"cost_usd={result.usage.cost_usd}"
    )
    typer.echo(f"reply: {result.first_line}")


def _print_embed_test_result(result: _EmbedTestResult) -> None:
    """Print an embed test's model, dimension and latency, in `hive llm test embedder`'s format."""
    typer.echo(f"model: {result.model}")
    typer.echo(f"dimension: {result.dimensions}")
    typer.echo(f"latency: {result.latency_s:.2f}s")


def _print_provider_table(rows: tuple[ProviderRow, ...]) -> None:
    """Print one fixed-width table row per configured provider."""
    typer.echo(f"{'NAME':<16}  {'KIND':<14}  {'BASE URL':<34}  {'SEATS':>5}  {'KEY':<3}  HEALTH")
    for row in rows:
        key = "yes" if row.has_api_key else "no"
        typer.echo(
            f"{row.name:<16}  {row.kind:<14}  {row.base_url:<34}  {row.seats:>5}  {key:<3}  "
            f"{row.health}"
        )


def _print_slot_table(rows: tuple[SlotRow, ...]) -> None:
    """Print one fixed-width table row per resolved ModelSlot."""
    typer.echo(
        f"{'SLOT':<12}  {'BINDING':<14}  {'PROVIDER':<12}  {'MODEL':<20}  {'EFFORT':<7}  "
        f"{'WINDOW':>8}  {'PRICE':<20}  FALLBACK"
    )
    for row in rows:
        typer.echo(
            f"{row.slot:<12}  {row.binding:<14}  {row.provider:<12}  {row.model:<20}  "
            f"{row.effort:<7}  {_window(row.context_window):>8}  {row.price:<20}  "
            f"{row.fallback_chain}"
        )


def _window(context_window: int | None) -> str:
    """Format a slot row's context window, '-' for an embedding binding that has none."""
    return "-" if context_window is None else str(context_window)


async def _run_test_call(registry: ProviderRegistry, slot: ModelSlot, prompt: str) -> _TestResult:
    """Resolve `slot` and send one small completion through it, timed on the injected Clock."""
    bound = registry.bound(slot)
    request = LLMRequest(
        slot=slot,
        messages=(Message.text(Role.USER, prompt),),
        max_output_tokens=TEST_MAX_OUTPUT_TOKENS,
    )
    # SystemClock only at this very edge (codingrules section 11); .monotonic() is the same
    # elapsed-time primitive hivemind.llm.fanner.lane measures a real call's latency with.
    clock = SystemClock()
    start = clock.monotonic()
    # This await talks to whatever the resolved binding's provider is (a hosted API, a local
    # server, or FakeLLMProvider in this package's own tests); DirectCallGate stamps bound.model
    # onto the request exactly as every ladder call does, so a manual test matches a real one.
    response = await DirectCallGate().complete(bound, request)
    latency_s = clock.monotonic() - start
    first_line = response.text.splitlines()[0] if response.text else ""
    return _TestResult(latency_s=latency_s, usage=response.usage, first_line=first_line)


async def _run_embed_test_call(registry: ProviderRegistry, text: str) -> _EmbedTestResult:
    """Resolve the EMBEDDER slot and embed one short text through it, timed on a fresh Clock."""
    bound = registry.embedder()
    request = EmbeddingRequest(texts=(text,))
    clock = SystemClock()
    start = clock.monotonic()
    # This await talks to whatever the resolved binding's provider is (a hosted server, an
    # in-process model, or FakeEmbedding in this package's own tests); DirectEmbedGate walks a
    # same-model fallback on an outage, so a manual test matches what a real caller would see.
    response = await DirectEmbedGate().embed(bound, request)
    latency_s = clock.monotonic() - start
    return _EmbedTestResult(
        latency_s=latency_s, model=response.model, dimensions=response.dimensions
    )
