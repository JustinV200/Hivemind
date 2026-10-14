"""Tests for hivemind.cli.in_cell.borrowed.config: the environment a packet gives a device Warden.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/config.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.config for the module under test.
    - tests/contracts/test_pollen_warden_env.py for the check that pollen sets what this reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.borrowed import SERVER_ID, BorrowedKit, make_kit

from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.cli.in_cell.borrowed import (
    BORROWED,
    BorrowedConfig,
    build_borrowed_config,
    is_borrowed,
)
from hivemind.cli.in_cell.borrowed.config import (
    DEVICE_MAX_OFFLINE_S,
    DEVICE_RECONNECT_BUDGET,
    SCRATCH_DIR,
    SOURCE_NAME,
)
from hivemind.common.errors import ConfigurationError
from hivemind.manifest import ManifestError, read_in_cell_env
from waggle.clock import SystemClock
from waggle.codec import canonical_bytes

_URL = "ws://127.0.0.1:8720"
_PIN = "ab" * 32  # A SHA-256 fingerprint in lowercase hex.


def _config(kit: BorrowedKit, **changes: str | None) -> BorrowedConfig:
    """Build the config for `kit`'s environment with `changes` applied (None removes a variable)."""
    environ = {**kit.environ}
    for name, value in changes.items():
        if value is None:
            environ.pop(name)
        else:
            environ[name] = value
    return build_borrowed_config(read_in_cell_env(environ), SystemClock())


def test_the_packets_environment_builds_a_device_config(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, _URL)

    config = _config(kit)

    runtime = config.runtime
    assert (runtime.warden_id, runtime.node_id, runtime.hive_id) == (
        kit.warden_id,
        kit.node_id,
        kit.hive_id,
    )  # The ids the move keeps, not freshly minted ones.
    assert runtime.spawn_config.cell_id == kit.cell_id
    assert runtime.queen_waggle_url == _URL
    assert config.state_dir == kit.state_dir
    assert config.tls_pin is None
    assert runtime.environ == kit.environ  # Provider keys are read from the device's environment.


def test_the_warden_signs_as_the_devices_node_with_the_packets_key(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, _URL)
    runtime = _config(kit).runtime

    message = canonical_bytes({"hello": "queen"})
    runtime_signature = runtime.signer.sign(message)
    # The device's key as the packet holds it, and the Queen's as she signs with hers.
    kit.device_verifier.verify(kit.node_id, message, runtime_signature)
    runtime.verifier.verify(kit.queen_node_id, message, kit.queen_signer.sign(message))


def test_the_machine_is_leased_as_the_queens_cell_for_the_device(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, _URL, access_level="scratch")

    cell = _config(kit).cell

    assert cell.cell_id == kit.cell_id
    assert cell.source_name == SOURCE_NAME == "swarm"  # Not the Hive Stand's source name.
    assert cell.access_level is AccessLevel.SCRATCH  # Parsed whatever the case.
    assert cell.comb_shield is CombShieldLevel.MEADOW
    assert cell.scratch_root == kit.state_dir / SCRATCH_DIR  # Nothing outside the state dir.
    assert cell.keep_root is None and cell.real_display_allowed is False
    assert cell.can_host_model is False  # The manifest declares no server.


def test_a_declared_server_makes_the_machine_able_to_host_a_model(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, _URL, with_server=True)

    config = _config(kit)

    assert config.cell.can_host_model is True
    assert [server.server_id for server in config.llm.servers] == [SERVER_ID]


def test_the_model_tables_come_from_the_device_manifest(tmp_path: Path) -> None:
    config = _config(make_kit(tmp_path, _URL))

    assert set(config.runtime.providers) == {"shared"}
    assert config.runtime.providers["shared"].base_url == "http://hive.example.lan:1234/v1"
    assert config.runtime.provider_seats == {"shared": 2}
    assert {binding.provider for binding in config.runtime.slots} == {"shared"}
    assert config.runtime.llm_offline is False


@pytest.mark.parametrize(
    "variable",
    [
        "HIVEMIND_STATE_DIR",
        "HIVEMIND_DEVICE_MANIFEST",
        "HIVEMIND_CELL_SIGNING_KEY_FILE",
        "HIVEMIND_WARDEN_ID",
        "HIVEMIND_NODE_ID",
        "HIVEMIND_ACCESS_LEVEL",
        "HIVEMIND_CELL_ID",
        "HIVEMIND_HIVE_ID",
        "HIVEMIND_QUEEN_NODE_ID",
        "HIVEMIND_QUEEN_VERIFY_KEY",
        "HIVEMIND_QUEEN_WAGGLE_URL",
    ],
)
def test_every_variable_a_device_needs_is_required_and_named(tmp_path: Path, variable: str) -> None:
    kit = make_kit(tmp_path, _URL)

    with pytest.raises(ConfigurationError, match=variable):
        _config(kit, **{variable: None})


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("HIVEMIND_WARDEN_ID", "node_01ARZ3NDEKTSV4RRFFQ69G5FAV"),  # An id of another kind.
        ("HIVEMIND_NODE_ID", "not-an-id"),
        ("HIVEMIND_ACCESS_LEVEL", "everything"),
    ],
)
def test_a_malformed_value_is_refused_naming_its_variable(
    tmp_path: Path, variable: str, value: str
) -> None:
    kit = make_kit(tmp_path, _URL)

    with pytest.raises(ConfigurationError, match=variable):
        _config(kit, **{variable: value})


@pytest.mark.parametrize("size", [0, 31, 33, 64])
def test_a_node_key_that_is_not_a_32_byte_seed_is_refused_without_showing_it(
    tmp_path: Path, size: int
) -> None:
    kit = make_kit(tmp_path, _URL)
    kit.key_path.write_bytes(b"k" * size)  # 64 is what a hex-encoded key would be.

    with pytest.raises(ConfigurationError, match="32-byte") as raised:
        _config(kit)

    assert "kkkk" not in str(raised.value)


def test_a_missing_device_manifest_is_a_manifest_error(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, _URL)
    kit.manifest_path.unlink()

    with pytest.raises(ManifestError, match="Could not read device manifest"):
        _config(kit)


@pytest.mark.parametrize("tier", ["PROPOLIS", "NIGHT_VEIL"])
def test_a_device_holds_the_meadow_tier_only(tmp_path: Path, tier: str) -> None:
    kit = make_kit(tmp_path, _URL)

    with pytest.raises(ConfigurationError):
        _config(kit, HIVEMIND_COMB_SHIELD=tier)


def test_a_wss_listener_needs_the_pin_and_a_ws_listener_has_none(tmp_path: Path) -> None:
    secure = make_kit(tmp_path, "wss://hive.example.org:8443")
    assert _config(secure, HIVEMIND_TLS_PIN=_PIN).tls_pin == _PIN
    with pytest.raises(ConfigurationError, match="HIVEMIND_TLS_PIN"):
        _config(secure)  # Pinned certificates are what a device trusts.
    with pytest.raises(ConfigurationError, match="SHA-256"):
        _config(secure, HIVEMIND_TLS_PIN="AB:CD")

    plain = make_kit(tmp_path, _URL)
    with pytest.raises(ConfigurationError, match="HIVEMIND_TLS_PIN"):
        _config(plain, HIVEMIND_TLS_PIN=_PIN)


def test_a_virtual_cells_gateway_host_is_not_a_devices_listener(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, "ws://host.docker.internal:8720")

    with pytest.raises(ConfigurationError, match="invalid for a device"):
        _config(kit)


@pytest.mark.parametrize(("value", "expected"), [(None, False), ("owned", False), (BORROWED, True)])
def test_the_lease_mode_selects_the_borrowed_warden(
    tmp_path: Path, value: str | None, expected: bool
) -> None:
    kit = make_kit(tmp_path, _URL)
    environ = {**kit.environ}
    environ.pop("HIVEMIND_CELL_LEASE")
    if value is not None:
        environ["HIVEMIND_CELL_LEASE"] = value

    assert is_borrowed(read_in_cell_env(environ)) is expected


def test_a_lease_mode_nobody_knows_is_refused_not_run_as_a_virtual_cell(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, _URL)

    with pytest.raises(ConfigurationError, match="HIVEMIND_CELL_LEASE"):
        is_borrowed(read_in_cell_env({**kit.environ, "HIVEMIND_CELL_LEASE": "rented"}))


def test_the_budget_for_finding_out_why_the_link_went_is_carved_out_of_the_offline_limit(
    tmp_path: Path,
) -> None:
    config = _config(make_kit(tmp_path, _URL))

    # The shipped defaults of `[supervision] reconnect_budget`, which a device manifest lacks.
    assert config.reconnect_budget is DEVICE_RECONNECT_BUDGET
    assert (config.reconnect_budget.attempts, config.reconnect_budget.wall_clock_s) == (2, 300.0)
    assert config.reconnect_budget.wall_clock_s < DEVICE_MAX_OFFLINE_S == config.max_offline_s


def test_the_diagnostic_drone_is_told_the_queens_host_and_nothing_else_of_her_address(
    tmp_path: Path,
) -> None:
    kit = make_kit(tmp_path, "wss://hive.example:8720/waggle?token=secret")

    config = _config(kit, HIVEMIND_TLS_PIN=_PIN)

    assert config.queen_host == "hive.example"  # No port, no path, no credential.
