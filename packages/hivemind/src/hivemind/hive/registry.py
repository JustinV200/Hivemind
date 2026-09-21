"""Define BackendRegistry: name a CellBackend factory, then look the built backend up by name.

Backends, worker roles, tools and executors are discovered through an explicit registry, never an
`if backend == "docker"` chain (codingrules section 8.4). `BackendRegistry` is that registry for
`hivemind.hive.backends.base.CellBackend`: the composition root (a later phase's `cli/`) calls
`register(name, factory)` once per backend it wires up, keyed by the name a Hive Manifest's
`[hive] backend` value names, and everything above the composition root calls `get(name)` to reach
the live backend. `get` builds a backend from its factory at most once and caches it, mirroring
`hivemind.llm.registry.ProviderRegistry.provider`'s own lazy-construct-and-cache shape for the same
reason: a Hive that never uses a given backend should never pay to construct it. This module
defines no module-level registry instance on purpose (codingrules section 8.2: "no module-level
singletons, no service locator") -- the composition root owns the one `BackendRegistry` a process
uses and passes it down explicitly.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Constructed by the composition root
    (a later phase's `cli/`) and read by whatever needs a live CellBackend: `queen.placement`
    (provisioning) and the Undertaker (destroying, roadmap step 5.8). Calls into
    hivemind.hive.backends.base and hivemind.hive.errors only.

Key invariants:
    - register() never silently replaces an existing factory: a duplicate name is almost always
      two backends wired to the same manifest name by mistake, so it raises instead.
    - get(name) constructs at most once per name: a second call for the same name returns the
      cached instance, never a fresh one.

See Also:
    - .claude/codingrules.md section 8.4 for the "plugins by registry, not an if chain" rule this
      module implements for Cell backends.
    - .claude/codingrules.md section 8.2 for the "no module-level singleton" rule this module's
      class-based (not free-function) shape follows.
    - hivemind.llm.registry for ProviderRegistry, the lazy-construct-and-cache pattern this module
      mirrors for a different Protocol.
    - hivemind.hive.backends.base for CellBackend, the Protocol this registry looks up by name.
    - hivemind.hive.errors for UnknownBackendError, the error get() raises for an unregistered name.
"""

from __future__ import annotations

from collections.abc import Callable

from hivemind.hive.backends.base import CellBackend
from hivemind.hive.errors import UnknownBackendError

# Builds one CellBackend instance on demand; takes no arguments, since every backend's own
# construction-time collaborators (a Clock, Docker SDK client, credentials, ...) are already
# closed over by the composition root before it is handed to register().
CellBackendFactory = Callable[[], CellBackend]

__all__ = ["BackendRegistry", "CellBackendFactory"]


class BackendRegistry:
    """Name -> CellBackend, built lazily from registered factories and cached thereafter."""

    def __init__(self) -> None:
        """Create an empty registry; the composition root registers every backend it wires up."""
        self._factories: dict[str, CellBackendFactory] = {}
        self._cache: dict[str, CellBackend] = {}

    def register(self, name: str, factory: CellBackendFactory) -> None:
        """Register `factory` under `name` (the Hive Manifest's `[hive] backend` value).

        Args:
            name: The manifest-facing backend name ("docker", "qemu", "fake", ...).
            factory: Builds one CellBackend instance; called at most once per name (get() caches
                the result).

        Raises:
            ValueError: `name` is already registered.
        """
        # A duplicate name is almost always two backends wired to the same manifest name by
        # mistake; silently letting the second call win would hide that at construction time
        # instead of failing loudly where the mistake was made.
        if name in self._factories:
            raise ValueError(f"a CellBackend is already registered under {name!r}.")
        self._factories[name] = factory

    def get(self, name: str) -> CellBackend:
        """Return the CellBackend registered under `name`, constructing it on first use.

        Args:
            name: The backend name to look up.

        Returns:
            The same CellBackend instance on every call for a given `name`.

        Raises:
            UnknownBackendError: No factory is registered under `name`.
        """
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        factory = self._factories.get(name)
        if factory is None:
            raise UnknownBackendError(name, tuple(self._factories))
        instance = factory()
        self._cache[name] = instance
        return instance

    def names(self) -> tuple[str, ...]:
        """Return every registered backend name, in registration order."""
        return tuple(self._factories)
