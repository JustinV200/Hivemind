"""Check every configured provider answers and every slot's model is on its server.

Two checks over `[llm]`. Reachability probes each `[llm.providers.*]` row through the same rows
`hive llm providers` prints, so the doctor and that command can never disagree; only providers the
manifest configures are probed, and offline mode's refusal of a non-loopback one happens before
any probe leaves the machine. Presence asks each provider that can list its models (an
OpenAI-compatible server's `/models`) for the ids it serves, once per provider, and checks every
`[llm.slots]` row's model is among them. A hosted provider that lists none, or an in-process one,
is SKIPPED: its model is checked at the first call instead.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.doctor`. Called by
    `hivemind.cli.doctor.command`. Calls into `hivemind.cli.llm` (the provider rows and the
    listing protocol), `hivemind.llm` (the registry and its errors) and `hivemind.manifest`.

Key invariants:
    - Each provider is listed at most once per run, however many slots bind it.
    - A provider error becomes a `Check`; nothing here raises an `LLMError`.

See Also:
    - hivemind.cli.llm.rows for the probe this reuses.
"""

from __future__ import annotations

from hivemind.cli.doctor.report import Check, Outcome
from hivemind.cli.llm import ModelListing, ProviderRow, provider_rows
from hivemind.llm import LLMError, ProviderRegistry
from hivemind.llm.providers.ollama import ModelServerManager
from hivemind.manifest import HiveManifest

SHOWN_MODELS = 5  # How many served ids a "not served" line names before it says how many more.

# What each health reading's first word means for the Hive: anything else is a provider the
# chat door cannot probe (an embedding-only or transcription-only kind), reported as skipped.
_HEALTH_OUTCOMES = {
    "healthy": Outcome.PASSED,
    "degraded": Outcome.WARNED,
    "down": Outcome.FAILED,
    "refused": Outcome.FAILED,
}

__all__ = ["check_providers", "check_slot_models"]


async def check_providers(manifest: HiveManifest, registry: ProviderRegistry) -> tuple[Check, ...]:
    """Probe every configured provider once; one check each."""
    try:
        # External awaits: one health probe per provider, each under its own client timeout.
        rows = await provider_rows(manifest, registry, _not_ollama)
    except LLMError as exc:
        return (Check(name="providers", outcome=Outcome.FAILED, detail=str(exc)),)
    return tuple(_provider_check(row) for row in rows)


async def check_slot_models(
    manifest: HiveManifest, registry: ProviderRegistry
) -> tuple[Check, ...]:
    """Check each `[llm.slots]` row's model is served by its provider; one check each."""
    served: dict[str, frozenset[str] | str] = {}
    checks = []
    # Every binding, fallbacks included: a fallback that is not loaded fails exactly when needed.
    for key, binding in manifest.llm.slots.items():
        if binding.provider not in served:
            served[binding.provider] = await _served_models(registry, binding.provider)
        checks.append(_slot_check(key, binding.provider, binding.model, served[binding.provider]))
    return tuple(checks)


def _provider_check(row: ProviderRow) -> Check:
    """Turn one `hive llm providers` row into a check, its health deciding the outcome."""
    outcome = _HEALTH_OUTCOMES.get(row.health.split(":", 1)[0], Outcome.SKIPPED)
    # A hosted provider with no key reads as down with "no credentials"; a local one needs none.
    detail = f"{row.kind} at {row.base_url}: {row.health}"
    return Check(name=f"provider {row.name}", outcome=outcome, detail=detail)


async def _served_models(registry: ProviderRegistry, name: str) -> frozenset[str] | str:
    """Return the ids `name` serves, or why they cannot be listed from here."""
    try:
        provider = registry.provider(name)
    except LLMError as exc:
        # An embedding- or transcription-only kind has no chat door, and offline refuses a remote.
        return f"not checked: {exc}"
    if not isinstance(provider, ModelListing):
        return "not checked: the provider lists no models; the first call checks it"
    try:
        # External await: one GET /models on the server, under the provider's own timeout.
        return frozenset(await provider.list_models())
    except LLMError as exc:
        return f"not checked: {name} could not list its models ({exc})"


def _slot_check(key: str, provider: str, model: str, served: frozenset[str] | str) -> Check:
    """Check one binding's model against what its provider serves."""
    name = f"model {key}"
    if isinstance(served, str):
        return Check(name=name, outcome=Outcome.SKIPPED, detail=f"{provider}/{model}: {served}")
    if model in served:
        return Check(name=name, outcome=Outcome.PASSED, detail=f"{provider} serves {model}")
    shown = ", ".join(sorted(served)[:SHOWN_MODELS]) or "nothing"
    more = len(served) - SHOWN_MODELS
    listing = shown + (f" and {more} more" if more > 0 else "")
    detail = f"{provider} does not serve {model}; it serves {listing}"
    return Check(name=name, outcome=Outcome.FAILED, detail=detail)


async def _not_ollama(_name: str, _base_url: str, _timeout_s: float) -> ModelServerManager | None:
    """Skip `hive llm providers`' Ollama detection: the doctor asks only whether it answers."""
    return None
