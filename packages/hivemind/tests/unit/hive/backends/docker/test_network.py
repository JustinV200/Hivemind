"""Unit tests for hivemind.hive.backends.docker.network: NetworkPolicy -> Docker network mapping.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/docker/network.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.docker.network for plan_network, host_gateway_extra_hosts and
      network_name, under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.docker.network import (
    ALLOWLIST_LABEL,
    HOST_GATEWAY_HOSTNAME,
    HOST_GATEWAY_VALUE,
    control_network,
    host_gateway_extra_hosts,
    network_name,
    plan_network,
)
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id


def _make_spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec, with sensible defaults for every field a test ignores."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 2.0,
        "memory_bytes": 2 * 1024**3,
        "disk_bytes": 10 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def test_network_name_is_deterministic_from_cell_id_alone() -> None:
    cell_id = new_cell_id(FakeClock())

    assert network_name(cell_id) == network_name(cell_id)
    assert cell_id in network_name(cell_id)


def test_host_gateway_extra_hosts_is_the_one_documented_entry() -> None:
    assert host_gateway_extra_hosts() == {HOST_GATEWAY_HOSTNAME: HOST_GATEWAY_VALUE}


def test_plan_network_none_policy_is_internal() -> None:
    cell_id = new_cell_id(FakeClock())
    spec = _make_spec(network_policy=NetworkPolicy.NONE)

    plan = plan_network(spec, cell_id)

    assert plan.spec.internal is True
    assert plan.spec.name == network_name(cell_id)
    # Every policy, NONE included, still needs the control link to reach the Queen.
    assert plan.extra_hosts == host_gateway_extra_hosts()


def test_plan_network_egress_only_policy_is_not_internal() -> None:
    cell_id = new_cell_id(FakeClock())
    spec = _make_spec(network_policy=NetworkPolicy.EGRESS_ONLY)

    plan = plan_network(spec, cell_id)

    assert plan.spec.internal is False
    assert ALLOWLIST_LABEL not in plan.spec.labels


def test_plan_network_allowlist_policy_is_not_internal_and_labels_the_allowlist() -> None:
    cell_id = new_cell_id(FakeClock())
    spec = _make_spec(
        network_policy=NetworkPolicy.ALLOWLIST,
        network_allowlist=("api.example.com", "cdn.example.com"),
    )

    plan = plan_network(spec, cell_id)

    assert plan.spec.internal is False
    assert plan.spec.labels[ALLOWLIST_LABEL] == "api.example.com,cdn.example.com"


def test_plan_network_stamps_hive_id_and_cell_id_labels() -> None:
    cell_id = new_cell_id(FakeClock())
    spec = _make_spec()

    plan = plan_network(spec, cell_id)

    assert plan.spec.labels["hivemind.hive_id"] == spec.hive_id
    assert plan.spec.labels["hivemind.cell_id"] == cell_id


def test_plan_network_vpn_tor_policy_is_not_internal_and_labels_the_policy() -> None:
    cell_id = new_cell_id(FakeClock())
    # VirtualCellSpec's own validator requires VPN_TOR and NIGHT_VEIL together (hive/models.py).
    spec = _make_spec(
        image="night-veil-ubuntu",
        network_policy=NetworkPolicy.VPN_TOR,
        comb_shield=CombShieldLevel.NIGHT_VEIL,
    )

    plan = plan_network(spec, cell_id)

    # Roadmap step 5.7a: Docker gives the same unrestricted outbound reach as EGRESS_ONLY -- the
    # in-image nftables kill-switch is VPN_TOR's real enforcement, not this network's own shape.
    assert plan.spec.internal is False
    assert plan.spec.labels["hivemind.network_policy"] == "VPN_TOR"


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 10.6a: the per-Hive control network, and the dual-homed plan.
# ──────────────────────────────────────────────────────────────────────────────


def test_control_network_is_internal_keeps_peers_apart_and_gates_on_its_first_host() -> None:
    hive_id = new_hive_id(FakeClock())

    control = control_network(hive_id, "10.213.7.0/24")

    assert control.gateway == "10.213.7.1"
    assert control.name == f"hivemind-{hive_id}-control"
    assert control.spec.internal is True
    assert control.spec.isolates_peers is True
    assert (control.spec.subnet, control.spec.gateway) == ("10.213.7.0/24", "10.213.7.1")
    assert control.spec.labels["hivemind.hive_id"] == hive_id


@pytest.mark.parametrize(
    "subnet",
    ["8.8.8.0/24", "10.213.7.0/30", "fd00::/64", "10.213.7.1/24", "not-a-subnet"],
)
def test_control_network_refuses_a_subnet_the_cells_could_not_use(subnet: str) -> None:
    # Public, too small for the gateway and a few Cells, IPv6, host bits set, or no subnet at all.
    with pytest.raises(ValueError, match=r"\S"):
        control_network(new_hive_id(FakeClock()), subnet)


def test_plan_network_dual_homes_a_cell_when_the_hive_has_a_control_network() -> None:
    cell_id = new_cell_id(FakeClock())
    spec = _make_spec(network_policy=NetworkPolicy.EGRESS_ONLY)
    control = control_network(spec.hive_id, "10.213.7.0/24")

    plan = plan_network(spec, cell_id, control)

    # Created on the control network; its own network, the egress, is attached before it starts.
    assert plan.control == control.name
    assert plan.spec.name == network_name(cell_id)
    assert plan.spec.internal is False


def test_plan_network_leaves_a_cell_single_homed_without_a_control_network() -> None:
    plan = plan_network(_make_spec(), new_cell_id(FakeClock()))

    assert plan.control is None


def test_plan_network_never_dual_homes_a_vpn_tor_cell() -> None:
    # Its link rides Tor over its own network, so the control network would give it nothing but a
    # path to the host that bypasses Tor.
    spec = _make_spec(
        image="night-veil-ubuntu",
        network_policy=NetworkPolicy.VPN_TOR,
        comb_shield=CombShieldLevel.NIGHT_VEIL,
    )

    plan = plan_network(
        spec, new_cell_id(FakeClock()), control_network(spec.hive_id, "10.9.0.0/24")
    )

    assert plan.control is None
