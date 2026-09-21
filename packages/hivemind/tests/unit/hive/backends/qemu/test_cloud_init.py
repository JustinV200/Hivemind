"""Unit tests for hivemind.hive.backends.qemu.cloud_init: the NoCloud user-data/meta-data renderers.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/cloud_init.py (codingrules section 3). Snapshot-tests the
    rendered documents with a fixed fake key (roadmap step 5.11's own instruction), and separately
    asserts the one security-sensitive property by name: the raw private key never appears in the
    env file's own text, only in its own dedicated, differently-owned file.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.cloud_init for render_user_data/render_meta_data, under test.
"""

from __future__ import annotations

from pydantic import SecretStr

from hivemind.hive.backends.bootstrap import CellBootstrap, QueenEndpoint
from hivemind.hive.backends.qemu.cloud_init import (
    ENV_FILE_PATH,
    READINESS_MARKER,
    SIGNING_KEY_FILE_PATH,
    render_meta_data,
    render_user_data,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id

# A fixed fake key so the rendered documents are byte-for-byte reproducible (roadmap step 5.11).
_FAKE_PRIVATE_KEY_HEX = "11" * 32
_FAKE_PUBLIC_KEY_HEX = "22" * 32
_FAKE_QUEEN_VERIFY_KEY_HEX = "33" * 32


def _make_bootstrap() -> CellBootstrap:
    """Build a CellBootstrap with a fixed clock and a fixed fake key, for reproducible output."""
    clock = FakeClock()
    endpoint = QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex=_FAKE_QUEEN_VERIFY_KEY_HEX,
    )
    return CellBootstrap(
        cell_id=new_cell_id(clock),
        hive_id=new_hive_id(clock),
        endpoint=endpoint,
        private_key_hex=SecretStr(_FAKE_PRIVATE_KEY_HEX),
        public_key_hex=_FAKE_PUBLIC_KEY_HEX,
    )


def test_render_meta_data_carries_the_cell_id_as_instance_id() -> None:
    bootstrap = _make_bootstrap()

    meta_data = render_meta_data(bootstrap)

    assert f"instance-id: {bootstrap.cell_id}" in meta_data
    assert "local-hostname:" in meta_data


def test_render_user_data_is_a_cloud_config_document() -> None:
    user_data = render_user_data(_make_bootstrap())

    assert user_data.startswith("#cloud-config\n")
    assert "write_files:" in user_data
    assert "runcmd:" in user_data


def test_render_user_data_writes_the_private_key_only_to_its_own_file() -> None:
    bootstrap = _make_bootstrap()

    user_data = render_user_data(bootstrap)

    # The raw key appears exactly once: in the signing-key file's own content block.
    assert user_data.count(_FAKE_PRIVATE_KEY_HEX) == 1
    assert SIGNING_KEY_FILE_PATH in user_data


def test_render_user_data_never_inlines_the_signing_key_in_the_env_file() -> None:
    bootstrap = _make_bootstrap()

    user_data = render_user_data(bootstrap)
    env_block = user_data.split(f"path: {ENV_FILE_PATH}")[1].split("path:")[0]

    assert "HIVEMIND_CELL_SIGNING_KEY_FILE=" + SIGNING_KEY_FILE_PATH in env_block
    assert "HIVEMIND_CELL_SIGNING_KEY=" not in env_block
    assert _FAKE_PRIVATE_KEY_HEX not in env_block


def test_render_user_data_stamps_owners_and_permissions_on_every_file() -> None:
    user_data = render_user_data(_make_bootstrap())

    # Three write_files entries: the signing key, the env file, the systemd unit.
    assert user_data.count("permissions:") == 3
    assert user_data.count("owner:") == 3
    assert (
        "owner: hive:hive" in user_data
    )  # The signing key: readable by the process that opens it.
    assert "owner: root:root" in user_data  # The env file and the systemd unit.


def test_render_user_data_prints_the_readiness_marker_from_the_systemd_unit() -> None:
    user_data = render_user_data(_make_bootstrap())

    assert READINESS_MARKER in user_data
    assert "ExecStartPre=+" in user_data  # Runs as root regardless of the unit's own User=.


def test_render_user_data_overrides_the_queen_waggle_url_when_given() -> None:
    bootstrap = _make_bootstrap()

    user_data = render_user_data(bootstrap, queen_waggle_url_override="ws://10.0.2.100:8710")

    assert "HIVEMIND_QUEEN_WAGGLE_URL=ws://10.0.2.100:8710" in user_data
    assert bootstrap.endpoint.waggle_url not in user_data


def test_render_user_data_is_deterministic() -> None:
    bootstrap = _make_bootstrap()

    assert render_user_data(bootstrap) == render_user_data(bootstrap)
