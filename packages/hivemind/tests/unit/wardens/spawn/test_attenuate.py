"""Tests for hivemind.wardens.spawn.attenuate: one sub-bee's slice, read off its TaskAssign.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/attenuate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.attenuate for the module under test.
    - hivemind.workers.capabilities for worker_capabilities, the narrowing it feeds.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from builders.wardens import make_warden_deps
from builders.workers import make_assignment

from hivemind.cell import AccessLevel
from hivemind.guard import CapabilitySet, warden_set
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn.attenuate import sub_bee_capabilities
from waggle.messages.task import TaskAssign

_SCRATCH = PurePosixPath("/hive/scratch/lease-1")


def _slice(deps: WardenDeps, assignment: TaskAssign, level: AccessLevel) -> CapabilitySet:
    """The slice a Warden on a Cell at `level` computes for `assignment`."""
    ceiling = warden_set(deps.guard, level, _SCRATCH)
    capabilities, _roots = sub_bee_capabilities(deps, ceiling, _SCRATCH, assignment)
    return capabilities


def test_a_tasks_network_scope_reaches_the_worker_as_net_on_a_full_cell() -> None:
    # Waggle 1.8: before it, a task's network needs never reached its Warden at all.
    deps, _queen_end, _warden_id = make_warden_deps()
    assignment = make_assignment(network_scopes=("api.example.com",))

    capabilities = _slice(deps, assignment, AccessLevel.FULL)

    assert "net:api.example.com" in capabilities.as_strings()


def test_a_scratch_cell_never_offers_net_whatever_the_task_needs() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    assignment = make_assignment(network_scopes=("api.example.com",))

    capabilities = _slice(deps, assignment, AccessLevel.SCRATCH)

    assert not any(text.startswith("net:") for text in capabilities.as_strings())


def test_the_goal_set_narrows_the_slice_to_what_both_sets_allow() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    assignment = make_assignment(capabilities=("fs:read:**", "llm:worker", "tool:*"))

    capabilities = _slice(deps, assignment, AccessLevel.FULL)

    assert capabilities.as_strings() == ("fs:read:**", "llm:worker", "tool:*")


def test_no_goal_set_leaves_the_role_default_within_the_warden() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()

    capabilities = _slice(deps, make_assignment(), AccessLevel.FULL)

    assert "question:human" in capabilities.as_strings()
    assert f"fs:write:{_SCRATCH.as_posix()}/**" in capabilities.as_strings()


def test_an_unreadable_goal_set_narrows_to_nothing_never_to_no_ceiling() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    assignment = make_assignment(capabilities=("this is no capability",))

    assert _slice(deps, assignment, AccessLevel.FULL).as_strings() == ()


def test_an_unreadable_network_scope_is_skipped_not_raised() -> None:
    deps, _queen_end, _warden_id = make_warden_deps()
    assignment = make_assignment(network_scopes=("exa mple.com", "api.example.com"))

    strings = _slice(deps, assignment, AccessLevel.FULL).as_strings()

    assert "net:api.example.com" in strings
    assert not any("exa mple" in text for text in strings)
