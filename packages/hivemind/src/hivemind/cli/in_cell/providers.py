"""Build the in-Cell Warden's own ProviderRegistry: model access from inside a Virtual Cell.

`hivemind.wardens.deps.WardenDeps.bound`/`.rebind` need a live `hivemind.llm.registry.
ProviderRegistry` to resolve `ModelSlot.WARDEN` (awake episodes) and every sub-bee's own
`ModelSlot.WORKER` (`hivemind.wardens.spawn.spawn.spawn_sub_bee`'s `deps.rebind(binding_key)`).
The Hive Stand's own composition root (`hivemind.cli.compose.deps.build_warden_deps`) builds one
from a loaded Hive Manifest's `[llm.providers]`/`[llm.slots]`; a Virtual Cell has no manifest of
its own (`hivemind.manifest.env`'s own module docstring). Roadmap step 8.x closed the wire-shape
gap this module used to flag as a TODO: `hivemind.cli.compose.virtual_cells` now hands a
provisioned Cell its own copy of that table (`hivemind.hive.backends.bootstrap.QueenEndpoint.
providers`/`.slots`), rewritten so every base URL is reachable from inside the Cell, and
`hivemind.cli.in_cell.config.build_runtime_config` parses it back into the same
`ProviderConfig`/`SlotBinding` shapes the Hive Stand's own registry is built from
(`InCellRuntimeConfig.providers`/`.slots`).

`build_in_cell_provider_registry` picks between that real table and today's placeholder: with a
non-empty `config.providers`, it builds a real `ProviderRegistry` exactly the way `hivemind.cli.
compose.deps.build_provider_registry` does; with none (the manifest carried no `[llm.providers]`
at all, or an older Queen that predates this wiring), it falls back to one `FakeLLMProvider` bound
to `ModelSlot.WARDEN` and `ModelSlot.WORKER`, so the in-Cell Warden always constructs and its
autopilot table (which never awaits a model, codingrules section 4/8.8) keeps the Hive alive with
no model reachable at all -- exactly this module's own pre-8.x behaviour, byte for byte, so every
existing test and the fake-backend e2e (which never sets `HIVEMIND_PROVIDERS`) keep working
unchanged.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cli.in_cell.config` (InCellRuntimeConfig), `hivemind.forage.slots` (ModelSlot),
    `hivemind.forage.map` (SlotBinding), `hivemind.llm` (ProviderRegistry, RegistryDeps,
    ProviderConfig, default_factories) and waggle only.

Key invariants:
    - `config.providers` empty is the one and only branch condition (never a `kind` check,
      `scripts/check_no_kind_branches.py`): whichever table the composition root actually sent
      decides fake-vs-real, exactly the way `HIVEMIND_PROVIDERS`'s own absence already means
      "no table" one layer down (`hivemind.hive.backends.bootstrap.CellBootstrap.environment()`'s
      own Key invariants).
    - The fallback fake provider is always named `DEFAULT_IN_CELL_PROVIDER_NAME` and always
      `kind="fake"`: nothing on that path ever opens a real network connection (codingrules
      section 15 "least privilege is code, not policy").
    - The real path's `RegistryDeps.environ` is `config.environ`, this process's own full
      environment (`hivemind.cli.in_cell.config.InCellRuntimeConfig.environ`'s own docstring):
      `hivemind.llm.registry.ProviderRegistry._resolve_api_key` is what actually reads a key out
      of it, by the exact `HIVEMIND_<NAME>_API_KEY`-shaped variable name each provider names.

See Also:
    - hivemind.cli.in_cell.config for InCellRuntimeConfig/build_runtime_config, this module's own
      input.
    - hivemind.cli.in_cell.deps for build_in_cell_warden_deps, this module's one caller.
    - hivemind.hive.backends.bootstrap for QueenEndpoint/CellBootstrap.environment(), where this
      table starts its trip from the composition root.
    - hivemind.llm.registry for ProviderRegistry, ProviderConfig and default_factories.
    - hivemind.llm.fake for FakeLLMProvider, what the fallback path's one provider resolves to.
"""

from __future__ import annotations

from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.registry import (
    ProviderConfig,
    ProviderRegistry,
    RegistryDeps,
    default_factories,
    runs_locally,
)
from waggle.clock import Clock

DEFAULT_IN_CELL_PROVIDER_NAME = "fake"  # Never a real vendor or server name (codingrules 8.6).
_FAKE_MODEL_ID = "in-cell-placeholder"  # Named, not a magic string repeated at each binding.

__all__ = [
    "DEFAULT_IN_CELL_PROVIDER_NAME",
    "build_in_cell_provider_registry",
    "local_provider_names",
]


def build_in_cell_provider_registry(clock: Clock, config: InCellRuntimeConfig) -> ProviderRegistry:
    """Build the in-Cell Warden's own ProviderRegistry: real when `config` has a table, else fake.

    Args:
        clock: Passed to every provider this registry later constructs.
        config: This process's own validated runtime config (`hivemind.cli.in_cell.config.
            build_runtime_config`); `.providers`/`.slots`/`.llm_offline`/`.environ` are read.

    Returns:
        A ProviderRegistry that resolves `ModelSlot.WARDEN` and every sub-bee's own
        `ModelSlot.WORKER` -- against `config.providers`/`.slots` when non-empty (module
        docstring: the real path), or a scriptable `FakeLLMProvider` otherwise.
    """
    if not config.providers:
        return _build_fake_registry(clock)
    deps = RegistryDeps(
        factories=default_factories(), environ=config.environ, clock=clock, map=None
    )
    return ProviderRegistry(config.providers, config.slots, config.llm_offline, deps)


def local_provider_names(config: InCellRuntimeConfig) -> frozenset[str]:
    """Name the providers this Cell's own registry serves locally: in process, or on loopback.

    Roadmap step 10.3a: a Night Veil binding is local only when every provider its chain reaches
    is one of these. A gateway-host base URL is the Hive Stand's machine, never this Cell's.

    Args:
        config: This process's own validated runtime config; `.providers` is read.

    Returns:
        The fallback fake's name when no table was sent (the same branch the registry takes),
        otherwise every provider `hivemind.llm.registry.runs_locally` accepts.
    """
    if not config.providers:
        return frozenset({DEFAULT_IN_CELL_PROVIDER_NAME})  # The in-process fake, and only it.
    return frozenset(name for name, cfg in config.providers.items() if runs_locally(cfg))


def _build_fake_registry(clock: Clock) -> ProviderRegistry:
    """Build the pre-8.x placeholder registry: one scriptable fake for WARDEN and WORKER.

    Byte-for-byte this module's own pre-roadmap-8.x behaviour (module docstring): every existing
    caller that never sets `HIVEMIND_PROVIDERS` -- every unit test and the fake-backend e2e -- must
    see the identical `FakeLLMProvider` this always built.
    """
    providers = {
        DEFAULT_IN_CELL_PROVIDER_NAME: ProviderConfig(kind="fake", base_url="", default_model=None)
    }
    bindings = tuple(
        SlotBinding(
            key=slot.manifest_key,
            provider=DEFAULT_IN_CELL_PROVIDER_NAME,
            model=_FAKE_MODEL_ID,
            fallback=None,
            effort=Effort.MEDIUM,
        )
        for slot in (ModelSlot.WARDEN, ModelSlot.WORKER)
    )
    deps = RegistryDeps(factories=default_factories(), environ={}, clock=clock, map=None)
    return ProviderRegistry(providers, bindings, offline=False, deps=deps)
