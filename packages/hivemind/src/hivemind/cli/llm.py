"""Provide `hive llm`: inspect configured providers and slots, and send one test call.

Three commands, each a thin typer layer over `hivemind.llm.registry.ProviderRegistry`
(`hivemind.cli.stores.build_registry`): `providers` lists every `[llm.providers.<name>]` row with
its declared shape and a live health probe; `slots` lists every `hivemind.forage.slots.ModelSlot`
resolved to its current binding, price and fallback chain; `test` resolves one slot and sends a
single small completion through it, the way `hivemind.llm.ladders.gate.DirectCallGate` would, to
prove a binding actually answers before a real goal depends on it. No rule about what a provider or
a slot binding *is* lives here; every one of those lives in `hivemind.llm` and `hivemind.forage`
(codingrules section 2's CLI row: "commands call into subsystem APIs, never contain logic"). This
module is the CLI's own composition root for one loaded `HiveManifest`'s worth of `os.environ`
reads (codingrules section 13: environment variables are read in exactly one place, `manifest.env`,
but the raw mapping is still supplied at the very edge by whichever composition root needs it).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.llm`, `hivemind.forage`,
    `hivemind.manifest` and `hivemind.cli.stores` only.

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
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    ManifestOption,
    build_registry,
    load_manifest_or_exit,
)
from hivemind.forage import ModelSlot
from hivemind.llm import (
    BoundModel,
    DirectCallGate,
    LLMError,
    LLMRequest,
    Message,
    OfflineViolationError,
    ProviderRegistry,
    Role,
    Usage,
)
from hivemind.manifest import HiveManifest, provider_api_key
from waggle.clock import SystemClock

app = typer.Typer(name="llm", help="Inspect the Hive's model providers and slots, or test one.")

__all__ = ["app"]

# A short, neutral prompt: enough for a provider to prove it answers, cheap enough to run against
# a hosted API without worrying about spend (codingrules section 8.6's model-id rule keeps this
# free of any real model name; it is just English text).
DEFAULT_TEST_PROMPT = "Reply with exactly one word: ready."
TEST_MAX_OUTPUT_TOKENS = 32  # A one-word reply never needs more; keeps the probe call small.


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


class _ProviderRow(BaseModel):
    """One `hive llm providers` row: a provider's shape, key presence and live health."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="The [llm.providers.<name>] key.")
    kind: str = Field(description="Which adapter speaks to this provider.")
    base_url: str = Field(description="The provider's base URL, or '(vendor default)' when hosted.")
    seats: int = Field(description="Concurrent requests this provider allows.")
    has_api_key: bool = Field(description="Whether an API key is set in the environment.")
    health: str = Field(description="The live health probe result, or 'refused: offline'.")


class _SlotRow(BaseModel):
    """One `hive llm slots` row: a resolved ModelSlot binding and its fallback chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: str = Field(description="The ModelSlot's manifest key.")
    binding: str = Field(description="The [llm.slots] key this binding actually resolved.")
    provider: str = Field(description="The provider name serving this binding.")
    model: str = Field(description="The provider's own model id.")
    effort: str = Field(description="How hard this binding asks the model to think.")
    context_window: int = Field(description="This binding's context window, in tokens.")
    price: str = Field(description="Per-million-token price, or '-' when the Forage map has none.")
    fallback_chain: str = Field(description="Every binding key in the chain, as 'a -> b -> c'.")


@dataclass(frozen=True, slots=True)
class _TestResult:
    """What `hive llm test` prints: elapsed time, normalised usage, and the reply's first line."""

    latency_s: float
    usage: Usage
    first_line: str


@app.command("providers")
def providers_command(
    manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False
) -> None:
    """List every configured provider: kind, base URL, seats, API key presence and health."""
    loaded = load_manifest_or_exit(manifest)
    registry = build_registry(loaded, os.environ, SystemClock())
    rows = asyncio.run(_provider_rows(loaded, registry))
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_provider_table(rows)


@app.command("slots")
def slots_command(manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False) -> None:
    """List every ModelSlot resolved to its current binding, price and fallback chain."""
    loaded = load_manifest_or_exit(manifest)
    registry = build_registry(loaded, os.environ, SystemClock())
    rows = tuple(_slot_row(registry.bound(slot)) for slot in ModelSlot)
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
    """Resolve SLOT and send one small completion through it; print latency, usage and the reply."""
    loaded = load_manifest_or_exit(manifest)
    registry = build_registry(loaded, os.environ, SystemClock())
    model_slot = ModelSlot.from_manifest_key(slot)
    try:
        result = asyncio.run(_run_test_call(registry, model_slot, prompt))
    except LLMError as exc:
        # Every call failure the provider boundary can raise is a typed LLMError (codingrules
        # section 8.6); its own message already names the provider and the reason.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"latency: {result.latency_s:.2f}s")
    typer.echo(
        f"usage: input={result.usage.input_tokens} output={result.usage.output_tokens} "
        f"cost_usd={result.usage.cost_usd}"
    )
    typer.echo(f"reply: {result.first_line}")


async def _provider_rows(
    manifest: HiveManifest, registry: ProviderRegistry
) -> tuple[_ProviderRow, ...]:
    """Build one row per configured provider, probing each one's live health in turn."""
    rows = []
    for name in registry.names():
        rows.append(await _one_provider_row(manifest, registry, name))
    return tuple(rows)


async def _one_provider_row(
    manifest: HiveManifest, registry: ProviderRegistry, name: str
) -> _ProviderRow:
    """Build one provider's row: its manifest shape, key presence and live health."""
    spec = manifest.llm.providers[name]
    has_key = provider_api_key(name, spec, os.environ) is not None
    return _ProviderRow(
        name=name,
        kind=spec.kind,
        base_url=spec.base_url or "(vendor default)",
        seats=spec.seats,
        has_api_key=has_key,
        health=await _probe_health(registry, name),
    )


async def _probe_health(registry: ProviderRegistry, name: str) -> str:
    """Construct and probe one provider, or report why offline mode refused it."""
    try:
        provider = registry.provider(name)
    except OfflineViolationError:
        # [llm] offline=true refuses a non-loopback provider at construction (codingrules 8.6);
        # show that refusal instead of letting it fail the whole command.
        return "refused: offline"
    reading = await provider.health()
    return f"{reading.state.value.lower()}: {reading.detail}"


def _print_provider_table(rows: tuple[_ProviderRow, ...]) -> None:
    """Print one fixed-width table row per configured provider."""
    typer.echo(f"{'NAME':<16}  {'KIND':<14}  {'BASE URL':<34}  {'SEATS':>5}  {'KEY':<3}  HEALTH")
    for row in rows:
        key = "yes" if row.has_api_key else "no"
        typer.echo(
            f"{row.name:<16}  {row.kind:<14}  {row.base_url:<34}  {row.seats:>5}  {key:<3}  "
            f"{row.health}"
        )


def _slot_row(bound: BoundModel) -> _SlotRow:
    """Build one `hive llm slots` row from a resolved BoundModel."""
    return _SlotRow(
        slot=bound.slot.manifest_key,
        binding=bound.binding,
        provider=bound.provider.name,
        model=bound.model,
        effort=bound.effort.value,
        context_window=bound.context_window,
        price=_format_price(bound),
        fallback_chain=_fallback_chain(bound),
    )


def _format_price(bound: BoundModel) -> str:
    """Format a binding's per-million-token price, or '-' when the Forage map has none."""
    if bound.cost_per_million_input_usd is None or bound.cost_per_million_output_usd is None:
        return "-"
    return f"${bound.cost_per_million_input_usd:.2f}/${bound.cost_per_million_output_usd:.2f} per M"


def _fallback_chain(bound: BoundModel) -> str:
    """Walk `bound.fallback` and join every link's binding key as 'a -> b -> c'."""
    keys = []
    current: BoundModel | None = bound
    while current is not None:
        keys.append(current.binding)
        current = current.fallback
    return " -> ".join(keys)


def _print_slot_table(rows: tuple[_SlotRow, ...]) -> None:
    """Print one fixed-width table row per resolved ModelSlot."""
    typer.echo(
        f"{'SLOT':<12}  {'BINDING':<14}  {'PROVIDER':<12}  {'MODEL':<20}  {'EFFORT':<7}  "
        f"{'WINDOW':>8}  {'PRICE':<20}  FALLBACK"
    )
    for row in rows:
        typer.echo(
            f"{row.slot:<12}  {row.binding:<14}  {row.provider:<12}  {row.model:<20}  "
            f"{row.effort:<7}  {row.context_window:>8}  {row.price:<20}  {row.fallback_chain}"
        )


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
