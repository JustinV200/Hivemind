"""Unit tests for hivemind.hive.backends.docker.sdk_client.images: recreate specs, image listing.

Roadmap step 5.10's pure inspect-attrs -> `containers.create` kwargs mapper keeps a recreated
container's name, network, volumes and limits (10.6a: its first network, its hosts entries and
added capabilities too), and the Night Veil teardown's image listing sends the daemon every label
it must match (codingrules section 12). The docker-py calls themselves need a real daemon
(packages/hivemind/tests/integration/test_docker_night_veil_teardown.py).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/sdk_client/images.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.sdk_client.images for the functions under test.
    - packages/hivemind/tests/integration/test_docker_snapshot_and_cli.py for real snapshots.
"""

from __future__ import annotations

from hivemind.hive.backends.docker.sdk_client.handle import DockerHandle
from hivemind.hive.backends.docker.sdk_client.images import recreate_spec, sync_list_images


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

    spec = recreate_spec(attrs, "hivemind-snapshot:abc")

    assert spec["image"] == "hivemind-snapshot:abc"
    assert spec["name"] == "hivemind-cell-test"  # Leading "/" stripped.
    assert spec["network"] == "hivemind-cell-test-net"
    assert spec["volumes"] == {"hivemind-cell-test-scratch": {"bind": "/scratch", "mode": "rw"}}
    assert spec["nano_cpus"] == 1_000_000_000
    assert spec["mem_limit"] == 1073741824
    assert spec["read_only"] is True


def test_recreate_spec_tolerates_missing_optional_sections() -> None:
    spec = recreate_spec({}, "hivemind-snapshot:abc")

    assert spec["network"] is None
    assert spec["volumes"] == {}
    assert spec["nano_cpus"] is None
    assert spec["read_only"] is False


def test_recreate_spec_keeps_the_first_network_its_hosts_entries_and_added_capabilities() -> None:
    attrs: dict[str, object] = {
        "HostConfig": {
            "ExtraHosts": ["host.docker.internal:host-gateway"],
            "CapAdd": ["NET_ADMIN"],
        },
        "NetworkSettings": {"Networks": {"own-net": {}, "control": {}}},
    }

    spec = recreate_spec(attrs, "hivemind-snapshot:abc")

    # The rest are attached before the recreated container starts (sync_recreate_from_image).
    assert spec["network"] == "control"
    assert spec["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert spec["cap_add"] == ["NET_ADMIN"]


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

    found = sync_list_images(DockerHandle(client, None), {"b": "2", "a": "1"})

    assert found == ("sha256:one", "sha256:two")
    assert client.images.filters == [{"label": ["a=1", "b=2"]}]
