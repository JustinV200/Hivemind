"""Tests for hivemind.cli.in_cell.providers: build_in_cell_provider_registry.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/providers.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.providers for the module under test.
"""

from __future__ import annotations

from hivemind.cli.in_cell.providers import (
    DEFAULT_IN_CELL_PROVIDER_NAME,
    build_in_cell_provider_registry,
)
from hivemind.forage.slots import ModelSlot
from hivemind.llm.fake import FakeLLMProvider
from waggle.clock import FakeClock


def test_the_warden_slot_resolves_to_a_fake_provider() -> None:
    registry = build_in_cell_provider_registry(FakeClock())

    bound = registry.bound(ModelSlot.WARDEN)

    assert isinstance(bound.provider, FakeLLMProvider)
    assert bound.slot is ModelSlot.WARDEN


def test_the_worker_slot_resolves_to_a_fake_provider() -> None:
    registry = build_in_cell_provider_registry(FakeClock())

    bound = registry.bound(ModelSlot.WORKER)

    assert isinstance(bound.provider, FakeLLMProvider)
    assert bound.slot is ModelSlot.WORKER


def test_warden_and_worker_share_the_same_provider_instance() -> None:
    """One `fake` provider is constructed and cached, never one per slot."""
    registry = build_in_cell_provider_registry(FakeClock())

    warden_bound = registry.bound(ModelSlot.WARDEN)
    worker_bound = registry.bound(ModelSlot.WORKER)

    assert warden_bound.provider is worker_bound.provider
    assert registry.names() == (DEFAULT_IN_CELL_PROVIDER_NAME,)


def test_rebind_by_key_resolves_the_worker_slots_own_manifest_key() -> None:
    registry = build_in_cell_provider_registry(FakeClock())

    bound = registry.bound_for_key(ModelSlot.WORKER.manifest_key, ModelSlot.WORKER)

    assert isinstance(bound.provider, FakeLLMProvider)
