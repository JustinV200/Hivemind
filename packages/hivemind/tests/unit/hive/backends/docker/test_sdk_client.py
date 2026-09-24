"""Unit tests for hivemind.hive.backends.docker.sdk_client: the lazy import and pure helpers.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/sdk_client.py (codingrules section 3). SdkDockerClient's own
    docker-py calls need a real daemon and are exercised by
    packages/hivemind/tests/integration/test_docker_backend.py instead; this module covers what
    can be proven with no daemon: the lazy-import guard and the "Created" timestamp parser.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.sdk_client for SdkDockerClient, under test.
    - packages/hivemind/tests/integration/test_docker_backend.py for the real-daemon coverage.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from hivemind.common.errors import ConfigurationError
from hivemind.hive.backends.docker.sdk_client import (
    SdkDockerClient,
    _parse_created_at,
    _recreate_spec,
)


def test_missing_docker_package_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A None entry in sys.modules is CPython's own documented way to make `import docker` behave
    # as though the package is not installed, without needing it actually absent from the venv --
    # this venv has the "hivemind[docker]" extra installed so ruff/mypy/CI can check this module.
    monkeypatch.setitem(sys.modules, "docker", None)

    with pytest.raises(ConfigurationError, match="hivemind\\[docker\\]"):
        SdkDockerClient()


def test_parse_created_at_handles_nanosecond_precision() -> None:
    parsed = _parse_created_at("2024-01-15T10:30:00.123456789Z")

    assert parsed == datetime(2024, 1, 15, 10, 30, 0, 123456, tzinfo=UTC)


def test_parse_created_at_handles_no_fractional_seconds() -> None:
    parsed = _parse_created_at("2024-01-15T10:30:00Z")

    assert parsed == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)


def test_parse_created_at_falls_back_to_now_for_none() -> None:
    before = datetime.now(UTC)

    parsed = _parse_created_at(None)

    assert before <= parsed <= datetime.now(UTC)


def test_parse_created_at_falls_back_to_now_for_garbage() -> None:
    before = datetime.now(UTC)

    parsed = _parse_created_at("not-a-timestamp")

    assert before <= parsed <= datetime.now(UTC)


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.10: _recreate_spec, the pure inspect-attrs -> containers.create kwargs mapper.
# ──────────────────────────────────────────────────────────────────────────────


def test_recreate_spec_maps_network_volumes_and_resource_limits() -> None:
    attrs = {
        "Name": "/hivemind-cell-test",
        "Config": {"Env": ["A=1"], "Labels": {"hivemind.cell_id": "cell_test"}},
        "HostConfig": {
            "NanoCpus": 1_000_000_000,
            "Memory": 1073741824,
            "PidsLimit": 256,
            "CapDrop": ["ALL"],
            "CapAdd": ["NET_ADMIN"],
            "ExtraHosts": ["host.docker.internal:host-gateway"],
            "SecurityOpt": ["no-new-privileges:true"],
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": ""},  # noqa: S108  # SAFETY: a container-internal path, not a host one.
        },
        "NetworkSettings": {"Networks": {"hivemind-cell-test-net": {}}},
        "Mounts": [
            {"Type": "volume", "Name": "hivemind-cell-test-scratch", "Destination": "/scratch"}
        ],
    }

    spec = _recreate_spec(attrs, "hivemind-snapshot:abc")

    assert spec["image"] == "hivemind-snapshot:abc"
    assert spec["name"] == "hivemind-cell-test"  # Leading "/" stripped.
    assert spec["network"] == "hivemind-cell-test-net"
    assert spec["volumes"] == {"hivemind-cell-test-scratch": {"bind": "/scratch", "mode": "rw"}}
    assert spec["nano_cpus"] == 1_000_000_000
    assert spec["mem_limit"] == 1073741824
    assert spec["read_only"] is True
    # Every field the create path sets survives: without the host-gateway entry a Linux Cell
    # could not dial the Queen after a rollback (found by the real-daemon rollback test).
    assert spec["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert spec["cap_add"] == ["NET_ADMIN"]


def test_recreate_spec_tolerates_missing_optional_sections() -> None:
    spec = _recreate_spec({}, "hivemind-snapshot:abc")

    assert spec["network"] is None
    assert spec["extra_hosts"] is None
    assert spec["cap_add"] is None
    assert spec["volumes"] == {}
    assert spec["nano_cpus"] is None
    assert spec["read_only"] is False
