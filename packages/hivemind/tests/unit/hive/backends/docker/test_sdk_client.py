"""Unit tests for hivemind.hive.backends.docker.sdk_client: the lazy import and pure helpers.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/sdk_client.py (codingrules section 3). SdkDockerClient's own
    docker-py calls need a real daemon and are exercised by
    packages/hivemind/tests/integration/test_docker_backend.py instead; this module covers what
    can be proven with no daemon: the lazy-import guard, the "Created" timestamp parser, the log
    driver a Night Veil container is created with and the label filter an image listing sends.

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
from hivemind.hive.backends.docker.client import DockerClientError, NetworkSpec
from hivemind.hive.backends.docker.sdk_client import (
    SdkDockerClient,
    _attached_networks,
    _check_matches,
    _DockerHandle,
    _log_config,
    _parse_created_at,
    _recreate_spec,
    _sync_list_images,
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


def test_recreate_spec_tolerates_missing_optional_sections() -> None:
    spec = _recreate_spec({}, "hivemind-snapshot:abc")

    assert spec["network"] is None
    assert spec["volumes"] == {}
    assert spec["nano_cpus"] is None
    assert spec["read_only"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 10.6a: attachments read back, a reused network checked, a dual-homed recreate.
# ──────────────────────────────────────────────────────────────────────────────

_CONTROL = NetworkSpec(
    name="hivemind-hive_x-control",
    internal=True,
    labels={},
    subnet="10.213.7.0/24",
    gateway="10.213.7.1",
    isolates_peers=True,
)
_CONTROL_ATTRS = {
    "Internal": True,
    "IPAM": {"Config": [{"Subnet": "10.213.7.0/24", "Gateway": "10.213.7.1"}]},
    "Options": {"com.docker.network.bridge.enable_icc": "false"},
}


def test_attached_networks_reads_every_network_a_container_is_on_sorted() -> None:
    attrs: dict[str, object] = {"NetworkSettings": {"Networks": {"own-net": {}, "control": {}}}}

    assert _attached_networks(attrs) == ("control", "own-net")
    assert _attached_networks({}) == ()


def test_an_existing_network_that_enforces_the_plan_is_reused() -> None:
    _check_matches(_CONTROL_ATTRS, _CONTROL)  # Does not raise.


@pytest.mark.parametrize(
    "change",
    [
        {"Internal": False},
        {"Options": {}},
        {"IPAM": {"Config": [{"Subnet": "10.99.0.0/24", "Gateway": "10.99.0.1"}]}},
    ],
)
def test_an_existing_network_that_does_not_enforce_the_plan_is_refused(
    change: dict[str, object],
) -> None:
    # Not internal (egress through it), peers able to talk, or another subnet than the listener's.
    with pytest.raises(DockerClientError, match="does not match"):
        _check_matches({**_CONTROL_ATTRS, **change}, _CONTROL)


def test_recreate_spec_keeps_the_first_network_its_hosts_entries_and_added_capabilities() -> None:
    attrs: dict[str, object] = {
        "HostConfig": {
            "ExtraHosts": ["host.docker.internal:host-gateway"],
            "CapAdd": ["NET_ADMIN"],
        },
        "NetworkSettings": {"Networks": {"own-net": {}, "control": {}}},
    }

    spec = _recreate_spec(attrs, "hivemind-snapshot:abc")

    # The rest are attached before the recreated container starts (_sync_recreate_from_image).
    assert spec["network"] == "control"
    assert spec["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert spec["cap_add"] == ["NET_ADMIN"]


def test_a_log_driver_becomes_docker_pys_own_log_config_and_none_keeps_the_default() -> None:
    assert _log_config("none") == {"type": "none"}
    assert _log_config(None) is None


class _Image:
    """What docker-py's image list hands back: only its id is read."""

    def __init__(self, image_id: str) -> None:
        self.id = image_id


class _Images:
    """docker-py's `client.images`, recording the filters each listing sent."""

    def __init__(self) -> None:
        self.filters: list[dict[str, list[str]]] = []

    def list(self, *, filters: dict[str, list[str]]) -> list[_Image]:
        self.filters.append(filters)
        return [_Image("sha256:one"), _Image("sha256:two")]


class _Client:
    """docker-py's client, with only its images collection."""

    def __init__(self) -> None:
        self.images = _Images()


def test_listing_images_filters_by_every_label_and_returns_their_ids() -> None:
    client = _Client()

    found = _sync_list_images(_DockerHandle(client, None), {"b": "2", "a": "1"})

    assert found == ("sha256:one", "sha256:two")
    assert client.images.filters == [{"label": ["a=1", "b=2"]}]
