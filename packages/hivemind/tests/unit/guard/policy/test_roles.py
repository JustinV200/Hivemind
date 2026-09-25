"""Tests for hivemind.guard.policy.roles: role_set, warden_set, proposed_set, worker_role_name.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/roles.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.roles for the module under test.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.cell import AccessLevel
from hivemind.guard.access import governs
from hivemind.guard.capabilities import Capability, CapabilityFamily
from hivemind.guard.errors import GuardPolicyError
from hivemind.guard.policy.defaults import load_guard_policy
from hivemind.guard.policy.roles import proposed_set, role_set, warden_set, worker_role_name
from hivemind.guard.policy.table import POLICY_ROLES
from hivemind.manifest import GuardSection
from waggle.messages.task import WorkerRole

_POLICY = load_guard_policy()
_SCRATCH = Path("/hive/scratch")
# codingrules 14.3: a deterministic example budget and no per-test deadline.
_SETTINGS = settings(max_examples=50, deadline=None)


# ──────────────────────────────────────────────────────────────────────────────
# role_set
# ──────────────────────────────────────────────────────────────────────────────


def test_role_set_fills_the_scratch_placeholder_with_the_posix_root() -> None:
    drone = role_set(_POLICY, "drone", _SCRATCH)

    assert drone.allows(Capability.parse("fs:write:/hive/scratch/sub/out.txt"))
    assert not drone.allows(Capability.parse("fs:write:/etc/passwd"))


def test_role_set_fills_a_windows_root_so_both_separators_match() -> None:
    drone = role_set(_POLICY, "drone", PureWindowsPath("C:\\scratch"))

    assert drone.allows(Capability.parse("fs:write:C:/scratch/out.txt"))


def test_role_set_needs_no_root_for_a_role_that_names_no_scratch() -> None:
    assert role_set(_POLICY, "device").allows(Capability.parse("observe"))


def test_role_set_refuses_a_scratch_role_without_a_root() -> None:
    with pytest.raises(GuardPolicyError, match="no scratch root"):
        role_set(_POLICY, "drone")


def test_role_set_refuses_an_unknown_role() -> None:
    with pytest.raises(GuardPolicyError, match="no role 'beekeeper'"):
        role_set(_POLICY, "beekeeper", _SCRATCH)


# ──────────────────────────────────────────────────────────────────────────────
# warden_set: role default, narrowed to the Cell's level, less the deny list
# ──────────────────────────────────────────────────────────────────────────────


def test_a_read_only_warden_keeps_every_family_that_does_not_touch_the_cell() -> None:
    warden = warden_set(_POLICY, AccessLevel.READ_ONLY, _SCRATCH)

    for spec in ["question:human", "llm:warden", "tool:x", "spend:5", "forage:request"]:
        assert warden.allows(Capability.parse(spec)), spec
    governed = {capability.family for capability in warden if governs(capability.family)}
    assert governed == {CapabilityFamily.FS_READ}


def test_a_scratch_warden_writes_only_inside_its_scratch_and_reaches_no_network() -> None:
    warden = warden_set(_POLICY, AccessLevel.SCRATCH, _SCRATCH)

    assert warden.allows(Capability.parse("fs:write:/hive/scratch/out.txt"))
    assert not warden.allows(Capability.parse("fs:write:/etc/passwd"))
    assert warden.allows(Capability.parse("exec:ls"))
    assert not warden.allows(Capability.parse("net:api.example.com"))


def test_a_full_warden_holds_its_whole_role_default_once_its_operator_allows_the_display() -> None:
    real_display = Capability.parse("exoskeleton:real_display")

    warden = warden_set(_POLICY, AccessLevel.FULL, _SCRATCH)
    opted_in = warden_set(_POLICY, AccessLevel.FULL, _SCRATCH, real_display=True)

    assert opted_in == role_set(_POLICY, "warden", _SCRATCH)
    # The operator's own screen only where that Cell's operator allowed it (ADR-0031).
    assert not warden.allows(real_display)
    assert opted_in.allows(real_display)
    assert warden.allows(Capability.parse("exoskeleton:display"))


def test_a_scratch_warden_keeps_only_the_browser_of_the_exoskeleton() -> None:
    warden = warden_set(_POLICY, AccessLevel.SCRATCH, _SCRATCH, real_display=True)

    assert warden.allows(Capability.parse("exoskeleton:browser"))
    assert not warden.allows(Capability.parse("exoskeleton:display"))
    assert not warden.allows(Capability.parse("exoskeleton:real_display"))


def test_warden_set_drops_every_entry_the_deny_list_covers() -> None:
    policy = load_guard_policy(None, GuardSection(deny=("exoskeleton:*", "tactic:*")))

    warden = warden_set(policy, AccessLevel.FULL, _SCRATCH, real_display=True)

    assert not warden.allows(Capability.parse("exoskeleton:display"))
    assert not warden.allows(Capability.parse("exoskeleton:real_display"))
    assert not warden.allows(Capability.parse("tactic:write_like_human"))
    assert warden.allows(Capability.parse("tool:x"))


@given(level=st.sampled_from(list(AccessLevel)))
@_SETTINGS
def test_warden_set_is_always_a_subset_of_the_role_default(level: AccessLevel) -> None:
    warden = warden_set(_POLICY, level, _SCRATCH)

    assert warden.capabilities <= role_set(_POLICY, "warden", _SCRATCH).capabilities


# ──────────────────────────────────────────────────────────────────────────────
# proposed_set and worker_role_name
# ──────────────────────────────────────────────────────────────────────────────


def test_proposed_set_is_the_device_roles_proposed_list() -> None:
    assert proposed_set(_POLICY) == _POLICY.roles["device"].proposed


@pytest.mark.parametrize("role", list(WorkerRole))
def test_every_worker_role_names_a_policy_role(role: WorkerRole) -> None:
    assert worker_role_name(role) in POLICY_ROLES


def test_worker_role_name_is_the_lowercase_wire_name() -> None:
    assert worker_role_name(WorkerRole.GUARD_BEE) == "guard_bee"
