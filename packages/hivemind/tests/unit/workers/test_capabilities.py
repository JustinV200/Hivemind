"""Unit tests for hivemind.workers.capabilities: worker_capabilities' strict-slice guarantee."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import Isolation, OsFamily, TaskNeeds
from hivemind.cell.tiers import CombShieldLevel
from hivemind.guard import CapabilitySet
from hivemind.guard.errors import CapabilityWideningError
from hivemind.workers.capabilities import worker_capabilities


def _needs(**overrides: object) -> TaskNeeds:
    fields: dict[str, object] = {
        "isolation": Isolation.PREFERRED,
        "exoskeleton": False,
        "os": OsFamily.LINUX,
        "network_scopes": (),
        "disposable": True,
        "comb_shield": CombShieldLevel.MEADOW,
    }
    fields.update(overrides)
    return TaskNeeds(**fields)


def test_result_never_exceeds_the_wardens_own_capability_set() -> None:
    warden_caps = CapabilitySet.parse("fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*")

    slice_ = worker_capabilities(warden_caps, _needs(), Path("/scratch"))

    assert slice_.issubset(warden_caps)


def test_scratch_write_is_always_present_when_the_warden_allows_it() -> None:
    warden_caps = CapabilitySet.parse("fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*")

    slice_ = worker_capabilities(warden_caps, _needs(), Path("/scratch"))

    assert any(str(cap).startswith("fs:write:/scratch") for cap in slice_)


def test_network_scope_is_granted_only_when_needs_asks_and_warden_has_it() -> None:
    warden_caps = CapabilitySet.parse(
        "fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*", "net:api.example.com"
    )

    slice_with_need = worker_capabilities(
        warden_caps, _needs(network_scopes=("api.example.com",)), Path("/scratch")
    )
    slice_without_need = worker_capabilities(warden_caps, _needs(), Path("/scratch"))

    assert "net:api.example.com" in {str(cap) for cap in slice_with_need}
    assert not any(str(cap).startswith("net:") for cap in slice_without_need)


def test_network_scope_is_withheld_when_the_warden_lacks_it_even_if_needs_asks() -> None:
    # The Warden holds no `net:*` capability at all: needing a scope cannot invent one.
    warden_caps = CapabilitySet.parse("fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*")

    slice_ = worker_capabilities(
        warden_caps, _needs(network_scopes=("api.example.com",)), Path("/scratch")
    )

    assert not any(str(cap).startswith("net:") for cap in slice_)


def test_exoskeleton_need_grants_device_only_when_the_warden_has_it() -> None:
    warden_caps_with_device = CapabilitySet.parse(
        "fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*", "device:*"
    )
    warden_caps_without_device = CapabilitySet.parse(
        "fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*"
    )

    slice_with = worker_capabilities(
        warden_caps_with_device, _needs(exoskeleton=True), Path("/scratch")
    )
    slice_without = worker_capabilities(
        warden_caps_without_device, _needs(exoskeleton=True), Path("/scratch")
    )

    assert "device:*" in {str(cap) for cap in slice_with}
    assert not any(str(cap).startswith("device:") for cap in slice_without)


def test_a_warden_with_read_only_style_capabilities_yields_no_write_slice() -> None:
    # A Warden holding only reads (no fs:write, no exec, no tool) never grants any of those.
    warden_caps = CapabilitySet.parse("fs:read:**")

    slice_ = worker_capabilities(warden_caps, _needs(), Path("/scratch"))

    assert slice_.issubset(warden_caps)
    assert not any(str(cap).startswith("fs:write") for cap in slice_)
    assert not any(str(cap).startswith("exec") for cap in slice_)


def test_empty_warden_capabilities_yield_an_empty_slice_without_raising() -> None:
    slice_ = worker_capabilities(CapabilitySet.empty(), _needs(), Path("/scratch"))

    assert len(slice_) == 0


def test_never_raises_capability_widening_error_for_any_ordinary_needs() -> None:
    # Defensive proof (module docstring): with correct filtering, attenuate never rejects.
    warden_caps = CapabilitySet.parse(
        "fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*", "net:*", "device:*"
    )
    try:
        worker_capabilities(
            warden_caps, _needs(network_scopes=("x",), exoskeleton=True), Path("/scratch")
        )
    except CapabilityWideningError:
        pytest.fail("worker_capabilities raised CapabilityWideningError on a normal input")
