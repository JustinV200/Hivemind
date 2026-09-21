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

    VPN_TOR: never reaches this module. `DockerCellBackend.provision` refuses it before calling
    here (Night Veil needs its own image, roadmap step 5.3a, and its own routing, 5.7a).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.docker`. Called by
    `hivemind.hive.backends.docker.backend.DockerCellBackend`. Calls into hivemind.hive.models
    (NetworkPolicy) only.

Key invariants:
    - Never called with `NetworkPolicy.VPN_TOR`: `plan_network` raises `ValueError` if it is, as a
      defensive check on the caller's own prior guard, not as VPN_TOR's real error path (that is
      `hive.errors.CellProvisionError`, raised by `backend.py` before this module is ever reached).
    - `host_gateway_extra_hosts()` is added to every Cell's container regardless of policy: the
      control link (ADR-0027) must work under every network policy, NONE included.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why the control
      link must survive every network policy.
    - hivemind.hive.models for NetworkPolicy and VirtualCellSpec.
    - hivemind.hive.backends.docker.client for NetworkSpec, this module's own return shape.
    - hivemind.hive.backends.docker.backend for DockerCellBackend, this module's one caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.hive.backends.docker.client import NetworkSpec
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.ids import CellId

# Docker Desktop resolves this automatically; native Linux Engine (>=20.10) needs the special
# "host-gateway" value passed as an extra_hosts entry, which is harmless to also pass on Desktop.
HOST_GATEWAY_HOSTNAME = "host.docker.internal"
HOST_GATEWAY_VALUE = "host-gateway"

# TODO(5.7a): Docker's own network API cannot restrict outbound reach to a named allowlist of
# hosts; the in-Cell firewall Night Veil's kill-switch work adds (roadmap step 5.7a) is what will
# actually enforce this label's contents. Until then this is audit metadata only.
ALLOWLIST_LABEL = "hivemind.network_allowlist"

__all__ = [
    "ALLOWLIST_LABEL",
    "HOST_GATEWAY_HOSTNAME",
    "HOST_GATEWAY_VALUE",
    "NetworkPlan",
    "host_gateway_extra_hosts",
    "network_name",
    "plan_network",
]


@dataclass(frozen=True, slots=True)
class NetworkPlan:
    """What `DockerCellBackend.provision` needs to create and label this Cell's network.

    Attributes:
        spec: The `NetworkSpec` to pass `DockerClientPort.create_network`.
        extra_hosts: `/etc/hosts` entries every container on this network needs, host-gateway
            included, so the control link works under every policy.
    """

    spec: NetworkSpec
    extra_hosts: Mapping[str, str]


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


def plan_network(spec: VirtualCellSpec, cell_id: CellId) -> NetworkPlan:
    """Decide the Docker-level network for one Cell, per its `network_policy`.

    Args:
        spec: The Cell's own request; only `network_policy`, `network_allowlist`, `hive_id` and
            `labels` are read.
        cell_id: The Cell this network belongs to, for its deterministic name.

    Returns:
        A NetworkPlan ready for `DockerClientPort.create_network` plus the container's own
        `extra_hosts`.

    Raises:
        ValueError: `spec.network_policy` is `NetworkPolicy.VPN_TOR` (see the module docstring's
            key invariant: the real caller never reaches this with VPN_TOR).
    """
    if spec.network_policy is NetworkPolicy.VPN_TOR:
        raise ValueError(
            "plan_network never handles VPN_TOR: DockerCellBackend.provision must refuse it "
            "before calling here (Night Veil needs its own image and routing)."
        )
    labels: dict[str, str] = {"hivemind.hive_id": str(spec.hive_id), "hivemind.cell_id": cell_id}
    if spec.network_policy is NetworkPolicy.ALLOWLIST:
        # See the module docstring and the TODO(5.7a) marker above: recorded for audit and for a
        # later enforcement layer, not enforced by Docker's own network API.
        labels[ALLOWLIST_LABEL] = ",".join(spec.network_allowlist)
    return NetworkPlan(
        spec=NetworkSpec(
            name=network_name(cell_id),
            # Only NONE withholds the outbound NAT rule; EGRESS_ONLY and ALLOWLIST both need full
            # outbound reach at the Docker level (see the module docstring's per-policy summary).
            internal=spec.network_policy is NetworkPolicy.NONE,
            labels=labels,
        ),
        extra_hosts=host_gateway_extra_hosts(),
    )
