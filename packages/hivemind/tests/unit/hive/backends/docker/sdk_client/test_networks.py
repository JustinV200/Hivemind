"""Unit tests for hivemind.hive.backends.docker.sdk_client.networks: attachments and reuse checks.

Roadmap step 10.6a: a container's attachments are read back sorted, and an existing network is
reused only when it enforces what the plan asks of it (internal, peers kept apart, the listener's
own subnet and gateway). The docker-py calls themselves need a real daemon
(packages/hivemind/tests/integration/test_docker_egress.py).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/sdk_client/networks.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.sdk_client.networks for the functions under test.
    - packages/hivemind/tests/integration/test_docker_egress.py for the real-daemon coverage.
"""

from __future__ import annotations

import pytest

from hivemind.hive.backends.docker.client import DockerClientError, NetworkSpec
from hivemind.hive.backends.docker.sdk_client.networks import attached_networks, check_matches

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

    assert attached_networks(attrs) == ("control", "own-net")
    assert attached_networks({}) == ()


def test_an_existing_network_that_enforces_the_plan_is_reused() -> None:
    check_matches(_CONTROL_ATTRS, _CONTROL)  # Does not raise.


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
        check_matches({**_CONTROL_ATTRS, **change}, _CONTROL)
