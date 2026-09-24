"""Provision Virtual Cells on a Docker daemon: the first working CellBackend (roadmap step 5.4).

ADR-0026 picks Docker as the first backend and ADR-0027 sets the shape every backend follows (a
Virtual Cell dials out; `provision()` returns only once a Warden has announced itself and sent its
first Heartbeat). `DockerCellBackend` implements `hivemind.hive.backends.base.CellBackend` over a
narrow `DockerClientPort` (two implementations: the real `SdkDockerClient`, the only module in the
workspace allowed to import the `docker` package, and the in-memory `FakeDockerClient`), plus the
backend-independent identity and readiness seam from `hivemind.hive.backends.bootstrap`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.hive.backends`.
    Constructed by the composition root (a later phase's `cli/`) through `build_docker_backend`
    when `[hive] backend = "docker"`, and registered in `hivemind.hive.registry.BackendRegistry`
    (codingrules 5.5: nothing here registers itself at import time). Calls into
    hivemind.hive.backends.base, hivemind.hive.backends.bootstrap, hivemind.hive.cell_state,
    hivemind.hive.errors, hivemind.hive.models and waggle.

Key invariants:
    - Importing this package never imports the vendor `docker` SDK: only constructing a real
      `SdkDockerClient` does (`hivemind.hive.backends.docker.sdk_client`'s own key invariant), so
      `from hivemind.hive.backends.docker import DockerCellBackend` succeeds without the
      `hivemind[docker]` extra installed.
    - Every Cell `DockerCellBackend.provision` returns has kind == CellKind.VIRTUAL and
      access_level == AccessLevel.FULL, exactly like every other CellBackend implementation.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md
    - hivemind.hive.backends.bootstrap for CellBootstrap, QueenEndpoint, ReadinessGate and
      CellReadyInfo, the pieces this backend shares with the QEMU and cloud backends to come.
    - packages/hivemind/tests/contracts/test_cell_backend_contract.py for the shared CellBackend
      contract this backend is proven against, over FakeDockerClient and FakeReadinessGate.

Public API:
    - DockerCellBackend, DockerBackendConfig (`backend.py`): the CellBackend implementation and
      its headroom and control network.
    - build_docker_backend (`backend.py`... factory, see below): what a composition root calls to
      get a `hivemind.hive.registry.CellBackendFactory` for `BackendRegistry.register`.
    - DockerClientPort, DockerNetworkPort, DockerClientError, ContainerSpec, ContainerInfo,
      NetworkSpec, VolumeSpec (`client.py`): the narrow Docker seam, its network slice and its
      value types.
    - SdkDockerClient (`sdk_client.py`): the real DockerClientPort, over the `docker` SDK.
    - FakeDockerClient (`fake.py`): the in-memory DockerClientPort for tests and demos.
    - NetworkPlan, plan_network, network_name, host_gateway_extra_hosts (`network.py`): what each
      NetworkPolicy means at the Docker level; ControlNetwork, control_network: the per-Hive
      control network a dual-homed Cell's link rides (roadmap step 10.6a).
    - cut_egress, restore_egress, ensure_control (`egress.py`): isolation's lever on a dual-homed
      Cell's own network, and the control network made or reused before the listener binds.
"""

from hivemind.hive.backends.docker.backend import (
    DockerBackendConfig,
    DockerCellBackend,
    build_docker_backend,
)
from hivemind.hive.backends.docker.client import (
    ContainerInfo,
    ContainerSpec,
    DockerClientError,
    DockerClientPort,
    DockerNetworkPort,
    NetworkSpec,
    VolumeSpec,
)
from hivemind.hive.backends.docker.egress import cut_egress, ensure_control, restore_egress
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.backends.docker.network import (
    ControlNetwork,
    NetworkPlan,
    control_network,
    host_gateway_extra_hosts,
    network_name,
    plan_network,
)
from hivemind.hive.backends.docker.sdk_client import SdkDockerClient

__all__ = [
    "ContainerInfo",
    "ContainerSpec",
    "ControlNetwork",
    "DockerBackendConfig",
    "DockerCellBackend",
    "DockerClientError",
    "DockerClientPort",
    "DockerNetworkPort",
    "FakeDockerClient",
    "NetworkPlan",
    "NetworkSpec",
    "SdkDockerClient",
    "VolumeSpec",
    "build_docker_backend",
    "control_network",
    "cut_egress",
    "ensure_control",
    "host_gateway_extra_hosts",
    "network_name",
    "plan_network",
    "restore_egress",
]
