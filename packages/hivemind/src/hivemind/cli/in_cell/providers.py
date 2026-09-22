"""Build the in-Cell Warden's own ProviderRegistry: model access from inside a Virtual Cell.

`hivemind.wardens.deps.WardenDeps.bound`/`.rebind` need a live `hivemind.llm.registry.
ProviderRegistry` to resolve `ModelSlot.WARDEN` (awake episodes) and every sub-bee's own
`ModelSlot.WORKER` (`hivemind.wardens.spawn.spawn.spawn_sub_bee`'s `deps.rebind(binding_key)`).
The Hive Stand's own composition root (`hivemind.cli.compose.deps.build_warden_deps`) builds one
from a loaded Hive Manifest's `[llm.providers]`/`[llm.slots]`; a Virtual Cell has no manifest of
its own (`hivemind.manifest.env`'s own module docstring), and today's wire shape gives it nothing
to build a real one from either: a `hivemind.wardens.warden.Warden` learns a sub-bee's *slot* and
*grant budgets* from `waggle.messages.forage.grants.GrantIssued`, but `AllowedBinding`/`SourceRef`
(`waggle.messages.forage.values`) name a slot's provider only by its manifest *name* and model id,
never a base URL -- a base URL is exactly the piece `hivemind.cli.in_cell.config.
rewrite_loopback_base_url` exists to fix up once one is available, and nothing on the wire hands
one to this Cell yet (that module's own docstring flags the same TODO(8.x)).

`build_in_cell_provider_registry` is the smallest correct thing until that lands: a
`ProviderRegistry` with one `FakeLLMProvider` bound to `ModelSlot.WARDEN` and `ModelSlot.WORKER`,
so the in-Cell Warden always constructs and its autopilot table (which never awaits a model,
codingrules section 4/8.8) keeps the Hive alive with no model reachable at all; an awake episode
or a sub-bee's tool loop that actually reaches for a model gets an honest, scriptable fake rather
than a construction-time crash. This is a deliberate, documented placeholder, not a hidden
default: every call site that reaches it does so through this module, never a bare
`FakeLLMProvider()` sprinkled elsewhere.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.forage.slots` (ModelSlot), `hivemind.forage.map` (SlotBinding), `hivemind.llm`
    (ProviderRegistry, RegistryDeps, ProviderConfig, default_factories) and waggle only.

Key invariants:
    - The provider this module builds is always named `DEFAULT_IN_CELL_PROVIDER_NAME` and always
      `kind="fake"`: nothing here ever opens a real network connection (codingrules section 15
      "least privilege is code, not policy" -- a Cell with no real hosting plan gets no real
      egress from this module).
    - `[llm] offline` is passed as `False`: a fake provider has no base URL to be provably local
      or not, and `ProviderRegistry._check_offline` only ever inspects `base_url`, so this choice
      has no bearing on whether a real provider (once wired in) is later allowed to be remote.

See Also:
    - hivemind.cli.in_cell.config for rewrite_loopback_base_url, the URL-rewrite half of this gap.
    - hivemind.cli.in_cell.deps for build_in_cell_warden_deps, this module's one caller.
    - hivemind.llm.registry for ProviderRegistry, ProviderConfig and default_factories.
    - hivemind.llm.fake for FakeLLMProvider, what this registry's one provider resolves to.
"""

from __future__ import annotations

from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.registry import ProviderConfig, ProviderRegistry, RegistryDeps, default_factories
from waggle.clock import Clock

DEFAULT_IN_CELL_PROVIDER_NAME = "fake"  # Never a real vendor or server name (codingrules 8.6).
_FAKE_MODEL_ID = "in-cell-placeholder"  # Named, not a magic string repeated at each binding.

__all__ = ["DEFAULT_IN_CELL_PROVIDER_NAME", "build_in_cell_provider_registry"]


def build_in_cell_provider_registry(clock: Clock) -> ProviderRegistry:
    """Build the in-Cell Warden's own ProviderRegistry: today, a scriptable fake for every slot.

    Args:
        clock: Passed to the registry's `FakeLLMProvider`, for its `health()`/token readings.

    Returns:
        A ProviderRegistry that resolves `ModelSlot.WARDEN` and `ModelSlot.WORKER` (the two slots
        this Warden ever asks for: its own awake episodes and every sub-bee it spawns) to a
        `FakeLLMProvider` (module docstring: the smallest correct thing until a grant's own
        binding can name a real, Cell-reachable base URL).
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
