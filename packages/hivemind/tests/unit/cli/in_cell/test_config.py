"""Tests for hivemind.cli.in_cell.config: InCellRuntimeConfig, build_runtime_config.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/config.py (codingrules section 3). Exercises
    `build_runtime_config`'s own validation: which `HIVEMIND_*` variables are required, how a key
    given as a file is preferred over the inline variable, and that a missing or malformed value
    raises `ConfigurationError` naming the variable, never the secret itself.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.config for the module under test.
    - hivemind.manifest.env for InCellEnv/read_in_cell_env, this module's own input shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell.tiers import CombShieldLevel
from hivemind.cli.in_cell.config import (
    DEFAULT_SCRATCH_ROOT,
    build_runtime_config,
    gateway_host,
    rewrite_loopback_base_url,
)
from hivemind.common.errors import ConfigurationError
from hivemind.manifest.env import read_in_cell_env
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id
from waggle.signing import Ed25519Signer, Ed25519Verifier

_CLOCK = FakeClock()
_CELL_SIGNER = Ed25519Signer.generate()
_QUEEN_SIGNER = Ed25519Signer.generate()


def _full_environ(**overrides: str) -> dict[str, str]:
    """A complete, valid `HIVEMIND_*` environment for the in-Cell Warden entry point."""
    fields = {
        "HIVEMIND_QUEEN_WAGGLE_URL": "wss://queen.example.org:8443/waggle",
        "HIVEMIND_CELL_ID": new_cell_id(_CLOCK),
        "HIVEMIND_HIVE_ID": new_hive_id(_CLOCK),
        "HIVEMIND_QUEEN_NODE_ID": new_node_id(_CLOCK),
        "HIVEMIND_CELL_SIGNING_KEY": _CELL_SIGNER.private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": _QUEEN_SIGNER.public_key_bytes.hex(),
    }
    fields.update(overrides)
    return fields


def test_build_runtime_config_succeeds_with_every_required_variable_set() -> None:
    config = build_runtime_config(read_in_cell_env(_full_environ()), FakeClock())

    assert config.queen_waggle_url == "wss://queen.example.org:8443/waggle"
    assert config.spawn_config.comb_shield is CombShieldLevel.MEADOW
    assert config.spawn_config.scratch_root == DEFAULT_SCRATCH_ROOT
    assert isinstance(config.signer, Ed25519Signer)
    assert isinstance(config.verifier, Ed25519Verifier)
    assert config.queen_node_id in config.verifier.known_nodes


@pytest.mark.parametrize(
    "missing_var",
    [
        "HIVEMIND_QUEEN_WAGGLE_URL",
        "HIVEMIND_CELL_ID",
        "HIVEMIND_HIVE_ID",
        "HIVEMIND_QUEEN_NODE_ID",
        "HIVEMIND_CELL_SIGNING_KEY",
        "HIVEMIND_QUEEN_VERIFY_KEY",
    ],
)
def test_build_runtime_config_refuses_to_start_without_each_required_variable(
    missing_var: str,
) -> None:
    environ = _full_environ()
    del environ[missing_var]

    with pytest.raises(ConfigurationError, match=missing_var):
        build_runtime_config(read_in_cell_env(environ), FakeClock())


def test_build_runtime_config_prefers_a_key_file_over_the_inline_variable(tmp_path: Path) -> None:
    other_signer = Ed25519Signer.generate()
    key_file = tmp_path / "cell-signing-key"
    key_file.write_text(other_signer.private_key_bytes.hex())
    environ = _full_environ(HIVEMIND_CELL_SIGNING_KEY_FILE=str(key_file))

    config = build_runtime_config(read_in_cell_env(environ), FakeClock())

    assert config.signer.public_key_bytes == other_signer.public_key_bytes
    assert config.signer.public_key_bytes != _CELL_SIGNER.public_key_bytes


def test_build_runtime_config_rejects_malformed_hex_without_leaking_it() -> None:
    environ = _full_environ(HIVEMIND_CELL_SIGNING_KEY="not-hex-at-all")

    with pytest.raises(ConfigurationError) as excinfo:
        build_runtime_config(read_in_cell_env(environ), FakeClock())

    assert "not-hex-at-all" not in str(excinfo.value)
    assert "HIVEMIND_CELL_SIGNING_KEY" in str(excinfo.value)


def test_build_runtime_config_rejects_a_malformed_id() -> None:
    environ = _full_environ(HIVEMIND_CELL_ID="not-a-cell-id")

    with pytest.raises(ConfigurationError, match="HIVEMIND_CELL_ID"):
        build_runtime_config(read_in_cell_env(environ), FakeClock())


def test_build_runtime_config_mints_a_fresh_node_id_and_warden_id_each_call() -> None:
    environ = _full_environ()

    first = build_runtime_config(read_in_cell_env(environ), FakeClock())
    second = build_runtime_config(read_in_cell_env(environ), FakeClock())

    assert first.node_id != second.node_id
    assert first.warden_id != second.warden_id


def test_gateway_host_returns_the_queen_waggle_urls_own_host() -> None:
    assert gateway_host("ws://host.docker.internal:9443/waggle") == "host.docker.internal"


def test_rewrite_loopback_base_url_replaces_a_loopback_host_keeping_port_and_path() -> None:
    rewritten = rewrite_loopback_base_url("http://127.0.0.1:1234/v1", "host.docker.internal")

    assert rewritten == "http://host.docker.internal:1234/v1"


def test_rewrite_loopback_base_url_leaves_a_non_loopback_host_unchanged() -> None:
    url = "https://api.example.com/v1"

    assert rewrite_loopback_base_url(url, "host.docker.internal") == url


def test_rewrite_loopback_base_url_handles_localhost_too() -> None:
    rewritten = rewrite_loopback_base_url("http://localhost:9999", "host.docker.internal")

    assert rewritten == "http://host.docker.internal:9999"
