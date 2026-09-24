"""Tests for hivemind.llm.registry's shutdown path: ProviderRegistry.aclose.

Fits into the Hive:
    Mirrors src/hivemind/llm/registry.py (codingrules section 3), split out of test_registry.py
    by feature (codingrules 5.1: a test module splits by feature under test before it outgrows
    its limit), the same way test_lane_seats.py sits beside test_lane.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.registry for the module under test.
    - hivemind.llm.provider for LLMProvider.aclose, the per-provider contract this aggregates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

import structlog
from builders.llm import make_provider_config, make_registry_deps
from pydantic import SecretStr

from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.provider import LLMProvider
from hivemind.llm.registry import ProviderConfig, ProviderFactory, ProviderRegistry
from waggle.clock import Clock

_TINY_TIMEOUT_S = 0.01  # A hanging close is abandoned after this; keeps the test fast.


class _BrokenCloseProvider(FakeLLMProvider):
    """A FakeLLMProvider whose close fails the way a socket teardown can: an OSError."""

    async def aclose(self) -> None:
        """Fail like a connection reset mid-teardown."""
        raise ConnectionResetError("peer reset the connection during close")


class _HangingCloseProvider(FakeLLMProvider):
    """A FakeLLMProvider whose close never finishes, to prove the per-provider timeout."""

    async def aclose(self) -> None:
        """Wait forever on an event nothing sets."""
        await asyncio.Event().wait()


def _factory_by_name(builders: Mapping[str, Callable[[str], LLMProvider]]) -> ProviderFactory:
    """Return a "fake"-kind factory that builds each provider name with its own class."""

    def build(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        return builders[name](name)

    return build


def _registry(builders: Mapping[str, Callable[[str], LLMProvider]]) -> ProviderRegistry:
    """Build a registry with one "fake"-kind provider per name in `builders`."""
    configs = {name: make_provider_config(kind="fake") for name in builders}
    deps = make_registry_deps(factories={"fake": _factory_by_name(builders)})
    return ProviderRegistry(configs, [], offline=False, deps=deps)


def _fake(name: str) -> LLMProvider:
    """Build a plain FakeLLMProvider named `name`."""
    return FakeLLMProvider(name=name)


async def test_aclose_closes_every_constructed_provider() -> None:
    registry = _registry({"a": _fake, "b": _fake})
    first = registry.provider("a")
    second = registry.provider("b")

    await registry.aclose()

    assert isinstance(first, FakeLLMProvider)
    assert isinstance(second, FakeLLMProvider)
    assert first.is_closed
    assert second.is_closed


async def test_aclose_never_constructs_a_provider_nobody_used() -> None:
    built: list[str] = []

    def recording(name: str) -> LLMProvider:
        built.append(name)
        return FakeLLMProvider(name=name)

    registry = _registry({"used": recording, "unused": recording})
    registry.provider("used")

    await registry.aclose()

    assert built == ["used"]


async def test_aclose_is_idempotent_and_forgets_what_it_closed() -> None:
    registry = _registry({"a": _fake})
    first = registry.provider("a")

    await registry.aclose()
    await registry.aclose()

    # Forgotten, so the next use builds a fresh provider rather than handing out a closed one.
    assert registry.provider("a") is not first


async def test_aclose_logs_a_failed_close_and_still_closes_the_rest() -> None:
    registry = _registry({"broken": _BrokenCloseProvider, "fine": _fake})
    registry.provider("broken")
    fine = registry.provider("fine")

    with structlog.testing.capture_logs() as captured:
        await registry.aclose()

    assert isinstance(fine, FakeLLMProvider)
    assert fine.is_closed
    failures = [entry for entry in captured if entry["event"] == "llm.provider_close_failed"]
    assert len(failures) == 1
    assert failures[0]["provider"] == "broken"
    assert failures[0]["log_level"] == "warning"
    assert failures[0]["error"] == "ConnectionResetError"


async def test_aclose_abandons_a_hanging_close_after_its_timeout_and_moves_on() -> None:
    registry = _registry({"stuck": _HangingCloseProvider, "fine": _fake})
    registry.provider("stuck")
    fine = registry.provider("fine")

    with structlog.testing.capture_logs() as captured:
        await registry.aclose(timeout_s=_TINY_TIMEOUT_S)

    assert isinstance(fine, FakeLLMProvider)
    assert fine.is_closed
    failures = [entry for entry in captured if entry["event"] == "llm.provider_close_failed"]
    assert [entry["provider"] for entry in failures] == ["stuck"]
    assert failures[0]["error"] == "TimeoutError"
