"""Map VirtualCellSpec.network_policy onto what the Docker backend can actually create and enforce.

Pure functions only (codingrules 8.3: "pure core, effectful edges"); `hivemind.hive.backends.
docker.backend.DockerCellBackend` calls these to decide what network to create and how to label
it, then does the actual Docker calls itself through `DockerClientPort`. `NetworkPolicy` is defined
once, in `hivemind.hive.models`, for every backend; this module is Docker's own reading of it.

What each policy really enforces at the Docker level, and what it does not:

    NONE: an `internal` bridge network. Docker's `internal` flag withholds the outbound NAT/
    MASQUERADE rule it would otherwise install for the network, so a container on it has no route
    to the wider internet through that network. It can still reach the Docker host itself over the
    bridge (that traffic never needs the NAT rule), which is how the control link keeps working:
    every container also gets `host_gateway_extra_hosts()` as an `/etc/hosts` entry, so
    `HIVEMIND_QUEEN_WAGGLE_URL` can point at `host.docker.internal` and reach the Queen wherever
    she listens on (or is reachable from) the host, without opening the network to anything else.
    Docker Desktop limitation, stated plainly rather than assumed: Desktop (Windows, macOS) runs
    container networking through its own VM and NAT layer (vpnkit and its Windows equivalent)
    rather than the host's native netfilter rules that `internal` relies on natively on Linux;
    Docker Desktop has a documented history of not fully honouring `internal` the same way native
    Linux Engine does. Treat NONE as best-effort isolation on Docker Desktop until verified against
    the actual Desktop version in use -- the QEMU and cloud backends (roadmap 5.11, 5.12) give a
    harder boundary than a container network ever can.

    EGRESS_ONLY: a plain (non-internal) bridge network -- normal outbound NAT, so the Cell can
    reach anything. "No inbound" is not this network's job: no Virtual Cell backend ever publishes
    a port (codingrules section 15), whatever the network policy.

    ALLOWLIST: the same plain bridge network as EGRESS_ONLY -- Docker's own SDK has no built-in way
    to restrict a container's outbound reach to a named set of hosts (that needs either per-rule
    firewalling the SDK does not expose, or a proxy the Cell is configured to use). This module
    only stamps `spec.network_allowlist` onto a label for audit and for a later enforcement layer
    to read; see the TODO(5.7a) marker below for exactly what still has to close this gap.

    VPN_TOR (roadmap step 5.7a): the same plain bridge network as EGRESS_ONLY, labelled
    `hivemind.network_policy=VPN_TOR` for audit. Docker's own network API can give a Night Veil
    container outbound reach and nothing more -- it cannot itself restrict that reach to "only the
    VPN endpoint and Tor's own bootstrap addresses" the way ADR-0030 asks for. The real enforcement
    is the `night-veil-ubuntu` image's own in-container nftables kill-switch (roadmap step 5.3a),
    which runs before anything else starts (that image's own README documents the exact ordering);
    Docker's job here is only to not get in its way -- unrestricted outbound NAT, so the kill-switch
    inside the container is what actually decides what leaves it, never Docker's own network layer.
    `DockerCellBackend.provision` still refuses a VPN_TOR spec whose image is not
    `"night-veil-ubuntu"` before ever reaching here (defensive: the in-image kill-switch is the
    only real enforcement, so a spec that does not boot that image must never be accepted at all).

Cutting a RUNNING Cell's egress (isolation, roadmap step 10.6a) needs the Cell's Waggle link to
ride a network the cut leaves alone. Docker fixes a network's options when it creates it
(`internal`, the one flag above that withholds outbound NAT, cannot be set on an existing network,
and `container update` scopes resources, not egress); its one runtime lever is `network connect`/
`disconnect`, and it takes an interface away with every connection on it. So a Hive that sets
`[virtual_cells] control_subnet` dual-homes every Docker Cell (ADR-0027: the Cell still dials
out, only over a second network):

    CONTROL: one per-Hive `internal` bridge network (`control_network`), on the subnet the operator
    names, whose gateway is the host's own address on it; inter-container traffic on it is off, so
    a Cell reaches the host there and no other Cell. The Queen's listener binds that gateway, and a
    Cell dials it (`[virtual_cells] listen_host`), so the Waggle link rides this network alone. An
    `internal` network gives a container no default route, so the only thing it can reach over
    this network is the gateway: the host's own services bound to the gateway or to every
    interface, the listener among them, and nothing beyond the host.

    EGRESS: the Cell's own per-policy network above (`network_name`), attached before the Cell
    starts. It carries the default route, so it is how the Cell reaches anything else, the host's
    other addresses (`host.docker.internal`, the host gateway a provider's URL names) included.
    Disconnecting it (`hivemind.hive.backends.docker.egress.cut_egress`) leaves the Cell the
    control network alone; connecting it again restores the policy. A VPN_TOR Cell is never
    dual-homed: its link rides Tor, over its egress, so it is never cut either.

Without a control subnet a Cell has its per-policy network alone, as before, and the backend
declares no `can_cut_egress`. Under NONE that means no link at all on native Linux, since an
`internal` network routes nowhere but its own subnet and `host-gateway` is docker0's address; a
control subnet is what makes NONE work there too.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Called by
    `hivemind.hive.backends.docker.backend.DockerCellBackend`, `.egress` and the composition root
    (`control_network`). Calls into hivemind.hive.models (NetworkPolicy), this package's client
    (NetworkSpec) and waggle only.

Key invariants:
    - `host_gateway_extra_hosts()` is added to every Cell's container regardless of policy: the
      control link (ADR-0027) must work under every network policy, NONE included.
    - VPN_TOR's own network plan is created exactly like EGRESS_ONLY's (module docstring): Docker
      gives unrestricted outbound reach either way, and the difference between the two tiers is
      entirely inside the container, not in what this module asks Docker to create.
    - A dual-homed Cell is created on the control network and attached to its egress network
      before it starts; a VPN_TOR Cell never joins the control network.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why the control
      link must survive every network policy.
    - hivemind.hive.models for NetworkPolicy and VirtualCellSpec.
    - hivemind.hive.backends.docker.client for NetworkSpec, this module's own return shape.
    - hivemind.hive.backends.docker.backend for DockerCellBackend, this module's one caller.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.hive.backends.docker.client import NetworkSpec
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.ids import CellId, HiveId

# Docker Desktop resolves this automatically; native Linux Engine (>=20.10) needs the special
# "host-gateway" value passed as an extra_hosts entry, which is harmless to also pass on Desktop.
HOST_GATEWAY_HOSTNAME = "host.docker.internal"
HOST_GATEWAY_VALUE = "host-gateway"

# TODO(5.7a): Docker's own network API cannot restrict outbound reach to a named allowlist of
# hosts; the in-Cell firewall Night Veil's kill-switch work adds (roadmap step 5.7a) is what will
# actually enforce this label's contents. Until then this is audit metadata only.
ALLOWLIST_LABEL = "hivemind.network_allowlist"
# A control subnet needs the gateway plus room for Cells; smaller than a /29 holds five at most.
MIN_CONTROL_ADDRESSES = 8
_ROLE_LABEL = "hivemind.network_role"  # "control" on the per-Hive control network.

__all__ = [
    "ALLOWLIST_LABEL",
    "HOST_GATEWAY_HOSTNAME",
    "HOST_GATEWAY_VALUE",
    "MIN_CONTROL_ADDRESSES",
    "ControlNetwork",
    "NetworkPlan",
    "control_network",
    "host_gateway_extra_hosts",
    "network_name",
    "plan_network",
]


@dataclass(frozen=True, slots=True)
class ControlNetwork:
    """The per-Hive internal network every dual-homed Cell's Waggle link rides (roadmap 10.6a).

    Attributes:
        name: The network's name, recomputable from the Hive's id alone.
        spec: What `DockerNetworkPort.ensure_network` creates or reuses: internal, its subnet and
            gateway fixed, inter-container traffic off.
        gateway: The host's own address on it: where the Queen's listener binds and a Cell dials.
    """

    name: str
    spec: NetworkSpec
    gateway: str


@dataclass(frozen=True, slots=True)
class NetworkPlan:
    """What `DockerCellBackend.provision` needs to create and label this Cell's network.

    Attributes:
        spec: The `NetworkSpec` to pass `DockerClientPort.create_network`: the Cell's own
            per-policy network, its egress network when it is dual-homed.
        extra_hosts: `/etc/hosts` entries every container on this network needs, host-gateway
            included, so the control link works under every policy.
        control: The control network the container is created on, when it is dual-homed; None
            for a Cell on its own network alone (no control subnet, or VPN_TOR).
    """

    spec: NetworkSpec
    extra_hosts: Mapping[str, str]
    control: str | None = None


def network_name(cell_id: CellId) -> str:
    """Return this Cell's deterministic network name, recomputable with no other state.

    Args:
        cell_id: The Cell the network belongs to.

    Returns:
        A name `destroy()` can recompute from `cell_id` alone (codingrules Appendix A.1:
        `destroy` must be idempotent even after a process restart with no in-memory table left).
    """
    return f"hivemind-cell-{cell_id}-net"


def host_gateway_extra_hosts() -> Mapping[str, str]:
    """Return the one `/etc/hosts` entry every Cell needs to reach the Queen through the host.

    Returns:
        `{"host.docker.internal": "host-gateway"}`, safe to pass on every platform and policy.
    """
    return {HOST_GATEWAY_HOSTNAME: HOST_GATEWAY_VALUE}


def control_network(hive_id: HiveId, subnet: str) -> ControlNetwork:
    """Plan the Hive's control network on `subnet`: internal, peers apart, gateway its first host.

    Args:
        hive_id: The Hive it serves, for its name and its label.
        subnet: The operator's `[virtual_cells] control_subnet`, an IPv4 CIDR.

    Returns:
        The ControlNetwork every dual-homed Cell of this Hive joins.

    Raises:
        ValueError: `subnet` is not a private IPv4 network of at least `MIN_CONTROL_ADDRESSES`.
    """
    network = ipaddress.ip_network(subnet, strict=True)
    # A public range would let the host answer the Cells on an address the world may route to.
    if network.version != 4 or not network.is_private:
        raise ValueError(f"control subnet {subnet!r} must be a private IPv4 network")
    if network.num_addresses < MIN_CONTROL_ADDRESSES:
        raise ValueError(f"control subnet {subnet!r} holds fewer than {MIN_CONTROL_ADDRESSES}")
    gateway = str(next(network.hosts()))
    name = f"hivemind-{hive_id}-control"
    labels = {"hivemind.hive_id": str(hive_id), _ROLE_LABEL: "control"}
    spec = NetworkSpec(
        name=name,
        internal=True,
        labels=labels,
        subnet=str(network),
        gateway=gateway,
        isolates_peers=True,
    )
    return ControlNetwork(name=name, spec=spec, gateway=gateway)


def plan_network(
    spec: VirtualCellSpec, cell_id: CellId, control: ControlNetwork | None = None
) -> NetworkPlan:
    """Decide the Docker-level network for one Cell, per its `network_policy`.

    Args:
        spec: The Cell's own request; only `network_policy`, `network_allowlist`, `hive_id` and
            `labels` are read.
        cell_id: The Cell this network belongs to, for its deterministic name.
        control: The Hive's control network, when it has one: the Cell is dual-homed on it and
            its own network, unless it is VPN_TOR (module docstring).

    Returns:
        A NetworkPlan ready for `DockerClientPort.create_network` plus the container's own
        `extra_hosts` and the control network it is created on, if any.
    """
    labels: dict[str, str] = {"hivemind.hive_id": str(spec.hive_id), "hivemind.cell_id": cell_id}
    if spec.network_policy is NetworkPolicy.ALLOWLIST:
        # See the module docstring and the TODO(5.7a) marker above: recorded for audit and for a
        # later enforcement layer, not enforced by Docker's own network API.
        labels[ALLOWLIST_LABEL] = ",".join(spec.network_allowlist)
    if spec.network_policy is NetworkPolicy.VPN_TOR:
        # Audit metadata only, exactly like ALLOWLIST_LABEL above: the real enforcement is the
        # night-veil-ubuntu image's own nftables kill-switch (module docstring's VPN_TOR entry).
        labels["hivemind.network_policy"] = NetworkPolicy.VPN_TOR.value
    return NetworkPlan(
        spec=NetworkSpec(
            name=network_name(cell_id),
            # Only NONE withholds the outbound NAT rule; every other policy, VPN_TOR included,
            # needs full outbound reach at the Docker level (module docstring's per-policy
            # summary: VPN_TOR's own restriction is enforced inside the container, not here).
            internal=spec.network_policy is NetworkPolicy.NONE,
            labels=labels,
        ),
        extra_hosts=host_gateway_extra_hosts(),
        # A VPN_TOR Cell's link rides Tor over its own network: never dual-homed (module docstring).
        control=None
        if control is None or spec.network_policy is NetworkPolicy.VPN_TOR
        else control.name,
    )
