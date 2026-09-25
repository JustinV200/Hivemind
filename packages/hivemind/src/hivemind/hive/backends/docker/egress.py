"""Cut and restore a dual-homed Docker Cell's egress, leaving its control network alone.

Isolation (roadmap step 10.6a, ADR-0043) sets a Cell's egress to none except its Waggle link. A
Docker Cell of a Hive with a control subnet is dual-homed (`hivemind.hive.backends.docker.network`
has the design): its link rides the per-Hive internal control network, and everything else rides
its own per-policy network, which carries the default route. Cutting is detaching the container
from that own network; restoring is attaching it again. The link's connection lives on the other
interface, so it never notices: no redial, and no missed heartbeat. Both levers read which
networks the container is on first, so they are idempotent, and both refuse a Cell that is not on
the control network (provisioned before the control subnet was set, or a VPN_TOR Cell, whose link
rides Tor over its egress): cutting its egress would cut its link, which isolation must keep.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Called by
    `hivemind.hive.backends.docker.backend.DockerCellBackend` (its `EgressCutter` half and its
    provisioning) and by the composition root, which ensures the control network before the
    Queen's listener binds its gateway. Calls into `hivemind.hive.errors`, this package's client
    (DockerClientPort, DockerClientError) and network (ControlNetwork, network_name), and waggle.

Key invariants:
    - The control network is never touched by a cut or a restore, and never removed by a Cell's
      destroy: it is the Hive's, shared by every Cell.
    - A Cell whose container is not on the control network is never cut: the refusal is a
      `BackendCapabilityError`, which the isolation records as `unsupported` for that Cell.

See Also:
    - hivemind.hive.backends.docker.network for the dual-homing design and its limits.
    - hivemind.hive.egress for LifecycleEgress, which calls the backend's cut and restore.
    - docs/guard/isolation.md, "Egress on the backends".
"""

from __future__ import annotations

from hivemind.hive.backends.docker.client import (
    ContainerInfo,
    DockerClientError,
    DockerClientPort,
    DockerNetworkPort,
)
from hivemind.hive.backends.docker.network import ControlNetwork, network_name
from hivemind.hive.errors import BackendCapabilityError, CellEgressError, UnknownCellError
from waggle.ids import CellId

_CELL_LABEL = "hivemind.cell_id"  # Every Cell's container carries it (backend's own labels).
_BACKEND_NAME = "docker"  # The backend these levers belong to, for every error they raise.

__all__ = ["cut_egress", "ensure_control", "restore_egress"]


async def ensure_control(client: DockerNetworkPort, control: ControlNetwork) -> None:
    """Create the Hive's control network, or reuse it when it already exists as planned.

    Args:
        client: The Docker client (its network slice).
        control: The Hive's control network.

    Raises:
        CellEgressError: The daemon refused it, or a network of its name exists that is not
            internal on the planned subnet, so no Cell may be attached to it.
    """
    try:
        await client.ensure_network(control.spec)
    except DockerClientError as exc:
        raise CellEgressError(_BACKEND_NAME, control.name, str(exc)) from exc


async def cut_egress(client: DockerClientPort, control: ControlNetwork, cell_id: CellId) -> None:
    """Detach `cell_id` from its own network, leaving it the control network alone.

    Args:
        client: The Docker client.
        control: The Hive's control network, which the Cell's link rides.
        cell_id: The running Cell to cut off.

    Raises:
        UnknownCellError: No container carries the Cell's id.
        BackendCapabilityError: The Cell is not on the control network (module docstring).
        CellEgressError: The daemon refused the detachment.
    """
    container = await _dual_homed(client, control, cell_id, "cut_egress")
    try:
        await client.disconnect_network(network_name(cell_id), container.name)
    except DockerClientError as exc:
        raise CellEgressError(_BACKEND_NAME, cell_id, str(exc)) from exc


async def restore_egress(
    client: DockerClientPort, control: ControlNetwork, cell_id: CellId
) -> None:
    """Attach `cell_id` to its own network again, giving it back its network policy.

    Args:
        client: The Docker client.
        control: The Hive's control network, which the Cell's link rides.
        cell_id: The Cell whose isolation the human lifted.

    Raises:
        UnknownCellError: No container carries the Cell's id.
        BackendCapabilityError: The Cell is not on the control network (module docstring).
        CellEgressError: The daemon refused the attachment.
    """
    container = await _dual_homed(client, control, cell_id, "restore_egress")
    try:
        await client.connect_network(network_name(cell_id), container.name)
    except DockerClientError as exc:
        raise CellEgressError(_BACKEND_NAME, cell_id, str(exc)) from exc


async def _dual_homed(
    client: DockerClientPort, control: ControlNetwork, cell_id: CellId, lever: str
) -> ContainerInfo:
    """Return the Cell's container, once it is known to be on the control network."""
    try:
        found = await client.list_containers({_CELL_LABEL: cell_id})
    except DockerClientError as exc:
        raise CellEgressError(_BACKEND_NAME, cell_id, str(exc)) from exc
    if not found:
        raise UnknownCellError(_BACKEND_NAME, cell_id)
    container = found[0]
    # Its link rides the control network only if the container is on it; otherwise the link
    # rides the very network this lever would take away.
    if control.name not in container.networks:
        raise BackendCapabilityError(_BACKEND_NAME, lever, cell_id=cell_id)
    return container
