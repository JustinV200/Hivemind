"""Re-export the swarm tests' second machine: a device image, a container to run it in and a Stand.

Real Cells that are borrowed devices are proved on a machine that is not the Hive Stand's: a
container with a filesystem, a process table and a network namespace of its own, running the
Pollen Packet installed from wheels built from this tree, dialling the Hive Stand's swarm listener
over `wss://` with a pinned self-signed certificate. This package is that rig, split by
responsibility (codingrules 5.2): `image` builds the wheelhouse and the device image from
`device/Dockerfile`, `container` wraps the docker command line (start, exec, logs, process and
file snapshots, networks, cleanup), `tls` makes the certificate and its pin, and `enrolled` starts
a Stand the containers can dial and enrols a packet in a container on it. A scenario starts from
`device_image()`, a `Janitor`, `start_stand` and `enrol_container`, and leases the device with
`builders.swarm.lease_device`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    `tests/integration/test_pollen_in_container.py` and by the phase 11 end-to-end scenarios;
    needs a docker daemon (`daemon_answers` is the skip probe).

Key invariants:
    - This file holds re-exports and `__all__` only.

See Also:
    - tests/e2e/README.md for how the scenarios use it.
    - .claude/phase-11-handoff.md section 5 for the exit-criteria plan it serves.

Public API:
    - device_image, wheelhouse, ImageUnavailableError, IMAGE_VARIABLE: the device image (image).
    - Janitor, Container, ContainerSpec, Network, BRIDGE, FileEntry, CommandResult, CommandError,
      docker, run, daemon_answers: containers, networks, snapshots and cleanup (container).
    - certificate_for: the Stand's certificate for a host name, with its pin (tls).
    - start_stand, enrol_container, ContainerDevice, DeviceStartError, HIVE_HOSTNAME, DATA_DIR: a
      Stand the containers dial and a packet enrolled in a container (enrolled).
"""

from e2e.swarm.container import (
    BRIDGE,
    CommandError,
    CommandResult,
    Container,
    ContainerSpec,
    FileEntry,
    Janitor,
    Network,
    daemon_answers,
    docker,
    run,
)
from e2e.swarm.enrolled import (
    DATA_DIR,
    HIVE_HOSTNAME,
    ContainerDevice,
    DeviceStartError,
    enrol_container,
    start_stand,
)
from e2e.swarm.image import IMAGE_VARIABLE, ImageUnavailableError, device_image, wheelhouse
from e2e.swarm.tls import certificate_for

__all__ = [
    "BRIDGE",
    "DATA_DIR",
    "HIVE_HOSTNAME",
    "IMAGE_VARIABLE",
    "CommandError",
    "CommandResult",
    "Container",
    "ContainerDevice",
    "ContainerSpec",
    "DeviceStartError",
    "FileEntry",
    "ImageUnavailableError",
    "Janitor",
    "Network",
    "certificate_for",
    "daemon_answers",
    "device_image",
    "docker",
    "enrol_container",
    "run",
    "start_stand",
    "wheelhouse",
]
