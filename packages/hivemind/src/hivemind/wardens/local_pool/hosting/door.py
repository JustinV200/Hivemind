"""Close the two provider callables a model-server host needs over a ProviderRegistry.

A Warden (a Cell's always-on supervisor) that starts a model server must hand the model registry
the provider that server answers as, then take it back when the server stops. The Warden holds no
registry (codingrules section 4: it sees a provider only through ``hivemind.llm``'s own seams), so
each composition root calls ``provider_door`` once and passes the two closures it returns to
``WardenDeps``. The manifest already declares every server's provider (``[llm.servers]`` requires
it), so ``register`` normally just returns the registry's own instance for that name, built from
that same row: replacing it would drop a client some other holder (the Queen's slot binding) may
already be using, and leak it unclosed. Only a config that differs from the declared row, or names
a provider the manifest never declared, is added at runtime; ``unregister`` then removes exactly
that and reports True so the host closes the instance it now owns. A manifest-declared provider is
never removed: the registry keeps it, and closes it at its own shutdown with every other.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Called by the two composition roots (``hivemind.cli.compose.
    deps``, ``hivemind.cli.in_cell.deps``); the closures are called by ``host``. Calls into
    ``hivemind.llm`` (ProviderRegistry, ProviderConfig) and ``config`` only.

Key invariants:
    - ``register`` never replaces a provider with an identical manifest row, so no live client is
      dropped unclosed.
    - ``unregister`` returns True only for a provider ``register`` added at runtime, the one case
      the caller must close.

See Also:
    - hivemind.llm.registry.provider_registry for add_provider, remove_provider and provider.
    - hivemind.wardens.local_pool.hosting.config for the two callable types.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.llm import LLMProvider, ProviderConfig, ProviderRegistry
from hivemind.wardens.local_pool.hosting.config import RegisterProvider, UnregisterProvider

__all__ = ["provider_door"]


def provider_door(
    registry: ProviderRegistry, declared: Mapping[str, ProviderConfig]
) -> tuple[RegisterProvider, UnregisterProvider]:
    """Return ``(register, unregister)`` closures over ``registry``.

    Args:
        registry: The model registry the Hive's calls resolve providers through.
        declared: The manifest's own ``[llm.providers]`` rows, by name.

    Returns:
        ``register(name, config)``, which returns the live provider for ``name`` (adding
        ``config`` first only when the manifest does not already declare exactly it), and
        ``unregister(name)``, which removes a runtime-added provider and says whether it did.
    """

    def register(name: str, config: ProviderConfig) -> LLMProvider:
        # The declared row is what the registry already holds; re-adding it would only drop the
        # cached instance (and its open connection pool) for nothing.
        if declared.get(name) != config:
            registry.add_provider(name, config)
        return registry.provider(name)

    def unregister(name: str) -> bool:
        # A declared provider stays: the manifest says it exists whether or not its server runs.
        if name in declared:
            return False
        return registry.remove_provider(name)

    return register, unregister
