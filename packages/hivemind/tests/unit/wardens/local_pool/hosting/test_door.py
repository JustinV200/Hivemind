"""Tests for hivemind.wardens.local_pool.hosting.door: the registry closures a host is given.

Fits into the Hive:
    Mirrors src/hivemind/wardens/local_pool/hosting/door.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.local_pool.hosting.door for the module under test.
"""

from __future__ import annotations

from hivemind.forage import ForageMap
from hivemind.llm import ProviderConfig, ProviderRegistry
from hivemind.llm.registry import RegistryDeps, default_factories
from hivemind.wardens.local_pool.hosting import provider_door
from waggle.clock import FakeClock

_CONFIG = ProviderConfig(kind="openai_compat", base_url="http://127.0.0.1:1/v1", default_model="m")
_OTHER = ProviderConfig(kind="openai_compat", base_url="http://127.0.0.1:2/v1", default_model="m")


def _registry(rows: dict[str, ProviderConfig]) -> ProviderRegistry:
    clock = FakeClock()
    deps = RegistryDeps(
        map=ForageMap((), clock=clock), factories=default_factories(), clock=clock, environ={}
    )
    return ProviderRegistry(rows, (), False, deps)


async def test_registering_a_declared_row_returns_the_registrys_own_instance() -> None:
    registry = _registry({"local": _CONFIG})
    register, _ = provider_door(registry, {"local": _CONFIG})

    first = register("local", _CONFIG)

    # Not replaced: the instance is the one every other holder already uses.
    assert register("local", _CONFIG) is first
    assert registry.provider("local") is first
    await registry.aclose()


async def test_registering_an_undeclared_name_adds_it_and_unregister_removes_it() -> None:
    registry = _registry({})
    register, unregister = provider_door(registry, {})

    provider = register("runtime", _CONFIG)

    assert registry.provider("runtime") is provider
    assert unregister("runtime") is True
    assert unregister("runtime") is False
    await provider.aclose()


async def test_unregister_never_removes_a_manifest_declared_provider() -> None:
    registry = _registry({"local": _CONFIG})
    _, unregister = provider_door(registry, {"local": _CONFIG})

    assert unregister("local") is False
    assert registry.provider("local") is not None
    await registry.aclose()


async def test_a_config_that_differs_from_the_declared_row_replaces_it() -> None:
    registry = _registry({"local": _CONFIG})
    register, _ = provider_door(registry, {"local": _CONFIG})

    first = register("local", _CONFIG)
    second = register("local", _OTHER)

    assert second is not first
    assert registry.provider("local") is second
    await first.aclose()
    await registry.aclose()
