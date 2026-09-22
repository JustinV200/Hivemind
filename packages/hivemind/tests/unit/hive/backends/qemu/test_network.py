"""Unit tests for hivemind.hive.backends.qemu.network: NetworkPolicy -> QEMU user-net mapping.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/network.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.network for plan_network, under test.
"""

from __future__ import annotations

from builders.forage import make_capacity

from hivemind.cell import CombShieldLevel
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.qemu.network import (
    ALLOWLIST_LABEL,
    GUEST_CONTROL_ADDR,
    GUEST_CONTROL_PORT,
    USER_NET_HOST_ALIAS,
    plan_network,
)
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


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


def _make_endpoint(waggle_url: str) -> QueenEndpoint:
    """Build a QueenEndpoint at `waggle_url`; no test in this suite asserts on the other fields."""
    return QueenEndpoint(
        waggle_url=waggle_url,
        queen_node_id=new_node_id(FakeClock()),
        queen_verify_key_hex="00" * 32,
    )


def test_plan_network_egress_only_rewrites_a_loopback_endpoint_to_the_host_alias() -> None:
    spec = _make_spec(network_policy=NetworkPolicy.EGRESS_ONLY)
    endpoint = _make_endpoint("ws://localhost:8710")

    plan = plan_network(spec, endpoint)

    assert plan.netdev_arg == "user,id=net0"
    assert plan.queen_waggle_url == f"ws://{USER_NET_HOST_ALIAS}:8710"
    assert plan.relay_target is None


def test_plan_network_egress_only_leaves_a_non_loopback_endpoint_unchanged() -> None:
    spec = _make_spec(network_policy=NetworkPolicy.EGRESS_ONLY)
    endpoint = _make_endpoint("wss://queen.example.org:8443")

    plan = plan_network(spec, endpoint)

    assert plan.queen_waggle_url == "wss://queen.example.org:8443"


def test_plan_network_allowlist_behaves_like_egress_only_for_networking() -> None:
    spec = _make_spec(
        network_policy=NetworkPolicy.ALLOWLIST, network_allowlist=("api.example.com",)
    )
    endpoint = _make_endpoint("ws://localhost:8710")

    plan = plan_network(spec, endpoint)

    assert plan.netdev_arg == "user,id=net0"
    assert plan.queen_waggle_url == f"ws://{USER_NET_HOST_ALIAS}:8710"


def test_plan_network_none_restricts_and_adds_a_guestfwd_relay() -> None:
    spec = _make_spec(network_policy=NetworkPolicy.NONE)
    endpoint = _make_endpoint("ws://queen.internal:9000")

    plan = plan_network(spec, endpoint)

    assert "restrict=on" in plan.netdev_arg
    assert f"guestfwd=tcp:{GUEST_CONTROL_ADDR}:{GUEST_CONTROL_PORT}-cmd:" in plan.netdev_arg
    assert plan.queen_waggle_url == f"ws://{GUEST_CONTROL_ADDR}:{GUEST_CONTROL_PORT}"
    assert plan.relay_target == ("queen.internal", 9000)


def test_plan_network_vpn_tor_behaves_like_egress_only_for_networking() -> None:
    spec = _make_spec(
        image="night-veil-ubuntu",
        network_policy=NetworkPolicy.VPN_TOR,
        comb_shield=CombShieldLevel.NIGHT_VEIL,
    )
    endpoint = _make_endpoint("ws://localhost:8710")

    plan = plan_network(spec, endpoint)

    # Roadmap step 5.7a: QEMU gives the same unrestricted outbound reach as EGRESS_ONLY -- the
    # in-guest nftables kill-switch is VPN_TOR's real enforcement, not this network's own shape.
    assert plan.netdev_arg == "user,id=net0"
    assert plan.queen_waggle_url == f"ws://{USER_NET_HOST_ALIAS}:8710"


def test_allowlist_label_constant_matches_docker_backends_own_convention() -> None:
    # Mirrors hivemind.hive.backends.docker.network.ALLOWLIST_LABEL's own name, deliberately: an
    # operator auditing labels across backends should see the same key regardless of which one
    # provisioned a given Cell.
    assert ALLOWLIST_LABEL == "hivemind.network_allowlist"
