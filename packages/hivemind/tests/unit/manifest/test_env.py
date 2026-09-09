"""Tests for hivemind.manifest.env: read_env, apply_env, provider_api_key.

Fits into the Hive:
    Mirrors src/hivemind/manifest/env.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.manifest.env for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from hivemind.forage import ModelSlot
from hivemind.manifest.env import EnvOverrides, apply_env, provider_api_key, read_env
from hivemind.manifest.errors import ManifestError
from hivemind.manifest.schema import HiveManifest, ProviderSpec
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id

_CLOCK = FakeClock()


def _local_offline_manifest() -> HiveManifest:
    """A manifest that is already offline-consistent, so apply_env's re-validation always passes."""
    return HiveManifest.model_validate(
        {
            "hive": {"id": new_hive_id(_CLOCK), "node_id": new_node_id(_CLOCK)},
            "llm": {
                "offline": True,
                "providers": {
                    "local": {"kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1"}
                },
                "slots": {
                    slot.manifest_key: {"provider": "local", "model": "test-model"}
                    for slot in ModelSlot
                },
            },
            "forage": {
                "roles": {
                    "drone": {
                        "cpu_cores": 0.5,
                        "memory_bytes": 1024,
                        "token_rate_per_minute": 100.0,
                    }
                }
            },
        }
    )


def test_read_env_returns_all_none_when_nothing_is_set() -> None:
    overrides = read_env({})

    assert overrides == EnvOverrides()


def test_read_env_reads_every_recognised_variable() -> None:
    overrides = read_env(
        {
            "HIVEMIND_DB": "custom.sqlite3",
            "HIVEMIND_LLM_OFFLINE": "TRUE",
            "HIVEMIND_HIVE_STAND_SCRATCH_ROOT": "/scratch",
            "HIVEMIND_LOG_LEVEL": "DEBUG",
            "HIVEMIND_ENV": "prod",
            "HIVEMIND_SOMETHING_ELSE": "ignored",
        }
    )

    assert overrides.db == Path("custom.sqlite3")
    assert overrides.llm_offline is True
    assert overrides.hive_stand_scratch_root == Path("/scratch")
    assert overrides.log_level == "DEBUG"
    assert overrides.env == "prod"


@pytest.mark.parametrize("value", ["1", "true", "yes", "True", "YES"])
def test_read_env_accepts_every_true_spelling_of_offline(value: str) -> None:
    assert read_env({"HIVEMIND_LLM_OFFLINE": value}).llm_offline is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "banana"])
def test_read_env_rejects_every_other_value_of_offline(value: str) -> None:
    with pytest.raises(ManifestError, match="HIVEMIND_LLM_OFFLINE"):
        read_env({"HIVEMIND_LLM_OFFLINE": value})


def test_read_env_rejects_an_unrecognised_hive_env() -> None:
    with pytest.raises(ManifestError, match="HIVEMIND_ENV"):
        read_env({"HIVEMIND_ENV": "staging"})


def test_apply_env_returns_the_identical_manifest_when_nothing_is_set() -> None:
    manifest = _local_offline_manifest()

    result = apply_env(manifest, EnvOverrides())

    assert result is manifest


def test_apply_env_overrides_db_and_env_together() -> None:
    manifest = _local_offline_manifest()

    result = apply_env(manifest, EnvOverrides(db=Path("override.sqlite3"), env="prod"))

    assert result.hive.db == Path("override.sqlite3")
    assert result.hive.env == "prod"
    assert result is not manifest


def test_apply_env_overrides_llm_offline() -> None:
    base = _local_offline_manifest()
    manifest = base.model_copy(update={"llm": base.llm.model_copy(update={"offline": False})})

    result = apply_env(manifest, EnvOverrides(llm_offline=True))

    assert result.llm.offline is True


def test_apply_env_overrides_hive_stand_scratch_root() -> None:
    manifest = _local_offline_manifest()

    result = apply_env(manifest, EnvOverrides(hive_stand_scratch_root=Path("/scratch")))

    assert result.hive_stand.scratch_root == Path("/scratch")


def test_apply_env_preserves_source_path() -> None:
    manifest = _local_offline_manifest().model_copy(
        update={"source_path": Path("/hive/manifest.toml")}
    )

    result = apply_env(manifest, EnvOverrides(db=Path("override.sqlite3")))

    assert result.source_path == Path("/hive/manifest.toml")


def test_apply_env_rejects_an_override_that_makes_the_manifest_inconsistent() -> None:
    # A hosted-only manifest cannot become offline=true through the environment any more than it
    # could through the file itself (hivemind.manifest.schema.llm.LlmSection's own validator).
    manifest = HiveManifest.model_validate(
        {
            "hive": {"id": new_hive_id(_CLOCK), "node_id": new_node_id(_CLOCK)},
            "llm": {
                "providers": {"anthropic": {"kind": "anthropic"}},
                "slots": {
                    slot.manifest_key: {"provider": "anthropic", "model": "test-model"}
                    for slot in ModelSlot
                },
            },
            "forage": {
                "roles": {
                    "drone": {
                        "cpu_cores": 0.5,
                        "memory_bytes": 1024,
                        "token_rate_per_minute": 100.0,
                    }
                }
            },
        }
    )

    with pytest.raises(ManifestError, match="not provably local"):
        apply_env(manifest, EnvOverrides(llm_offline=True))


def test_provider_api_key_reads_the_derived_variable_name() -> None:
    spec = ProviderSpec(kind="anthropic")

    key = provider_api_key("anthropic", spec, {"HIVEMIND_ANTHROPIC_API_KEY": "sk-ant-secret"})

    assert isinstance(key, SecretStr)
    assert key.get_secret_value() == "sk-ant-secret"
    assert "sk-ant-secret" not in repr(key)


def test_provider_api_key_reads_a_named_variable_when_set() -> None:
    spec = ProviderSpec(kind="anthropic", api_key_env="MY_OWN_KEY_VAR")

    key = provider_api_key("anthropic", spec, {"MY_OWN_KEY_VAR": "sk-ant-secret"})

    assert key is not None
    assert key.get_secret_value() == "sk-ant-secret"


def test_provider_api_key_returns_none_when_not_set() -> None:
    spec = ProviderSpec(kind="openai_compat", base_url="http://127.0.0.1:11434/v1")

    assert provider_api_key("local", spec, {}) is None
