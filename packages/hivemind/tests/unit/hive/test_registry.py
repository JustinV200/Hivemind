"""Unit tests for hivemind.hive.registry: BackendRegistry's register/get contract.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/hive/registry.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.registry for BackendRegistry, the class under test.
    - hivemind.hive.backends.fake for FakeCellBackend, used here as a stand-in CellBackend.
"""

from __future__ import annotations

import pytest

from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.errors import UnknownBackendError
from hivemind.hive.registry import BackendRegistry
from waggle.clock import FakeClock


def test_get_returns_the_same_instance_every_call() -> None:
    registry = BackendRegistry()
    registry.register("fake", lambda: FakeCellBackend(FakeClock()))

    first = registry.get("fake")
    second = registry.get("fake")

    assert first is second


def test_get_unregistered_name_raises_unknown_backend_error() -> None:
    registry = BackendRegistry()
    registry.register("fake", lambda: FakeCellBackend(FakeClock()))

    with pytest.raises(UnknownBackendError) as exc_info:
        registry.get("docker")

    assert exc_info.value.name == "docker"
    assert exc_info.value.known == ("fake",)


def test_register_duplicate_name_raises_value_error() -> None:
    registry = BackendRegistry()
    registry.register("fake", lambda: FakeCellBackend(FakeClock()))

    with pytest.raises(ValueError, match="fake"):
        registry.register("fake", lambda: FakeCellBackend(FakeClock()))


def test_names_lists_every_registered_name_in_registration_order() -> None:
    registry = BackendRegistry()
    registry.register("fake", lambda: FakeCellBackend(FakeClock()))
    registry.register("docker", lambda: FakeCellBackend(FakeClock()))

    assert registry.names() == ("fake", "docker")


def test_get_never_constructs_before_first_use() -> None:
    calls: list[str] = []

    def factory() -> FakeCellBackend:
        calls.append("built")
        return FakeCellBackend(FakeClock())

    registry = BackendRegistry()
    registry.register("fake", factory)

    assert calls == []
    registry.get("fake")
    assert calls == ["built"]
