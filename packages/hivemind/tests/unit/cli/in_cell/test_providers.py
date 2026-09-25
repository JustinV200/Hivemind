"""Tests for hivemind.cli.in_cell.providers: build_in_cell_provider_registry.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/providers.py (codingrules section 3). Exercises both
    branches: the pre-roadmap-8.x fake fallback (no `HIVEMIND_PROVIDERS`) and the real path a
    provider table now enables.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.providers for the module under test.
    - hivemind.cli.in_cell.config for InCellRuntimeConfig/build_runtime_config, this module's own
      test fixtures' input.
"""

from __future__ import annotations

import json

from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.providers import (
    DEFAULT_IN_CELL_PROVIDER_NAME,
    build_in_cell_provider_registry,
)
from hivemind.forage.slots import ModelSlot
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.providers.openai_compat.provider import OpenAICompatProvider
from hivemind.manifest.env import read_in_cell_env
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer

_PROVIDERS_JSON = json.dumps(
    [
        {
            "name": "local",
            "kind": "openai_compat",
            "base_url": "http://host.docker.internal:1234/v1",
            "default_model": "local-test-model",
            "capabilities": {},
            "api_key_env": "HIVEMIND_LOCAL_API_KEY",
        }
    ]
)


def _slot_row(key: str) -> dict[str, object]:
    """One `HIVEMIND_SLOTS` row binding `key` to the `local` provider from `_PROVIDERS_JSON`."""
    return {
        "key": key,
        "provider": "local",
        "model": "local-test-model",
        "fallback": None,
        "effort": "MEDIUM",
        "max_output_tokens": None,
    }


def _config(**overrides: str) -> InCellRuntimeConfig:
    """A valid InCellRuntimeConfig, with sensible defaults for every field a test ignores."""
    clock = FakeClock()
    cell_signer = Ed25519Signer.generate()
    queen_signer = Ed25519Signer.generate()
    environ = {
        "HIVEMIND_QUEEN_WAGGLE_URL": "wss://queen.example.org:8443/waggle",
        "HIVEMIND_CELL_ID": new_cell_id(clock),
        "HIVEMIND_HIVE_ID": new_hive_id(clock),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(clock),
        "HIVEMIND_CELL_SIGNING_KEY": cell_signer.private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": queen_signer.public_key_bytes.hex(),
    }
    environ.update(overrides)
    return build_runtime_config(read_in_cell_env(environ), clock)


def _config_with_providers() -> InCellRuntimeConfig:
    """A runtime config carrying a real `local` provider bound to WARDEN and WORKER."""
    slots_json = json.dumps([_slot_row("warden"), _slot_row("worker")])
    return _config(HIVEMIND_PROVIDERS=_PROVIDERS_JSON, HIVEMIND_SLOTS=slots_json)


def test_the_warden_slot_resolves_to_a_fake_provider_with_no_provider_table() -> None:
    registry = build_in_cell_provider_registry(FakeClock(), _config())

    bound = registry.bound(ModelSlot.WARDEN)

    assert isinstance(bound.provider, FakeLLMProvider)
    assert bound.slot is ModelSlot.WARDEN


def test_the_worker_slot_resolves_to_a_fake_provider_with_no_provider_table() -> None:
    registry = build_in_cell_provider_registry(FakeClock(), _config())

    bound = registry.bound(ModelSlot.WORKER)

    assert isinstance(bound.provider, FakeLLMProvider)
    assert bound.slot is ModelSlot.WORKER


def test_warden_and_worker_share_the_same_fake_provider_instance() -> None:
    """One `fake` provider is constructed and cached, never one per slot."""
    registry = build_in_cell_provider_registry(FakeClock(), _config())

    warden_bound = registry.bound(ModelSlot.WARDEN)
    worker_bound = registry.bound(ModelSlot.WORKER)

    assert warden_bound.provider is worker_bound.provider
    assert registry.names() == (DEFAULT_IN_CELL_PROVIDER_NAME,)


def test_rebind_by_key_resolves_the_worker_slots_own_manifest_key_with_no_provider_table() -> None:
    registry = build_in_cell_provider_registry(FakeClock(), _config())

    bound = registry.bound_for_key(ModelSlot.WORKER.manifest_key, ModelSlot.WORKER)

    assert isinstance(bound.provider, FakeLLMProvider)


def test_a_non_empty_provider_table_builds_a_real_registry() -> None:
    registry = build_in_cell_provider_registry(FakeClock(), _config_with_providers())

    bound = registry.bound(ModelSlot.WARDEN)

    assert isinstance(bound.provider, OpenAICompatProvider)
    assert registry.names() == ("local",)


def test_a_real_registry_sees_the_api_key_through_config_environ() -> None:
    # HIVEMIND_LOCAL_API_KEY rides its own variable (hivemind.hive.backends.bootstrap's own
    # rendering rule), landing in config.environ exactly like every other HIVEMIND_* variable;
    # RegistryDeps.environ is config.environ (hivemind.cli.in_cell.providers's own docstring), so
    # _resolve_api_key finds it there without this test reaching into the registry's own cache.
    slots_json = json.dumps([_slot_row("warden"), _slot_row("worker")])
    config = _config(
        HIVEMIND_PROVIDERS=_PROVIDERS_JSON,
        HIVEMIND_SLOTS=slots_json,
        HIVEMIND_LOCAL_API_KEY="sk-test-key",
    )
    assert config.environ["HIVEMIND_LOCAL_API_KEY"] == "sk-test-key"

    registry = build_in_cell_provider_registry(FakeClock(), config)
    bound = registry.bound(ModelSlot.WARDEN)

    assert isinstance(bound.provider, OpenAICompatProvider)


def test_offline_does_not_refuse_a_gateway_host_base_url() -> None:
    """`[llm] offline = true` must not reject a base_url already rewritten to a gateway alias.

    From inside the Cell, `host.docker.internal` IS the Hive Stand's own machine (roadmap step
    8.x's own gap; `hivemind.llm.registry.config._is_provably_local`'s own gateway carve-out).
    """
    slots_json = json.dumps([_slot_row("warden"), _slot_row("worker")])
    config = _config(
        HIVEMIND_PROVIDERS=_PROVIDERS_JSON, HIVEMIND_SLOTS=slots_json, HIVEMIND_LLM_OFFLINE="true"
    )
    assert config.llm_offline is True

    registry = build_in_cell_provider_registry(FakeClock(), config)
    bound = registry.bound(ModelSlot.WARDEN)  # Must not raise OfflineViolationError.

    assert isinstance(bound.provider, OpenAICompatProvider)
