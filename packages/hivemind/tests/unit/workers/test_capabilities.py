"""Tests for hivemind.workers.capabilities: worker_capabilities' strict-slice guarantee.

Fits into the Hive:
    Mirrors src/hivemind/workers/capabilities.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.capabilities for the module under test.
    - hivemind.guard.policy.roles for role_set, which builds the role default these tests pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import Isolation, OsFamily, TaskNeeds
from hivemind.cell.tiers import CombShieldLevel
from hivemind.guard import Capability, CapabilitySet, load_guard_policy, role_set
from hivemind.guard.errors import CapabilityWideningError, InvalidCapabilityError
from hivemind.workers.capabilities import worker_capabilities

# The shipped Drone default over a /scratch lease: scratch writes, reads, exec, every tool, its
# slot, spend, questions, Cell Wax and Honey reads (hivemind.guard.defaults' policy.toml).
_DRONE = role_set(load_guard_policy(), "drone", Path("/scratch"))
# What phase 3's baseline gave every Worker; the shipped Drone default must still include it.
_PHASE_THREE_BASELINE = {"fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*"}


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


def _warden(*extra: str) -> CapabilitySet:
    """A SCRATCH-shaped Warden set that holds the whole Drone default, plus `extra`."""
    return CapabilitySet.parse(*_DRONE.as_strings(), *extra)


def test_result_never_exceeds_the_wardens_own_capability_set() -> None:
    warden_caps = CapabilitySet.parse("fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*")

    slice_ = worker_capabilities(warden_caps, _DRONE, _needs())

    assert slice_.issubset(warden_caps)


def test_the_drone_default_still_grants_the_phase_three_baseline() -> None:
    slice_ = worker_capabilities(_warden(), _DRONE, _needs())

    assert set(slice_.as_strings()) >= _PHASE_THREE_BASELINE
    assert set(slice_.as_strings()) == set(_DRONE.as_strings())


def test_a_role_default_entry_the_warden_lacks_is_withheld() -> None:
    warden_caps = CapabilitySet.parse("fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*")

    slice_ = worker_capabilities(warden_caps, _DRONE, _needs())

    assert "llm:worker" not in slice_.as_strings()


def test_network_scope_is_granted_only_when_needs_asks_and_warden_has_it() -> None:
    warden_caps = _warden("net:api.example.com")

    slice_with_need = worker_capabilities(
        warden_caps, _DRONE, _needs(network_scopes=("api.example.com",))
    )
    slice_without_need = worker_capabilities(warden_caps, _DRONE, _needs())

    assert "net:api.example.com" in slice_with_need.as_strings()
    assert not any(str(cap).startswith("net:") for cap in slice_without_need)


def test_network_scope_is_withheld_when_the_warden_lacks_it_even_if_needs_asks() -> None:
    # The Warden holds no `net` capability at all: needing a scope cannot invent one.
    slice_ = worker_capabilities(_warden(), _DRONE, _needs(network_scopes=("api.example.com",)))

    assert not any(str(cap).startswith("net:") for cap in slice_)


def test_a_malformed_network_scope_is_refused_rather_than_granted() -> None:
    with pytest.raises(InvalidCapabilityError):
        worker_capabilities(_warden("net:*"), _DRONE, _needs(network_scopes=("api.*",)))


def _exoskeleton_scopes(slice_: CapabilitySet) -> set[str]:
    """The exoskeleton scopes a Worker's slice grants, as strings."""
    return {str(cap) for cap in slice_ if str(cap).startswith("exoskeleton:")}


def test_a_desktop_need_grants_display_and_browser_where_the_warden_has_them() -> None:
    warden_caps = _warden("exoskeleton:display", "exoskeleton:browser")

    slice_ = worker_capabilities(warden_caps, _DRONE, _needs(exoskeleton=True))

    # real_display is asked for too, but this Warden was never granted it, so it is dropped.
    assert _exoskeleton_scopes(slice_) == {"exoskeleton:display", "exoskeleton:browser"}


def test_the_operators_real_display_reaches_a_worker_only_through_its_wardens_grant() -> None:
    slice_ = worker_capabilities(_warden("exoskeleton:*"), _DRONE, _needs(exoskeleton=True))

    assert "exoskeleton:real_display" in _exoskeleton_scopes(slice_)


def test_a_browser_only_need_grants_the_browser_alone() -> None:
    needs = _needs(exoskeleton=True, browser_only=True)

    slice_ = worker_capabilities(_warden("exoskeleton:*"), _DRONE, needs)

    assert _exoskeleton_scopes(slice_) == {"exoskeleton:browser"}


def test_an_audio_need_adds_audio() -> None:
    needs = _needs(exoskeleton=True, audio=True)

    slice_ = worker_capabilities(_warden("exoskeleton:*"), _DRONE, needs)

    assert "exoskeleton:audio" in _exoskeleton_scopes(slice_)


def test_no_exoskeleton_need_grants_no_peripheral_even_to_a_warden_holding_all_of_them() -> None:
    slice_ = worker_capabilities(_warden("exoskeleton:*"), _DRONE, _needs())

    assert _exoskeleton_scopes(slice_) == set()


def test_a_warden_with_read_only_style_capabilities_yields_no_write_slice() -> None:
    # A Warden holding only reads (no fs:write, no exec) never grants any of those.
    warden_caps = CapabilitySet.parse("fs:read:**", "tool:*")

    slice_ = worker_capabilities(warden_caps, _DRONE, _needs())

    assert slice_.issubset(warden_caps)
    assert not any(str(cap).startswith("fs:write") for cap in slice_)
    assert not any(str(cap).startswith("exec") for cap in slice_)


def test_empty_warden_capabilities_yield_an_empty_slice_without_raising() -> None:
    slice_ = worker_capabilities(CapabilitySet.empty(), _DRONE, _needs())

    assert len(slice_) == 0


def test_extra_write_roots_are_granted_when_the_warden_holds_an_unconfined_fs_write() -> None:
    """Roadmap step 5.0e: FULL access's own fs:write:** covers a keep_root/leaving root too."""
    warden_caps = _warden("fs:write:**")

    slice_ = worker_capabilities(
        warden_caps, _DRONE, _needs(), extra_write_roots=(Path("/keep/artifact.exe"),)
    )

    assert slice_.issubset(warden_caps)
    granted = set(slice_.as_strings())
    assert "fs:write:/keep/artifact.exe" in granted
    assert "fs:write:/keep/artifact.exe/**" in granted


def test_an_extra_write_root_with_glob_characters_grants_only_itself() -> None:
    # Unescaped, "/keep/build*" would grant a write to every sibling it matches as a pattern.
    warden_caps = _warden("fs:write:**")

    slice_ = worker_capabilities(
        warden_caps, _DRONE, _needs(), extra_write_roots=(Path("/keep/build*"),)
    )

    assert slice_.allows(Capability.parse("fs:write:/keep/build*/out.exe"))
    assert not slice_.allows(Capability.parse("fs:write:/keep/build-other/out.exe"))
    assert not slice_.allows(Capability.parse("fs:write:/keep/buildX"))


def test_extra_write_roots_are_withheld_when_the_warden_lacks_an_unconfined_fs_write() -> None:
    """A SCRATCH-level Warden never holds a wider fs:write; nothing is granted."""
    slice_ = worker_capabilities(
        _warden(), _DRONE, _needs(), extra_write_roots=(Path("/keep/artifact.exe"),)
    )

    assert not any("keep" in str(cap) for cap in slice_)


def test_never_raises_capability_widening_error_for_any_ordinary_needs() -> None:
    # Defensive proof (module docstring): with correct filtering, attenuate never rejects.
    warden_caps = _warden("net:*", "device:*")
    try:
        worker_capabilities(warden_caps, _DRONE, _needs(network_scopes=("x",), exoskeleton=True))
    except CapabilityWideningError:
        pytest.fail("worker_capabilities raised CapabilityWideningError on a normal input")


def test_every_drone_is_granted_both_honey_tools_by_name_when_the_warden_allows_tools() -> None:
    """Roadmap step 7.8: `tool:recall`/`tool:remember` are named in the Drone's role default."""
    warden_caps = CapabilitySet.parse("fs:write:/scratch/**", "fs:read:**", "exec:*", "tool:*")

    names = set(worker_capabilities(warden_caps, _DRONE, _needs()).as_strings())

    assert {"tool:recall", "tool:remember"} <= names


def test_a_warden_granting_no_tools_grants_no_honey_tool_either() -> None:
    names = set(
        worker_capabilities(CapabilitySet.parse("fs:read:**"), _DRONE, _needs()).as_strings()
    )

    assert not {"tool:recall", "tool:remember"} & names
